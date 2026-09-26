"""OpenBao の接続設定の画面 (#273 F8・F9)

backend が openbao の端末でだけ、接続先 (``url``) とブートストラップ機密 (``role_id`` /
``secret_id``) を変える。保存は ``env backend use`` へ、確認は ``env backend test`` へ委譲する。
token は入力させず、保存もしない (前提 1・決定 9)。backend の切り替えと移行はしない (前提 8)。

- 空のまま確定した欄は変えない (I6。``use`` の「引数が無ければ既存を引き継ぐ」で保つ)
- ``role_id`` / ``secret_id`` は伏せ字の欄で受け、同じプロセスの属性で渡す (I9・決定 8)
- 保存の後に接続を確かめる。失敗しても保存した設定は残し、欄の入力へ戻れる (E11)
"""

from __future__ import annotations

from pathlib import Path

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.tui import flow, menu

logger = get_logger(__name__)

GUIDE_USE = ("devbase env backend use openbao --url <OpenBao の URL> --role-id <role_id> "
             "--secret-id-stdin")
GUIDE_MIGRATE = "devbase env backend migrate --to openbao"
MSG_ROLE_NEEDS_SECRET = "role_id を変えるときは secret_id も入れてください"


def _dispatch_backend(devbase_root: Path, action: str, **attrs) -> int:
    from devbase.tui import actions_env

    return actions_env._dispatch(devbase_root, "backend", backend_action=action, **attrs)


def _backend_name(store) -> str:
    from devbase.env.secret_store import SecretRef

    name = store.backend_name
    if name == "auto":
        return f"auto ({store.backend_for(SecretRef.for_global()).name})"
    return name


def _print_guidance(store) -> None:
    print(f"\n今の backend: {_backend_name(store)}")
    print("OpenBao の接続設定は backend が openbao の端末でだけ変えられます。")
    print("OpenBao を使うには、次のコマンドで設定と機密の移し替えを行ってください:")
    print(f"  {GUIDE_USE}")
    print(f"  {GUIDE_MIGRATE}")


def _print_heading(devbase_root: Path, config) -> None:
    from devbase.env import bootstrap

    try:
        state = "設定済み" if bootstrap.exists(devbase_root) else "未設定"
    except DevbaseError:
        state = "読めません"
    print("\n=== OpenBao の接続設定 ===")
    print(f"  接続先:               {config.openbao.url}")
    print(f"  ブートストラップ機密: {state}")
    print("  空のまま Enter の欄は変えません。")


def _use(devbase_root: Path, url: str, role_id: str, secret_id: str) -> int:
    """``env backend use openbao`` へ委譲する (値を入れた欄だけを渡す)"""
    return _dispatch_backend(
        devbase_root, "use", name="openbao", url=url or None, mount=None, user=None,
        role_id=role_id or None, secret_id_stdin=False, secret_id=secret_id or None,
        cache=None, layout=None, group_aliases=None)


@flow.collect_args
def run(devbase_root: Path):
    """env メニューの「OpenBao の接続設定」。

    戻り値: ``int`` (案内・保存・確認の結果) / ``ARG_CANCEL`` (Esc で env メニューへ) /
    ``None`` (Ctrl-C で全体中止)。
    """
    from devbase.env.secret_store import SecretStore

    try:
        store = SecretStore(devbase_root)
        config = store.config
        if config.backend != "openbao":
            _print_guidance(store)
            return 0
    except DevbaseError as e:
        logger.error("%s", e)
        return 1

    while True:
        _print_heading(devbase_root, SecretStore(devbase_root).config)
        url = flow.need(menu.text(f"接続先 (url) {menu.HINT_BACK}:", allow_empty=True)).strip()
        role_id = flow.need(menu.secret(f"role_id (伏せ字) {menu.HINT_BACK}:"))
        secret_id = flow.need(menu.secret(f"secret_id (伏せ字) {menu.HINT_BACK}:"))
        if role_id and not secret_id:
            logger.error("%s", MSG_ROLE_NEEDS_SECRET)
            continue
        if not (url or role_id or secret_id):
            print("変更はありません。")
            return 0
        if _use(devbase_root, url, role_id, secret_id) != 0:
            continue                    # use の文言が出ている。test を呼ばずに欄へ戻る
        rc = _dispatch_backend(devbase_root, "test")
        if rc == 0:
            return 0
        logger.warning("接続を確かめられませんでした。保存した設定は残っています")
        if flow.need(menu.confirm("接続設定を入れ直しますか?", default=True)) is not True:
            return rc
