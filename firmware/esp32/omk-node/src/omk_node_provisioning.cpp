#include "omk_node_provisioning.h"

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

#include <string>

namespace OmkNodeProvisioning {
namespace {

uint64_t derivedNodeId(uint64_t efuse_mac);

std::string serviceData(uint16_t capabilities) {
    // v1 payload: protocol (1), provisioning state (1), capability bitset (2),
    // six-byte factory eFuse-derived node ID. It contains no credential or
    // site-identifying data.
    const uint64_t node_id = derivedNodeId(ESP.getEfuseMac());
    std::string data;
    data.reserve(10);
    data.push_back(static_cast<char>(kProtocolVersion));
    data.push_back(static_cast<char>(kStateUnregistered));
    data.push_back(static_cast<char>(capabilities >> 8));
    data.push_back(static_cast<char>(capabilities & 0xff));
    for (int shift = 40; shift >= 0; shift -= 8) {
        data.push_back(static_cast<char>((node_id >> shift) & 0xff));
    }
    return data;
}

uint64_t derivedNodeId(uint64_t efuse_mac) {
    // FNV-1a is used only to avoid exposing the factory MAC as the visible
    // Node ID. It is not an authentication or secrecy mechanism.
    uint64_t hash = 14695981039346656037ULL;
    for (int shift = 40; shift >= 0; shift -= 8) {
        hash ^= (efuse_mac >> shift) & 0xff;
        hash *= 1099511628211ULL;
    }
    return hash & 0x0000ffffffffffffULL;
}

}  // namespace

void begin(uint16_t capabilities) {
    BLEDevice::init("OMK Node");
    BLEServer* server = BLEDevice::createServer();
    (void)server;
    BLEAdvertising* advertising = BLEDevice::getAdvertising();
    BLEAdvertisementData advertisement_data;
    advertisement_data.setFlags(0x06);
    advertisement_data.setServiceData(BLEUUID(kServiceUuid), serviceData(capabilities));
    advertising->setAdvertisementData(advertisement_data);
    advertising->start();
    Serial.printf("[INFO] OMK Node discovery advertising started (capabilities=0x%04x)\n", capabilities);
}

}  // namespace OmkNodeProvisioning
