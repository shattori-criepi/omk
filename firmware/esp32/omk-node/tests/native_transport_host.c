#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "mesh_session.h"
#include "mesh_wire.h"

static const char topic[] = "omk/node/112233aabbcc/registration/config";
static const char config[] = "{\"protocol_version\":2,\"revision\":7,\"logical_id\":null}";

static void wire(void) {
    mesh_wire_message_t source = {.kind = MESH_MESSAGE_NODE_STATUS};
    strcpy(source.node_id, "112233aabbcc");
    source.length = MESH_TRANSPORT_PAYLOAD_MAX;
    memset(source.payload, 'x', source.length);
    uint8_t buffer[MESH_WIRE_MAX_SIZE + 1];
    mesh_wire_message_t decoded;
    size_t written;
    for (int kind = MESH_MESSAGE_SEN66; kind <= MESH_MESSAGE_CONFIG; ++kind) {
        source.kind = kind;
        assert(mesh_wire_encode(&source, buffer, sizeof(buffer), &written));
        assert(written == MESH_WIRE_MAX_SIZE);
        assert(buffer[6] == 4 && buffer[7] == 0);
        assert(mesh_wire_decode(buffer, written, &decoded));
        assert(decoded.kind == source.kind && decoded.length == source.length);
        assert(!strcmp(decoded.node_id, source.node_id));
        assert(!memcmp(decoded.payload, source.payload, source.length));
        assert(decoded.payload[source.length] == '\0');
    }
    assert(!mesh_wire_encode(&source, buffer, MESH_WIRE_MAX_SIZE - 1, &written));
    source.length = 0;
    assert(!mesh_wire_encode(&source, buffer, sizeof(buffer), &written));
    source.length = 2;
    memcpy(source.payload, "{}", 2);
    assert(mesh_wire_encode(&source, buffer, sizeof(buffer), &written));
    assert(!mesh_wire_decode(buffer, written - 1, &decoded));
    assert(!mesh_wire_decode(buffer, written + 1, &decoded));
    buffer[4] = 2;
    assert(!mesh_wire_decode(buffer, written, &decoded));
    buffer[4] = 1;
    buffer[5] = 255;
    assert(!mesh_wire_decode(buffer, written, &decoded));
    buffer[5] = MESH_MESSAGE_SEN66;
    buffer[8] = '/';
    assert(!mesh_wire_decode(buffer, written, &decoded));
    buffer[8] = '1';
    buffer[MESH_WIRE_HEADER_SIZE] = 0;
    assert(!mesh_wire_decode(buffer, written, &decoded));
    assert(!mesh_wire_decode(NULL, 0, &decoded));
}

static void topics(void) {
    char id[13];
    assert(mesh_wire_config_topic(topic, strlen(topic), id));
    assert(!strcmp(id, "112233aabbcc"));
    assert(!mesh_wire_config_topic(topic, strlen(topic) - 1, id));
    const char *bad[] = {
        "omk/node/+/registration/config", "omk/node/112233AABBCC/registration/config",
        "omk/node/112233aabbcc/registration/config/", "omk/node/112233aabbcc/status",
        "omk/node/112233/aabbc/registration/config", "omk/node/112233aabbcc/registration/config#",
    };
    for (size_t i = 0; i < sizeof(bad)/sizeof(bad[0]); ++i)
        assert(!mesh_wire_config_topic(bad[i], strlen(bad[i]), id));
    assert(mesh_wire_valid_logical_id("sen66-001_ABC"));
    assert(!mesh_wire_valid_logical_id("../../node/status"));
    assert(!mesh_wire_valid_logical_id("sensor+"));
    assert(!mesh_wire_valid_logical_id(""));
    char long_id[50]; memset(long_id, 'a', 49); long_id[49] = 0;
    assert(!mesh_wire_valid_logical_id(long_id));
    long_id[48] = 0;
    assert(mesh_wire_valid_logical_id(long_id));
}

static void fragments(void) {
    mesh_config_assembler_t a = {0};
    const size_t n = strlen(config);
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), config, 7, n, 0) == 0);
    assert(mesh_wire_config_fragment(&a, NULL, 0, config + 7, n - 7, n, 7) == 1);
    assert(!strcmp(a.message.payload, config));
    assert(!strcmp(a.message.node_id, "112233aabbcc"));
    assert(a.message.kind == MESH_MESSAGE_CONFIG);
    /* Remainders cannot turn a prior complete or rejected message into config. */
    assert(mesh_wire_config_fragment(&a, NULL, 0, config + 7, n - 7, n, 7) == -1);
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), config, 7, n, 0) == 0);
    assert(mesh_wire_config_fragment(&a, NULL, 0, config + 8, n - 8, n, 8) == -1);
    assert(mesh_wire_config_fragment(&a, NULL, 0, config + 7, n - 7, n, 7) == -1);
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), config, 7, n, 0) == 0);
    assert(mesh_wire_config_fragment(&a, NULL, 0, config + 7, n - 7, n + 1, 7) == -1);
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), config, 7, 1025, 0) == -1);
    assert(mesh_wire_config_fragment(&a, NULL, 0, config, n, n, 0) == -1);
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), config, 7, n, 0) == 0);
    const char other[] = "omk/node/112233aabbcd/registration/config";
    assert(mesh_wire_config_fragment(&a, other, strlen(other), config + 7, n - 7, n, 7) == -1);
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), "x\0y", 3, 3, 0) == -1);
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), config, 7, n, 0) == 0);
    /* New first fragment resets an unfinished prior payload. */
    assert(mesh_wire_config_fragment(&a, topic, strlen(topic), "{}", 2, 2, 0) == 1);
    assert(!strcmp(a.message.payload, "{}"));
}

