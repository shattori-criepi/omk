import json

from ichijo_energy_node.mqtt_publisher import MqttPublisher


class Result:
    rc = 0
    def wait_for_publish(self, timeout): self.timeout = timeout


class FakeClient:
    def __init__(self, **kwargs): self.kwargs, self.published = kwargs, []
    def reconnect_delay_set(self, **kwargs): self.delay = kwargs
    def max_queued_messages_set(self, value): self.queue = value
    def will_set(self, *args, **kwargs): self.will = (args, kwargs)
    def connect_async(self, *args, **kwargs): self.connect = (args, kwargs)
    def loop_start(self): self.started = True
    def publish(self, *args, **kwargs): self.published.append((args, kwargs)); return Result()
    def loop_stop(self): self.stopped = True
    def disconnect(self): self.disconnected = True


def test_topics_retention_last_will_and_payload():
    publisher = MqttPublisher(device_id="ichijo-001", host="broker", port=1883, keepalive=60, client_factory=FakeClient)
    client = publisher._client
    assert publisher.power_flow_topic == "omk/ichijo-001/power-flow"
    assert client.will[0][0] == "omk/ichijo-001/status" and client.will[1]["retain"] is True
    publisher._on_connect(client, None, None, 0, None)
    publisher.publish_power_flow({"device_id": "ichijo-001", "load_power_w": 1107})
    online, flow = client.published
    assert online[0][0] == publisher.status_topic and online[1]["retain"] is True
    assert flow[0][0] == publisher.power_flow_topic and flow[1]["retain"] is False
    assert json.loads(flow[0][1])["load_power_w"] == 1107


def test_start_uses_async_connection_and_reconnect_backoff():
    publisher = MqttPublisher(device_id="id", host="broker", port=1883, keepalive=12, client_factory=FakeClient)
    publisher.start()
    assert publisher._client.connect == (("broker", 1883), {"keepalive": 12})
    assert publisher._client.delay == {"min_delay": 1, "max_delay": 30}
