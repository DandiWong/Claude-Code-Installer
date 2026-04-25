"""
Client-side crypto: verify Ed25519 signature + decrypt AES-256-GCM.

Uses only the `cryptography` library (no other deps).
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.exceptions import InvalidSignature


def _load_keys() -> tuple[bytes, Ed25519PublicKey]:
    """Load embedded AES key + Ed25519 public key from app/keys/public.json."""
    if getattr(os, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key_path = os.path.join(base, "app", "keys", "public.json")
    with open(key_path, encoding="utf-8") as f:
        data = json.load(f)
    aes_key = base64.b64decode(data["aes_key"])
    pub_bytes = base64.b64decode(data["ed_public_key"])
    public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
    return aes_key, public_key


import sys  # noqa: E402 (must be after function def to avoid circular)

# Cache keys after first load
_cached_keys: tuple[bytes, Ed25519PublicKey] | None = None


def verify_and_decrypt(payload: dict) -> bytes | None:
    """Verify Ed25519 signature and AES-GCM decrypt. Returns plaintext or None."""
    global _cached_keys
    try:
        if _cached_keys is None:
            _cached_keys = _load_keys()
        aes_key, public_key = _cached_keys

        ciphertext = base64.b64decode(payload["ciphertext"])
        nonce = base64.b64decode(payload["nonce"])
        signature = base64.b64decode(payload["signature"])

        # Verify signature over ciphertext + nonce
        public_key.verify(signature, ciphertext + nonce)

        # Decrypt
        aesgcm = AESGCM(aes_key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except (InvalidSignature, KeyError, Exception):
        return None
