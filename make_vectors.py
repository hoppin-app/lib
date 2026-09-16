#!/usr/bin/env python3
"""
Generate docs/fixtures/crypto_vectors.json.

The fixtures are fully deterministic: every key is derived from a fixed ASCII
label via SHA-256, and every timestamp/id/nonce is a literal. Re-running this
script must produce a byte-identical file.

The Swift test target consumes the same file, so both languages are pinned to
one set of expected bytes for: canonical encoding, certificates, wire
signatures, envelope sealing, safety numbers and phone normalization.

    ./venv/bin/python server/make_vectors.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from typing import Any, Dict, List

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bootstrap as bs  # noqa: E402
from server.client import raw_private, raw_public, seal_envelope  # noqa: E402

OUT_PATH = os.path.join(_ROOT, "docs", "fixtures", "crypto_vectors.json")

# --- fixed inputs ---------------------------------------------------------- #

ALICE_PHONE = "+351912345678"
BOB_PHONE = "+14155552671"

# Safely in the past (2023-11-14) so the fixtures are already valid, and
# expiring in 2100 so they never go stale.
CERT_ISSUED_AT = 1700000000
CERT_EXPIRES_AT = 4102444800          # 2100-01-01, so fixtures never expire
ALICE_SERIAL = "11111111-1111-4111-8111-111111111111"
BOB_SERIAL = "22222222-2222-4222-8222-222222222222"

WIRE_TXID = "33333333-3333-4333-8333-333333333333"
WIRE_TIMESTAMP = 1790000500

ENVELOPE_ID = "44444444-4444-4444-8444-444444444444"
ENVELOPE_TIMESTAMP = 1790000600
ENVELOPE_NONCE = bytes(range(12))     # 000102...0b


def seed(label: str) -> bytes:
    return hashlib.sha256(label.encode("ascii")).digest()


# --- §7 safety number ------------------------------------------------------ #

SAFETY_ITERATIONS = 5200


def safety_number_half(phone: str, signing_key_b64: str) -> str:
    """
    h = Canonical(["hopchat-safety-v1", phone, signingKeyBase64])
    repeat 5200 times: h = SHA512(h || rawSigningKeyBytes)
    take the first 30 bytes, split into 6 chunks of 5 bytes,
    each chunk = big-endian uint40 mod 100000, zero-padded to 5 digits.
    """
    key_raw = base64.b64decode(signing_key_b64)
    h = bs.canonical(["hopchat-safety-v1", phone, signing_key_b64])
    for _ in range(SAFETY_ITERATIONS):
        h = hashlib.sha512(h + key_raw).digest()
    out = h[:30]
    digits = []
    for i in range(6):
        chunk = out[i * 5:(i + 1) * 5]
        digits.append("%05d" % (int.from_bytes(chunk, "big") % 100000))
    return "".join(digits)


def safety_number(phone_a: str, key_a: str, phone_b: str, key_b: str) -> str:
    halves = sorted([safety_number_half(phone_a, key_a),
                     safety_number_half(phone_b, key_b)])
    return "".join(halves)


# --- builders --------------------------------------------------------------- #

def canonical_vectors() -> List[Dict[str, Any]]:
    cases = [
        ["ab", "", "é"],
        [],
        [""],
        ["", "", ""],
        ["hopchat-wire-v2", "1", "0"],
        ["é", "€", "𝄞"],
        ["emoji 👍🏽", "family 👨‍👩‍👧‍👦"],
        ["+351912345678", "0", "1790000000"],
        ["a:b", "3:c", "10:"],
        ["x" * 300],
    ]
    out = []
    for fields in cases:
        encoded = bs.canonical(fields)
        out.append({
            "fields": fields,
            "hex": encoded.hex(),
            "byteLength": len(encoded),
        })
    return out


def phone_vectors() -> List[Dict[str, Any]]:
    cases = [
        ("+351 912 345 678", "PT"),
        ("00351912345678", "PT"),
        ("912345678", "PT"),
        ("0912345678", "PT"),
        ("(415) 555-2671", "US"),
        ("07700 900123", "GB"),
        ("+1 (415) 555-2671", "US"),
        ("12", "PT"),
        ("abc", "US"),
        ("", "PT"),
        ("+0123456789", "PT"),
        ("912345678", None),
    ]
    out = []
    for raw, region in cases:
        out.append({
            "raw": raw,
            "region": region,
            "expected": bs.normalize_phone(raw, region),
        })
    return out


def main() -> None:
    # --- server identity ---------------------------------------------------- #
    server_seed = seed("hopchat-vectors-server")
    server_key = Ed25519PrivateKey.from_private_bytes(server_seed)
    server_pub = bs.public_key_b64(server_key)

    # --- parties ------------------------------------------------------------ #
    parties = {}
    for name, phone, serial in (("alice", ALICE_PHONE, ALICE_SERIAL),
                                ("bob", BOB_PHONE, BOB_SERIAL)):
        sign_seed = seed("hopchat-vectors-%s-signing" % name)
        agree_seed = seed("hopchat-vectors-%s-agreement" % name)
        signing = Ed25519PrivateKey.from_private_bytes(sign_seed)
        agreement = X25519PrivateKey.from_private_bytes(agree_seed)
        parties[name] = {
            "phone": phone,
            "serial": serial,
            "signing": signing,
            "agreement": agreement,
            "signingSeedBase64": bs.b64e(sign_seed),
            "signingKeyBase64": bs.b64e(raw_public(signing)),
            "agreementPrivateBase64": bs.b64e(raw_private(agreement)),
            "agreementKeyBase64": bs.b64e(raw_public(agreement)),
        }

    # --- certificates -------------------------------------------------------- #
    certificates: Dict[str, Any] = {}
    for name, p in parties.items():
        cert = bs.make_certificate(
            server_key, p["phone"], p["signingKeyBase64"], p["agreementKeyBase64"],
            CERT_ISSUED_AT, CERT_EXPIRES_AT, p["serial"])
        p["cert"] = cert
        certificates[name] = {
            "certificate": cert,
            "json": bs.encode_certificate(cert),
            "signedFieldsHex": bs.canonical(bs.cert_signed_fields(
                p["phone"], p["signingKeyBase64"], p["agreementKeyBase64"],
                CERT_ISSUED_AT, CERT_EXPIRES_AT, p["serial"])).hex(),
            "valid": True,
        }

    tampered = dict(parties["alice"]["cert"])
    tampered["phone"] = "+351999999999"
    certificates["tampered"] = {
        "certificate": tampered,
        "json": bs.encode_certificate(tampered),
        "valid": False,
        "note": "alice's certificate with the phone field swapped; the signature "
                "must fail to verify",
    }

    # --- wire message --------------------------------------------------------- #
    alice = parties["alice"]
    wire_value = json.dumps({"wait": 25, "max": 100}, separators=(",", ":"))
    wire = {
        "v": bs.WIRE_VERSION,
        "type": bs.T_RELAY_FETCH,
        "txID": WIRE_TXID,
        "senderPhone": ALICE_PHONE,
        "senderPort": 0,
        "timestamp": WIRE_TIMESTAMP,
        "certificate": bs.encode_certificate(alice["cert"]),
        "value": wire_value,
    }
    wire_fields = bs.wire_signed_fields(wire)
    wire["signature"] = bs.b64e(alice["signing"].sign(bs.canonical(wire_fields)))

    # --- envelope -------------------------------------------------------------- #
    bob = parties["bob"]
    payload_obj = {
        "kind": "text",
        "text": "Olá Bob 👋 — é seguro?",
        "sentAt": ENVELOPE_TIMESTAMP,
    }
    payload = json.dumps(payload_obj, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    ephemeral = X25519PrivateKey.from_private_bytes(seed("hopchat-vectors-ephemeral"))

    env, aes_key = seal_envelope(
        payload=payload,
        envelope_id=ENVELOPE_ID,
        sender_phone=ALICE_PHONE,
        recipient_phone=BOB_PHONE,
        timestamp=ENVELOPE_TIMESTAMP,
        sender_signing=alice["signing"],
        sender_agreement=alice["agreement"],
        recipient_signing_b64=bob["signingKeyBase64"],
        recipient_agreement_b64=bob["agreementKeyBase64"],
        sender_certificate_json=bs.encode_certificate(alice["cert"]),
        ephemeral=ephemeral,
        nonce=ENVELOPE_NONCE,
    )

    vectors: Dict[str, Any] = {
        "_comment": (
            "Generated by server/make_vectors.py - do not edit by hand. "
            "Deterministic: every key is SHA-256 of a fixed ASCII label."
        ),
        "version": 1,
        "canonical": canonical_vectors(),
        "server": {
            "seedBase64": bs.b64e(server_seed),
            "publicKeyBase64": server_pub,
        },
        "alice": {
            "phone": ALICE_PHONE,
            "signingSeedBase64": alice["signingSeedBase64"],
            "signingKeyBase64": alice["signingKeyBase64"],
            "agreementPrivateBase64": alice["agreementPrivateBase64"],
            "agreementKeyBase64": alice["agreementKeyBase64"],
        },
        "bob": {
            "phone": BOB_PHONE,
            "signingSeedBase64": bob["signingSeedBase64"],
            "signingKeyBase64": bob["signingKeyBase64"],
            "agreementPrivateBase64": bob["agreementPrivateBase64"],
            "agreementKeyBase64": bob["agreementKeyBase64"],
        },
        "certificates": certificates,
        "wire": {
            "message": wire,
            "signedFields": wire_fields,
            "signedFieldsHex": bs.canonical(wire_fields).hex(),
            "signature": wire["signature"],
        },
        "envelope": {
            "envelope": env,
            "header": bs.envelope_header(env),
            "headerCanonicalHex": bs.canonical(bs.envelope_header(env)).hex(),
            "signedFieldsHex": bs.canonical(bs.envelope_signed_fields(env)).hex(),
            "digestHex": bs.envelope_digest_hex(env),
            "ephemeralPrivateBase64": bs.b64e(raw_private(ephemeral)),
            "nonceHex": ENVELOPE_NONCE.hex(),
            "derivedKeyHex": aes_key.hex(),
            "plaintextJSON": payload.decode("utf-8"),
            "plaintextHex": payload.hex(),
            "hkdfSalt": "HopChat-E2E-v2",
            "note": "Open with bob.agreementPrivateBase64 per DESIGN_CONTRACT §4.",
        },
        "safetyNumber": {
            "iterations": SAFETY_ITERATIONS,
            "aliceHalf": safety_number_half(ALICE_PHONE, alice["signingKeyBase64"]),
            "bobHalf": safety_number_half(BOB_PHONE, bob["signingKeyBase64"]),
            "expected": safety_number(ALICE_PHONE, alice["signingKeyBase64"],
                                      BOB_PHONE, bob["signingKeyBase64"]),
            "algorithm": (
                "h = Canonical([\"hopchat-safety-v1\", phone, signingKeyBase64]); "
                "repeat 5200: h = SHA512(h || rawSigningKeyBytes); "
                "first 30 bytes -> 6 chunks of 5 bytes -> big-endian uint40 mod "
                "100000 zero-padded to 5 digits -> 30 digits per side; "
                "sort the two 30-digit strings lexicographically and concatenate."
            ),
        },
        "phones": phone_vectors(),
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(vectors, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print("wrote %s" % OUT_PATH)
    print("server public key:", server_pub)


if __name__ == "__main__":
    main()
