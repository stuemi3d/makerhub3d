"""Login: password hashing, setup code, sessions, lockout and form tokens (CSRF).

Agreed in issue #8: 5 failed logins in a row lock an account for 15 minutes, a login ends
after 8 hours without activity, passwords have at least 12 characters.
"""

import hashlib
import secrets
import threading
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import cache
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import LoginSession, User, UserRole

MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)
SESSION_IDLE_TIMEOUT = timedelta(hours=8)
MIN_PASSWORD_LENGTH = 12
# Upper limits keep oversized input from slowing down the server.
MAX_PASSWORD_LENGTH = 1024
MAX_USERNAME_LENGTH = 50
# Writing "last seen" on every request is not needed; once a minute is precise enough.
TOUCH_INTERVAL = timedelta(minutes=1)

SETUP_FIELDS = ("setup_code", "username", "password", "password_repeat")


def make_password_hasher() -> PasswordHasher:
    # The library defaults follow RFC 9106 (Argon2id); they are updated with the library.
    return PasswordHasher()


PASSWORD_HASHER = make_password_hasher()


def utcnow() -> datetime:
    """Current time in UTC without time zone, as stored in the database."""
    return datetime.now(UTC).replace(tzinfo=None)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@cache
def _dummy_hash() -> str:
    return PASSWORD_HASHER.hash(secrets.token_urlsafe(16))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(expected: str | None, given: str | None) -> bool:
    if not expected or not given:
        return False
    return secrets.compare_digest(expected.encode(), given.encode())


def cookie_names(secure: bool) -> tuple[str, str]:
    """Names of the session cookie and the form token cookie used before login.

    With HTTPS the "__Host-" prefix makes browsers refuse cookies set by other subdomains.
    """
    prefix = "__Host-" if secure else ""
    return f"{prefix}makerhub_session", f"{prefix}makerhub_csrf"


