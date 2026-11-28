"""ホストの userns の制限を読む・外す・戻す (#533) のテスト

実の sudo・sysctl・docker は起動しない。paths は tmp、runner は :class:`FakeHost` を渡す。
番号は要求の受け入れ条件の番号である。
"""

from __future__ import annotations

import subprocess

import pytest

from devbase.utils import userns
from tests.conftest import UnexpectedHostCall
from tests.utils.userns_fake import FakeHost, make_paths, write_conf


@pytest.fixture
def root_user(monkeypatch):
    monkeypatch.setattr(userns, '_euid', lambda: 0)


def _text(report):
    return '\n'.join(report.lines + report.warnings)


# --- read_runtime_value -----------------------------------------------------

@pytest.mark.parametrize('content, expected', [('0', 0), ('1\n', 1), ('2', None), ('x', None)])
def test_read_runtime_value(tmp_path, content, expected):
    paths = make_paths(tmp_path, value=None)
    paths.proc_key.write_text(content)
    assert userns.read_runtime_value(paths) == expected


def test_read_runtime_value_missing(tmp_path):
    assert userns.read_runtime_value(make_paths(tmp_path, value=None)) is None


def test_read_runtime_value_unreadable(tmp_path):
    paths = make_paths(tmp_path, value=None)
    paths.proc_key.mkdir()
    assert userns.read_runtime_value(paths) is None


# --- config_value / later_overrides -----------------------------------------

def test_config_value_last_file_wins(tmp_path):
    paths = make_paths(tmp_path)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    p60 = write_conf(paths, '60-x.conf', 0)
    assert userns.config_value(paths) == (0, p60)


def test_config_value_etc_shadows_same_name_in_usr(tmp_path):
    paths = make_paths(tmp_path)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    etc = write_conf(paths, '10-apparmor.conf', 0)
    assert userns.config_value(paths) == (0, etc)


def test_config_value_dash_and_slash_key(tmp_path):
    paths = make_paths(tmp_path)
    p = paths.sysctl_dirs[0] / '30-a.conf'
    p.write_text('-kernel/apparmor_restrict_unprivileged_userns=0\n')
    assert userns.config_value(paths) == (0, p)


def test_config_value_skips_unreadable_and_skip(tmp_path):
    paths = make_paths(tmp_path)
    p10 = write_conf(paths, '10-apparmor.conf', 1, where='usr')
    (paths.sysctl_dirs[0] / '50-dir.conf').mkdir()
    dev = write_conf(paths, '60-devbase-userns.conf', 0, marker=True)
    assert userns.config_value(paths) == (0, dev)
    assert userns.config_value(paths, skip=dev) == (1, p10)


def test_later_overrides(tmp_path):
    paths = make_paths(tmp_path)
    p99 = write_conf(paths, '99-hardening.conf', 1)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    assert userns.later_overrides(paths) == [p99]
    write_conf(paths, '99-hardening.conf', 0)
    assert userns.later_overrides(paths) == []


# --- choose_privilege -------------------------------------------------------

def test_choose_privilege_root(tmp_path, root_user):
    host = FakeHost(make_paths(tmp_path))
    assert userns.choose_privilege(host.paths, host) is userns.Privilege.ROOT
    assert host.calls == []


def test_choose_privilege_sudo_n(tmp_path):
    host = FakeHost(make_paths(tmp_path), sudo_n=0)
    assert userns.choose_privilege(host.paths, host) is userns.Privilege.SUDO


def test_choose_privilege_tty(tmp_path):
    host = FakeHost(make_paths(tmp_path, tty=True))
    assert userns.choose_privilege(host.paths, host) is userns.Privilege.SUDO_PROMPT


def test_choose_privilege_none(tmp_path):
    host = FakeHost(make_paths(tmp_path))
    assert userns.choose_privilege(host.paths, host) is userns.Privilege.NONE


def test_choose_privilege_no_sudo(tmp_path):
    def runner(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])
    paths = make_paths(tmp_path, tty=True)
    assert userns.choose_privilege(paths, runner) is userns.Privilege.NONE


