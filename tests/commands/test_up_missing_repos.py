"""`devbase up` が clone できなかったリポジトリを伝える (PLAN37)。

clone の失敗は entrypoint 側で warning に留まりコンテナ起動は続く。その warning は
`docker logs` にしか出ないため、`up` の画面だけでは「揃っている」と読めてしまう。
ここでは `/work` の実体を見て不足を報告する経路を固定する。
"""

from __future__ import annotations

import logging
import subprocess

import pytest

from devbase.commands import container
from devbase.project.config import parse_project_config


def config_of(*repos):
    return parse_project_config(
        {"version": 1, "defaults": {"owner": "volareinc"},
         "repos": [{"repo": r} for r in repos]},
        source="project.yml")


@pytest.fixture
def work_listing(monkeypatch):
    """`docker compose exec ... ls -A1 /work` の応答を差し替える。"""
    calls: list = []

    def install(responses):
        def fake(command, **kwargs):
            calls.append(command)
            service = command[2]
            reply = responses[service]
            if isinstance(reply, Exception):
                raise reply
            return subprocess.CompletedProcess(command, 0, stdout=reply, stderr="")

        monkeypatch.setattr(container, 'docker_compose', fake)
        return calls

    return install


def warnings_of(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_missing_repositories_are_named_with_their_clone_url(work_listing, caplog):
    work_listing({"dev-1": "carmo\n"})

    with caplog.at_level(logging.WARNING):
        container._report_missing_repos(config_of("carmo", "carmo-batch"),
                                        scale=1, dev_service_name="dev",
                                        project_name="carmo")

    messages = "\n".join(warnings_of(caplog))
    assert "carmo-batch" in messages
    assert "https://github.com/volareinc/carmo-batch.git" in messages


def test_nothing_is_reported_when_every_repository_is_present(work_listing, caplog):
    """揃っているときの出力は従来どおり (正常時にノイズを増やさない)。"""
    work_listing({"dev-1": "carmo\ncarmo-batch\n"})

    with caplog.at_level(logging.WARNING):
        container._report_missing_repos(config_of("carmo", "carmo-batch"),
                                        scale=1, dev_service_name="dev",
                                        project_name="carmo")

    assert warnings_of(caplog) == []


def test_other_directories_in_the_shared_work_volume_are_ignored(work_listing, caplog):
    """/work は他プロジェクトと共有される。関係ないディレクトリは判定に使わない。"""
    work_listing({"dev-1": "carmo\ncarmo-batch\nuttaro-system\n.pnpm-store\n"})

    with caplog.at_level(logging.WARNING):
        container._report_missing_repos(config_of("carmo", "carmo-batch"),
                                        scale=1, dev_service_name="dev",
                                        project_name="carmo")

    assert warnings_of(caplog) == []


def test_every_instance_is_checked(work_listing, caplog):
    """scale>1 では instance ごとに /work ボリュームが別なので全部見る。"""
    calls = work_listing({"dev-1": "carmo\ncarmo-batch\n", "dev-2": "carmo\n"})

    with caplog.at_level(logging.WARNING):
        container._report_missing_repos(config_of("carmo", "carmo-batch"),
                                        scale=2, dev_service_name="dev",
                                        project_name="carmo")

    assert [c[2] for c in calls] == ["dev-1", "dev-2"]
    messages = "\n".join(warnings_of(caplog))
    assert "dev-2" in messages
    assert "dev-1" not in messages


def test_a_failed_lookup_stays_silent(work_listing, caplog):
    """問い合わせが失敗しても up は成功済み。付随情報のために騒がない。"""
    work_listing({"dev-1": subprocess.CalledProcessError(1, "docker")})

    with caplog.at_level(logging.WARNING):
        container._report_missing_repos(config_of("carmo", "carmo-batch"),
                                        scale=1, dev_service_name="dev",
                                        project_name="carmo")

    assert warnings_of(caplog) == []
