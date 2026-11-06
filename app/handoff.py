"""Ed25519-signed sign-in handoff from the apex auth hub to a workspace.

The hub (watchmecode.live) holds the private key and signs a short-lived token naming the user,
the workspace, and the exact origin it is for. Workspaces, including dedicated instances that only
know the public key, verify it and start their own host-only session.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.config import Settings

TOKEN_VERSION = 1
_used_nonces: dict[str, float] = {}
_nonce_lock = threading.Lock()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _load_raw_or_pem(value: str) -> bytes | Any:
    value = value.strip()
    if value.startswith("-----BEGIN"):
        return value.encode()
    return _b64d(value.replace("+", "-").replace("/", "_"))


def private_key(settings: Settings) -> Ed25519PrivateKey:
    """HANDOFF_PRIVATE_KEY (base64 32-byte seed or PEM), else derived from SESSION_SECRET."""
    if settings.handoff_private_key:
        loaded = _load_raw_or_pem(settings.handoff_private_key)
        if isinstance(loaded, bytes) and loaded.startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(loaded, password=None)
            assert isinstance(key, Ed25519PrivateKey)
            return key
        return Ed25519PrivateKey.from_private_bytes(loaded)
    seed = hashlib.sha256(f"{settings.session_secret}|handoff-ed25519".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key(settings: Settings) -> Ed25519PublicKey:
    if settings.handoff_public_key:
        loaded = _load_raw_or_pem(settings.handoff_public_key)
        if isinstance(loaded, bytes) and loaded.startswith(b"-----BEGIN"):
            key = serialization.load_pem_public_key(loaded)
            assert isinstance(key, Ed25519PublicKey)
            return key
        return Ed25519PublicKey.from_public_bytes(loaded)
    return private_key(settings).public_key()


def public_key_b64(settings: Settings) -> str:
    raw = public_key(settings).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def issue(settings: Settings, user: dict[str, Any], tenant: str, audience: str, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {
        "v": TOKEN_VERSION,
        "sub": user["email"],
        "name": user.get("name") or user["email"],
        "picture": user.get("picture"),
        "tenant": tenant,
        "aud": audience.rstrip("/"),
        "iat": issued,
        "exp": issued + settings.handoff_max_age,
        "nonce": secrets.token_urlsafe(12),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = private_key(settings).sign(body.encode())
    return f"{body}.{_b64e(signature)}"


def _consume_nonce(nonce: str, expires: float) -> bool:
    now = time.time()
    with _nonce_lock:
        for key in [k for k, exp in _used_nonces.items() if exp < now]:
            del _used_nonces[key]
        if nonce in _used_nonces:
            return False
        _used_nonces[nonce] = expires
    return True


def verify(settings: Settings, token: str, tenant: str, audience: str, now: float | None = None) -> dict[str, Any] | None:
    """The user dict when the token is valid for this workspace and origin, used once, and unexpired."""
    try:
        body, signature = token.split(".", 1)
        public_key(settings).verify(_b64d(signature), body.encode())
        payload = json.loads(_b64d(body))
    except (ValueError, InvalidSignature, json.JSONDecodeError):
        return None
    current = now if now is not None else time.time()
    if (
        payload.get("v") != TOKEN_VERSION
        or payload.get("tenant") != tenant
        or payload.get("aud") != audience.rstrip("/")
        or not isinstance(payload.get("exp"), int)
        or payload["exp"] < current
        or payload.get("iat", 0) > current + 30
        or not _consume_nonce(str(payload.get("nonce")), payload["exp"])
    ):
        return None
    return {"name": payload.get("name"), "email": payload["sub"], "picture": payload.get("picture")}