# --- probe_container_kernel（受け入れ条件 15）--------------------------------

def test_probe_reads_from_image_with_pull_never(tmp_path):
    host = FakeHost(make_paths(tmp_path), image=True, container_value='1')
    ck = userns.probe_container_kernel(host)
    assert ck.value == 1 and ck.source == 'image'
    run = [c for c in host.calls if c[:2] == ['docker', 'run']]
    assert len(run) == 1
    assert ['--pull', 'never'] == run[0][run[0].index('--pull'):run[0].index('--pull') + 2]
    assert 'devbase-base:latest' in run[0]


def test_probe_image_without_key(tmp_path):
    host = FakeHost(make_paths(tmp_path), image=True, container_value=None)
    ck = userns.probe_container_kernel(host)
    assert ck.value is None and ck.source == 'image' and 'キーが無い' in ck.reason


def test_probe_no_image_no_apparmor(tmp_path):
    host = FakeHost(make_paths(tmp_path), image=False, security_options='["name=seccomp"]')
    ck = userns.probe_container_kernel(host)
    assert not host.ran('docker', 'run')
    assert ck.source == 'docker-info' and '制限は効かない' in ck.reason


def test_probe_no_image_with_apparmor_is_unconfirmed(tmp_path):
    host = FakeHost(make_paths(tmp_path), image=False)
    ck = userns.probe_container_kernel(host)
    assert not host.ran('docker', 'run')
    assert ck.source == 'none' and 'devbase build' in ck.reason


@pytest.mark.parametrize('case', ['missing', 'timeout', 'unknown-flag', 'daemon'])
def test_probe_unconfirmed(tmp_path, case):
    paths = make_paths(tmp_path)
    if case == 'missing':
        host = FakeHost(paths, docker_missing=True)
    elif case == 'unknown-flag':
        host = FakeHost(paths, image=True, run_rc=125, run_err='unknown flag: --pull\n')
    elif case == 'daemon':
        host = FakeHost(paths, docker_info_rc=1)
    else:
        def host(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get('timeout'))
    ck = userns.probe_container_kernel(host)
    assert ck.value is None and ck.source == 'none' and ck.reason


def test_probe_passes_timeout(tmp_path):
    host = FakeHost(make_paths(tmp_path))
    userns.probe_container_kernel(host)
    assert all(k.get('timeout') == 20.0 for k in host.kwargs)


# --- apply ------------------------------------------------------------------

def test_ac1_root_writes_marker_file_and_sysctl_p(tmp_path, root_user):
    host = FakeHost(make_paths(tmp_path, value=1))
    report = userns.apply(host.paths, host)
    assert report.ok and report.changed
    lines = host.paths.devbase_file.read_text().splitlines()
    assert lines[0].startswith('# devbase:')
    assert lines[1] == 'kernel.apparmor_restrict_unprivileged_userns = 0'
    assert ['sysctl', '-p', str(host.paths.devbase_file)] in host.calls
    assert not host.ran('sysctl', '-w')
    assert userns.read_runtime_value(host.paths) == 0
    assert not any(c[0] == 'sudo' for c in host.calls)


def test_ac2_passwordless_sudo_prefixes_sudo(tmp_path):
    host = FakeHost(make_paths(tmp_path, value=1), sudo_n=0)
    report = userns.apply(host.paths, host)
    assert report.ok
    writes = [c for c in host.non_docker() if 'install' in c or 'sysctl' in c]
    assert writes and all(c[0] == 'sudo' for c in writes)
    assert userns.read_runtime_value(host.paths) == 0


def test_ac3_explanation_before_sudo_v(tmp_path):
    events = []
    host = FakeHost(make_paths(tmp_path, value=1, tty=True), events=events)
    report = userns.apply(host.paths, host, emit=lambda level, line: events.append('out: ' + line))
    assert report.ok
    sudo_v = events.index('run: sudo -v')
    before = '\n'.join(e for e in events[:sudo_v] if e.startswith('out: '))
    assert 'kernel.apparmor_restrict_unprivileged_userns' in before
    assert str(host.paths.devbase_file) in before
    assert '緩むこと' in before
    assert 'devbase userns revert' in before


