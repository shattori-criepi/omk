import hashlib


def derive(domain: bytes, ssid: bytes, psk: bytes) -> bytes:
    return hashlib.sha256(domain + ssid + b"\0" + psk).digest()


def test_mesh_credential_derivation_vector():
    ssid = b"OMK-Test-SSID"
    psk = b"correct-horse-battery-staple"
    assert derive(b"OMK-MESH-ID-v2\0", ssid, psk)[:6].hex() == "ed67d4a3fe8e"
    assert derive(b"OMK-MESH-PSK-v2\0", ssid, psk)[:16].hex() == "0b0af930113b29727544c2cc4914ea84"


def test_native_network_cannot_elect_a_legacy_ip_root():
    ssid, psk = b"OMK-Test-SSID", b"correct-horse-battery-staple"
    assert derive(b"OMK-MESH-ID-v1\0", ssid, psk)[:6] != derive(b"OMK-MESH-ID-v2\0", ssid, psk)[:6]
