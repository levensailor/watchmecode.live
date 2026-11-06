"""Encryption for stored integration credentials and hashing for API keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "wmc_"
API_KEY_DISPLAY_CHARS = 12


class DecryptionError(RuntimeError):
    pass


class Vault:
    def __init__(self, settings: Settings) -> None:
        key = settings.integration_encryption_key
        if not key:
            logger.warning("INTEGRATION_ENCRYPTION_KEY is not set; deriving one from SESSION_SECRET")
            key = base64.urlsafe_b64encode(hashlib.sha256(settings.session_secret.encode()).digest()).decode()
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError("Stored credential could not be decrypted") from exc


def new_api_key(workspace: str | None = None) -> tuple[str, str, str]:
    """Return (plaintext, display prefix, sha256 hash). Only the hash is stored.

    Keys for a multi-tenant workspace look like `wmc_<slug>.<token>` so the apex can route them.
    """
    scope = f"{workspace}." if workspace else ""
    plaintext = API_KEY_PREFIX + scope + secrets.token_urlsafe(32)
    return plaintext, plaintext[: API_KEY_DISPLAY_CHARS + len(scope)], hash_api_key(plaintext)


def key_workspace(plaintext: str) -> str | None:
    if not plaintext.startswith(API_KEY_PREFIX):
        return None
    scope, dot, _token = plaintext[len(API_KEY_PREFIX) :].partition(".")
    return scope if dot and scope else None


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def new_webhook_secret() -> str:
    return secrets.token_hex(32)


def verify_signature(secret: str, raw_body: bytes, header_value: str | None) -> bool:
    """Check an `sha256=<hex>` HMAC header (Ashby format) in constant time."""
    if not header_value or "=" not in header_value:
        return False
    algorithm, _, received = header_value.partition("=")
    if algorithm.strip().lower() != "sha256":
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received.strip())


def sign(secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