def test_ac4_no_privilege_prints_commands_and_fails(tmp_path):
    host = FakeHost(make_paths(tmp_path, value=1))
    report = userns.apply(host.paths, host)
    assert not report.ok
    assert not host.paths.devbase_file.exists()
    assert not host.ran('sudo', '-v')
    assert userns.read_runtime_value(host.paths) == 1
    assert f'sudo sysctl -p {host.paths.devbase_file}' in _text(report)


@pytest.mark.parametrize('outcome', [1, KeyboardInterrupt])
def test_ac5_sudo_v_refused_or_interrupted_writes_nothing(tmp_path, outcome):
    host = FakeHost(make_paths(tmp_path, value=1, tty=True), sudo_v=outcome)
    report = userns.apply(host.paths, host)
    assert not report.ok
    assert not host.paths.devbase_file.exists()
    assert not host.ran('install')
    if outcome is KeyboardInterrupt:
        assert '中断' in _text(report)


def test_ac6_already_zero_calls_nothing_but_docker(tmp_path):
    paths = make_paths(tmp_path, value=0)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    write_conf(paths, '20-apparmor.conf', 0)
    host = FakeHost(paths)
    report = userns.apply(paths, host)
    assert report.ok and not report.changed
    assert host.non_docker() == []
    assert '既に 0' in _text(report)
    assert not paths.devbase_file.exists()


def test_ac7_runtime_zero_config_one_writes_file(tmp_path, root_user):
    paths = make_paths(tmp_path, value=0)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    host = FakeHost(paths)
    report = userns.apply(paths, host)
    assert report.ok and report.changed
    assert paths.devbase_file.exists()
    assert '再起動の後も 0' in _text(report)


def test_ac8_no_key_does_not_touch_host(tmp_path):
    host = FakeHost(make_paths(tmp_path, value=None), sudo_n=0)
    report = userns.apply(host.paths, host)
    assert report.ok
    assert host.non_docker() == []
    assert not host.paths.devbase_file.exists()


def test_ac9_in_container_does_not_change(tmp_path):
    host = FakeHost(make_paths(tmp_path, value=1, container=True), sudo_n=0)
    report = userns.apply(host.paths, host)
    assert report.ok
    assert host.non_docker() == []
    assert not host.paths.devbase_file.exists()
    assert 'ホストで' in _text(report)


def test_ac10_later_file_warns_and_is_untouched(tmp_path, root_user):
    paths = make_paths(tmp_path, value=1)
    p99 = write_conf(paths, '99-hardening.conf', 1)
    before = p99.read_text()
    host = FakeHost(paths)
    report = userns.apply(paths, host)
    assert report.ok
    assert any(str(p99) in w for w in report.warnings)
    assert '再起動の後も 0' not in _text(report)
    assert p99.read_text() == before


def test_ac10_rerun_with_devbase_file_does_not_rewrite(tmp_path):
    paths = make_paths(tmp_path, value=0)
    write_conf(paths, '60-devbase-userns.conf', 0, marker=True)
    p99 = write_conf(paths, '99-hardening.conf', 1)
    host = FakeHost(paths, sudo_n=0)
    report = userns.apply(paths, host)
    assert report.ok and not report.changed
    assert not any(c[0] in ('sudo', 'install') for c in host.calls)
    assert any(str(p99) in w for w in report.warnings)


def test_ac11_sysctl_failure_removes_new_file(tmp_path, root_user):
    host = FakeHost(make_paths(tmp_path, value=1), sysctl_p_rc=1)
    report = userns.apply(host.paths, host)
    assert not report.ok
    assert not host.paths.devbase_file.exists()
    assert userns.read_runtime_value(host.paths) == 1


