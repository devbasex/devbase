"""ホストの資格情報の取り込みの方針 (#314)

ホストの資格情報ファイル (``~/gcp-credentials/``・``~/.aws/``・``~/.git-credentials``・
``git config --global``) から読んだ値を、対象のグループの参照へ書くかどうかを決める。

- 方針は ``ask`` (尋ねる)・``skip`` (取り込まない)・``import`` (取り込む) の 3 つ
- グループごとの名指しは ``$DEVBASE_ROOT/secrets/host-import.yml`` に利用者が書く。
  名指しの無いグループは ``ask``。devbase はこのファイルを書かず、グループ名から方針を決めない
- 方針を決めるのは呼び出し側 (``env init``) だけで、collector は :class:`HostImport` を
  受け取って従う (I1)。端末でない「尋ねる」はここで「取り込まない」へ落とす (I3・決定 2)
"""

import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)

#: 取り込みを許すグループの設定の置き場 (``$DEVBASE_ROOT`` からの相対)
CONFIG_RELPATH = Path('secrets') / 'host-import.yml'

#: 「全部」を選んだ取り込みの選択 (AWS では丸ごとの取り込み。決定 8)
ALL = 'all'


class ImportPolicy(Enum):
    """取り込みの方針。設定の値と 1 対 1"""
    ASK = 'ask'
    SKIP = 'skip'
    IMPORT = 'import'


_ACCEPTED = ', '.join(p.value for p in ImportPolicy)


class HostImportConfigError(DevbaseError):
    """取り込みを許すグループの設定の誤り。文はファイルの場所と直す箇所を持つ"""


def config_path(devbase_root: Path) -> Path:
    return Path(devbase_root) / CONFIG_RELPATH


def load(devbase_root: Path) -> Dict[str, ImportPolicy]:
    """設定を読み、グループ名 → 方針を返す。ファイルが無ければ空 (すべて ``ask``)。

    Raises:
        HostImportConfigError: YAML でない・形が不正・未知の方針・使えないグループ名 (I4)
    """
    path = config_path(devbase_root)
    if not path.exists():
        return {}

    def fail(what: str):
        raise HostImportConfigError(f"{path}: {what} (受け付ける値: {_ACCEPTED})")

    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        fail(f"YAML として読めません: {e}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        fail("最上位は groups を持つマッピングにします")
    unknown = sorted(str(k) for k in data if k != 'groups')
    if unknown:
        fail(f"{', '.join(unknown)} は使えないキーです。最上位に置けるのは groups だけです")
    groups = data.get('groups')
    if groups is None:
        return {}
    if not isinstance(groups, dict):
        fail("groups はグループ名から方針へのマッピングにします")

    from devbase.volume.manager import validate_account_group

    policies: Dict[str, ImportPolicy] = {}
    for name, value in groups.items():
        try:
            group = validate_account_group(str(name) if name is not None else '')
        except DevbaseError as e:
            fail(f"groups.{name} は使えないグループ名です: {e}")
        if not isinstance(value, str) or value not in {p.value for p in ImportPolicy}:
            fail(f"groups.{name} の方針が不正です: {value!r}")
        policies[group] = ImportPolicy(value)
    return policies


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


@dataclass
class Selection:
    """番号の選択の結果。``all`` は「all」と入れた (または「取り込む」で全部を選んだ) こと"""
    indexes: List[int] = field(default_factory=list)
    all: bool = False

    def __bool__(self) -> bool:
        return self.all or bool(self.indexes)


@dataclass
class HostImport:
    """1 回の ``init`` の取り込みの場。

    ``policy`` は実際に使う方針 (端末でない「尋ねる」を落とした後)。``choose`` / ``confirm`` は
    ``ASK`` のときだけ質問を出し、``SKIP`` なら何も選ばず、``IMPORT`` なら全部を選ぶ。
    collector は選んだ結果を ``record`` し、呼び出し側が控えの更新へ渡す。
    """
    policy: ImportPolicy
    group: Optional[str] = None
    fell_back: bool = False
    selections: Dict[str, object] = field(default_factory=dict)

    @property
    def asking(self) -> bool:
        return self.policy is ImportPolicy.ASK

    @property
    def importing(self) -> bool:
        return self.policy is ImportPolicy.IMPORT

    def _group_label(self) -> str:
        return self.group or 'グループの指定なし'

    def _skipped(self, step: str) -> None:
        if self.fell_back:
            logger.info("%s: 標準入力が端末でないため飛ばしました (%s)", step, self._group_label())
        else:
            logger.info("%s: 取り込まない設定のため飛ばしました (%s)", step, self._group_label())

    def declined(self, step: str) -> None:
        """「尋ねる」で断られたことを 1 行知らせる (「取り込まない」は ``_skipped`` が知らせ済み)"""
        if self.asking:
            logger.info("%s: 取り込みません", step)

    def choose(self, step: str, title: str, labels: Sequence[str], hint: str) -> Selection:
        """候補を番号で選ぶ (決定 4)。``labels`` には名前だけを渡す (I10)"""
        if self.policy is ImportPolicy.IMPORT:
            return Selection(list(range(len(labels))), all=True)
        if self.policy is ImportPolicy.SKIP:
            self._skipped(step)
            return Selection()
        print(f"\n{title}")
        for i, label in enumerate(labels, 1):
            print(f"  {i}) {label}")
        from devbase.env.store import safe_input
        raw = safe_input(f"取り込む番号 ({hint}): ")
        return parse_selection(raw, len(labels))

    def confirm(self, step: str, question: str) -> bool:
        """y/N で尋ねる (既定は N)"""
        if self.policy is ImportPolicy.IMPORT:
            return True
        if self.policy is ImportPolicy.SKIP:
            self._skipped(step)
            return False
        from devbase.env.store import safe_input
        return safe_input(f"{question} [y/N]: ", 'n').lower() == 'y'

    def record(self, source: str, selection) -> None:
        self.selections[source] = selection


def parse_selection(raw: str, count: int) -> Selection:
    """番号の入力を読む。空は 0 件、``all`` は全部、読めない入力は 0 件で 1 行知らせる"""
    raw = (raw or '').strip()
    if not raw:
        return Selection()
    if raw.lower() == 'all':
        return Selection(list(range(count)), all=True)
    tokens = [t for t in re.split(r'[,\s]+', raw) if t]
    indexes: List[int] = []
    for token in tokens:
        if not token.isdigit() or not 1 <= int(token) <= count:
            logger.info("取り込む番号として読めないため、取り込みません: %s", raw)
            return Selection()
        index = int(token) - 1
        if index not in indexes:
            indexes.append(index)
    return Selection(indexes)


def resolve(policies: Dict[str, ImportPolicy], group: Optional[str], *,
            interactive: Optional[bool] = None) -> HostImport:
    """対象のグループの方針を決める (E2)。

    ``group`` が ``None`` (``layout: flat`` でプロジェクトの外) なら ``ask``。``ask`` で
    標準入力が端末でなければ ``skip`` へ落とし、1 行知らせる (I3)。
    """
    policy = policies.get(group, ImportPolicy.ASK) if group else ImportPolicy.ASK
    if interactive is None:
        interactive = _stdin_is_tty()
    if policy is ImportPolicy.ASK and not interactive:
        logger.info(
            "標準入力が端末でないため、ホストの資格情報は取り込みません (%s)。"
            "取り込むなら %s で import を指定します", group or 'グループの指定なし', CONFIG_RELPATH)
        return HostImport(ImportPolicy.SKIP, group, fell_back=True)
    return HostImport(policy, group)
