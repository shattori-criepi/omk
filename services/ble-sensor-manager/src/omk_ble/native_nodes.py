"""Node-owned registration, with a durable Gateway desired-state mailbox.

Only the current desired registration is retried. Measurements retain their
existing topics and do not enter this control protocol.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger(__name__)
NODE_AVAILABLE_SECONDS = 120.0
CONFIG_RETRY_SECONDS = 5.0
UINT32_MAX = 0xffffffff


def uint32(value: Any) -> bool:
    return type(value) is int and 0 <= value <= UINT32_MAX


class NativeNodeControl:
    def _node_control_init(self) -> None:
        self._node_availability: dict[str, str] = {}
        # One Paho receipt per Node, not another delivery queue. Paho handles
        # unacknowledged MQTT delivery; we only retry missing endpoint applies.
        self._config_publications: dict[str, tuple[int, Any, float]] = {}

    def _publish_node(self, topic: str, payload: str | bytes, *, qos: int, retain: bool) -> Any:
        if self._mqtt is None or not self._mqtt.is_connected():
            raise RuntimeError("MQTT client is unavailable")
        result = self._mqtt.publish(topic, payload, qos=qos, retain=retain)
        if getattr(result, "rc", mqtt.MQTT_ERR_SUCCESS) != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("MQTT publish failed; desired state remains pending")
        return result

    @staticmethod
    def _next_revision(node: dict[str, Any], observed: int = 0) -> int:
        values = (observed, node.get("revision", 0), node.get("desired_revision", 0))
        if not all(uint32(value) for value in values):
            raise RuntimeError("Node registration revision exhausted or invalid")
        revision = max(values)
        if revision == UINT32_MAX:
            raise RuntimeError("Node registration revision exhausted or invalid")
        return revision + 1

    def _send_desired(self, node_id: str, node: dict[str, Any]) -> None:
        if self._mqtt is None or not self._mqtt.is_connected():
            return
        revision = node.get("desired_revision")
        if not uint32(revision) or revision == 0:
            return
        desired = node.get("desired_logical_id")
        if node.get("revision") == revision and node.get("logical_id") == desired:
            self._config_publications.pop(node_id, None)
            if desired is None:
                self._cleanup_revocation(node_id, revision)
            return
        if node.get("request_state") not in {"request_sent", "remove_pending"}:
            return
        now = self._monotonic_provider()
        previous = self._config_publications.get(node_id)
        if previous:
            previous_revision, info, sent_at = previous
            if not info.is_published() or (previous_revision == revision and now - sent_at < CONFIG_RETRY_SECONDS):
                return
        info = self._publish_node(f"omk/node/{node_id}/registration/config", json.dumps({
            "protocol_version": 2, "revision": revision, "logical_id": desired,
        }), qos=1, retain=True)
        self._config_publications[node_id] = (revision, info, now)

    @staticmethod
    def _reserved_ids(node: dict[str, Any]) -> list[str]:
        # Superseded requests may already be applied on an absent Node. Hold
        # every such name until its latest desired revision is confirmed.
        return sorted(set(node.get("reserved_logical_ids", [])) |
                      {value for key in ("logical_id", "requested_logical_id")
                       if (value := node.get(key))})

    def _cleanup_revocation(self, node_id: str, revision: int) -> None:
        # Caller holds the registration lock across the check and MQTT enqueue.
        # A newer API request must never have its retained config erased here.
        for suffix in ("config", "ack"):
            current = self.node_registry.list().get(node_id, {})
            if (current.get("desired_revision") != revision or current.get("revision") != revision
                    or current.get("desired_logical_id") is not None or current.get("logical_id") is not None):
                return
            self._publish_node(f"omk/node/{node_id}/registration/{suffix}", "", qos=1, retain=True)

    def _cleanup_retired_status(self, node_id: str) -> None:
        current = self.node_registry.list().get(node_id, {})
        retired = list(current.get("retired_logical_ids", []))
        for logical_id in list(retired):
            # Another Node may now own this name. Its availability must survive.
            owner = self._logical_id_owner(logical_id)
            if owner is None or (owner == node_id and current.get("logical_id") != logical_id):
                self._publish_node(f"omk/{logical_id}/status", "", qos=0, retain=True)
            retired.remove(logical_id)
            self.node_registry.update(node_id, retired_logical_ids=retired)
            self._node_availability.pop(logical_id, None)

    def mqtt_nodes_connected(self) -> None:
        """Repopulate retained pending intent after broker/Gateway reconnect."""
        with self._node_registration_lock:
            self._node_availability.clear()
            self._config_publications.clear()
            if self.node_registry:
                for node_id, node in self.node_registry.list().items():
                    try:
                        self._send_desired(node_id, node)
                    except RuntimeError as error:
                        LOGGER.warning("Node config replay failed: %s", error)
        self.refresh_node_availability()

    def retry_pending_node_configs(self) -> None:
        """Retry durable intent even when its first Mesh delivery was lost.

        MQTT QoS 1 confirms broker receipt, not Node application. The service's
        five-second loop is the bounded retry clock; a silent Node or rejected
        identity-conflicting report must not strand a desired registration.
        """
        if self._mqtt is None or not self._mqtt.is_connected() or self.node_registry is None:
            return
        with self._node_registration_lock:
            for node_id, node in self.node_registry.list().items():
                if node.get("request_state") not in {"request_sent", "remove_pending"}:
                    continue
                try:
                    self._send_desired(node_id, node)
                except RuntimeError as error:
                    LOGGER.warning("Pending Node config retry failed: %s", error)

    def request_node_registration(self, node_id: str, logical_id: str) -> dict[str, Any]:
        if not self._node_id_valid(node_id) or not self._logical_id_valid(logical_id):
            raise ValueError("invalid node_id or logical_id")
        with self._node_registration_lock:
            current = next((item for item in self.node_list() if item["node_id"] == node_id), None)
            if current is None or self.node_registry is None:
                raise KeyError(node_id)
            if current.get("registration_state") not in {"provisioned", "registered"}:
                raise ValueError("node Wi-Fi provisioning is not complete")
            owner = self._logical_id_owner(logical_id, excluding_node_id=node_id)
            if owner is not None:
                raise ValueError(f"logical_id is already assigned to node {owner}")
            saved = self.node_registry.list().get(node_id, {})
            # Persist BEFORE publication; a crash or MQTT refusal cannot lose it.
            saved = self.node_registry.update(node_id,
                reserved_logical_ids=self._reserved_ids(saved),
                desired_revision=self._next_revision(saved), desired_logical_id=logical_id,
                requested_logical_id=logical_id, request_state="request_sent", registration_revoked=False)
            self._send_desired(node_id, saved)
        return {"node_id": node_id, "logical_id": logical_id, "status": "request_sent"}

    def remove_node_registration(self, node_id: str) -> dict[str, Any]:
        if not self._node_id_valid(node_id) or self.node_registry is None:
            raise KeyError(node_id)
        with self._node_registration_lock:
            node = self.node_registry.list().get(node_id)
            if node is None:
                raise KeyError(node_id)
            old_id = node.get("logical_id")
            retired = list(node.get("retired_logical_ids", []))
            if old_id and old_id not in retired:
                retired.append(old_id)
            node = self.node_registry.clear_registration(node_id,
                reserved_logical_ids=self._reserved_ids(node),
                desired_revision=self._next_revision(node), desired_logical_id=None,
                registration_state="provisioned", registration_revoked=True,
                request_state="remove_pending", removed_at=self._now_provider().isoformat(),
                retired_logical_ids=retired)
            self._send_desired(node_id, node)
            self._cleanup_retired_status(node_id)
        return {"node_id": node_id, "status": "removed"}

    def handle_node_mqtt(self, topic: str, payload: bytes, *, retain: bool = False) -> None:
        parts = topic.split("/")
        heartbeat = len(parts) == 4 and parts[:2] == ["omk", "node"] and parts[3] == "status"
        registration = (len(parts) == 5 and parts[:2] == ["omk", "node"]
                        and parts[3] == "registration" and parts[4] in {"status", "ack"})
        if not (heartbeat or registration) or not self._node_id_valid(parts[2]) or self.node_registry is None:
            return
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if (not isinstance(value, dict) or value.get("protocol_version") != 2
                or value.get("node_id") != parts[2] or not uint32(value.get("revision"))):
            return
        state, logical_id = value.get("registration_state"), value.get("logical_id")
        if (state not in {"registered", "provisioned"}
                or (state == "registered" and not self._logical_id_valid(logical_id))
                or (state == "provisioned" and logical_id is not None)):
            return
        is_ack = registration and parts[4] == "ack"
        if is_ack and value.get("result") != "applied":
            return
        if not is_ack and (type(value.get("capabilities")) is not int
                or not 0 <= value["capabilities"] <= 3
                or not isinstance(value.get("connected_sensors"), list)
                or any(sensor != "sen66" for sensor in value["connected_sensors"])):
            return
        with self._node_registration_lock:
            node_id = parts[2]
            current = self.node_registry.list().get(node_id, {})
            activity = {}
            if heartbeat:
                if retain or not self._accept_heartbeat(current, value, activity):
                    return
            revision = value["revision"]
            # Migrate only unversioned intent, never manufacture an assignment
            # from the legacy applied ID. A legacy revoke remains a tombstone.
            if "desired_revision" not in current:
                desired = current.get("requested_logical_id")
                if current.get("registration_revoked") or desired:
                    current = self.node_registry.update(node_id,
                        desired_revision=self._next_revision(current, revision),
                        desired_logical_id=None if current.get("registration_revoked") else desired,
                        request_state="remove_pending" if current.get("registration_revoked") else "request_sent")
            desired_revision = current.get("desired_revision")
            desired = current.get("desired_logical_id")
            matches = revision == desired_revision and logical_id == desired
            pending = current.get("request_state") in {"request_sent", "remove_pending"}
            if current.get("registration_revoked") and logical_id is not None:
                pending = True
            # Only matching endpoint results complete a local intent. A newer
            # conflicting endpoint state rebases that intent; old ACKs do not.
            if desired_revision is not None and not matches and pending:
                if not is_ack and revision >= desired_revision:
                    current = self.node_registry.update(node_id,
                        desired_revision=self._next_revision(current, revision),
                        request_state="remove_pending" if desired is None else "request_sent", **activity)
                elif activity:
                    current = self.node_registry.update(node_id, **activity)
                try:
                    self._send_desired(node_id, current)
                except RuntimeError as error:
                    LOGGER.warning("Node config replay failed: %s", error)
                return
            if is_ack and (desired_revision is None or not matches):
                return
            # Resolve/replay pending intent before ownership validation. After
            # revoke the old name may already belong to another Node; its old
            # owner must still receive the pending clear, never adopt the name.
            if logical_id and self._logical_id_owner(logical_id, excluding_node_id=node_id) is not None:
                return
            if revision < current.get("revision", 0):
                return
            if (revision == current.get("revision") and logical_id != current.get("logical_id")
                    and not matches):
                return
            fields = {"protocol_version": 2, "revision": revision,
                      "registration_state": state, **activity}
            old_id = current.get("logical_id")
            if old_id and old_id != logical_id:
                retired = list(current.get("retired_logical_ids", []))
                if old_id not in retired:
                    retired.append(old_id)
                fields["retired_logical_ids"] = retired
            if not is_ack:
                fields.update(capabilities=value["capabilities"], connected_sensors=value["connected_sensors"],
                              mqtt_status_seen_at=self._now_provider().isoformat())
            if matches:
                fields.update(requested_logical_id=None, request_state="registered" if logical_id else "removed",
                              registration_revoked=logical_id is None, reserved_logical_ids=[])
                if pending:
                    fields["ack_seen_at"] = self._now_provider().isoformat()
            elif desired_revision is not None:
                # A physical Node reset/clear with a newer revision supersedes
                # an already completed request, never a pending user intent.
                fields.update(desired_revision=revision, desired_logical_id=logical_id,
                              registration_revoked=logical_id is None,
                              request_state="registered" if logical_id else "removed")
            if logical_id:
                fields["logical_id"] = logical_id
                current = self.node_registry.update(node_id, **fields)
            else:
                current = self.node_registry.clear_registration(node_id, **fields)
            if matches:
                self._config_publications.pop(node_id, None)
            if matches and logical_id is None:
                try:
                    self._cleanup_revocation(node_id, revision)
                except RuntimeError as error:
                    LOGGER.warning("Node revocation cleanup pending: %s", error)
        self.refresh_node_availability()

    def _accept_heartbeat(self, node: dict[str, Any], value: dict[str, Any], fields: dict[str, Any]) -> bool:
        return self._accept_stream(node, value, "heartbeat", fields, require_uptime=True)

    def _accept_telemetry(self, node_id: str, node: dict[str, Any], value: dict[str, Any],
                          kind: str, fields: dict[str, Any]) -> bool:
        if (not self._node_id_valid(node_id) or value.get("node_id") != node_id
                or value.get("protocol_version") != 2 or not uint32(value.get("revision"))):
            return False
        return self._accept_stream(node, value, kind, fields)

    def _accept_stream(self, node: dict[str, Any], value: dict[str, Any], kind: str,
                       fields: dict[str, Any], *, require_uptime: bool = False) -> bool:
        boot, seq, uptime = value.get("boot_id"), value.get("seq"), value.get("uptime_s")
        if (not isinstance(boot, str) or len(boot) != 16 or any(c not in "0123456789abcdef" for c in boot)
                or not uint32(seq) or (require_uptime and not uint32(uptime))):
            return False
        previous = node.get("activity_boot_id")
        retired = node.get("activity_retired_boots", [])
        if boot in retired:
            return False
        if boot == previous and node.get(f"{kind}_boot_id") == boot:
            delta = (seq - node[f"{kind}_seq"]) & UINT32_MAX
            if not 0 < delta < 0x80000000 or (require_uptime and uptime < node[f"{kind}_uptime_s"]):
                return False
        elif boot != previous and previous:
            # Transport queues are bounded to seconds; keep a bounded history
            # across Gateway restart to reject delayed frames from retired boots.
            retired = (retired + [previous])[-8:]
        fields.update({f"{kind}_boot_id": boot, f"{kind}_seq": seq,
                       "activity_boot_id": boot, "activity_retired_boots": retired,
                       f"{kind}_last_seen_at": self._now_provider().isoformat()})
        if require_uptime:
            fields[f"{kind}_uptime_s"] = uptime
        return True

    def refresh_node_availability(self) -> None:
        """Called periodically, also when no message arrives. Root Will is irrelevant."""
        if self._mqtt is None or self.node_registry is None:
            return
        with self._node_registration_lock:
            for node in self.node_list():
                try:
                    self._cleanup_retired_status(node["node_id"])
                except RuntimeError:
                    continue
                logical_id = node.get("logical_id")
                if not logical_id or node.get("registration_revoked"):
                    continue
                state = "online" if node["online"] else "offline"
                if self._node_availability.get(logical_id) == state:
                    continue
                try:
                    self._publish_node(f"omk/{logical_id}/status", json.dumps({
                        "device_id": logical_id, "status": state,
                    }), qos=0, retain=True)
                except RuntimeError:
                    continue
                self._node_availability[logical_id] = state
