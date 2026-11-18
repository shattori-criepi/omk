#!/usr/bin/env python3
"""Decode schema 1/2 and schema 3 with a durable Wi-Fi TX probe.

identity.valid means bounded top-level boot_id/seq extraction succeeded. It is
not a full JSON validity/admission result. Raw payloads carry no CRC themselves;
use diag_flash.py for stored records and its CRC/commit validation first.
"""
import argparse
import ctypes as c
import json
import importlib.util
from pathlib import Path

U8, U16, U32, I32, U64 = c.c_uint8, c.c_uint16, c.c_uint32, c.c_int32, c.c_uint64

class Packed(c.LittleEndianStructure):
    _layout_ = "ms"  # Explicit packed layout, no bitfields; same fixed byte offsets.
    _pack_ = 1

class Identity(Packed):
    _fields_ = [("kind", U8), ("valid", U8), ("reserved", U16),
                ("node_id", c.c_char * 13), ("boot_id", c.c_char * 17),
                ("padding", U16), ("seq", U32)]

class Topology(Packed):
    _fields_ = [("flags", U32), ("layer", I32), ("parent", U8 * 6), ("root", U8 * 6)]

class Context(Packed):
    _fields_ = [("topology", Topology)] + [(n, U32) for n in (
        "tx_accepted", "tx_dropped", "rx_dropped", "mesh_rx_ok", "mesh_rx_errors",
        "mqtt_published", "parent_disconnects", "root_changes", "recovery_requests",
        "recovery_ok", "recovery_link", "mqtt_pubacks")]

class Event(Packed):
    _fields_ = [("ms", U64), ("type", U32), ("code", I32), ("detail", U32),
                ("duration_us", U32), ("tx_ok", U32), ("tx_errors", U32),
                ("topology", Topology), ("identity", Identity)]

class Sample(Packed):
    _fields_ = [("ms", U64), ("valid", U32), ("tx_pending", I32 * 6),
                ("rx_pending", I32 * 2), ("tx_result", I32), ("rx_result", I32),
                ("heap_free", U32), ("heap_min", U32), ("heap_largest", U32),
                ("counts", U32 * 8)]

class Peer(Packed):
    _fields_ = [("rx_identity", Identity), ("rx_ms", U64), ("stage_identity", Identity),
                ("stage_ms", U64), ("stage", U32), ("rx_count", U32), ("mqtt_accepted", U32), ("failure_since_ms", U64), ("last_failure_ms", U64)]

class Core(Packed):
    _fields_ = [
        ("schema", U32), ("bytes", U32), ("firmware", c.c_char * 24),
        ("node_id", c.c_char * 13), ("boot_id", c.c_char * 17), ("reserved", U16),
        ("reset_reason", U32), ("capture_ms", U64), ("capture_reason", U32),
        ("context", Context), ("counts", U32 * 8), ("first_seen_ms", U64 * 8),
        ("last_result", I32), ("last_duration_us", U32), ("max_duration_us", U32),
        ("tx_start_us", U64), ("inflight", U32), ("upstream", U32),
        ("active_identity", Identity), ("last_ok_identity", Identity),
        ("last_ok_ms", U64), ("first_upstream_ms", U64), ("last_upstream_ms", U64),
        ("last_upstream_ok_ms", U64), ("upstream_attempts", U32),
        ("first_error_valid", U32), ("onset_sample_needed", U32), ("first_error", Event),
        ("baseline", Sample), ("onset", Sample), ("current", Sample),
        ("episode_first_error", Event), ("episode_baseline", Sample), ("episode_onset", Sample),
        ("last_error_ms", U64), ("episode_number", U32),
        ("kind_last_ok_ms", U64 * 7), ("kind_first_error_ms", U64 * 7),
        ("kind_last_error_ms", U64 * 7), ("kind_error_mask", U32),
        ("events", Event * 16), ("event_head", U32), ("event_count", U32), ("event_overwrites", U32),
        ("root_counts", U32 * 8), ("peers", Peer * 4), ("peer_overflows", U32),
        ("last_root_failure", Identity), ("last_root_failure_ms", U64), ("last_root_failure_stage", U32),
        ("last_receipt", Identity), ("last_receipt_ms", U64),
        ("receipt_count", U32), ("recovery_trigger", U32),
        ("demand_observed_ms", U64), ("tx_demand_since_ms", U64), ("delivery_demand_since_ms", U64),
        ("delivery_proven_ms", U64), ("inhibit_until_ms", U64), ("root_since_ms", U64),
        ("tx_demand", U32), ("delivery_demand", U32), ("voting", U32),
        ("observation_start_ms", U64),
    ]

