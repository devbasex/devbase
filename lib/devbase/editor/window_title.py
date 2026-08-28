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

settings.json は VS Code が JSONC (コメント / 末尾カンマ可) として扱うため、
読み取りは JSONC を許容し、コメント付きの設定へは**原文を保ったまま**
``window.title`` だけを差し替える (丸ごと書き直すとコメントを失うため)。
書き込みは同一ディレクトリの一時ファイル + ``mv`` による原子的置換で行い、
稼働中の VS Code が中途半端な JSON を読んだり、失敗時に設定を丸ごと失ったり
しないようにする。一時ファイルには既存ファイルの mode を写し取り (新規なら
``umask 077``)、置換でパーミッションが緩まないようにする。
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

# 原子的置換用の一時ファイル。``$$`` はコンテナ内 sh の PID に展開されるので、
# 同時実行しても衝突しない。settings.json と同一ディレクトリなので ``mv`` は
# 同一ファイルシステム内の rename になり原子的。
_TMP_FILE = f"{_SETTINGS_DIR}/.settings.json.devbase.$$"

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


# ---------------------------------------------------------------------------
# JSONC (コメント / 末尾カンマ付き JSON) の読み取りと最小差し替え
#
# VS Code の settings.json は JSONC。``json.loads`` だけだとコメント 1 行で
# 機能が丸ごと止まるため、読み取りは JSONC を許容する。ただしコメント付きの
# 設定を ``json.dumps`` で書き戻すとコメントが消えるので、その場合は原文の
# ``window.title`` の値だけを差し替える (無ければ先頭へ 1 行挿入する)。
# ---------------------------------------------------------------------------

def _end_of_string(text: str, start: int) -> int:
    """``text[start]`` の ``"`` から始まる JSON 文字列の**次**の位置を返す。"""
    i, n = start + 1, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            return i + 1
        i += 1
    return n


