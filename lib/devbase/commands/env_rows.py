"""TUI の一覧に並べるキーの行 (#273 決定 4)

TUI は機密の置き場を直接読まない。選んだ範囲の参照を現物から 1 回ずつ読み、キーと
そのキーがある参照 (グループ・持ち主・適用範囲) の組を返す。値の平文は返さない
(決定 10。画面の値の列は常に同じ伏せ字になる)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from devbase.env import keys
from devbase.env.secret_store import SecretRef, SecretStore

OWNER_LABELS = {'team': 'チーム', 'user': '個人'}


@dataclass(frozen=True)
class KeyRow:
    """一覧の 1 行。キーと参照の組で、値を持たない"""
    key: str
    ref: SecretRef
    owner_label: str
    scope_label: str
    #: ``version: 2`` のときだけ。読み替えがあれば ``default → nyle`` の形
    group_label: Optional[str]
    #: 並べた参照の中で重ね順が最後の行 (勝つ行)
    wins: bool


@dataclass
class KeyListing:
    rows: List[KeyRow] = field(default_factory=list)
    refs: List[SecretRef] = field(default_factory=list)
    has_user_refs: bool = False
    grouped: bool = False
    #: ``--group`` に渡す名前 (共通のときだけ。プロジェクトと ``version: 2`` でないときは ``None``)
    group: Optional[str] = None
    project: Optional[str] = None
    backend: str = ''


def is_grouped(store: SecretStore) -> bool:
    config = store.config
    return (config.backend == 'openbao' and config.openbao is not None
            and config.openbao.grouped)


def scope_label(ref: SecretRef) -> str:
    return '共通' if ref.kind == 'global' else f'プロジェクト {ref.name}'


def collect_key_rows(devbase_root: Path, project: Optional[str] = None,
                     group: Optional[str] = None) -> KeyListing:
    """選んだ範囲の参照を読み、キーの行を返す。

    ``project`` が ``None`` なら共通 (チーム共通・個人共通)、あればプロジェクトの 2 つも読む。
    ``group`` は ``--group`` と同じ検証を通す (:class:`GroupOptionError`)。``project`` があれば
    無視し、そのプロジェクトのグループを使う (決定 6)。``group`` が無ければ
    ``$DEVBASE_ROOT/env`` のグループ。

    読み出しは 1 つの ``SecretStore`` で参照ごとに ``fetch`` を 1 回 (キャッシュへ落ちない。I4)。
    接続・403・復号の失敗は ``DevbaseError`` のまま送る。
    """
    from devbase.commands.env import _target_group

    store = SecretStore(devbase_root)
    grouped = is_grouped(store)
    if project is not None:
        target = store.ref_group(project)
        option_group = None
    elif group is not None:
        target = option_group = _target_group(devbase_root, store, group)
    else:
        target = store.ref_group(None)
        option_group = target if grouped else None

    team_global = SecretRef.for_global(group=target)
    has_user = store.has_user_refs(team_global)
    owners = ('team', 'user') if has_user else ('team',)
    refs = [SecretRef.for_global(owner=o, group=target) for o in owners]
    if project is not None:
        refs += [SecretRef.for_project(project, owner=o, group=target) for o in owners]

    settings = store.config.openbao if grouped else None
    rows: List[KeyRow] = []
    for ref in refs:
        data = store.fetch(ref)
        group_label = settings.display_group(ref.group) if settings and ref.group else None
        rows += [KeyRow(key=k, ref=ref, owner_label=OWNER_LABELS[ref.owner],
                        scope_label=scope_label(ref), group_label=group_label, wins=False)
                 for k in sorted(data)]

    last = {row.key: i for i, row in enumerate(rows) if row.key != keys.DEVBASE_ACCOUNT_GROUP}
    rows = [row if last.get(row.key) != i else _won(row) for i, row in enumerate(rows)]
    return KeyListing(rows=rows, refs=refs, has_user_refs=has_user, grouped=grouped,
                      group=option_group, project=project,
                      backend=store.backend_for(team_global).name)


def _won(row: KeyRow) -> KeyRow:
    from dataclasses import replace

    return replace(row, wins=True)


def group_choices(devbase_root: Path) -> List[str]:
    """共通のグループの候補 (決定 6)。サーバへ尋ねない。

    ``$DEVBASE_ROOT/env`` のグループを先頭に、各プロジェクトのグループを置き場のグループ名で
    重複を除いて並べる。名前の使えないプロジェクトは飛ばす。
    """
    from devbase.errors import DevbaseError
    from devbase.utils import names

    store = SecretStore(devbase_root)
    choices: List[str] = []
    seen = set()
    candidates = [None] + [p.name for p in names.project_dirs(Path(devbase_root) / 'projects')]
    for project in candidates:
        try:
            name = store.ref_group(project)
            storage = store.storage_group(name)
        except DevbaseError:
            continue
        if name is None or storage in seen:
            continue
        seen.add(storage)
        choices.append(name)
    return choices
