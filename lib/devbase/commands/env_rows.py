"""TUI の一覧に並べるキーの行 (#273 決定 4)

TUI は機密の置き場を直接読まない。選んだ範囲の参照を現物から 1 回ずつ読み、キーと
そのキーがある参照 (グループ・持ち主・適用範囲) の組を返す。値の平文は返さない
(決定 10。画面の値の列は常に同じ伏せ字になる)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from devbase.env.secret_store import OWNER_TEAM, OWNER_USER, SecretRef, SecretStore

OWNER_LABELS = {OWNER_TEAM: 'チーム', OWNER_USER: '個人'}


@dataclass(frozen=True)
class KeyRow:
    """一覧の 1 行。キーと参照の組で、値を持たない"""
    key: str
    ref: SecretRef
    owner_label: str
    scope_label: str
    #: ``version: 2`` のときだけ。読み替えがあれば ``default → nyle`` の形
    group_label: Optional[str]


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


def _scope_refs(store: SecretStore, project: Optional[str],
                group: Optional[str]) -> Tuple[List[SecretRef], bool]:
    """範囲の参照を持ち主ごとに作る (先頭はチーム)。持ち主はその範囲のチームの参照で
    個人の参照があるかを尋ねて決める"""
    def ref(owner: str) -> SecretRef:
        if project is None:
            return SecretRef.for_global(owner=owner, group=group)
        return SecretRef.for_project(project, owner=owner, group=group)

    has_user = store.has_user_refs(ref(OWNER_TEAM))
    owners = (OWNER_TEAM, OWNER_USER) if has_user else (OWNER_TEAM,)
    return [ref(o) for o in owners], has_user


def _resolve_target(store: SecretStore, devbase_root: Path, project: Optional[str],
                    group: Optional[str], grouped: bool) -> Tuple[Optional[str], Optional[str]]:
    """読むグループと ``--group`` に渡す名前の組"""
    from devbase.commands.env import _target_group

    if project is not None:
        return store.ref_group(project), None
    if group is not None:
        target = _target_group(devbase_root, store, group)
        return target, target
    target = store.ref_group(None)
    return target, (target if grouped else None)


def _rows_for_refs(store: SecretStore, refs: List[SecretRef], settings) -> List[KeyRow]:
    """参照ごとに ``fetch`` を 1 回行い、キーの行を並べる"""
    rows: List[KeyRow] = []
    for ref in refs:
        data = store.fetch(ref)
        group_label = settings.display_group(ref.group) if settings and ref.group else None
        rows += [KeyRow(key=k, ref=ref, owner_label=OWNER_LABELS[ref.owner],
                        scope_label=scope_label(ref), group_label=group_label)
                 for k in sorted(data)]
    return rows


def collect_key_rows(devbase_root: Path, project: Optional[str] = None,
                     group: Optional[str] = None) -> KeyListing:
    """選んだ範囲の参照を読み、キーの行を返す。

    ``project`` が ``None`` なら共通 (チーム共通・個人共通)、あればそのプロジェクトの 2 つ
    (チーム・個人) だけを読む。
    ``group`` は ``--group`` と同じ検証を通す (:class:`GroupOptionError`)。``project`` があれば
    無視し、そのプロジェクトのグループを使う (決定 6)。``group`` が無ければ
    ``$DEVBASE_ROOT/env`` のグループ。

    読み出しは 1 つの ``SecretStore`` で参照ごとに ``fetch`` を 1 回 (キャッシュへ落ちない。I4)。
    接続・403・復号の失敗は ``DevbaseError`` のまま送る。
    """
    store = SecretStore(devbase_root)
    grouped = is_grouped(store)
    target, option_group = _resolve_target(store, devbase_root, project, group, grouped)

    refs, has_user = _scope_refs(store, project, target)

    settings = store.config.openbao if grouped else None
    rows = _rows_for_refs(store, refs, settings)
    return KeyListing(rows=rows, refs=refs, has_user_refs=has_user, grouped=grouped,
                      group=option_group, project=project,
                      backend=store.backend_for(refs[0]).name)


def project_names(devbase_root: Path) -> List[str]:
    """``$DEVBASE_ROOT/projects`` のプロジェクトの名前の一覧"""
    from devbase.utils import names

    return [p.name for p in names.project_dirs(Path(devbase_root) / 'projects')]


def count_project_keys(devbase_root: Path) -> List[Tuple[str, Optional[int]]]:
    """プロジェクトごとのキーの数 (TUI の対象プロジェクトの選択に添える)。

    数はそのプロジェクトの参照 (チーム・個人) の行の数で、:func:`collect_key_rows` の
    一覧の件数と同じになる。1 つの ``SecretStore`` で参照ごとに ``fetch`` を 1 回行う。
    読めないプロジェクト (名前が使えない・接続・403・復号の失敗) は ``None`` にして続ける。
    """
    from devbase.errors import DevbaseError

    store = SecretStore(devbase_root)
    counts: List[Tuple[str, Optional[int]]] = []
    for name in project_names(devbase_root):
        try:
            refs, _ = _scope_refs(store, name, store.ref_group(name))
            count = sum(len(store.fetch(r)) for r in refs)
        except DevbaseError:
            count = None
        counts.append((name, count))
    return counts


def group_choices(devbase_root: Path) -> List[str]:
    """共通のグループの候補 (決定 6)。サーバへ尋ねない。

    ``$DEVBASE_ROOT/env`` のグループを先頭に、各プロジェクトのグループを置き場のグループ名で
    重複を除いて並べる。名前の使えないプロジェクトは飛ばす。
    """
    from devbase.errors import DevbaseError

    store = SecretStore(devbase_root)
    choices: List[str] = []
    seen = set()
    candidates = [None] + project_names(devbase_root)
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
