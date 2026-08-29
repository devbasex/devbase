"""スナップショット管理のコアロジック"""

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from devbase.errors import DevbaseError, SnapshotError
from devbase.log import get_logger
from devbase.volume.manager import (
    HOME_UBUNTU_VOLUME,
    SHARED_VOLUME_PREFIX,
    get_group_volume,
    resolve_account_group,
)

logger = get_logger(__name__)

# 後方互換のために残す旧定数 (共通ボリューム 1 本だった頃の対象)
VOLUME_NAME = HOME_UBUNTU_VOLUME
# 対象ボリュームのマウント先サブディレクトリ (PLAN39)。
# 共通ボリュームとアカウントグループのボリュームを 1 つのアーカイブへまとめるため、
# コンテナ内では /source/<sub> に並べて置く。
SHARED_MOUNT = 'ai'
GROUP_MOUNT = 'group'
# メタデータから受け入れるマウント名。空文字は旧レイアウト (共通ボリューム 1 本を
# ルートへ直接マウント) を表す。
_ALLOWED_MOUNTS = frozenset({'', SHARED_MOUNT, GROUP_MOUNT})
SNAPSHOT_IMAGE = 'devbase-snapshot:latest'
DEFAULT_MAX_GENERATIONS = 3
DEFAULT_MAX_INCREMENTALS = 10
METADATA_FILE = 'snapshot.yml'
_VALID_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


