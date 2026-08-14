from __future__ import annotations

import subprocess

import pytest

from omk_system_manager.service_control import BROUTE_SERVICE, BRouteServiceController, ServiceControlError


def test_controller_uses_only_fixed_sudo_systemctl_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    BRouteServiceController("/usr/bin/systemctl").restart_and_verify()

    assert calls == [
        ["sudo", "-n", "/usr/bin/systemctl", "restart", BROUTE_SERVICE],
        ["sudo", "-n", "/usr/bin/systemctl", "is-active", BROUTE_SERVICE],
    ]


@pytest.mark.parametrize("returncodes", [(1,), (0, 3)])
def test_controller_reports_restart_or_active_failure(
    monkeypatch: pytest.MonkeyPatch, returncodes: tuple[int, ...]
) -> None:
    results = iter(returncodes)

    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, next(results), "private output", "private error")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ServiceControlError) as caught:
        BRouteServiceController("/usr/bin/systemctl").restart_and_verify()
    assert caught.value.code in {"credentials_saved_restart_failed", "credentials_saved_service_inactive"}


def test_relative_systemctl_path_is_rejected() -> None:
    with pytest.raises(ValueError):
        BRouteServiceController("systemctl")


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "active\n", True),
        (3, "inactive\n", False),
    ],
)
def test_is_active_uses_systemctl_result(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    expected: bool,
) -> None:
    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)

    assert BRouteServiceController("/usr/bin/systemctl").is_active() is expected