def test_ac11_value_not_zero_restores_previous_marked_file(tmp_path, root_user):
    paths = make_paths(tmp_path, value=1)
    prev = write_conf(paths, '60-devbase-userns.conf', 1, marker=True)
    before = prev.read_text()
    host = FakeHost(paths, sysctl_p_sets=1)
    report = userns.apply(paths, host)
    assert not report.ok
    assert prev.read_text() == before


def test_apply_install_failure(tmp_path, root_user):
    host = FakeHost(make_paths(tmp_path, value=1), install_rc=1)
    report = userns.apply(host.paths, host)
    assert not report.ok
    assert 'Read-only' in _text(report)
    assert not host.ran('sysctl')


def test_ac16_container_kernel_one_shows_guide_without_vm_writes(tmp_path, root_user):
    host = FakeHost(make_paths(tmp_path, value=None), image=True, container_value='1')
    report = userns.apply(host.paths, host)
    assert report.ok
    assert 'colima ssh' in _text(report)
    assert not host.ran('nsenter')
    assert all(c[0] == 'docker' for c in host.calls)
    assert not any('--privileged' in c for c in host.calls)


def test_ac16_after_host_zero_still_one_shows_guide(tmp_path, root_user):
    host = FakeHost(make_paths(tmp_path, value=1), image=True, container_value='1')
    report = userns.apply(host.paths, host)
    assert report.ok and 'colima ssh' in _text(report)


def test_guide_not_shown_when_host_still_one(tmp_path):
    host = FakeHost(make_paths(tmp_path, value=1, container=True), image=True, container_value='1')
    report = userns.apply(host.paths, host)
    assert 'colima ssh' not in _text(report)


def test_ac18_every_call_has_devnull_stdin(tmp_path):
    host = FakeHost(make_paths(tmp_path, value=1, tty=True), image=True)
    userns.apply(host.paths, host)
    write_conf(host.paths, '10-apparmor.conf', 1, where='usr')
    userns.revert(host.paths, host)
    assert host.kwargs
    assert all(k.get('stdin') is subprocess.DEVNULL for k in host.kwargs)


def test_ac22_unmarked_file_is_not_overwritten(tmp_path, root_user):
    paths = make_paths(tmp_path, value=1)
    hand = paths.devbase_file
    hand.write_text('kernel.apparmor_restrict_unprivileged_userns = 1\n')
    host = FakeHost(paths)
    report = userns.apply(paths, host)
    assert not report.ok
    assert hand.read_text() == 'kernel.apparmor_restrict_unprivileged_userns = 1\n'
    assert not host.ran('install')


# --- revert -----------------------------------------------------------------

def test_ac12_revert_removes_and_restores_only_this_key(tmp_path, root_user):
    paths = make_paths(tmp_path, value=0)
    p10 = write_conf(paths, '10-apparmor.conf', 1, where='usr')
    write_conf(paths, '60-devbase-userns.conf', 0, marker=True)
    host = FakeHost(paths)
    report = userns.revert(paths, host)
    assert report.ok and report.changed
    assert not paths.devbase_file.exists()
    assert ['sysctl', '-w', 'kernel.apparmor_restrict_unprivileged_userns=1'] in host.calls
    assert not host.ran('--system')
    assert userns.read_runtime_value(paths) == 1
    assert str(p10) in _text(report)
    assert 'ホスト: 1' in report.lines


def test_revert_without_config_value_leaves_runtime(tmp_path, root_user):
    paths = make_paths(tmp_path, value=0)
    write_conf(paths, '60-devbase-userns.conf', 0, marker=True)
    host = FakeHost(paths)
    report = userns.revert(paths, host)
    assert report.ok
    assert not paths.devbase_file.exists()
    assert not host.ran('sysctl')
    assert userns.read_runtime_value(paths) == 0