def strip_jsonc(text: str) -> str:
    """コメントと末尾カンマを空白へ潰した本文を返す (**位置は原文と一対一**)。

    位置を保つのは、後段で原文へ最小限の差し替えを行うため。改行は残すので
    行番号も変わらない。純粋な JSON なら入力がそのまま返る。
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i = _end_of_string(text, i)
            continue
        if ch == "/" and i + 1 < n and text[i + 1] in "/*":
            line = text[i + 1] == "/"
            end = text.find("\n", i) if line else text.find("*/", i + 2)
            end = n if end < 0 else (end if line else end + 2)
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    # 末尾カンマ (``,`` の次の非空白が ``}`` / ``]``) を空白へ。コメントは
    # 既に空白化済みなので ``, /* c */ }`` も拾える。
    stripped = "".join(out)
    i, n = 0, len(stripped)
    out = list(stripped)
    while i < n:
        ch = stripped[i]
        if ch == '"':
            i = _end_of_string(stripped, i)
            continue
        if ch == ",":
            j = i + 1
            while j < n and stripped[j].isspace():
                j += 1
            if j < n and stripped[j] in "}]":
                out[i] = " "
        i += 1
    return "".join(out)


def _skip_value(text: str, i: int) -> int:
    """``text[i]`` から始まる JSON 値の**次**の位置を返す (壊れていれば -1)。"""
    ch = text[i]
    if ch == '"':
        return _end_of_string(text, i)
    if ch in "{[":
        depth, j, n = 0, i, len(text)
        while j < n:
            c = text[j]
            if c == '"':
                j = _end_of_string(text, j)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return -1
    j, n = i, len(text)
    while j < n and text[j] not in ",}]" and not text[j].isspace():
        j += 1
    return j if j > i else -1


def _root_members(text: str):
    """トップレベルオブジェクトの ``(開き括弧位置, [(key, 値の開始, 値の終了)])``。

    解析できなければ None。``text`` は :func:`strip_jsonc` 済みかつ
    ``json.loads`` が通ったものを渡す前提なので、想定外の形は None で諦める。
    """
    n = len(text)
    i = 0
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "{":
        return None
    open_index = i
    i += 1
    members = []
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return None
        if text[i] == "}":
            return open_index, members
        if text[i] != '"':
            return None
        key_end = _end_of_string(text, i)
        try:
            key = json.loads(text[i:key_end])
        except ValueError:
            return None
        i = key_end
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != ":":
            return None
        i += 1
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return None
        value_start = i
        i = _skip_value(text, i)
        if i < 0:
            return None
        members.append((key, value_start, i))
        while i < n and text[i].isspace():
            i += 1
        if i < n and text[i] == ",":
            i += 1


def _splice_title(text: str, stripped: str, title: str) -> Optional[str]:
    """原文 ``text`` の ``window.title`` の値だけを差し替える (コメント保持)。

    キーが無ければ先頭へ 1 行挿入する。解析できなければ None (何もしない)。
    """
    root = _root_members(stripped)
    if root is None:
        return None
    open_index, members = root
    encoded = json.dumps(title, ensure_ascii=False)
    for key, value_start, value_end in members:
        if key == _TITLE_KEY:
            out = text[:value_start] + encoded + text[value_end:]
            break
    else:
        entry = f'\n\t{json.dumps(_TITLE_KEY)}: {encoded}' + ("," if members else "")
        out = text[:open_index + 1] + entry + text[open_index + 1:]
    return out if out.endswith("\n") else out + "\n"


def merge_settings(current_text: str, title: str) -> Optional[str]:
    """既存 settings.json の本文へ ``window.title`` を差し込んだ本文を返す。

    settings.json は JSONC なので、コメントや末尾カンマがあっても読み取れる。
    書き戻しは中身に応じて 2 通り:

    - 純粋な JSON: 辞書へマージして丸ごと書き直す (Dev Containers 拡張と同じ
      タブインデントへ揃う)
    - コメント / 末尾カンマ入り: 原文を保ったまま ``window.title`` の値だけを
      差し替える (丸ごと書き直すと利用者のコメントを失うため)

    書き込み不要・不可なら None:

    - 既に同じ ``window.title`` が入っている (毎回書くと mtime が変わり、
      VS Code の設定ファイル監視が無駄に反応する)
    - JSONC としても読めない (編集途中で壊れている等)。
      **上書きして壊すより何もしない方が安全**なので握り潰す。
    """
    text = (current_text or "").strip()
    if not text:
        return json.dumps({_TITLE_KEY: title}, indent="\t", ensure_ascii=False) + "\n"

    stripped = strip_jsonc(text)
    try:
        data = json.loads(stripped)
    except ValueError:
        logger.debug("Machine settings.json を解釈できないため window.title は設定しません")
        return None
    if not isinstance(data, dict):
        return None
    if data.get(_TITLE_KEY) == title:
        return None
    if stripped == text:
        data[_TITLE_KEY] = title
        # Dev Containers 拡張が書くのと同じタブインデントに合わせる。
        return json.dumps(data, indent="\t", ensure_ascii=False) + "\n"
    return _splice_title(text, stripped, title)


def _docker_exec(args, runner: Optional[Callable] = None, input_text: Optional[str] = None):
    """``docker exec`` を 1 回実行する (失敗は例外にせず呼び出し側で判定)。"""
    run = runner or subprocess.run
    return run(
        args,
        # settings.json は UTF-8 固定。``text=True`` だけだとホストの既定
        # エンコーディング (Windows 等で非 UTF-8) に引きずられるため明示する。
        capture_output=True, text=True, encoding="utf-8", timeout=_EXEC_TIMEOUT,
        input=input_text, check=False,
    )


def _read_command() -> str:
    """settings.json を読むコンテナ内シェルコマンド。

    **不在だけ**を成功 (空出力) 扱いにする。VS Code が一度も繋がっていない
    コンテナには settings.json が無く、そこで ``cat`` の rc=1 をそのまま
    受け取ると「exec 失敗」と取り違えて初回のコンテナに一切書けない。

    一方で ``cat ... || true`` のように**全ての**失敗を握り潰すと、権限不足や
    I/O エラーで読めなかった既存ファイルまで「空」とみなし、空から作り直した
    設定で :func:`_write_command` が上書きしてしまう。そこで ``[ -e ]`` で
    不在のみを切り分け、存在するファイルの ``cat`` 失敗は非 0 のまま返す
    (呼び出し側が書き込みを中止する)。
    """
    return f'[ -e "{_SETTINGS_FILE}" ] || exit 0\ncat "{_SETTINGS_FILE}"'


def _write_command() -> str:
    """settings.json を**原子的に**置き換えるコンテナ内シェルコマンド。

    稼働中の VS Code が監視・更新するファイルなので、``cat >`` で直接
    truncate すると書き込み途中の不完全な JSON を読まれたり、中断時に設定を
    丸ごと失ったりする。同一ディレクトリの一時ファイルへ書いてから ``mv``
    (同一 FS の rename = 原子的) で差し替える。途中で失敗したときは一時
    ファイルを片付けて非 0 で終わるので、**既存ファイルは元のまま残る**。

    パーミッションは ``mv`` で一時ファイルのものが引き継がれるため、
    既存ファイルがあれば ``cp -p`` でその mode を先に写し取る (設定に秘密値が
    入っていて 600 等にしてある場合、umask 任せの 644 へ緩めてしまわない)。
    新規作成時は ``umask 077`` で本人のみ読める mode にする。
    ``mkdir`` は umask 変更前に実行し、ディレクトリの mode は変えない。
    """
    return (
        f'mkdir -p "{_SETTINGS_DIR}" || exit 1\n'
        "umask 077\n"
        f'if [ -e "{_SETTINGS_FILE}" ]; then\n'
        f'  cp -p "{_SETTINGS_FILE}" "{_TMP_FILE}" || {{ rm -f "{_TMP_FILE}"; exit 1; }}\n'
        "fi\n"
        f'cat > "{_TMP_FILE}" && mv -f "{_TMP_FILE}" "{_SETTINGS_FILE}" '
        f'|| {{ rm -f "{_TMP_FILE}"; exit 1; }}'
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
            # ファイル不在のみ成功 (空) 扱い。読めなかった既存ファイルは非 0 で
            # 返り、下で書き込みを中止する (詳細は _read_command を参照)。
            ["docker", "exec", container_name, "sh", "-c", _read_command()],
            runner=runner,
        )
    except Exception as e:  # noqa: BLE001 - docker 不在/タイムアウト等で up を倒さない
        logger.debug("window.title の読み取りに失敗 (%s): %s", container_name, e)
        return False
    if getattr(read, "returncode", 1) != 0:
        # exec 失敗 (コンテナ不在・停止中) と、存在する settings.json を読めなかった
        # 場合の両方。どちらも中身が分からないので上書きしない。
        logger.debug("window.title: %s の設定を読めませんでした", container_name)
        return False

    merged = merge_settings(read.stdout or "", title)
    if merged is None:
        return False

    try:
        write = _docker_exec(
            ["docker", "exec", "-i", container_name, "sh", "-c", _write_command()],
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
