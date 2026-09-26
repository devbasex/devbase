"""#276: プロジェクトとして数える名前の述語と、名前の形の説明の消費側。

``projects/`` の走査 13 か所 (S1〜S13。番号は ``issues/issue-276-design.md`` の走査の表) が、
名前の条件として ``devbase.utils.names.counts_as_project`` を呼ぶことを見る。定義元の 1 か所を
差し替え、それがすべての走査へ届くかで確かめる。独自の規則を持つ走査や、
``from devbase.utils.names import counts_as_project`` と読み込んだ走査には差し替えが届かず、
その走査の行が落ちる。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest
from devbase.utils import names

#: 差し替えた述語が偽を返す名前。形に合う名前 (S2 だけは形に合わない名前を使う)
TARGET = 'bar'
#: S2 は名前の形に合わない名前を知らせる走査なので、形に合わない名前で見る
TARGET_BAD_FORM = '_bar'
DOT_NAME = '.foo'

LIB = Path(__file__).resolve().parents[2] / 'lib'


def _make_projects(projects_dir: Path) -> None:
    for name in (TARGET, TARGET_BAD_FORM, DOT_NAME):
        d = projects_dir / name
        d.mkdir(parents=True)
        (d / '.env').write_text('K=1\n')
        (d / 'compose.yml').write_text('services: {}\n')


def _plugin(root: Path) -> SimpleNamespace:
    plugin_path = 'plugins/p'
    _make_projects(root / plugin_path / 'projects')
    return SimpleNamespace(name='p', path=plugin_path, version='1', source='s',
                           installed_at='t', linked=False)


# ---------------------------------------------------------------------------
# 走査ごとの「数えた名前」の取り出し方。どれも root を受け、数えた名前の集合を返す
# ---------------------------------------------------------------------------

def _s1_discover_projects(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.plugin import syncer
    plugin = _plugin(root)
    return syncer.discover_projects(root / plugin.path)


def _s2_sync_real_directory_warning(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.plugin import syncer
    _make_projects(root / 'projects')
    registry = SimpleNamespace(get_projects_dir=lambda: root / 'projects',
                               list_installed=list)
    with caplog.at_level(logging.WARNING):
        syncer.sync_projects(registry, verbose=False)
    return {n for n in (TARGET, TARGET_BAD_FORM, DOT_NAME)
            if any(f"'{n}'" in r.getMessage() for r in caplog.records)}


def _s3_plugin_info(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.plugin import info
    plugin = _plugin(root)
    registry = SimpleNamespace(get=lambda name: plugin, devbase_root=root)
    info.show_plugin_info(registry, 'p')
    out = capsys.readouterr().out
    return {line.strip()[2:] for line in out.splitlines() if line.strip().startswith('- ')}


def _s4_status_plugin_count(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import status
    plugin = _plugin(root)
    registry = SimpleNamespace(list_installed=lambda: [plugin], devbase_root=root)
    [row] = status._get_plugin_info(registry)
    # 数だけが出るので、3 つのうち数えなかった名前の分だけ減る。数から名前へ戻す
    counted = {TARGET, TARGET_BAD_FORM, DOT_NAME}
    if row['project_count'] < 3:
        counted.discard(DOT_NAME)
    if row['project_count'] < 2:
        counted.discard(TARGET)
    return counted


def _stub_container_status(monkeypatch) -> None:
    from devbase.commands import status
    monkeypatch.setattr(status, '_running_counts_by_project', dict)
    monkeypatch.setattr(status, '_container_status_for',
                        lambda entry, counts=None: {'name': entry.name, 'status': 'stopped'})


def _s5_status_containers(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import status
    _make_projects(root / 'projects')
    _stub_container_status(monkeypatch)
    return {row['name'] for row in status._get_container_status(root / 'projects')}


def _s6_project_list(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import project
    _make_projects(root / 'projects')
    _stub_container_status(monkeypatch)
    return {row['name'] for row in project.list_projects(root / 'projects')}


def _s7_unknown_project_candidates(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import container
    _make_projects(root / 'projects')
    with caplog.at_level(logging.ERROR):
        container._report_unknown_project('nope', root / 'projects')
    listing = next(r.getMessage() for r in caplog.records
                   if '利用可能なプロジェクト' in r.getMessage())
    return set(listing.split(': ', 1)[1].split(', '))


def _s8_migrate_config(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.project import migrate
    _make_projects(root / 'projects')
    monkeypatch.setattr(migrate, 'migrate_project', lambda entry, dry_run=False: entry.name)
    return set(migrate.migrate_projects(root / 'projects', dry_run=True))


def _s9_env_export(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.env import bundle
    _make_projects(root / 'projects')
    entries = bundle.make_entries_from_disk(root, include_global=False, include_metadata=False)
    return {e.arcname.split('/')[2] for e in entries}


def _s10_doctor_conflicts(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import env_ops
    _make_projects(root / 'projects')
    seen: set = set()

    def _exists(ref):
        if ref.kind == 'project':
            seen.add(ref.name)
        return False

    backend = SimpleNamespace(exists=_exists)
    store = SimpleNamespace(age=backend, plaintext=backend)
    env_ops._check_conflicts(root, store, SimpleNamespace(add=lambda *a, **k: None))
    return seen


def _s11_doctor_ignore_probe(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import env_ops
    _make_projects(root / 'projects')
    paths = env_ops._ignore_probe_paths(root, None)
    return {n for n in (TARGET, TARGET_BAD_FORM, DOT_NAME) if f'projects/{n}/.env' in paths}


def _s12_env_encrypt_targets(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import env_migrate
    _make_projects(root / 'projects')
    return set(env_migrate._project_names(root))


def _s13_env_backend_targets(root, monkeypatch, capsys, caplog) -> Iterable[str]:
    from devbase.commands import env_backend
    _make_projects(root / 'projects')
    return set(env_backend._project_names(root))


SCANS: dict[str, tuple[Callable, str]] = {
    'S1_discover_projects': (_s1_discover_projects, TARGET),
    'S2_sync_real_directory_warning': (_s2_sync_real_directory_warning, TARGET_BAD_FORM),
    'S3_plugin_info': (_s3_plugin_info, TARGET),
    'S4_status_plugin_count': (_s4_status_plugin_count, TARGET),
    'S5_status_containers': (_s5_status_containers, TARGET),
    'S6_project_list': (_s6_project_list, TARGET),
    'S7_unknown_project_candidates': (_s7_unknown_project_candidates, TARGET),
    'S8_migrate_config': (_s8_migrate_config, TARGET),
    'S9_env_export': (_s9_env_export, TARGET),
    'S10_doctor_conflicts': (_s10_doctor_conflicts, TARGET),
    'S11_doctor_ignore_probe': (_s11_doctor_ignore_probe, TARGET),
    'S12_env_encrypt_targets': (_s12_env_encrypt_targets, TARGET),
    'S13_env_backend_targets': (_s13_env_backend_targets, TARGET),
}


@pytest.mark.parametrize('scan_id', list(SCANS))
def test_scan_counts_the_target_and_skips_dot_prefixed(scan_id, tmp_path, monkeypatch,
                                                        capsys, caplog):
    """規則のままなら、どの走査も対象の名前を数え、`.` 始まりを数えない。"""
    scan, target = SCANS[scan_id]
    counted = set(scan(tmp_path, monkeypatch, capsys, caplog))
    assert target in counted
    assert DOT_NAME not in counted


@pytest.mark.parametrize('scan_id', list(SCANS))
def test_scan_follows_the_replaced_predicate(scan_id, tmp_path, monkeypatch, capsys, caplog):
    """述語を「対象の名前も偽」へ差し替えると、どの走査も対象の名前を数えない。

    落ちた行の走査は、述語を呼ばずに独自の名前の条件を持っている。
    """
    scan, target = SCANS[scan_id]
    original = names.counts_as_project
    monkeypatch.setattr(names, 'counts_as_project',
                        lambda name: original(name) and name != target)
    counted = set(scan(tmp_path, monkeypatch, capsys, caplog))
    assert target not in counted


# ---------------------------------------------------------------------------
# #226 の受け入れ条件: plugin info・status と同期の一致、.vscode が書き換わらないこと
# ---------------------------------------------------------------------------

def test_plugin_info_and_status_agree_with_discover_projects(tmp_path, capsys):
    from devbase.commands import status
    from devbase.plugin import info, syncer

    plugin_dir = tmp_path / 'plugins' / 'p'
    for name in ('bar', '.foo'):
        (plugin_dir / 'projects' / name).mkdir(parents=True)
    plugin = SimpleNamespace(name='p', path='plugins/p', version='1', source='s',
                             installed_at='t', linked=False)
    registry = SimpleNamespace(get=lambda name: plugin, list_installed=lambda: [plugin],
                               devbase_root=tmp_path)

    info.show_plugin_info(registry, 'p')
    out = capsys.readouterr().out
    assert 'Projects (1):' in out
    assert '- bar' in out
    assert '.foo' not in out
    assert status._get_plugin_info(registry) == [{'name': 'p', 'project_count': 1}]
    assert syncer.discover_projects(plugin_dir) == ['bar']


def test_migrate_config_leaves_dot_prefixed_directory_untouched(tmp_path):
    from devbase.project import migrate

    projects_dir = tmp_path / 'projects'
    for name in ('bar', '.vscode'):
        d = projects_dir / name
        d.mkdir(parents=True)
        (d / 'env').write_text('COMPOSE_PROJECT_NAME=x\n')
    before = (projects_dir / '.vscode' / 'env').read_bytes()

    migrate.migrate_projects(projects_dir, dry_run=False)

    assert sorted(p.name for p in (projects_dir / '.vscode').iterdir()) == ['env']
    assert (projects_dir / '.vscode' / 'env').read_bytes() == before


def test_env_export_warns_for_unbundleable_name_but_not_dot_prefixed(tmp_path, caplog):
    from devbase.env import bundle

    for name in ('bar', '.vscode', 'a b'):
        d = tmp_path / 'projects' / name
        d.mkdir(parents=True)
        (d / '.env').write_text('K=1\n')

    with caplog.at_level(logging.WARNING):
        entries = bundle.make_entries_from_disk(tmp_path, include_global=False,
                                                include_metadata=False)

    assert [e.arcname for e in entries] == ['env/projects/bar/.env']
    messages = [r.getMessage() for r in caplog.records]
    assert any("'a b'" in m for m in messages)
    assert not any('.vscode' in m for m in messages)


# ---------------------------------------------------------------------------
# 規則と文言の所在
# ---------------------------------------------------------------------------

def _lib_files_containing(text: str) -> list[str]:
    return sorted(str(p.relative_to(LIB)) for p in LIB.rglob('*.py')
                  if text in p.read_text(encoding='utf-8'))


def test_dot_prefix_rule_lives_only_in_names():
    """`.` 始まりの判定を持つのは names.py だけ (走査が独自に持たない)。"""
    assert _lib_files_containing("startswith('.')") == ['devbase/utils/names.py']
    assert _lib_files_containing('startswith(".")') == []


def test_name_form_hint_text_lives_only_in_names():
    """名前の形の説明の文は names.py の NAME_FORM_HINT だけが持つ (#229)。"""
    assert _lib_files_containing('英数字で始まり、英数字') == ['devbase/utils/names.py']


def test_resolve_project_name_error_text_is_unchanged(tmp_path, monkeypatch, caplog):
    """#229: 文を NAME_FORM_HINT の埋め込みへ替えても、出る文字列は 1 文字も変わらない。"""
    from devbase.commands import container

    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    with caplog.at_level(logging.ERROR):
        assert container._resolve_project_name('../etc') is False
    assert "プロジェクト名に使えない形です: '../etc'"\
           "（英数字で始まり、英数字・'.'・'-'・'_' だけからなる名前）" in [
               r.getMessage() for r in caplog.records]
