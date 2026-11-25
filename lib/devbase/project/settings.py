"""``project.yml`` / ``project.local.yml`` の設定を書き換える。

``scale`` の書き込み (:func:`devbase.project.runtime.write_scale`) 以外の項目を扱う。
書き換えた結果をそのファイルのローダで検証し、通ったものだけをファイルへ書く。検証に
通らない値・自動で書き換えられない書き方では、ファイルに触らずに ``ConfigError`` を送る。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from devbase.errors import ConfigError
from devbase.project.config import config_path, parse_project_config
from devbase.project.local_config import local_config_path, parse_project_local_config
from devbase.project.yaml_edit import YamlDocument

#: ``project.yml`` の最上位へ足すキーを置く位置 (このうち最後のもののすぐ下)
_TOP_LEVEL_ANCHORS = ("version", "scale", "open_editor", "work_dir")


def read_project_document(project_dir: Path) -> YamlDocument:
    """``project.yml`` の書き換え前の文面と中身 (継承前の生の値)。"""
    return _read(config_path(project_dir), missing_ok=False)


def set_project_value(project_dir: Path, key: str, value: Optional[Any]) -> bool:
    """最上位のキーを書き換える。``None`` ならキーを消す。"""
    return _edit_project(project_dir, lambda doc: _put(
        doc, (key,), value, after=_TOP_LEVEL_ANCHORS))


def set_default_value(project_dir: Path, key: str, value: Optional[Any]) -> bool:
    """``defaults`` のキーを書き換える。``None`` ならキーを消す。"""
    return _edit_project(project_dir, lambda doc: _put(doc, ("defaults", key), value))


def set_repo_value(project_dir: Path, index: int, key: str, value: Optional[Any]) -> bool:
    """``repos[index]`` のキーを書き換える。``None`` ならキーを消す。"""
    return _edit_project(project_dir,
                         lambda doc: _put(doc, ("repos", index, key), value))


def set_primary(project_dir: Path, index: int) -> bool:
    """``repos[index]`` を primary にする。ほかの要素の ``primary`` は消す。"""
    def edit(doc: YamlDocument) -> None:
        for i in range(len(doc.data["repos"])):
            _put(doc, ("repos", i, "primary"), True if i == index else None)
    return _edit_project(project_dir, edit)


def add_repo(project_dir: Path, repo: str, owner: Optional[str]) -> bool:
    """``repos`` の末尾へ 1 件足す。``owner`` が ``None`` なら ``defaults.owner`` に従う。"""
    item = {"repo": repo}
    if owner is not None:
        item["owner"] = owner
    return _edit_project(project_dir, lambda doc: doc.append(("repos",), item))


def remove_repo(project_dir: Path, index: int) -> bool:
    """``repos[index]`` を消す。最後の 1 件は消せない。"""
    def edit(doc: YamlDocument) -> None:
        if len(doc.data["repos"]) <= 1:
            raise ConfigError(f"{doc.source}: リポジトリは 1 件以上必要です。")
        doc.delete(("repos", index))
    return _edit_project(project_dir, edit)


def set_docker_value(project_dir: Path, key: str, value: Optional[Any]) -> bool:
    """``project.local.yml`` の ``docker`` 節のキーを書き換える。``None`` ならキーを消す。

    ファイルが無ければ作る。
    """
    path = local_config_path(project_dir)
    doc = _read(path, missing_ok=True)
    return _apply(path, doc, lambda d: _put(d, ("docker", key), value),
                  parse_project_local_config)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _put(doc: YamlDocument, path, value: Optional[Any], *, after=()) -> None:
    if value is None:
        doc.delete(path)
    else:
        doc.set(path, value, after=after)


def _edit_project(project_dir: Path, edit: Callable[[YamlDocument], None]) -> bool:
    path = config_path(project_dir)
    doc = _read(path, missing_ok=False)
    parse_project_config(doc.data, source=str(path))
    return _apply(path, doc, edit, parse_project_config)


def _apply(path: Path, doc: YamlDocument, edit: Callable[[YamlDocument], None],
           validate: Callable[..., Any]) -> bool:
    """編集・検証してから書く。戻り値は文面が変わったか。"""
    before = doc.text
    edit(doc)
    validate(doc.data, source=str(path))
    if doc.text == before:
        return False
    try:
        path.write_bytes(doc.text.encode("utf-8"))
    except OSError as e:
        raise ConfigError(f"{path} へ書き込めません: {e}") from e
    return True


def _read(path: Path, *, missing_ok: bool) -> YamlDocument:
    if missing_ok and not path.exists():
        return YamlDocument("", str(path))
    try:
        # 改行コードを保つため、テキストモードで読まない
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path} を読み込めません: {e}") from e
    return YamlDocument(text, str(path))
