"""Process entry point, including a one-cycle diagnostic mode."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading

from .collector import Collector
from .config import Config
from .echonet import EchonetClient, get_interface_ipv4
from .mqtt_publisher import MqttPublisher

LOGGER = logging.getLogger(__name__)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read Ichijo ECHONET Lite power flow and publish OMK MQTT.")
    parser.add_argument("--once", action="store_true", help="collect one cycle")
    parser.add_argument("--no-mqtt", action="store_true", help="print JSON instead of publishing")
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = Config.from_env()
        local_ip = get_interface_ipv4(config.interface)
    except ValueError as error:
        raise SystemExit(f"Configuration error: {error}") from error
    except Exception as error:
        raise SystemExit(f"Network configuration error: {error}") from error
    LOGGER.info("Starting Ichijo energy node target=%s interface=%s local_ip=%s", config.target_ip, config.interface, local_ip)
    reader = EchonetClient(config.target_ip, local_ip, timeout_seconds=config.echonet_timeout_seconds, retry_count=config.echonet_retry_count)
    collector = Collector(reader, device_id=config.device_id, property_interval_seconds=config.property_interval_seconds)
    publisher = None if args.no_mqtt else MqttPublisher(device_id=config.device_id, host=config.mqtt_host, port=config.mqtt_port, keepalive=config.mqtt_keepalive_seconds)
    stopping = threading.Event()
    previous_handlers = {sig: signal.signal(sig, lambda _sig, _frame: stopping.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if publisher:
            publisher.start()
        while not stopping.is_set():
            payload = collector.collect()
            if args.no_mqtt:
                print(json.dumps(payload, ensure_ascii=False, allow_nan=False), flush=True)
            elif publisher:
                publisher.publish_power_flow(payload)
            if args.once:
                break
            stopping.wait(config.poll_interval_seconds)
    finally:
        if publisher:
            publisher.close()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        LOGGER.info("Ichijo energy node stopped")
