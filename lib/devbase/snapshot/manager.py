"""スナップショット管理のコアロジック"""

import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from devbase.errors import DevbaseError, SnapshotCommandError, SnapshotError
from devbase.log import get_logger
from devbase.utils.names import is_single_segment_name
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

# GNU tar の incremental はディレクトリを (dev, ino) で追跡して rename を検出する。
# ディレクトリが削除され作り直されると **inode 番号が再利用される**ため、tar は無関係な
# ディレクトリを rename されたものと誤判定し、dumpdir に偽の R/T レコードを書く。
# 復元側はそれを rename() として実行して失敗するが、**展開自体は完遂している**ので、
# この失敗だけは警告にして復元を続ける (PLAN40)。
_RENAME_ERROR_RE = re.compile(r"^tar: Cannot rename '(?P<src>.*)' to '(?P<dst>.*)': .+$")
_TAR_EXIT_LINE = 'tar: Exiting with failure status due to previous errors'


def rename_only_failure(stderr: str) -> Optional[list]:
    """tar の失敗が rename エラーだけなら、その行の一覧を返す。

    1 行でも別のエラーが混ざっていれば ``None`` を返す。stderr が空の場合も、
    失敗の理由が分からない以上見逃してはならないので ``None`` を返す。
    """
    renames = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line or line == _TAR_EXIT_LINE:
            continue
        if _RENAME_ERROR_RE.match(line):
            renames.append(line)
            continue
        return None
    return renames or None


# 検証コマンド 1 回あたりの引数の上限。``docker run`` の引数は最終的に execve の
# ARG_MAX (多くの環境で 128KB) に収まる必要がある。巨大なツリーを入れ替えると
# 失敗した rename が大量に出うるので、余裕をもって分割する。
_CHECK_COMMAND_BUDGET = 60_000


def chunk_paths(paths: list, budget: int = _CHECK_COMMAND_BUDGET) -> list:
    """引用済みのパスを、1 コマンドの長さが budget を超えないように分ける。

    1 件で budget を超える異常に長いパスも、単独のチャンクとして必ず返す
    (捨てると検証から漏れるため)。
    """
    chunks: list = []
    current: list = []
    length = 0
    for path in paths:
        # +1 は区切りの空白
        if current and length + len(path) + 1 > budget:
            chunks.append(current)
            current, length = [], 0
        current.append(path)
        length += len(path) + 1
    if current:
        chunks.append(current)
    return chunks


