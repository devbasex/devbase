"""attach 先 VS Code のウィンドウタイトルを **コンテナ名始まり** に固定する。

`devbase up` は複数プロジェクトのコンテナへ次々と VS Code ウィンドウを開くため、
既定のタイトル (``${dirty}${activeEditorShort}${separator}${rootName}...``) では
先頭が編集中ファイル名になり、ウィンドウをいくつも並べたときにどれがどの
プロジェクトか判別できない。そこで ``window.title`` をコンテナ名始まりに変える。

**書き込み先はコンテナ内の Remote settings** ``~/.vscode-server/data/Machine/settings.json``:

- ``window.title`` は ``ConfigurationScope.WINDOW``。VS Code のスコープ定義上
  「ユーザー / **リモート** / ワークスペース設定で構成可能」なので、Remote settings
  に置けば効く (実機確認済み)。
- クライアント側の attached container config
  (``globalStorage/ms-vscode-remote.remote-containers/``) に書く手もあるが、

  1. ``imageConfigs/<image>.json`` は **イメージ単位**。devbase では
     ``devbase-php:latest`` のような共有イメージを複数プロジェクト/インスタンスが
     使うため、コンテナを区別できない。
  2. ``nameConfigs/<container>.json`` はコンテナ名単位だが、Dev Containers 拡張は
     **nameConfigs が在れば imageConfigs を読まない** (フォールバック関係) ため、
     既存の ``workspaceFolder`` / ``extensions`` を無効化してしまう。

  さらにクライアント側パスは OS 依存で、跨ホスト (Remote-SSH) 構成では
  そもそも devbase の走るホストに存在しない。コンテナ内に書けばこれら全てを回避できる。

コンテナ内に python 等を要求しないよう、読み書きは ``docker exec`` + ``sh`` の
``cat`` で行い、JSON のマージはホスト側で行う。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Optional

from devbase.env import keys
from devbase.log import get_logger

logger = get_logger(__name__)

# VS Code の Remote settings (コンテナ内)。``$HOME`` はコンテナ内で展開する。
_SETTINGS_DIR = "$HOME/.vscode-server/data/Machine"
_SETTINGS_FILE = f"{_SETTINGS_DIR}/settings.json"

_TITLE_KEY = "window.title"

# 既定テンプレート。``{container}`` を実コンテナ名へ置換し、残りは VS Code の
# タイトル変数としてそのまま渡す (``${...}`` は str.format と衝突するため
# format ではなく replace で埋める)。
DEFAULT_TEMPLATE = "{container}${separator}${dirty}${activeEditorShort}"

# ``DEVBASE_WINDOW_TITLE`` に与えると機能を止める値 (大小無視)。空文字も同じ。
_DISABLED = {"", "0", "false", "no", "off"}

# docker exec の待ち時間上限 (秒)。ローカルの docker への 1 コマンドなので即答する。
_EXEC_TIMEOUT = 15


def resolve_template(environ=None) -> Optional[str]:
    """使用するタイトルテンプレートを返す。無効化されていれば None。

    env ``DEVBASE_WINDOW_TITLE`` が未設定なら :data:`DEFAULT_TEMPLATE`。
    ``0`` / ``false`` / ``off`` / 空文字なら None (何もしない)。
    それ以外は値をそのままテンプレートとして使う。
    """
    env = os.environ if environ is None else environ
    value = env.get(keys.DEVBASE_WINDOW_TITLE)
    if value is None:
        return DEFAULT_TEMPLATE
    if value.strip().lower() in _DISABLED:
        return None
    return value


def render(template: str, container_name: str) -> str:
    """テンプレートの ``{container}`` を実コンテナ名へ置換する。"""
    return template.replace("{container}", container_name)


def merge_settings(current_text: str, title: str) -> Optional[str]:
    """既存 settings.json の本文へ ``window.title`` を差し込んだ本文を返す。

    書き込み不要・不可なら None:

    - 既に同じ ``window.title`` が入っている (毎回書くと mtime が変わり、
      VS Code の設定ファイル監視が無駄に反応する)
    - 中身が JSON として読めない (コメント付き jsonc へ手を入れた等)。
      **上書きして壊すより何もしない方が安全**なので握り潰す。
    """
    text = (current_text or "").strip()
    if text:
        try:
            data = json.loads(text)
        except ValueError:
            logger.debug("Machine settings.json を解釈できないため window.title は設定しません")
            return None
        if not isinstance(data, dict):
            return None
    else:
        data = {}
    if data.get(_TITLE_KEY) == title:
        return None
    data[_TITLE_KEY] = title
    # Dev Containers 拡張が書くのと同じタブインデントに合わせる。
    return json.dumps(data, indent="\t", ensure_ascii=False) + "\n"


def _docker_exec(args, runner: Optional[Callable] = None, input_text: Optional[str] = None):
    """``docker exec`` を 1 回実行する (失敗は例外にせず呼び出し側で判定)。"""
    run = runner or subprocess.run
    return run(
        args,
        capture_output=True, text=True, timeout=_EXEC_TIMEOUT,
        input=input_text, check=False,
    )


def apply_to_container(container_name: str, template: Optional[str] = None,
                       environ=None, runner: Optional[Callable] = None) -> bool:
    """コンテナ内の Remote settings へ ``window.title`` を書く。

    書けたら True。無効化・変更不要・docker 失敗はすべて False を返し、
    例外は投げない (``up`` を倒さないため)。
    """
    if template is None:
        template = resolve_template(environ)
    if template is None:
        return False

    title = render(template, container_name)

    try:
        read = _docker_exec(
            # ``|| true`` でファイル不在 (VS Code が一度も繋がっていないコンテナ) を
            # 成功扱いにする。これが無いと cat の rc=1 をそのまま受け取り、初回の
            # コンテナに一切書けない。``docker exec`` 自体の失敗 (コンテナ不在・
            # 停止中) は docker が非 0 を返すのでここでも検出できる。
            ["docker", "exec", container_name, "sh", "-c",
             f'cat "{_SETTINGS_FILE}" 2>/dev/null || true'],
            runner=runner,
        )
    except Exception as e:  # noqa: BLE001 - docker 不在/タイムアウト等で up を倒さない
        logger.debug("window.title の読み取りに失敗 (%s): %s", container_name, e)
        return False
    if getattr(read, "returncode", 1) != 0:
        logger.debug("window.title: %s へ exec できませんでした", container_name)
        return False

    merged = merge_settings(read.stdout or "", title)
    if merged is None:
        return False

    try:
        write = _docker_exec(
            ["docker", "exec", "-i", container_name, "sh", "-c",
             f'mkdir -p "{_SETTINGS_DIR}" && cat > "{_SETTINGS_FILE}"'],
            runner=runner, input_text=merged,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("window.title の書き込みに失敗 (%s): %s", container_name, e)
        return False
    if getattr(write, "returncode", 1) != 0:
        logger.debug("window.title の書き込みに失敗 (%s)", container_name)
        return False

    logger.debug("window.title = %r (%s)", title, container_name)
    return True
