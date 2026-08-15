"""Gateway-side orchestration for a single OMK Node BLE provisioning job."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable

from .ap_config import AccessPointConfigError, read_ap_ssid
from .ap_credentials import ApCredentialError, read_ap_psk
from .esp_prov_adapter import EspProvisioningToolingError, ensure_available, provision_wifi
from .node_credentials import NodeCredentialError, NodeCredentialStore

LOGGER = logging.getLogger(__name__)
PROVISIONING_TIMEOUT_SECONDS = 60.0


@dataclass
class ProvisioningJob:
    node_id: str
    state: str = "starting"
    error: str | None = None

    def as_dict(self) -> dict[str, str]:
        value = {"provisioning_job_state": self.state}
        if self.error:
            value["provisioning_error"] = self.error
        return value


class NodeProvisioner:
    """Coordinates BLE ownership without embedding secrets in manager state."""
    def __init__(
        self,
        pause_scan: Callable[[], Awaitable[None]],
        resume_scan: Callable[[], Awaitable[None]],
        node_state: Callable[[str], str | None],
        credentials: NodeCredentialStore,
        control_start: Callable[[str], Awaitable[None]],
        provision: Callable[[str, str, str, str], Awaitable[None]] = provision_wifi,
        ssid_reader: Callable[[], str] = read_ap_ssid,
        psk_reader: Callable[[], str] = read_ap_psk,
        timeout_seconds: float = PROVISIONING_TIMEOUT_SECONDS,
    ) -> None:
        self._pause_scan = pause_scan
        self._resume_scan = resume_scan
        self._node_state = node_state
        self._credentials = credentials
        self._control_start = control_start
        self._provision = provision
        self._ssid_reader = ssid_reader
        self._psk_reader = psk_reader
        self._timeout_seconds = timeout_seconds
        self._jobs: dict[str, ProvisioningJob] = {}
        self._active: asyncio.Task[None] | None = None

    def job(self, node_id: str) -> ProvisioningJob | None:
        return self._jobs.get(node_id)

    async def start(self, node_id: str) -> ProvisioningJob:
        if self._active and not self._active.done():
            raise RuntimeError("another node provisioning job is already active")
        if self._node_state(node_id) != "unregistered":
            raise ValueError("node is not awaiting Wi-Fi provisioning")
        ensure_available()
        job = ProvisioningJob(node_id=node_id)
        self._jobs[node_id] = job
        self._active = asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: ProvisioningJob) -> None:
        scanner_paused = False
        stage = "starting"
        try:
            LOGGER.info("Starting OMK Node provisioning node_id=%s", job.node_id)
            # Read secrets only for the active operation. They never become job
            # fields, logs, MQTT data, or HTTP response values.
            pop = self._credentials.read_pop(job.node_id)
            psk = self._psk_reader()
            ssid = self._ssid_reader()
            await self._pause_scan()
            scanner_paused = True
            stage = "waiting_for_provisioning"
            job.state = "waiting_for_provisioning"
            await asyncio.wait_for(self._control_start(job.node_id), timeout=self._timeout_seconds)
            stage = "security_session"
            job.state = "security_session"
            LOGGER.info("Entering Security 1 provisioning node_id=%s", job.node_id)
            await asyncio.wait_for(
                self._provision(f"OMK_{job.node_id}", pop, ssid, psk), timeout=self._timeout_seconds,
            )
            stage = "applying_wifi"
            job.state = "applying_wifi"
            LOGGER.info("Wi-Fi configuration applied node_id=%s", job.node_id)
            # ApplyConfig returns before the node has restarted and rejoined the
            # OMK AP. The normal boot's BLE state or retained MQTT status is the
            # only success signal accepted by the gateway.
            stage = "waiting_for_node"
            job.state = "waiting_for_node"
            LOGGER.info("Waiting for node to join OMK Wi-Fi node_id=%s", job.node_id)
            deadline = monotonic() + self._timeout_seconds
            while monotonic() < deadline:
                if self._node_state(job.node_id) in {"provisioned", "registered"}:
                    job.state = "provisioned"
                    return
                await asyncio.sleep(1)
            raise TimeoutError
        except (AccessPointConfigError, ApCredentialError, NodeCredentialError, EspProvisioningToolingError):
            LOGGER.warning("OMK Node provisioning failed node_id=%s stage=%s", job.node_id, stage)
            job.error = "Wi-Fi provisioning is unavailable or failed"
        except TimeoutError:
            LOGGER.warning("OMK Node provisioning failed node_id=%s stage=%s", job.node_id, stage)
            job.error = "Timed out waiting for the node to join OMK Wi-Fi"
        except Exception:
            # Avoid traceback logging from this secret-bearing operation.
            LOGGER.warning("OMK Node provisioning failed node_id=%s stage=%s", job.node_id, stage)
            job.error = "Wi-Fi provisioning failed"
        finally:
            if job.state != "provisioned":
                job.state = "failed"
            if scanner_paused:
                try:
                    await self._resume_scan()
                except Exception:
                    LOGGER.exception("Could not resume passive BLE collection after provisioning")