class SnapshotManager:
    """Docker volumeのスナップショット管理"""

    def __init__(self, devbase_root: Path, group: Optional[str] = None):
        """
        Args:
            devbase_root: devbase のルート
            group: 対象のアカウントグループ (省略時は環境から解決)
        """
        self.devbase_root = devbase_root
        self.backups_dir = devbase_root / 'backups'
        self.backups_dir.mkdir(exist_ok=True)
        self._metadata_path = self.backups_dir / METADATA_FILE
        self._group = group
        self._volumes: Optional[dict] = None

    @property
    def volumes(self) -> dict:
        """作成時の対象ボリューム (初回参照時に解決する)。

        復元時は**スナップショット自身のメタデータ**を見るので、ここでの解決結果は
        使わない (別グループのスナップショットを取り違えないため)。

        解決はグループ名の検証を伴い、不正な名前なら ``DevbaseError`` になる。
        一覧・コピー・削除のように対象ボリュームを必要としない操作まで倒さないよう、
        参照されるまで遅延させる。
        """
        if self._volumes is None:
            self._volumes = {
                SHARED_MOUNT: HOME_UBUNTU_VOLUME,
                GROUP_MOUNT: get_group_volume(self._group),
            }
        return self._volumes

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

    def last_snapshot_time(self) -> Optional[datetime]:
        """直近のスナップショット取得 (フル/差分) 日時を返す。

        各スナップショットディレクトリ内のアーカイブ実体
        (``full.tar.zst`` / ``incr-*.tar.zst``) の mtime のうち最新のものを採用する。
        差分更新は既存ディレクトリ名を再利用するため (ディレクトリ名の日付は世代
        作成時のまま) ファイルの mtime を実測する方が正確で、メタデータの整合性にも
        依存しない。

        ``meta.yml`` / ``snapshot.snar`` (listed-incremental 状態ファイル) や
        ``.bak`` 等の付随ファイルは集計対象から除外する。これらはバックアップ本体の
        作成に失敗 (コピーや差分作成失敗) しても残りうるため、これらの mtime を採用
        すると「成功したバックアップ本体が無いのに up がスキップされる」状態を招く。

        スナップショットが存在しない場合は None。
        """
        if not self.backups_dir.exists():
            return None
        latest: Optional[float] = None
        for snap_dir in self.backups_dir.iterdir():
            if not snap_dir.is_dir():
                continue
            for f in snap_dir.iterdir():
                if not f.is_file():
                    continue
                # アーカイブ実体 (full.tar.zst / incr-NNN.tar.zst) のみを対象とし、
                # meta.yml / snapshot.snar / *.bak 等は除外する。
                if f.name != 'full.tar.zst' and not (
                    f.name.startswith('incr-') and f.name.endswith('.tar.zst')
                ):
                    continue
                mtime = f.stat().st_mtime
                if latest is None or mtime > latest:
                    latest = mtime
        if latest is None:
            return None
        return datetime.fromtimestamp(latest, tz=timezone.utc)

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

        volumes = self.snapshot_volumes(snap_dir)
        logger.info("復元先のボリューム: %s", ', '.join(volumes.values()))

        # フルバックアップの復元
        logger.info("フルバックアップを復元中...")
        self._run_docker_tar(
            snap_dir, 'restore',
            self.clear_command(volumes) +
            "zstd -d /backup/full.tar.zst -c | "
            "tar --listed-incremental=/dev/null -xf - -C /target",
            volumes=volumes,
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
                f"zstd -d /backup/{incr.name} -c | "
                f"tar --listed-incremental=/dev/null -xf - -C /target",
                volumes=volumes,
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

        # 対象ボリュームの構成が変わったら新世代にする (PLAN39 の移行やグループ
        # 切替)。旧世代の snar は別のレイアウトを記録しているので、そこへ差分を
        # 積むと全ファイルが移動したものとして扱われ差分が壊れる。世代を分ければ
        # 旧世代はそのまま復元できる。
        snap_dir = self.backups_dir / latest.get('name', '')
        if snap_dir.is_dir() and self.snapshot_volumes(snap_dir) != self.volumes:
            logger.info(
                "対象ボリュームの構成が変わったため新しい世代を作成します "
                "(旧: %s / 新: %s)",
                ', '.join(self.snapshot_volumes(snap_dir).values()),
                ', '.join(self.volumes.values()))
            return True

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

    @staticmethod
    def volume_mount_args(volumes: dict, mode: str) -> list:
        """対象ボリュームの ``docker run -v`` 引数を組み立てる。

        サブディレクトリ名が空文字のエントリは、旧レイアウト (共通ボリューム 1 本を
        ルートへ直接マウント) を表す。旧スナップショットを復元するために残している。
        """
        root = '/source' if mode == 'backup' else '/target'
        suffix = ':ro' if mode == 'backup' else ''
        args = []
        for sub, name in volumes.items():
            target = f'{root}/{sub}' if sub else root
            args.extend(['-v', f'{name}:{target}{suffix}'])
        return args

    @staticmethod
    def clear_command(volumes: dict) -> str:
        """復元前に対象ボリュームの中身を空にするコマンドを組み立てる。

        マウントポイント自身は消せない (busy) ので、**各マウントの直下**を消す。
        旧レイアウトも同じ形で扱える。
        """
        roots = ' '.join(
            f'/target/{sub}' if sub else '/target' for sub in volumes)
        return (
            'for d in ' + roots + '; do '
            'find "$d" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null; '
            'done; '
        )

    def _run_docker_tar(self, snap_dir: Path, mode: str, command: str,
                        volumes: Optional[dict] = None) -> None:
        """Docker経由でtar操作を実行する。

        Args:
            snap_dir: スナップショットディレクトリ
            mode: 'backup' or 'restore'
            command: コンテナ内で実行するコマンド
            volumes: 対象ボリューム (省略時は作成時の対象)
        """
        image = self._ensure_snapshot_image()

        abs_snap_dir = snap_dir.resolve()
        backup_mount = f'{abs_snap_dir}:/backup:ro' if mode == 'restore' else f'{abs_snap_dir}:/backup'

        cmd = [
            'docker', 'run', '--rm',
            *self.volume_mount_args(volumes or self.volumes, mode),
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
            'volumes': dict(self.volumes),
            'files': ['full.tar.zst'],
            'incremental_count': 0,
        }
        self._save_snap_meta(snap_dir, meta)

    def _create_incremental(self, name: str, snap_dir: Path) -> None:
        """差分バックアップを作成"""
        recorded = self.snapshot_volumes(snap_dir)
        if recorded != self.volumes:
            # 通常はここへ来ない (should_start_new_generation が新世代へ倒す)。
            # 明示的に古い世代を指定されたときだけ到達する。黙って壊れた差分を
            # 積むより、理由を出して止める方がよい。
            raise SnapshotError(
                f"スナップショット '{name}' は別のボリューム構成 "
                f"({', '.join(recorded.values())}) で作られています。"
                f"現在の対象は {', '.join(self.volumes.values())} です。"
                "新しい世代を作成してください (devbase snapshot create)"
            )

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
        volumes = snap_meta.get('volumes') or self.snapshot_volumes(snap_dir)

        for snap in meta.get('snapshots', []):
            if snap['name'] == name:
                snap['updated_at'] = now
                snap['incremental_count'] = snap_meta.get('incremental_count', 0)
                snap['volumes'] = dict(volumes)
                found = True
                break

        if not found:
            meta.setdefault('snapshots', []).append({
                'name': name,
                'created_at': now,
                'updated_at': now,
                'incremental_count': snap_meta.get('incremental_count', 0),
                'volumes': dict(volumes),
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

    def snapshot_volumes(self, snap_dir: Path) -> dict:
        """スナップショットの対象ボリュームを、そのメタデータから解決する。

        新しいメタデータは ``volumes`` (サブディレクトリ名 → ボリューム名) を持つ。
        持たない旧スナップショットは共通ボリューム 1 本をルートへ直接マウントする
        レイアウトなので、サブディレクトリ名を空文字にした 1 件として返す。

        **値は検証してから返す。** ここで返した内容はマウント先として
        ``docker run -v <値>:/target/<キー>`` に、キーは
        :meth:`clear_command` が組み立てる ``bash -c`` の消去コマンドに入る。
        ``meta.yml`` は編集できるうえスナップショットは環境をまたいで持ち込めるため、
        絶対パスを値に書けば任意のホストディレクトリを bind mount して**復元前に
        中身を消せて**しまう。キーは既知のマウント名だけ、値は Docker の named
        volume として通る名前だけを許す。

        Raises:
            SnapshotError: メタデータの対象ボリュームが不正な場合
        """
        meta = self._load_snap_meta(snap_dir)
        volumes = meta.get('volumes')
        if isinstance(volumes, dict) and volumes:
            return self._validate_volumes(volumes, snap_dir)
        return self._validate_volumes(
            {'': meta.get('volume', HOME_UBUNTU_VOLUME)}, snap_dir)

    @staticmethod
    def _validate_volumes(volumes: dict, snap_dir: Path) -> dict:
        """メタデータ由来の対象ボリュームを検証する (不正なら SnapshotError)。

        **devbase が作るボリュームだけ**を許す。named volume の形をしていれば
        通す、では足りない: 同じ Docker 上の無関係なボリューム名 (``mysql_data``
        など) を書けば、復元前の消去でその中身を失わせられる。

        - 共通側 (``''`` / ``ai``) は ``devbase_home_ubuntu`` に限る
        - グループ側 (``group``) は ``devbase_home_<group>`` の形で、``<group>``
          がアカウントグループ名として妥当なものに限る
        """
        meta_path = snap_dir / 'meta.yml'

        def reject(reason: str) -> None:
            raise SnapshotError(
                f"スナップショットのメタデータが不正です ({meta_path}): {reason}")

        for sub, name in volumes.items():
            if sub not in _ALLOWED_MOUNTS:
                reject(f"未知のマウント名 '{sub}'。"
                       f"使えるのは "
                       f"{', '.join(repr(m) for m in sorted(_ALLOWED_MOUNTS))} です")
            if not isinstance(name, str):
                reject(f"'{sub}' のボリューム名が文字列ではありません: {name!r}")

            if sub in ('', SHARED_MOUNT):
                if name != HOME_UBUNTU_VOLUME:
                    reject(f"共通ボリュームに使えるのは {HOME_UBUNTU_VOLUME} だけです"
                           f" (指定: {name!r})")
                continue

            # group: devbase_home_<group> の形で、<group> が妥当であること
            if not name.startswith(SHARED_VOLUME_PREFIX):
                reject(f"グループボリュームは {SHARED_VOLUME_PREFIX}<group> の形で"
                       f"なければなりません (指定: {name!r})")
            group = name[len(SHARED_VOLUME_PREFIX):]
            try:
                # 正規化した結果が元の名前と**一致**することまで見る。
                # resolve_account_group は空文字を 'default' に、前後空白を
                # 落とした名前に正規化するので、通るかどうかだけでは
                # `devbase_home_` や `devbase_home_  kkg  ` を弾けない。
                # 実際にマウントされるのは正規化前の生の名前である。
                if get_group_volume(group) != name:
                    reject(f"グループボリューム {name!r} は正規化された名前では"
                           f"ありません (期待: {get_group_volume(group)!r})")
            except DevbaseError as e:
                reject(f"グループボリューム {name!r} のグループ名が不正です: {e}")

        return dict(volumes)

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