def test_ac13_revert_unmarked_file_is_kept(tmp_path, root_user):
    paths = make_paths(tmp_path, value=0)
    paths.devbase_file.write_text('kernel.apparmor_restrict_unprivileged_userns = 0\n')
    host = FakeHost(paths)
    report = userns.revert(paths, host)
    assert not report.ok
    assert paths.devbase_file.exists()
    assert host.calls == []


def test_ac13_revert_without_file_calls_nothing(tmp_path):
    paths = make_paths(tmp_path, value=0)
    report = userns.revert(paths, FakeHost(paths))
    assert report.ok and not report.changed
    # runner も呼ばない: 既定の落ちる runner でも通る
    assert userns.revert(paths).ok


def test_revert_no_privilege_prints_commands(tmp_path):
    paths = make_paths(tmp_path, value=0)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    write_conf(paths, '60-devbase-userns.conf', 0, marker=True)
    host = FakeHost(paths)
    report = userns.revert(paths, host)
    assert not report.ok
    assert paths.devbase_file.exists()
    text = _text(report)
    assert f'sudo rm -f {paths.devbase_file}' in text
    assert 'sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=1' in text


@pytest.mark.parametrize('outcome', [1, KeyboardInterrupt])
def test_revert_sudo_v_refused_keeps_file(tmp_path, outcome):
    paths = make_paths(tmp_path, value=0, tty=True)
    write_conf(paths, '60-devbase-userns.conf', 0, marker=True)
    events = []
    host = FakeHost(paths, sudo_v=outcome, events=events)
    report = userns.revert(paths, host, emit=lambda level, line: events.append('out: ' + line))
    assert not report.ok
    assert paths.devbase_file.exists()
    assert events.index(f'out: 消す: {paths.devbase_file}') < events.index('run: sudo -v')


def test_revert_sysctl_w_failure(tmp_path, root_user):
    paths = make_paths(tmp_path, value=0)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    write_conf(paths, '60-devbase-userns.conf', 0, marker=True)
    host = FakeHost(paths, sysctl_w_rc=1)
    report = userns.revert(paths, host)
    assert not report.ok
    assert not paths.devbase_file.exists()
    assert '再起動' in _text(report)


# --- status（受け入れ条件 14）-----------------------------------------------

def test_ac14_status_three_lines_and_no_writes(tmp_path):
    paths = make_paths(tmp_path, value=1)
    write_conf(paths, '10-apparmor.conf', 1, where='usr')
    host = FakeHost(paths, image=True, container_value='1')
    snapshot = sorted((p, p.read_bytes()) for p in tmp_path.rglob('*') if p.is_file())
    report = userns.status(paths, host)
    assert report.lines[0].startswith('ホスト:') and '10-apparmor.conf' in report.lines[0]
    assert report.lines[1].startswith('devbase のファイル:') and '無し' in report.lines[1]
    assert report.lines[2].startswith('コンテナが動くカーネル:')
    assert host.non_docker() == []
    assert sorted((p, p.read_bytes()) for p in tmp_path.rglob('*') if p.is_file()) == snapshot


def test_status_unconfirmed_reason(tmp_path):
    paths = make_paths(tmp_path, value=None)
    host = FakeHost(paths, docker_missing=True)
    report = userns.status(paths, host)
    assert '未確認（docker が無い）' in report.lines[2]


# --- テストの隔離（受け入れ条件 19）------------------------------------------

def test_ac19_default_runner_refuses_and_paths_point_to_tmp(tmp_path_factory):
    with pytest.raises(UnexpectedHostCall):
        userns.DEFAULT_RUNNER(['sudo', '-n', 'true'])
    paths = userns.DEFAULT_PATHS
    for p in (paths.proc_key, paths.devbase_file, *paths.sysctl_dirs, *paths.container_markers):
        assert str(p).startswith(str(tmp_path_factory.getbasetemp()))
    assert not paths.proc_key.exists()


def test_ac19_apply_with_defaults_never_reaches_host():
    # proc のキーが無い tmp を読み、docker を起動しようとして落ちる runner に当たる
    with pytest.raises(UnexpectedHostCall):
        userns.apply()
