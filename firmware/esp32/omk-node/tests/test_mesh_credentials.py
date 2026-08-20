import hashlib


def derive(domain: bytes, ssid: bytes, psk: bytes) -> bytes:
    return hashlib.sha256(domain + ssid + b"\0" + psk).digest()


def test_mesh_credential_derivation_vector():
    ssid = b"OMK-Test-SSID"
    psk = b"correct-horse-battery-staple"
    assert derive(b"OMK-MESH-ID-v1\0", ssid, psk)[:6].hex() == "433d71b86f8d"
    assert derive(b"OMK-MESH-PSK-v1\0", ssid, psk)[:16].hex() == "fed195b85f7c0447709a67a3c06e2585"
