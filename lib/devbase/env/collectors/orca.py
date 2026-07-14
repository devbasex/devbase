"""Orca 連携 (SSH 公開鍵) コレクター (PLAN33)

Orca からコンテナへ公開鍵認証で SSH 接続するため、laptop の公開鍵
(``~/.ssh/id_ed25519.pub`` など) を ``SSH_AUTHORIZED_KEYS`` として収集する。
この値は entrypoint がコンテナ内の ``~/.ssh/authorized_keys`` へ展開する。

併せて生成 config の ``HostName`` に使う ``DEVBASE_ORCA_HOSTNAME`` (Tailscale 名 /
Mac の LAN IP。Windows から直結する構成向け) を任意で収集する。
詳細: docs/user/orca.md
"""

from pathlib import Path

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input
from devbase.env.collector import Collector

logger = get_logger(__name__)

DEFAULT_ORCA_HOSTNAME = "127.0.0.1"

# 公開鍵の探索順 (最初に存在したものを既定として提示する)
_PUBKEY_CANDIDATES = ("id_ed25519.pub", "id_rsa.pub")


def _default_public_key() -> str:
    """laptop の公開鍵内容を返す。無ければ空文字。

    ``~/.ssh/id_ed25519.pub`` → ``~/.ssh/id_rsa.pub`` の順に最初に存在した
    ファイルの内容を返す (env export/import の既定鍵探索順と揃える)。
    """
    ssh_dir = Path.home() / ".ssh"
    for name in _PUBKEY_CANDIDATES:
        pub = ssh_dir / name
        try:
            if pub.is_file():
                return pub.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def _abbrev_key_for_prompt(value: str) -> str:
    """公開鍵をプロンプト表示用に短縮した文字列を返す。

    公開鍵は数百文字・複数行になり得るため、そのまま ``safe_input`` の
    プロンプトへ ``[{value}]`` として埋め込むとターミナル表示が崩れる。
    鍵種別 (``ssh-ed25519`` 等) と本体末尾の数文字だけを示し、後ろに
    ``(設定済み)`` を付けて「Enter で既存値を維持できる」ことを伝える。
    実際の既定値 (フル鍵) は呼び出し側で ``safe_input`` の ``default`` 引数
    として渡すため、表示を短縮しても Enter 時に返る値は変わらない。
    """
    first = value.strip().splitlines()[0] if value.strip() else ""
    parts = first.split()
    if len(parts) >= 2 and parts[0].startswith("ssh-"):
        key_type, body = parts[0], parts[1]
        tail = body[-6:] if len(body) > 6 else body
        return f"{key_type} …{tail} (設定済み)"
    return "(設定済み)"


def collect_orca_info(env_file: EnvFile) -> None:
    """Orca 連携情報 (SSH 公開鍵 / HostName) を対話的に収集する"""
    print("\n=== Orca 連携 (SSH 公開鍵) ===")

    # SSH_AUTHORIZED_KEYS: 既存値 > laptop (Mac) の公開鍵 を既定として提示する。
    # 登録すべきは「Orca を動かすマシンの公開鍵」。同一 Mac の Orca なら自動収集した
    # Mac の鍵で足りるが、Windows の Orca からは Windows の公開鍵を登録する必要がある
    # (詳細: docs/user/orca.md)。公開鍵が見つからず既存値も無い場合はスキップ。
    print("  ※ 登録するのは Orca を動かすマシンの公開鍵です (Windows の Orca なら Windows 側の鍵)。")
    default_keys = env_file.get(keys.SSH_AUTHORIZED_KEYS) or _default_public_key()
    if default_keys:
        # 公開鍵は長大・複数行になり得るので、プロンプト表示は短縮する
        # (Enter で維持される既定値は default_keys 全体のまま)。
        value = safe_input(
            f"{keys.SSH_AUTHORIZED_KEYS} [{_abbrev_key_for_prompt(default_keys)}]: ",
            default_keys,
        )
        if value:
            env_file.set(keys.SSH_AUTHORIZED_KEYS, value)
    else:
        logger.info(
            "%s: ~/.ssh/id_ed25519.pub / id_rsa.pub が見つからずスキップ "
            "(設定するまで Orca の公開鍵認証は利用できません)",
            keys.SSH_AUTHORIZED_KEYS,
        )

    # DEVBASE_ORCA_HOSTNAME: 任意。既定 127.0.0.1 (Tailscale 名 / LAN IP で上書き可
    # → Windows から直結する構成に対応)。
    default_host = env_file.get(keys.DEVBASE_ORCA_HOSTNAME) or DEFAULT_ORCA_HOSTNAME
    host = safe_input(f"{keys.DEVBASE_ORCA_HOSTNAME} [{default_host}]: ", default_host)
    if host:
        env_file.set(keys.DEVBASE_ORCA_HOSTNAME, host)


COLLECTOR = Collector(
    name="orca",
    display_name="Orca 連携 (SSH 公開鍵)",
    collect_fn=collect_orca_info,
)
