"""Encryption of MT5 account passwords at rest.

An MT5 *master* password grants full trading control of the account, so it is
stored encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and only decrypted for
the duration of a single gateway call.

Key management
--------------
``TC_CREDENTIAL_ENCRYPTION_KEY`` holds the Fernet key.  In production it must be
supplied by the environment (or a secret manager) and the process refuses to
start without it.  For local development a key is generated once into
``data/credential.key`` with owner-only permissions so restarts do not orphan
saved accounts.

Rotation: encrypt with the new key and re-save each account; ciphertexts carry
no key id, so keep the old key available until every row is re-encrypted.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..config import BACKEND_DIR, Settings, settings as default_settings
from ..errors import TradeCognitionError
from ..logging_conf import get_logger

log = get_logger(__name__)

_DEV_KEY_FILENAME = "credential.key"


class CredentialCipherError(TradeCognitionError):
    status_code = 500
    code = "credential_cipher_error"


class CredentialCipher:
    """Symmetric encryption for stored broker passwords."""

    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key if isinstance(key, bytes) else key.encode())
        except Exception as exc:
            raise CredentialCipherError(
                "TC_CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet;'
                'print(Fernet.generate_key().decode())"'
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            raise CredentialCipherError("Cannot encrypt an empty credential.")
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise CredentialCipherError(
                "A stored MT5 password could not be decrypted. The encryption key has changed "
                "since it was saved - reconnect the account to store it again."
            ) from exc


def _dev_key_path(settings: Settings) -> Path:
    sqlite_path = settings.sqlite_path
    directory = sqlite_path.parent if sqlite_path else (BACKEND_DIR / "data")
    return directory / _DEV_KEY_FILENAME


def _load_or_create_dev_key(settings: Settings) -> str:
    path = _dev_key_path(settings)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    path.write_text(key, encoding="utf-8")
    try:  # best effort: owner read/write only
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - platform dependent
        pass
    log.warning(
        "TC_CREDENTIAL_ENCRYPTION_KEY is not set. Generated a development key at %s. "
        "Set the variable explicitly before running in production.",
        path,
    )
    return key


_cipher: CredentialCipher | None = None


def get_cipher(settings: Settings | None = None) -> CredentialCipher:
    """Process-wide cipher instance."""
    global _cipher
    if _cipher is not None:
        return _cipher

    cfg = settings or default_settings
    key = cfg.credential_encryption_key.strip()

    if not key or key.startswith("CHANGE_ME"):
        if cfg.is_production:
            raise CredentialCipherError(
                "TC_CREDENTIAL_ENCRYPTION_KEY must be set in production. MT5 passwords cannot be "
                "stored without an explicit encryption key."
            )
        key = _load_or_create_dev_key(cfg)

    _cipher = CredentialCipher(key)
    return _cipher


def reset_cipher() -> None:
    """Test hook."""
    global _cipher
    _cipher = None