static mesh_session_t connected_root(void) {
    mesh_session_t s = {0};
    mesh_session_started(&s);
    assert(mesh_session_set_role(&s, true));
    assert(!mesh_session_got_ip(&s, s.epoch));
    mesh_session_parent_connected(&s, true);
    assert(mesh_session_got_ip(&s, s.epoch));
    assert(mesh_session_mqtt_event(&s, s.epoch, true));
    assert(mesh_session_root_ready(&s));
    return s;
}

static void demotion(void) {
    mesh_session_t s = connected_root();
    uint32_t old = s.epoch;
    assert(mesh_session_accept_record(&s, old, 100, 200));
    assert(mesh_session_set_role(&s, false));
    assert(!s.has_ip && !s.mqtt_connected && !s.parent_connected);
    assert(!mesh_session_accept_ingress(&s));
    assert(!mesh_session_got_ip(&s, old));
    assert(!mesh_session_mqtt_event(&s, old, true));
    assert(!mesh_session_accept_record(&s, old, 100, 201));
    mesh_session_parent_connected(&s, false);
    assert(!mesh_session_got_ip(&s, s.epoch));
    assert(mesh_session_set_role(&s, true));
    mesh_session_parent_connected(&s, true);
    assert(mesh_session_got_ip(&s, s.epoch));
    assert(!mesh_session_mqtt_event(&s, old, true));
    assert(!s.mqtt_connected);
    assert(mesh_session_mqtt_event(&s, s.epoch, true));
    /* Even a callback racing after a new Root session cannot clear it. */
    assert(!mesh_session_mqtt_event(&s, old, false));
    assert(s.mqtt_connected);
    assert(!mesh_session_accept_record(&s, old, 100, 202));
}

static void disconnect(void) {
    mesh_session_t s = connected_root();
    uint32_t old = s.epoch;
    mesh_session_parent_disconnected(&s);
    assert(s.is_root && !s.has_ip && !s.mqtt_connected);
    assert(!mesh_session_got_ip(&s, old));
    assert(!mesh_session_got_ip(&s, s.epoch));
    mesh_session_parent_connected(&s, true);
    assert(!mesh_session_got_ip(&s, old));
    assert(!mesh_session_mqtt_event(&s, old, true));
    assert(mesh_session_got_ip(&s, s.epoch));
    assert(!mesh_session_accept_record(&s, old, 100, 200));
    assert(mesh_session_accept_record(&s, s.epoch, 100, 200));
    mesh_session_stopped(&s);
    assert(!mesh_session_accept_ingress(&s));
    assert(!mesh_session_root_ready(&s));
}

static void backpressure(void) {
    mesh_session_t s = connected_root();
    uint32_t epoch = s.epoch;
    assert(mesh_session_mqtt_event(&s, epoch, false));
    /* These production admission functions do not wait for MQTT or require
     * its readiness. RX can continue discarding/handing off bounded records. */
    assert(mesh_session_accept_ingress(&s));
    assert(mesh_session_accept_record(&s, epoch, 1, 100));
    mesh_session_parent_disconnected(&s);
    assert(!s.has_ip);
    assert(mesh_session_accept_ingress(&s));
    assert(!mesh_session_accept_record(&s, epoch, 1, 100));
    assert(mesh_session_accept_record(&s, s.epoch, 100, 101));
}

static void rootless(void) {
    mesh_session_t s = connected_root();
    uint32_t old = s.epoch;
    mesh_session_set_rootless(&s, true);
    assert(s.has_ip && !s.mqtt_connected);
    assert(!mesh_session_root_ready(&s));
    assert(!mesh_session_accept_record(&s, old, 1, 100));
    assert(!mesh_session_mqtt_event(&s, old, true));
    uint32_t current = s.epoch;
    mesh_session_set_rootless(&s, true);
    assert(s.epoch == current);
    mesh_session_set_rootless(&s, false);
    assert(mesh_session_root_ready(&s));
    assert(!mesh_session_mqtt_event(&s, old, true));
    assert(mesh_session_mqtt_event(&s, current, true));
}

static void expiry(void) {
    mesh_session_t s = connected_root();
    assert(mesh_session_accept_record(&s, s.epoch, 1000, 30999));
    assert(!mesh_session_accept_record(&s, s.epoch, 1000, 31000));
    assert(!mesh_wire_is_fresh(1000, 999));
    assert(mesh_wire_is_fresh(UINT64_C(5000000000), UINT64_C(5000029999)));
    assert(!mesh_wire_is_fresh(UINT64_C(5000000000), UINT64_C(5000030000)));
}

int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "wire")) wire();
    else if (!strcmp(argv[1], "topics")) topics();
    else if (!strcmp(argv[1], "fragments")) fragments();
    else if (!strcmp(argv[1], "demotion")) demotion();
    else if (!strcmp(argv[1], "disconnect")) disconnect();
    else if (!strcmp(argv[1], "backpressure")) backpressure();
    else if (!strcmp(argv[1], "rootless")) rootless();
    else if (!strcmp(argv[1], "expiry")) expiry();
    else assert(0);
    puts("ok");
    return 0;
}
