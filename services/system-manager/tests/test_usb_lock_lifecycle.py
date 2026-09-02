from __future__ import annotations

import threading

import pytest

from omk_system_manager.main import scan_usb_candidates_with_serial_lock


def test_candidate_scan_releases_serial_lock_after_success() -> None:
    serial_lock = threading.Lock()
    assert serial_lock.acquire(blocking=False)

    assert scan_usb_candidates_with_serial_lock(serial_lock, lambda: [{"node_id": "9af9509eb8b6"}]) == [{"node_id": "9af9509eb8b6"}]
    assert serial_lock.acquire(blocking=False)
    serial_lock.release()


def test_candidate_scan_releases_serial_lock_after_exception() -> None:
    serial_lock = threading.Lock()
    assert serial_lock.acquire(blocking=False)

    with pytest.raises(RuntimeError, match="scan failed"):
        scan_usb_candidates_with_serial_lock(serial_lock, lambda: (_ for _ in ()).throw(RuntimeError("scan failed")))

    assert serial_lock.acquire(blocking=False)
    serial_lock.release()


def test_repeated_candidate_scans_leave_serial_lock_available_to_provisioning() -> None:
    serial_lock = threading.Lock()
    for _ in range(3):
        assert serial_lock.acquire(blocking=False)
        scan_usb_candidates_with_serial_lock(serial_lock, lambda: [])

    # This is the acquisition performed by a subsequent Provisioning POST.
    assert serial_lock.acquire(blocking=False)
    serial_lock.release()