class CoreV2Original(Packed):
    _fields_ = Core._fields_ + [
        ("arm_generation", U32), ("inflight_before_arm", U32),
        ("request_count", U32 * 7), ("sdk_count", U32 * 7), ("sdk_errors", U32 * 7),
        ("ble_periodic", U32 * 4), ("ble_event", U32 * 4),
        ("ble_rejected", U32 * 4), ("ble_suppressed", U32 * 4),
    ]

class CoreV2(Packed):
    _fields_ = CoreV2Original._fields_ + [
        ("save_failure_slot_plus1", U32), ("save_failure_phase", U32),
        ("save_failure_error", I32),
    ]

class CoreV3(Packed):
    _fields_ = [(name, Event * 5 if name == "events" else kind)
                for name, kind in CoreV2._fields_] + [
        ("link_bad_since_ms", U64), ("disconnect_ms", U64),
        ("disconnected_parent", U8 * 6), ("disconnect_reason", U8), ("disconnect_rssi", c.c_int8),
        ("capture_sequence", U32), ("quality_bad", U32), ("probe", U8 * 1424),
    ]


class CoreV3P1(Packed):
    _fields_ = CoreV3._fields_ + [("role_changed_ms", U64)]


def decode_probe(data):
    if data == bytes(1424):
        return None  # Diagnostic fixture / private probe disabled; never fake valid counters.
    root = Path(__file__).resolve()
    candidates = [root.parents[1] / "ble_scan_ab/probe_v2.py",
                  root.parents[1] / "omk-node/tools/ble_scan_ab/probe_v2.py"]
    location = next((p for p in candidates if p.is_file()), None)
    if location is None:
        raise ValueError("probe_v2.py is required for schema 3")
    spec = importlib.util.spec_from_file_location("durable_probe_v2", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.decode(data)


SAVE_PHASES = {0: "success", 1: "init", 2: "erase", 3: "erase_verify", 4: "blank_check",
               5: "header", 6: "payload", 7: "body_verify", 8: "commit", 9: "commit_verify",
               10: "invalid_argument", 11: "disarmed_or_used"}

ERROR_NAMES = {0: "ESP_OK", -1: "ESP_FAIL", 0x101: "ESP_ERR_NO_MEM", 0x102: "ESP_ERR_INVALID_ARG",
               0x103: "ESP_ERR_INVALID_STATE", 0x104: "ESP_ERR_INVALID_SIZE"}
for _i, _name in enumerate((
    "WIFI_NOT_START", "NOT_INIT", "NOT_CONFIG", "NOT_START", "NOT_SUPPORT", "NOT_ALLOWED",
    "NO_MEMORY", "ARGUMENT", "EXCEED_MTU", "TIMEOUT", "DISCONNECTED", "QUEUE_FAIL", "QUEUE_FULL",
    "NO_PARENT_FOUND", "NO_ROUTE_FOUND", "OPTION_NULL", "OPTION_UNKNOWN", "XON_NO_WINDOW", "INTERFACE",
    "DISCARD_DUPLICATE", "DISCARD", "VOTING", "XMIT", "QUEUE_READ", "PS", "RECV_RELEASE"), 1):
    ERROR_NAMES[0x4000 + _i] = "ESP_ERR_MESH_" + _name
BUCKET_NAMES = ["ESP_OK", "ESP_ERR_MESH_TIMEOUT", "ESP_ERR_MESH_NO_MEMORY", "ESP_ERR_MESH_QUEUE_FULL",
                "ESP_ERR_MESH_XMIT", "ESP_ERR_MESH_DISCONNECTED", "ESP_ERR_MESH_NO_ROUTE_FOUND", "OTHER"]
EVENT_NAMES = {1: "TX_RESULT_CHANGE", 2: "MESH", 3: "RECOVERY_REQUEST", 4: "RECOVERY_RESULT",
               5: "STATUS_RECEIPT_RESUMED", 6: "FLASH_IO_BEGIN", 7: "FLASH_IO_END"}
CAPTURE_NAMES = {0: "NONE", 1: "TX_STALL", 2: "RECOVERY", 3: "PEER_SILENT", 4: "FOLLOWUP",
                 5: "DELIVERY_STALL", 6: "KIND_STALL", 7: "ROOT_FAILURE", 8: "ONSET", 9: "PARENT_STALL", 10: "QUALITY", 11: "RECEIPT_SILENT"}
STAGE_NAMES = ["WIRE_BAD", "REMOTE_RX", "INGRESS_REJECT", "BRIDGE_REJECT", "MQTT_OFFLINE",
               "MQTT_OUTBOX_FULL", "MQTT_ENQUEUE_ERROR", "MQTT_ENQUEUE_ACCEPT"]
KIND_NAMES = {1: "SEN66", 2: "BLE_RAW", 3: "NODE_STATUS", 4: "REGISTRATION_STATUS",
              5: "REGISTRATION_ACK", 6: "CONFIG", 7: "STATUS_RECEIPT", 8: "MEASUREMENT"}
MESH_NAMES = ["STARTED", "STOPPED", "CHANNEL_SWITCH", "CHILD_CONNECTED", "CHILD_DISCONNECTED",
              "ROUTING_TABLE_ADD", "ROUTING_TABLE_REMOVE", "PARENT_CONNECTED", "PARENT_DISCONNECTED",
              "NO_PARENT_FOUND", "LAYER_CHANGE", "TODS_STATE", "VOTE_STARTED", "VOTE_STOPPED",
              "ROOT_ADDRESS", "ROOT_SWITCH_REQ", "ROOT_SWITCH_ACK", "ROOT_ASKED_YIELD", "ROOT_FIXED",
              "SCAN_DONE", "NETWORK_STATE", "STOP_RECONNECTION", "FIND_NETWORK", "ROUTER_SWITCH",
              "PS_PARENT_DUTY", "PS_CHILD_DUTY", "PS_DEVICE_DUTY"]


def error_name(code):
    return ERROR_NAMES.get(code, "UNKNOWN_%#x" % (code & 0xffffffff))


def plain(value):
    if isinstance(value, bytes):
        return value.split(b"\0", 1)[0].decode("ascii", errors="replace")
    if isinstance(value, c.Array):
        return [plain(v) for v in value]
    if isinstance(value, c.Structure):
        out = {name: plain(getattr(value, name)) for name, *_ in value._fields_ if name not in ("reserved", "padding")}
        if isinstance(value, Topology):
            for name in ("parent", "root"):
                out[name] = ":".join("%02x" % b for b in out[name])
            out["flag_names"] = [name for bit, name in ((1, "started"), (2, "parent_connected"),
                (4, "root"), (8, "has_ip"), (16, "mqtt_connected"), (32, "rootless")) if out["flags"] & bit]
        elif isinstance(value, Identity):
            out["kind_name"] = KIND_NAMES.get(out["kind"], "UNKNOWN")
            out["valid"] = bool(out["valid"])
        elif isinstance(value, Event):
            out["type_name"] = EVENT_NAMES.get(out["type"], "UNKNOWN")
            if out["type"] in (1, 4, 7):
                out["code_name"] = error_name(out["code"])
            if out["type"] == 2 and 0 <= out["code"] < len(MESH_NAMES):
                out["mesh_event_name"] = "MESH_EVENT_" + MESH_NAMES[out["code"]]
            if out["type"] in (3, 4):
                out["recovery_action"] = out["detail"] >> 16
                out["recovery_reason_flags"] = out["detail"] & 0xffff
        elif isinstance(value, Sample):
            out["counts"] = dict(zip(BUCKET_NAMES, out["counts"]))
            out["tx_result_name"] = error_name(out["tx_result"])
            out["rx_result_name"] = error_name(out["rx_result"])
            out["tx_pending"] = dict(zip(("to_parent", "to_parent_p2p", "to_child", "to_child_p2p", "mgmt", "broadcast"), out["tx_pending"]))
            out["rx_pending"] = dict(zip(("toDS", "toSelf"), out["rx_pending"]))
            out["valid"] = bool(out["valid"])
        elif isinstance(value, Peer):
            out["stage_name"] = STAGE_NAMES[out["stage"]] if out["stage"] < 8 else "UNKNOWN"
        return out
    return value


def decode_payload(payload):
    schema = int.from_bytes(payload[:4], "little")
    layout = {(1, c.sizeof(Core)): Core, (2, c.sizeof(CoreV2Original)): CoreV2Original,
              (2, c.sizeof(CoreV2)): CoreV2, (3, c.sizeof(CoreV3)): CoreV3, (3, c.sizeof(CoreV3P1)): CoreV3P1}.get((schema, len(payload)))
    if layout is None:
        raise ValueError("unsupported diagnostic schema/length")
    raw = layout.from_buffer_copy(payload)
    if raw.bytes != len(payload):
        raise ValueError("unsupported diagnostic schema/length")
    ring_size = len(raw.events)
    if raw.event_count > ring_size or raw.event_head >= ring_size:
        raise ValueError("invalid ring metadata")
    result = plain(raw)
    result["counts"] = dict(zip(BUCKET_NAMES, result["counts"]))
    result["first_seen_ms"] = {name: raw.first_seen_ms[i] for i, name in enumerate(BUCKET_NAMES) if raw.counts[i]}
    result["first_seen_order"] = sorted(result["first_seen_ms"].items(), key=lambda pair: pair[1])
    result["root_counts"] = dict(zip(STAGE_NAMES, result["root_counts"]))
    result["capture_reason_name"] = CAPTURE_NAMES.get(raw.capture_reason, "UNKNOWN")
    result["last_result_name"] = error_name(raw.last_result)
    first = (raw.event_head - raw.event_count) % ring_size
    result["events"] = [plain(raw.events[(first + i) % ring_size]) for i in range(raw.event_count)]
    result["peers"] = [plain(peer) for peer in raw.peers if peer.rx_count]
    result["inflight_age_ms"] = max(0, raw.capture_ms - raw.tx_start_us // 1000) if raw.inflight else None
    for sample_name in ("baseline", "onset", "current", "episode_baseline", "episode_onset"):
        sample = getattr(raw, sample_name)
        result["interval_from_" + sample_name] = (
            {name: raw.counts[i] - sample.counts[i] for i, name in enumerate(BUCKET_NAMES)} if sample.valid else None)
        result["interval_ms_from_" + sample_name] = (
            max(0, raw.capture_ms - sample.ms) if sample.valid else None)
    result["kind_progress"] = {KIND_NAMES[i + 1]: {
        "last_ok_ms": raw.kind_last_ok_ms[i],
        "failing_since_ms": raw.kind_first_error_ms[i] if raw.kind_error_mask & (1 << i) else None,
        "last_error_ms": raw.kind_last_error_ms[i],
    } for i in range(7)}
    if schema >= 2:
        result["traffic"] = {KIND_NAMES[i + 1]: {
            "requests": raw.request_count[i], "sdk_calls": raw.sdk_count[i], "sdk_errors": raw.sdk_errors[i],
        } for i in range(7)}
        for name in ("ble_periodic", "ble_event", "ble_rejected", "ble_suppressed"):
            result[name] = dict(zip(("unknown_or_encrypted", "motion", "contact", "presence"), result[name]))
    result["save_failure_supported"] = isinstance(raw, (CoreV2, CoreV3, CoreV3P1))
    result["save_failure"] = None
    if isinstance(raw, (CoreV2, CoreV3, CoreV3P1)) and raw.save_failure_slot_plus1:
        if raw.save_failure_slot_plus1 > 2:
            raise ValueError("invalid failed-save slot")
        result["save_failure"] = {
            "slot": raw.save_failure_slot_plus1 - 1, "phase": raw.save_failure_phase,
            "phase_name": SAVE_PHASES.get(raw.save_failure_phase, "unknown"),
            "error": raw.save_failure_error, "error_name": error_name(raw.save_failure_error),
        }
    if schema == 3:
        result["probe"] = decode_probe(bytes(raw.probe))
        result["last_disconnect"] = ({
            "ms": raw.disconnect_ms, "bssid": ":".join("%02x" % b for b in raw.disconnected_parent),
            "reason": raw.disconnect_reason, "rssi": raw.disconnect_rssi,
        } if raw.disconnect_ms else None)
        result["disconnected_parent"] = ":".join("%02x" % b for b in raw.disconnected_parent)
    result["notes"] = [
        "identity.valid is extraction validity, not full JSON validation or Root admission",
        "SDK success and MQTT enqueue acceptance are not Gateway delivery proofs",
        "No peer/event entry is not proof of absence; bounded slots/ring can omit history",
        "Uptime clocks differ across nodes; correlate node_id/boot_id/kind/seq",
        "Counters saturate at UINT32_MAX; intervals at saturation are lower bounds",
        "Diagnostic counts start at observation_start_ms (boot or explicit rearm); context counters remain boot totals",
        "A failed save survives reboot only if a later slot commits its failure tuple; legacy payloads have no tuple",
    ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    print(json.dumps(decode_payload(args.payload.read_bytes()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
