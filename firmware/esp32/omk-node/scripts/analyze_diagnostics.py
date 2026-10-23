#!/usr/bin/env python3
"""Read existing collector JSONL; emit diagnostic events and a summary to stdout.

No MQTT, USB, subprocess, network, or input-file writes. All supplied files form
one finite analysis window. Use --since/--until to bound memory for long runs.
Counter gaps locate an admission boundary; they do not establish packet loss.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return result


def analyze(paths, node=None, logical_id=None, since=None, until=None):
    rows, problems, mappings = [], [], set()
    for path in paths:
        with Path(path).open(encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                try:
                    r = json.loads(line)
                    at = timestamp(r["received_at"])
                    topic = r["topic"]
                    if not isinstance(topic, str):
                        raise ValueError("topic is not a string")
                    p = r.get("payload")
                    parts = topic.split("/")
                    # Mapping evidence is useful even outside the selected time window.
                    if (len(parts) == 5 and parts[:2] == ["omk", "node"]
                            and parts[3] == "registration" and parts[4] in ("status", "ack")
                            and isinstance(p, dict) and isinstance(p.get("logical_id"), str)):
                        mappings.add((parts[2], p["logical_id"]))
                    if (since and at < since) or (until and at > until):
                        continue
                    rows.append((at, str(path), line_no, r))
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    problems.append({"file": str(path), "line": line_no, "error": str(error)})
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    logical_ids = {logical_id} if logical_id else {l for n, l in mappings if n == node}
    nodes = {node} if node else {n for n, l in mappings if l == logical_id}
    filtered = bool(node or logical_id)
    current_boot = {}
    for _, _, _, r in rows:
        p = r.get("payload")
        parts = r["topic"].split("/")
        if len(parts) == 4 and parts[:2] == ["omk", "node"] and parts[3] == "network_diagnostics":
            if isinstance(p, dict) and p.get("view") == 0:
                current_boot[parts[2]] = p.get("boot")
    seen_history, duplicate_history = set(), 0
    measurements, statuses, last_topic = {}, {}, {}
    counts = Counter()
    for at, path, line_no, r in rows:
        topic, p = r["topic"], r.get("payload")
        parts = topic.split("/")
        n = parts[2] if len(parts) >= 4 and parts[:2] == ["omk", "node"] else None
        logical = parts[1] if len(parts) == 3 and parts[0] == "omk" else None
        if not isinstance(p, dict):
            continue
        relevant = n is not None or (logical is not None and parts[2] in ("sen66", "status"))
        if not relevant:
            continue
        # Summaries include peers so simultaneous receiver-side gaps remain visible.
        counts[topic] += 1
        last_topic[topic] = r["received_at"]
        if filtered and n not in nodes and logical not in logical_ids:
            continue
        event = {"kind": "record", "received_at": r["received_at"], "topic": topic,
                 "retain": r.get("retain", False), "file": path, "line": line_no,
                 "payload": p}
        if parts[-1] == "network_diagnostics":
            event["history"] = p.get("view") in (1, 2)
            event["matches_latest_live_boot"] = (
                p.get("boot") == current_boot[n] if n in current_boot else None)
            if event["history"]:
                key = (n, p.get("view"), p.get("boot"), p.get("seq"), p.get("at_us"), p.get("elf"))
                if key in seen_history:
                    duplicate_history += 1
                    continue
                seen_history.add(key)
        elif logical is not None and parts[-1] == "sen66":
            meta = p.get("meta", {})
            boot = meta.get("boot_id") if isinstance(meta, dict) else None
            seq = meta.get("seq") if isinstance(meta, dict) else None
            enq = meta.get("enqueue_seq") if isinstance(meta, dict) else None
            previous = measurements.get(logical)
            if previous:
                event["received_interval_s"] = (at-previous[0]).total_seconds()
                event["sensor_boot_changed"] = boot is not None and boot != previous[1]
                if boot is not None and boot == previous[1] and all(
                        isinstance(x, int) for x in (seq, enq, previous[2], previous[3])):
                    ds, de = (seq-previous[2]) & 0xffffffff, (enq-previous[3]) & 0xffffffff
                    if ds == de == 0:
                        event["duplicate_sample"] = True
                    elif 0 < ds < 0x80000000 and 0 < de < 0x80000000:
                        event["read_seq_delta"], event["enqueue_seq_delta"] = ds, de
                        event["accepted_but_not_in_log"] = de-1
                        event["reads_not_accepted_between"] = max(0, ds-de)
                    else:
                        event["sequence_order_uncertain"] = True
            measurements[logical] = (at, boot, seq, enq)
        elif n and parts[-1] == "status" and len(parts) == 4:
            previous = statuses.get(n)
            if previous:
                event["received_interval_s"] = (at-previous[0]).total_seconds()
                old = previous[1]
                event["counter_epoch_changed"] = (
                    p.get("boot_count") != old.get("boot_count") or
                    (isinstance(p.get("uptime_s"), int) and isinstance(old.get("uptime_s"), int)
                     and p["uptime_s"] < old["uptime_s"]))
                if not event["counter_epoch_changed"]:
                    event["admission_delta"] = [
                        b-a if isinstance(a, int) and isinstance(b, int) and b >= a else None
                        for a, b in zip(old.get("admission", []), p.get("admission", []))]
            statuses[n] = (at, p)
        yield event
    yield {"kind": "summary", "mapping_candidates": [list(m) for m in sorted(mappings)],
           "mapping_note": "Historical candidates, not proof of ownership throughout the window; compare registration events.",
           "topic_counts_all_nodes": dict(counts), "last_received_all_nodes": last_topic,
           "duplicate_history_records": duplicate_history, "input_errors": problems,
           "limits": ["Absence can be Node, path, Broker, collector, or file-write failure.",
                      "No automatic access to Docker/journal; correlate preserved logs.",
                      "Network boot and SEN66 boot_id are different identifiers.",
                      "A retained receipt is not a new state transition; QoS 0 has no delivery ACK."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--node")
    parser.add_argument("--logical-id")
    parser.add_argument("--since", type=timestamp)
    parser.add_argument("--until", type=timestamp)
    args = parser.parse_args()
    try:
        for event in analyze(args.files, args.node, args.logical_id, args.since, args.until):
            print(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