def safe_next(value: str | None) -> str:
    """Only paths of this app are allowed as target after login, never foreign addresses."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    return value


# --- Setup of the first admin ----------------------------------------------------------


def new_setup_code() -> str:
    raw = secrets.token_hex(8)
    return "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def _normalize_code(code: str) -> str:
    return "".join(code.split()).replace("-", "").lower()


def setup_code_matches(expected: str | None, given: str) -> bool:
    if not expected:
        return False
    return tokens_match(_normalize_code(expected), _normalize_code(given))


def validate_setup(raw: dict[str, str], expected_code: str | None) -> dict[str, str]:
    """Returns the errors (field name -> translation key); empty when everything is valid."""
    errors = {}
    if not setup_code_matches(expected_code, raw.get("setup_code", "")):
        errors["setup_code"] = "setup.error.code"
    if error := username_error(raw.get("username", "")):
        errors["username"] = error
    errors.update(password_errors(raw))
    return errors


# --- Users -----------------------------------------------------------------------------


def name_key(username: str) -> str:
    """Names are unique regardless of case: "Anna" and "anna" are the same user.

    NFKC first, so that the same letter typed in different ways (e.g. "ö" as one character
    or as "o" plus dots) counts as the same name.
    """
    return unicodedata.normalize("NFKC", username.strip()).casefold()


def username_error(username: str) -> str | None:
    username = username.strip()
    if not username:
        return "user.error.required"
    if len(username) > MAX_USERNAME_LENGTH:
        return "user.error.username_too_long"
    return None


def password_errors(raw: dict[str, str]) -> dict[str, str]:
    """Checks the fields password and password_repeat of a form."""
    # The values are translation keys, not passwords (hence the S105 exceptions).
    password = raw.get("password", "")
    if len(password) < MIN_PASSWORD_LENGTH:
        return {"password": "user.error.password_too_short"}
    if len(password) > MAX_PASSWORD_LENGTH:
        return {"password": "user.error.password_too_long"}
    if raw.get("password_repeat", "") != password:
        return {"password_repeat": "user.error.password_mismatch"}
    return {}


def has_users(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is not None


def name_taken(db: Session, username: str) -> bool:
    return db.scalar(select(User.id).where(User.username_key == name_key(username))) is not None


def create_user(
    db: Session, username: str, password: str, role: UserRole, *, must_change_password: bool
) -> User:
    user = User(
        username=username.strip(),
        username_key=name_key(username),
        password_hash=hash_password(password),
        role=role,
        failed_logins=0,
        locked_until=None,
        created_at=utcnow(),
        active=True,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.flush()
    return user


def reset_password(db: Session, user: User, password: str) -> None:
    """New password set by the admin: lifts a lock and ends all logins of this user.

    The user has to change it at the next login (issue #32).
    """
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.failed_logins = 0
    user.locked_until = None
    end_all_sessions(db, user.id)


def minutes_locked(user: User) -> int | None:
    """Remaining minutes of a lock after failed logins (rounded up), or None."""
    if user.locked_until is None:
        return None
    remaining = user.locked_until - utcnow()
    if remaining <= timedelta(0):
        return None
    return -(-remaining // timedelta(minutes=1))


# --- Login and lockout -----------------------------------------------------------------


def check_login(db: Session, username: str, password: str) -> User | None:
    """Returns the user for a correct login, otherwise None. Counts failures and locks."""
    now = utcnow()
    user = db.scalar(select(User).where(User.username_key == name_key(username)))
    if user is None or not user.active or len(password) > MAX_PASSWORD_LENGTH:
        # Same effort as for a real user, so the answer time does not reveal names
        # or deactivated users.
        verify_password(_dummy_hash(), password[:MAX_PASSWORD_LENGTH])
        return None
    if user.locked_until is not None:
        if user.locked_until > now:
            # Same effort as a real check, so the answer time does not reveal locked accounts.
            verify_password(_dummy_hash(), password)
            return None
        user.locked_until = None
        user.failed_logins = 0
    if not verify_password(user.password_hash, password):
        _count_failure(db, user)
        return None
    user.failed_logins = 0
    if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


# Login attempts run one after another, so that attempts sent at the same moment cannot
# overwrite each other's failure count and get around the lock. The app runs as a single
# process (one uvicorn worker), so a process-wide lock is enough.
_login_lock = threading.Lock()


def log_in(new_session: Callable[[], Session], username: str, password: str) -> str | None:
    """Checks a login and starts a session; returns its cookie token or None."""
    with _login_lock, new_session() as db, db.begin():
        user = check_login(db, username, password)
        return None if user is None else start_session(db, user)


def _count_failure(db: Session, user: User) -> bool:
    """Counts a wrong password; returns True when this failure locks the account."""
    user.failed_logins += 1
    if user.failed_logins < MAX_FAILED_LOGINS:
        return False
    user.locked_until = utcnow() + LOCK_DURATION
    user.failed_logins = 0
    return True


def change_own_password(
    new_session: Callable[[], Session], user_id: int, session_token: str, raw: dict[str, str]
) -> tuple[dict[str, str], bool]:
    """Changes the password of a logged-in user (issue #32).

    Returns the errors (empty when changed) and whether the user was locked and logged out
    because the current password was wrong too often. Other logins of the user end.
    """
    current = raw.get("current_password", "")
    with _login_lock, new_session() as db, db.begin():
        user = db.get(User, user_id)
        if user is None:
            return {}, True
        if len(current) > MAX_PASSWORD_LENGTH or not verify_password(user.password_hash, current):
            # Counts like a failed login, so nobody can try passwords on an open session.
            if _count_failure(db, user):
                end_all_sessions(db, user.id)
                return {"current_password": "account.error.current_wrong"}, True
            return {"current_password": "account.error.current_wrong"}, False
        user.failed_logins = 0
        errors = password_errors(raw)
        if not errors and raw["password"] == current:
            errors = {"password": "account.error.same"}
        if errors:
            return errors, False
        user.password_hash = hash_password(raw["password"])
        user.must_change_password = False
        db.execute(
            delete(LoginSession).where(
                LoginSession.user_id == user.id,
                LoginSession.id != token_fingerprint(session_token),
            )
        )
        return {}, False


# --- Sessions --------------------------------------------------------------------------


def start_session(db: Session, user: User) -> str:
    """Creates a new login and returns the token for the cookie."""
    token = new_token()
    now = utcnow()
    # Logins that ran out without logging off are cleaned up here.
    db.execute(delete(LoginSession).where(LoginSession.last_seen_at < now - SESSION_IDLE_TIMEOUT))
    db.add(
        LoginSession(
            id=token_fingerprint(token),
            user_id=user.id,
            csrf_token=new_token(),
            created_at=now,
            last_seen_at=now,
        )
    )
    return token


def find_session(db: Session, token: str | None) -> tuple[LoginSession, User] | None:
    """Returns the valid login for a cookie token; expired logins are removed."""
    if not token:
        return None
    login = db.get(LoginSession, token_fingerprint(token))
    if login is None:
        return None
    now = utcnow()
    if now - login.last_seen_at > SESSION_IDLE_TIMEOUT:
        db.delete(login)
        return None
    if now - login.last_seen_at >= TOUCH_INTERVAL:
        login.last_seen_at = now
    user = db.get(User, login.user_id)
    if user is None or not user.active:
        db.delete(login)
        return None
    return login, user


def end_all_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(LoginSession).where(LoginSession.id == token_fingerprint(token)))
