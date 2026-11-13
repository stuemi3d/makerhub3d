"""Two-factor login with codes from an authenticator app (TOTP, RFC 6238) - issue #30.

Agreed in issues #8 and #30: one 30-second step before or after is accepted, every code works
only once, 10 one-time recovery codes, secrets are stored encrypted with the key from
MAKERHUB_2FA_KEY. This module holds the building blocks; the login and account flows are in
app/auth.py.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app import APP_NAME

STEP_SECONDS = 30
# One step before and after: the clocks of server and phone may differ a little.
ALLOWED_STEPS = (-1, 0, 1)
CODE_DIGITS = 6
RECOVERY_CODE_COUNT = 10
# Time between the right password and the code; afterwards the login starts again.
PENDING_LOGIN_TIMEOUT = timedelta(minutes=5)
# Longer input is never a valid code; the limit keeps oversized input cheap.
MAX_CODE_LENGTH = 100


class Cipher:
    """Encrypts the two-factor secrets with a key derived from MAKERHUB_2FA_KEY."""

    def __init__(self, key: str) -> None:
        # Any long random text is allowed as key; HKDF turns it into a Fernet key.
        derived = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"makerhub3d two-factor"
        ).derive(key.encode())
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        """The plain value, or None if the token was made with another key or is broken."""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None


def new_secret() -> str:
    # 32 base32 characters = 160 bits, as recommended in RFC 4226.
    return pyotp.random_base32()


def time_step(now: datetime) -> int:
    """The 30-second step of a naive UTC time (as returned by auth.utcnow)."""
    return int(now.replace(tzinfo=UTC).timestamp()) // STEP_SECONDS


def code_for_step(secret: str, step: int) -> str:
    return pyotp.TOTP(secret, digits=CODE_DIGITS, interval=STEP_SECONDS).generate_otp(step)


def _compact(code: str) -> str:
    return "".join(code.split()).replace("-", "").lower()


def is_app_code(code: str) -> bool:
    compact = _compact(code)
    return len(compact) == CODE_DIGITS and compact.isascii() and compact.isdigit()


def matching_step(secret: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """The step of a valid app code, or None.

    Steps up to the last used one are refused, so a code (or an older one) works only once.
    """
    if not is_app_code(code):
        return None
    compact = _compact(code)
    current = time_step(now)
    found = None
    for offset in ALLOWED_STEPS:
        step = current + offset
        if last_step is not None and step <= last_step:
            continue
        # Compare all candidates, so the answer time does not reveal which step matched.
        if hmac.compare_digest(code_for_step(secret, step), compact):
            found = step
    return found


def new_recovery_codes() -> list[str]:
    """10 codes of 16 hex characters (64 bits) each, shown like a1b2-c3d4-e5f6-a7b8."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(8)
        codes.append("-".join(raw[index : index + 4] for index in range(0, len(raw), 4)))
    return codes


def recovery_code_hash(code: str) -> str:
    """Fingerprint of a recovery code; case, spaces and dashes do not matter.

    SHA-256 is enough here: the codes are random with 64 bits, so they cannot be guessed
    from the fingerprint (unlike passwords chosen by people).
    """
    return hashlib.sha256(_compact(code).encode()).hexdigest()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret, digits=CODE_DIGITS, interval=STEP_SECONDS).provisioning_uri(
        name=username, issuer_name=APP_NAME
    )


def qr_code_data_uri(uri: str) -> str:
    """The QR code as an SVG image inside a data URI, made locally without any web service."""
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=4, dark="#000", light="#fff")


def grouped_secret(secret: str) -> str:
    """The secret in groups of four characters, easier to type into the app by hand."""
    return " ".join(secret[index : index + 4] for index in range(0, len(secret), 4))
