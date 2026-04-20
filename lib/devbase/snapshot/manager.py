"""スナップショット管理のコアロジック"""

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from devbase.errors import SnapshotError
from devbase.log import get_logger

logger = get_logger(__name__)

VOLUME_NAME = 'devbase_home_ubuntu'
SNAPSHOT_IMAGE = 'devbase-snapshot:latest'
DEFAULT_MAX_GENERATIONS = 3
DEFAULT_MAX_INCREMENTALS = 10
METADATA_FILE = 'snapshot.yml'
_VALID_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


class SnapshotManager:
    """Docker volumeのスナップショット管理"""

    def __init__(self, devbase_root: Path):
        self.devbase_root = devbase_root
        self.backups_dir = devbase_root / 'backups'
        self.backups_dir.mkdir(exist_ok=True)
        self._metadata_path = self.backups_dir / METADATA_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> None:
        """スナップショット名のバリデーション（パストラバーサル防止）"""
        if not name or not _VALID_NAME_RE.match(name):
            raise SnapshotError(
                f"無効なスナップショット名: '{name}' "
                "(英数字・ハイフン・アンダースコア・ドットのみ使用可能、先頭は英数字)"
            )

    def _safe_snap_dir(self, name: str) -> Path:
        """名前からスナップショットディレクトリを安全に解決する"""
        self._validate_name(name)
        snap_dir = (self.backups_dir / name).resolve()
        if not str(snap_dir).startswith(str(self.backups_dir.resolve())):
            raise SnapshotError(f"無効なスナップショットパス: '{name}'")
        return snap_dir

    def create(self, name: Optional[str] = None, full: bool = False) -> str:
        """スナップショットを作成する。

        Args:
            name: スナップショット名（省略時はタイムスタンプ）
            full: Trueならフルバックアップを強制

        Returns:
            作成されたスナップショット名
        """
        if name is None:
            name = datetime.now().strftime('%Y%m%d-%H%M%S')

        snap_dir = self._safe_snap_dir(name)
        is_new = not snap_dir.exists()

        if is_new:
            snap_dir.mkdir(parents=True)
            full = True  # 初回は常にフル

        if full:
            self._create_full(name, snap_dir)
        else:
            self._create_incremental(name, snap_dir)

        self._update_global_metadata(name, snap_dir)
        return name

    def list(self) -> list[dict]:
        """スナップショット一覧を返す"""
        meta = self._load_metadata()
        snapshots = meta.get('snapshots', [])
        # ディレクトリの実サイズも取得
        for snap in snapshots:
            snap_dir = self.backups_dir / snap['name']
            if snap_dir.exists():
                snap['size_bytes'] = sum(
                    f.stat().st_size for f in snap_dir.iterdir() if f.is_file()
                )
            else:
                snap['size_bytes'] = 0
        return snapshots

    def restore(self, name: str, point: int | None = None) -> None:
        """スナップショットから復元する。

        Args:
            name: スナップショット名
            point: 差分の適用上限（例: 3なら incr-003 まで適用）。
                   Noneなら全差分を適用。
        """
        if point is not None and point <= 0:
            raise SnapshotError(f"--point は正の整数である必要があります: {point}")
        snap_dir = self._safe_snap_dir(name)
        if not snap_dir.exists():
            raise SnapshotError(f"スナップショット '{name}' が見つかりません")

        full_archive = snap_dir / 'full.tar.zst'
        if not full_archive.exists():
            raise SnapshotError(f"フルバックアップが見つかりません: {full_archive}")

        # 復元前に現在の状態を自動バックアップ
        pre_restore_name = f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        logger.info("復元前に現在の状態をバックアップします: %s", pre_restore_name)
        try:
            self.create(name=pre_restore_name, full=True)
        except Exception as e:
            logger.warning("復元前バックアップに失敗しましたが続行します: %s", e)

        # フルバックアップの復元
        logger.info("フルバックアップを復元中...")
        self._run_docker_tar(
            snap_dir, 'restore',
            "cd /target && find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null; "
            "zstd -d /backup/full.tar.zst -c | tar --listed-incremental=/dev/null -xf -"
        )

        # 差分バックアップを順番に適用（pointが指定されていればそこまで）
        incr_re = re.compile(r'^incr-(\d+)\.tar\.zst$')
        incr_files = sorted(snap_dir.glob('incr-*.tar.zst'))
        for incr in incr_files:
            if point is not None:
                m = incr_re.match(incr.name)
                if not m:
                    continue
                if int(m.group(1)) > point:
                    break
            logger.info("差分バックアップを適用中: %s", incr.name)
            self._run_docker_tar(
                snap_dir, 'restore',
                f"cd /target && zstd -d /backup/{incr.name} -c | tar --listed-incremental=/dev/null -xf -"
            )

        if point is not None:
            logger.info("復元完了: %s (incr-%03d まで)", name, point)
        else:
            logger.info("復元完了: %s", name)

    def copy(self, name: str, new_name: str) -> None:
        """スナップショットをコピーする"""
        src = self._safe_snap_dir(name)
        dst = self._safe_snap_dir(new_name)
        if not src.exists():
            raise SnapshotError(f"スナップショット '{name}' が見つかりません")
        if dst.exists():
            raise SnapshotError(f"スナップショット '{new_name}' は既に存在します")

        shutil.copytree(src, dst)

        # メタデータを更新
        meta = self._load_metadata()
        # 元のスナップショットのメタデータを探してコピー
        for snap in meta.get('snapshots', []):
            if snap['name'] == name:
                new_snap = dict(snap)
                new_snap['name'] = new_name
                new_snap['created_at'] = datetime.now().isoformat()
                meta['snapshots'].append(new_snap)
                break
        self._save_metadata(meta)
        logger.info("コピー完了: %s -> %s", name, new_name)

    def delete(self, name: str) -> None:
        """スナップショットを削除する"""
        snap_dir = self._safe_snap_dir(name)
        if not snap_dir.exists():
            raise SnapshotError(f"スナップショット '{name}' が見つかりません")

        shutil.rmtree(snap_dir)

        # メタデータから削除
        meta = self._load_metadata()
        meta['snapshots'] = [
            s for s in meta.get('snapshots', []) if s['name'] != name
        ]
        self._save_metadata(meta)
        logger.info("削除完了: %s", name)

    def rotate(self, keep: int = DEFAULT_MAX_GENERATIONS) -> int:
        """古い世代を削除する。

        Returns:
            削除された世代数
        """
        meta = self._load_metadata()
        snapshots = meta.get('snapshots', [])

        if len(snapshots) <= keep:
            return 0

        # 古い順にソート（created_atベース）
        snapshots.sort(key=lambda s: s.get('created_at', ''))
        to_delete = snapshots[:-keep]

        deleted = 0
        for snap in to_delete:
            snap_dir = self.backups_dir / snap['name']
            if snap_dir.exists():
                shutil.rmtree(snap_dir)
            deleted += 1

        meta['snapshots'] = snapshots[-keep:]
        meta['max_generations'] = keep
        self._save_metadata(meta)

        if deleted:
            logger.info("ローテーション: %d 世代を削除しました（%d 世代保持）", deleted, keep)
        return deleted

    def should_start_new_generation(
        self, max_incrementals: int = DEFAULT_MAX_INCREMENTALS,
    ) -> bool:
        """最新世代の差分バックアップ数が上限に達しているか判定する。

        Args:
            max_incrementals: 1世代あたりの最大差分バックアップ数

        Returns:
            True: 新世代を作成すべき（スナップショットなし or 差分数が上限以上）
            False: 既存世代に差分を追加すべき
        """
        meta = self._load_metadata()
        snapshots = meta.get('snapshots', [])
        if not snapshots:
            return True
        latest = snapshots[-1]
        return latest.get('incremental_count', 0) >= max_incrementals

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_snapshot_image(self) -> str:
        """スナップショット専用イメージを確保する（なければ自動ビルド）"""
        try:
            subprocess.run(
                ['docker', 'image', 'inspect', SNAPSHOT_IMAGE],
                capture_output=True, check=True
            )
            return SNAPSHOT_IMAGE
        except subprocess.CalledProcessError:
            dockerfile_dir = self.devbase_root / 'containers' / 'snapshot'
            if not dockerfile_dir.exists():
                raise SnapshotError(
                    f"スナップショット用Dockerfileが見つかりません: {dockerfile_dir}"
                )
            logger.info("devbase-snapshotイメージをビルド中...")
            build_cmds = [
                ['docker', 'buildx', 'build', '--load',
                 '-t', SNAPSHOT_IMAGE, str(dockerfile_dir)],
                ['docker', 'build',
                 '-t', SNAPSHOT_IMAGE, str(dockerfile_dir)],
            ]
            last_err = None
            for cmd in build_cmds:
                try:
                    subprocess.run(
                        cmd, check=True, capture_output=True, text=True
                    )
                    last_err = None
                    break
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    last_err = e
            if last_err is not None:
                stderr = getattr(last_err, 'stderr', str(last_err))
                raise SnapshotError(
                    f"devbase-snapshotのビルドに失敗: {stderr}"
                ) from last_err
            logger.info("devbase-snapshotイメージのビルド完了")
            return SNAPSHOT_IMAGE

    def _run_docker_tar(self, snap_dir: Path, mode: str, command: str) -> None:
        """Docker経由でtar操作を実行する。

        Args:
            snap_dir: スナップショットディレクトリ
            mode: 'backup' or 'restore'
            command: コンテナ内で実行するコマンド
        """
        image = self._ensure_snapshot_image()

        abs_snap_dir = snap_dir.resolve()
        volume_mount = f'{VOLUME_NAME}:/source:ro' if mode == 'backup' else f'{VOLUME_NAME}:/target'
        backup_mount = f'{abs_snap_dir}:/backup:ro' if mode == 'restore' else f'{abs_snap_dir}:/backup'

        cmd = [
            'docker', 'run', '--rm',
            '-v', volume_mount,
            '-v', backup_mount,
            image,
            'bash', '-c', command,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            if result.stdout.strip():
                logger.debug(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            raise SnapshotError(
                f"Dockerでのtar操作に失敗しました: {e.stderr}"
            ) from e

    def _create_full(self, name: str, snap_dir: Path) -> None:
        """フルバックアップを作成"""
        logger.info("フルバックアップを作成中: %s", name)
        self._run_docker_tar(
            snap_dir, 'backup',
            "tar --listed-incremental=/backup/snapshot.snar "
            "-cf - -C /source . | zstd -1 -T0 -o /backup/full.tar.zst"
        )

        # meta.yml を作成
        meta = {
            'name': name,
            'created_at': datetime.now().isoformat(),
            'type': 'full',
            'volume': VOLUME_NAME,
            'files': ['full.tar.zst'],
            'incremental_count': 0,
        }
        self._save_snap_meta(snap_dir, meta)

    def _create_incremental(self, name: str, snap_dir: Path) -> None:
        """差分バックアップを作成"""
        snar_file = snap_dir / 'snapshot.snar'
        if not snar_file.exists():
            # snarファイルがなければフルバックアップにフォールバック
            logger.info("snarファイルが見つかりません、フルバックアップに切り替えます")
            self._create_full(name, snap_dir)
            return

        # 差分番号を決定
        existing = sorted(snap_dir.glob('incr-*.tar.zst'))
        next_num = len(existing) + 1
        incr_name = f'incr-{next_num:03d}.tar.zst'

        logger.info("差分バックアップを作成中: %s/%s", name, incr_name)

        self._run_docker_tar(
            snap_dir, 'backup',
            f"cp /backup/snapshot.snar /backup/snapshot.snar.bak && "
            f"tar --listed-incremental=/backup/snapshot.snar "
            f"-cf - -C /source . | zstd -1 -T0 -o /backup/{incr_name}"
        )

        # meta.yml を更新
        snap_meta = self._load_snap_meta(snap_dir)
        snap_meta['type'] = 'incremental'
        snap_meta['files'].append(incr_name)
        snap_meta['incremental_count'] = next_num
        self._save_snap_meta(snap_dir, snap_meta)

    def _update_global_metadata(self, name: str, snap_dir: Path) -> None:
        """グローバルメタデータ(snapshot.yml)を更新"""
        meta = self._load_metadata()
        now = datetime.now().isoformat()

        snap_meta = self._load_snap_meta(snap_dir)

        # 既存エントリを探す
        found = False
        for snap in meta.get('snapshots', []):
            if snap['name'] == name:
                snap['updated_at'] = now
                snap['incremental_count'] = snap_meta.get('incremental_count', 0)
                found = True
                break

        if not found:
            meta.setdefault('snapshots', []).append({
                'name': name,
                'created_at': now,
                'updated_at': now,
                'incremental_count': snap_meta.get('incremental_count', 0),
            })

        self._save_metadata(meta)

    def _load_metadata(self) -> dict:
        """グローバルメタデータを読み込む"""
        if self._metadata_path.exists():
            with open(self._metadata_path) as f:
                return yaml.safe_load(f) or {}
        return {'max_generations': DEFAULT_MAX_GENERATIONS, 'snapshots': []}

    def _save_metadata(self, meta: dict) -> None:
        """グローバルメタデータを保存する"""
        with open(self._metadata_path, 'w') as f:
            yaml.dump(meta, f, default_flow_style=False, allow_unicode=True)

    def _load_snap_meta(self, snap_dir: Path) -> dict:
        """個別スナップショットのmeta.ymlを読み込む"""
        meta_path = snap_dir / 'meta.yml'
        if meta_path.exists():
            with open(meta_path) as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save_snap_meta(self, snap_dir: Path, meta: dict) -> None:
        """個別スナップショットのmeta.ymlを保存する"""
        meta_path = snap_dir / 'meta.yml'
        with open(meta_path, 'w') as f:
            yaml.dump(meta, f, default_flow_style=False, allow_unicode=True)
