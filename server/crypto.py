"""
Server-side crypto: encrypt + sign providers.json for E2E security.

- AES-256-GCM encryption (confidentiality)
- Ed25519 signing (integrity & authenticity)

Key files stored in server/keys/
"""

from __future__ import annotations

import base64
import json
import os
import pathlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature

KEYS_DIR = pathlib.Path(__file__).parent / "keys"
AES_KEY_FILE = KEYS_DIR / "aes.key"
ED_PRIVATE_KEY_FILE = KEYS_DIR / "ed25519_private.pem"
ED_PUBLIC_KEY_FILE = KEYS_DIR / "ed25519_public.pem"

# Client-embedded public key export path
CLIENT_KEY_FILE = pathlib.Path(__file__).parent.parent / "app" / "keys" / "public.json"


def generate_keys() -> None:
    """Generate AES key + Ed25519 key pair. Call once during setup."""
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

    # AES-256 key
    aes_key = AESGCM.generate_key(bit_length=256)
    AES_KEY_FILE.write_bytes(aes_key)

    # Ed25519 key pair
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    ED_PRIVATE_KEY_FILE.write_bytes(private_pem)

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    ED_PUBLIC_KEY_FILE.write_bytes(public_pem)

    # Export for client embedding
    _export_client_key(aes_key, public_key)

    print(f"Keys generated in {KEYS_DIR}")
    print(f"Client public config exported to {CLIENT_KEY_FILE}")


def _export_client_key(aes_key: bytes, public_key: object) -> None:
    """Export AES key + Ed25519 public key for client embedding."""
    public_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    CLIENT_KEY_FILE.write_text(json.dumps({
        "aes_key": base64.b64encode(aes_key).decode(),
        "ed_public_key": base64.b64encode(public_bytes).decode(),
    }, indent=2), encoding="utf-8")


def encrypt_and_sign(plaintext: bytes) -> dict:
    """Encrypt plaintext with AES-GCM, then sign the ciphertext with Ed25519.

    Returns dict with base64-encoded fields:
    {
        "ciphertext": "...",
        "nonce": "...",
        "signature": "..."
    }
    """
    # Load AES key
    aes_key = AES_KEY_FILE.read_bytes()
    aesgcm = AESGCM(aes_key)

    # Encrypt
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Load Ed25519 private key
    private_pem = ED_PRIVATE_KEY_FILE.read_bytes()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    private_key = load_pem_private_key(private_pem, password=None)

    # Sign ciphertext + nonce
    message = ciphertext + nonce
    signature = private_key.sign(message)

    return {
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "signature": base64.b64encode(signature).decode(),
    }


def verify_and_decrypt(payload: dict) -> bytes | None:
    """Verify signature and decrypt. Returns plaintext or None on failure."""
    try:
        ciphertext = base64.b64decode(payload["ciphertext"])
        nonce = base64.b64decode(payload["nonce"])
        signature = base64.b64decode(payload["signature"])

        # Verify
        public_pem = ED_PUBLIC_KEY_FILE.read_bytes()
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        public_key = load_pem_public_key(public_pem)
        public_key.verify(signature, ciphertext + nonce)

        # Decrypt
        aes_key = AES_KEY_FILE.read_bytes()
        aesgcm = AESGCM(aes_key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except (InvalidSignature, Exception):
        return None


if __name__ == "__main__":
    generate_keys()