def rename_targets(lines: list) -> list:
    """rename エラーの行から**宛先**のパスだけを取り出す。

    展開後にその宛先が空のままなら、偽 rename ではなく**正当な rename を
    取りこぼした**可能性がある。正当な rename の差分にはディレクトリの
    エントリしか入らず、中身は rename でしか移動しないためである。
    """
    targets = []
    for line in lines:
        match = _RENAME_ERROR_RE.match(line.strip())
        if match:
            targets.append(match.group('dst'))
    return targets


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
        """スナップショット名のバリデーション（パストラバーサル防止）

        文字の規則は位置引数のプロジェクト名と同じ ``is_single_segment_name`` を共有する
        (PLAN66 決定 5)。受理と拒否は ``tests/snapshot/test_manager_name.py`` で固定している。
        """
        if not is_single_segment_name(name):
            raise SnapshotError(
                f"無効なスナップショット名: '{name}' "
                "(英数字・ハイフン・アンダースコア・ドットのみ使用可能、先頭は英数字)"
            )

    def _safe_snap_dir(self, name: str) -> Path:
        """名前からスナップショットディレクトリを安全に解決する。

        次の 3 つを ``SnapshotError`` で止める (PLAN68 決定 7)。

        - 名前が不正 (``../outside`` など)
        - 世代がシンボリックリンク。devbase はリンクの世代を作らず、リンク先が
          ``backups/`` の外でも中の別の世代でも、消すと実体を失う
        - 解決後のパスが ``backups/`` の中に無い。文字列の前方一致ではなく
          パスの要素の単位で比べる (兄弟の ``backups-outside/`` を通さないため)

        ``backups/`` 自体をリンクにした構成は、解決後の ``backups/`` と比べるため使える。
        """
        self._validate_name(name)
        raw = self.backups_dir / name
        if raw.is_symlink():
            raise SnapshotError(
                f"スナップショット '{name}' はシンボリックリンクのため扱えません: "
                f"{raw} -> {raw.readlink()}")
        snap_dir = raw.resolve()
        if not snap_dir.is_relative_to(self.backups_dir.resolve()):
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

    def last_snapshot_time(self, volumes: Optional[dict] = None) -> Optional[datetime]:
        """直近のスナップショット取得 (フル/差分) 日時を返す。

        ``volumes`` を渡すと、その組の系列に属する世代 (``snapshot.yml`` のエントリ)
        のディレクトリだけを見る (PLAN68 決定 4)。省けば全ディレクトリを見る。

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
        if volumes is None:
            snap_dirs = list(self.backups_dir.iterdir())
        else:
            snap_dirs = [
                self.backups_dir / s['name'] for _, s in self._series_entries(volumes)
                if is_single_segment_name(s['name'])
            ]
        latest: Optional[float] = None
        for snap_dir in snap_dirs:
            if snap_dir.is_symlink() or not snap_dir.is_dir():
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
            # 失敗時の案内で「戻せる」と書けなくなるので、無いことを覚えておく
            pre_restore_name = None

        volumes = self.snapshot_volumes(snap_dir)
        logger.info("復元先のボリューム: %s", ', '.join(volumes.values()))

        # 偽 rename として飲み込んだ宛先。全アーカイブ適用後にまとめて検証する
        # (後続の差分が中身を埋める場合があるので、途中では判断できない)。
        skipped_renames: list = []

        # フルバックアップの復元
        logger.info("フルバックアップを復元中...")
        self._extract_archive(
            snap_dir, 'full.tar.zst',
            self.clear_command(volumes) +
            "zstd -d /backup/full.tar.zst -c | "
            "tar --listed-incremental=/dev/null -xf - -C /target",
            volumes, pre_restore_name, skipped_renames,
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
            self._extract_archive(
                snap_dir, incr.name,
                f"zstd -d /backup/{incr.name} -c | "
                f"tar --listed-incremental=/dev/null -xf - -C /target",
                volumes, pre_restore_name, skipped_renames,
            )

        self._warn_about_lost_renames(snap_dir, volumes, skipped_renames)

        if point is not None:
            logger.info("復元完了: %s (incr-%03d まで)", name, point)
        else:
            logger.info("復元完了: %s", name)

    def _extract_archive(self, snap_dir: Path, archive: str, command: str,
                         volumes: dict, pre_restore_name: Optional[str],
                         skipped_renames: Optional[list] = None) -> None:
        """アーカイブを 1 つ展開する。偽の rename エラーだけは飲み込む。

        GNU tar が inode 番号の再利用で誤検出した rename は、復元時に必ず失敗する。
        tar はその後も展開を続けて完遂しているため、ここで止めると**かえって**
        ボリュームが中途半端な状態で残る。失敗した rename は警告として出し、
        見逃しが分かるようにする (PLAN40)。
        """
        try:
            self._run_docker_tar(snap_dir, 'restore', command, volumes=volumes)
        except SnapshotError as e:
            # stderr を持たない失敗 (イメージのビルド失敗など) は判断材料が無いので、
            # rename エラーとはみなさず従来どおり止める。
            stderr = e.stderr if isinstance(e, SnapshotCommandError) else ''
            renames = rename_only_failure(stderr)
            if renames is None:
                raise SnapshotError(
                    self._restore_failure_message(archive, pre_restore_name, e)
                ) from e
            if skipped_renames is not None:
                skipped_renames.extend(rename_targets(renames))
            logger.warning(
                "%s の展開で tar が rename に失敗しました。GNU tar の incremental が "
                "inode 番号の再利用でディレクトリの rename を誤検出したものとみなし、"
                "復元を続けます:\n%s",
                archive, '\n'.join(renames))

    def _warn_about_lost_renames(self, snap_dir: Path, volumes: dict,
                                 targets: list) -> None:
        """飲み込んだ rename の宛先が**空のまま**なら、内容の欠落を警告する。

        偽 rename の宛先は、そのディレクトリ自身が新しく作られたものなので、
        アーカイブから中身が展開されて空にはならない。一方、**正当な** rename を
        取りこぼした場合は、中身が rename でしか移動しないため宛先が空のまま残る。
        両者はエラーメッセージだけでは区別できないが、展開後の状態でなら見分けられる。
        """
        if not targets:
            return
        quoted = [shlex.quote(t) for t in sorted(set(targets))]
        empty: list = []
        for chunk in chunk_paths(quoted):
            try:
                result = self._run_docker_tar(
                    snap_dir, 'restore',
                    'for p in ' + ' '.join(chunk) + '; do '
                    'full="/target/${p#./}"; '
                    'if [ -d "$full" ] && [ -z "$(ls -A "$full" 2>/dev/null)" ]; then '
                    'echo "$p"; fi; '
                    'done',
                    volumes=volumes,
                )
            except SnapshotError as e:
                # ここまで来た時点で復元自体は終わっている。**検証の失敗で復元を
                # 失敗にしない。** 検証できなかったことだけを伝える。
                logger.warning(
                    "復元後の rename 宛先の検証に失敗しました。復元自体は完了して"
                    "います: %s", e)
                return
            # 空白を含むパスを分断しないよう、行単位で解析する。
            # テストダブルは戻り値を返さない。その場合は検証を行わない。
            if result is not None and result.stdout:
                empty.extend(line.strip() for line in result.stdout.splitlines()
                             if line.strip())
        if not empty:
            return
        logger.warning(
            "復元後、次のディレクトリが空のままです。GNU tar が記録した rename を "
            "適用できなかったため、**中身が復元されていない可能性があります**。"
            "利用者が実際に mv したディレクトリであれば、そのスナップショットからは "
            "中身を復元できません:\n%s",
            '\n'.join(sorted(empty)))

    @staticmethod
    def _restore_failure_message(archive: str, pre_restore_name: Optional[str],
                                 error: Exception) -> str:
        """復元の失敗を、次に何をすればよいかまで含めて説明する。"""
        if pre_restore_name:
            recovery = (
                f"復元前の状態は '{pre_restore_name}' に退避してあります。"
                f"元に戻すには devbase snapshot restore {pre_restore_name} "
                f"を実行してください。")
        else:
            recovery = ("復元前の自動バックアップは作成できていません。"
                        "別のスナップショットから復元してください "
                        "(devbase snapshot list で確認できます)。")
        return (
            f"復元に失敗しました ({archive} の展開中)。"
            f"対象ボリュームは途中まで書き換わっている可能性があります。"
            f"{recovery}\n{error}")

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

    def rotate(self, keep: int = DEFAULT_MAX_GENERATIONS,
               max_total: Optional[int] = None) -> int:
        """古い世代を削除する (PLAN68 決定 1・6・7)。

        系列 (対象ボリュームの組) ごとに ``keep`` 世代を残し、残りの総数が
        ``max_total`` (省けば ``keep × 3``) を超えたら、系列をまたいで最も古い
        世代から消す。**各系列の最新の世代は消さない** (次の差分の積み先のため)。
        消す前に :meth:`_safe_snap_dir` で名前を検証し、拒否されたエントリは
        ディレクトリを消さずに一覧からだけ外す。

        Args:
            keep: 系列ごとに残す世代の数
            max_total: 全体で残す世代の上限。省けば ``keep × 3``

        Returns:
            削除された世代数

        Raises:
            SnapshotError: ``keep`` か ``max_total`` が 1 未満の場合 (何も消さない)
        """
        if max_total is None:
            max_total = keep * 3
        if keep < 1:
            raise SnapshotError(f"--keep は 1 以上である必要があります: {keep}")
        if max_total < 1:
            raise SnapshotError(f"--max-total は 1 以上である必要があります: {max_total}")

        meta = self._load_metadata()
        snapshots = meta.get('snapshots', []) or []
        plan = self._rotation_plan(snapshots, keep, max_total)
        if not plan:
            return 0

        removed_ids = set()
        deleted_ids = set()
        for index, reason in plan:
            snap = snapshots[index]
            name = snap.get('name', '')
            removed_ids.add(index)
            try:
                snap_dir = self._safe_snap_dir(name)
            except SnapshotError as e:
                logger.warning(
                    "snapshot.yml の世代 '%s' は場所が不正なため、ディレクトリを消さずに"
                    "一覧からだけ外します: %s", name, e)
                continue
            if snap_dir.exists():
                shutil.rmtree(snap_dir)
            deleted_ids.add(index)
            if reason is not None:
                logger.info(
                    "ローテーション: 全体の上限 %d 世代を超えたため、%s の %s を削除しました",
                    max_total, self.series_label(self._entry_volumes(snap)), name)

        per_series: dict = {}
        for index, reason in plan:
            if reason is None and index in deleted_ids:
                label = self.series_label(self._entry_volumes(snapshots[index]))
                per_series[label] = per_series.get(label, 0) + 1
        for label, count in per_series.items():
            logger.info(
                "ローテーション: %s の %d 世代を削除しました（グループごとに %d 世代保持）",
                label, count, keep)

        remaining = [s for i, s in enumerate(snapshots) if i not in removed_ids]
        order = {id(s): i for i, s in enumerate(snapshots)}
        remaining.sort(key=lambda s: self._entry_age(s, order[id(s)]))
        meta['snapshots'] = remaining
        meta['max_generations'] = keep
        self._save_metadata(meta)
        return len(deleted_ids)

    def _rotation_plan(self, snapshots: list, keep: int, max_total: int) -> list:
        """ローテーションで消すエントリを決める (副作用なし)。

        Returns:
            ``(snapshots の添字, 理由)`` の並び。理由は系列ごとの保持なら ``None``、
            全体の上限なら ``'total'``。
        """
        groups: dict = {}
        for index, snap in enumerate(snapshots):
            key = self.series_key(self._entry_volumes(snap))
            groups.setdefault(key, []).append(index)

        def age(index: int) -> tuple:
            return self._entry_age(snapshots[index], index)

        plan: list = []
        kept: dict = {}
        for key, indexes in groups.items():
            indexes.sort(key=age)
            excess = max(0, len(indexes) - keep)
            plan.extend((i, None) for i in indexes[:excess])
            kept[key] = indexes[excess:]

        total = sum(len(v) for v in kept.values())
        while total > max_total:
            candidates = [v for v in kept.values() if len(v) >= 2]
            if not candidates:
                logger.warning(
                    "全体の上限 %d 世代を超えていますが、各グループの最新の世代は"
                    "消さないため %d 世代を残します", max_total, total)
                break
            oldest = min(candidates, key=lambda v: age(v[0]))
            plan.append((oldest.pop(0), 'total'))
            total -= 1
        return plan

    # ------------------------------------------------------------------
    # 系列 (PLAN68)
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_age(entry: dict, index: int) -> tuple:
        # created_at が同じなら snapshot.yml で前にあるものを古いとみなす
        return (entry.get('created_at', '') or '', index)

    @staticmethod
    def _entry_volumes(entry: dict) -> dict:
        """``snapshot.yml`` のエントリの対象ボリュームの組。

        ``volumes`` が無い・空・dict でないエントリは PLAN39 より前の旧レイアウトで、
        共通ボリューム 1 本の組とみなす。値は検証しない (系列のキーにするだけで、
        マウントには使わないため)。
        """
        volumes = entry.get('volumes') if isinstance(entry, dict) else None
        if isinstance(volumes, dict) and volumes:
            return dict(volumes)
        return {'': HOME_UBUNTU_VOLUME}

    @staticmethod
    def series_key(volumes: dict) -> tuple:
        """系列の識別子。対象ボリュームの組を並べ替えたタプル。"""
        return tuple(sorted((str(k), str(v)) for k, v in volumes.items()))

    @staticmethod
    def series_label(volumes: dict) -> str:
        """系列の表示名 (ログ用)。例: ``グループ default``。"""
        group = volumes.get(GROUP_MOUNT)
        if isinstance(group, str) and group:
            if group.startswith(SHARED_VOLUME_PREFIX):
                group = group[len(SHARED_VOLUME_PREFIX):]
            return f"グループ {group}"
        return "旧レイアウト（共通ボリュームのみ）"

    def _entries(self) -> list:
        return [s for s in (self._load_metadata().get('snapshots') or [])
                if isinstance(s, dict) and 'name' in s]

    def _series_entries(self, volumes: dict) -> "list[tuple[int, dict]]":
        """対象ボリュームの系列に属するエントリを元の添字とともに返す。"""
        key = self.series_key(volumes)
        return [
            (index, snap) for index, snap in enumerate(self._entries())
            if self.series_key(self._entry_volumes(snap)) == key
        ]

    def series_latest(self, volumes: Optional[dict] = None) -> Optional[dict]:
        """系列の最新の世代のエントリ (``created_at`` が最大)。無ければ ``None``。

        ``created_at`` が同じなら ``snapshot.yml`` で後ろのものを新しいとみなす。
        """
        latest = None
        latest_age = None
        for index, snap in self._series_entries(self.volumes if volumes is None else volumes):
            age = self._entry_age(snap, index)
            if latest_age is None or age > latest_age:
                latest, latest_age = snap, age
        return latest

    def auto_snapshot_target(
        self, max_incrementals: int = DEFAULT_MAX_INCREMENTALS,
    ) -> Optional[str]:
        """自動スナップショットの積み先を返す (PLAN68 決定 2)。

        Returns:
            系列の最新の世代の名前。新しい世代を作るべきなら ``None``
            (その理由を INFO で 1 行出す)。
        """
        label = self.series_label(self.volumes)
        latest = self.series_latest()
        if latest is None:
            logger.info("%s の世代がまだ無いため、新しい世代を作成します", label)
            return None

        name = latest['name']
        snap_dir = self.backups_dir / name
        if (is_single_segment_name(name) and not snap_dir.is_symlink()
                and snap_dir.is_dir()):
            recorded = self.snapshot_volumes(snap_dir)
            if recorded != self.volumes:
                logger.info(
                    "世代 %s の meta.yml の対象ボリューム (%s) が %s と一致しないため、"
                    "新しい世代を作成します", name, ', '.join(recorded.values()), label)
                return None

        if latest.get('incremental_count', 0) >= max_incrementals:
            logger.info(
                "世代 %s（%s）の差分が上限 (%d) に達したため、新しい世代を作成します",
                name, label, max_incrementals)
            return None
        return name

    def should_start_new_generation(
        self, max_incrementals: int = DEFAULT_MAX_INCREMENTALS,
    ) -> bool:
        """新しい世代を作るべきか (``auto_snapshot_target(...) is None``)。

        系列 (対象ボリュームの組) の最新の世代に世代が無い、または差分数が上限以上
        なら True。グループを切り替えただけでは True にならない (PLAN68)。
        """
        return self.auto_snapshot_target(max_incrementals) is None

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
                        volumes: Optional[dict] = None
                        ) -> subprocess.CompletedProcess:
        """Docker経由でtar操作を実行する。

        Args:
            snap_dir: スナップショットディレクトリ
            mode: 'backup' or 'restore'
            command: コンテナ内で実行するコマンド
            volumes: 対象ボリューム (省略時は作成時の対象)

        Returns:
            実行結果。標準出力を読む呼び出し (rename 宛先の検証) がある。
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
            return result
        except subprocess.CalledProcessError as e:
            raise SnapshotCommandError(
                f"Dockerでのtar操作に失敗しました: {e.stderr}",
                stderr=e.stderr or '',
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
            # 通常はここへ来ない (auto_snapshot_target が新世代へ倒す)。
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
