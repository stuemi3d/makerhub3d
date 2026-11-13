"""Login: password hashing, setup code, sessions, lockout and form tokens (CSRF).

Agreed in issue #8: 5 failed logins in a row lock an account for 15 minutes, a login ends
after 8 hours without activity, passwords have at least 12 characters.
Issue #30: users with two-factor login enter a code after the password; wrong codes count
like wrong passwords.
"""

import hashlib
import secrets
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit, two_factor
from app.models import LoginSession, PendingLogin, RecoveryCode, User, UserRole
from app.two_factor import Cipher

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
        # The typed name of an unknown user is not logged: it may be a mistyped password.
        audit.record_login(db, None if user is None else user.id, "failed")
        return None
    if user.locked_until is not None:
        if user.locked_until > now:
            # Same effort as a real check, so the answer time does not reveal locked accounts.
            verify_password(_dummy_hash(), password)
            audit.record_login(db, user.id, "failed")
            return None
        user.locked_until = None
        user.failed_logins = 0
    if not verify_password(user.password_hash, password):
        audit.record_login(db, user.id, "locked" if _count_failure(db, user) else "failed")
        return None
    if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    if user.totp_secret is not None:
        # The login is not complete yet. The failure count stays, so that the right password
        # cannot be used to get more attempts at guessing the code.
        return user
    audit.record_login(db, user.id, "success")
    user.failed_logins = 0
    return user


# Login attempts run one after another, so that attempts sent at the same moment cannot
# overwrite each other's failure count and get around the lock. The app runs as a single
# process (one uvicorn worker), so a process-wide lock is enough.
_login_lock = threading.Lock()


@dataclass(frozen=True)
class LoginStep:
    """Result of a right password: a session token, or for two-factor users the token of
    a pending login that still needs the code."""

    token: str
    needs_code: bool


def log_in(new_session: Callable[[], Session], username: str, password: str) -> LoginStep | None:
    """Checks a login; returns None for a failed login."""
    with _login_lock, new_session() as db, db.begin():
        user = check_login(db, username, password)
        if user is None:
            return None
        if user.totp_secret is not None:
            return LoginStep(start_pending_login(db, user), needs_code=True)
        return LoginStep(start_session(db, user), needs_code=False)


def _is_locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > utcnow()


def _reject(db: Session, user: User, event: str) -> bool:
    """Counts a wrong password or code of a logged-in user (my account).

    Returns True when this failure locks the account; all logins of the user then end.
    """
    if _count_failure(db, user):
        audit.record_login(db, user.id, "locked")
        end_all_sessions(db, user.id)
        return True
    audit.record_login(db, user.id, event)
    return False


def _password_ok(user: User, password: str) -> bool:
    return len(password) <= MAX_PASSWORD_LENGTH and verify_password(user.password_hash, password)


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
        if _is_locked(user):
            # Locked meanwhile (e.g. by failed logins elsewhere): no more guesses here either.
            end_all_sessions(db, user.id)
            return {}, True
        if not _password_ok(user, current):
            # Counts like a failed login, so nobody can try passwords on an open session.
            locked = _reject(db, user, "wrong_current_password")
            return {"current_password": "account.error.current_wrong"}, locked
        user.failed_logins = 0
        errors = password_errors(raw)
        if not errors and raw["password"] == current:
            errors = {"password": "account.error.same"}
        if errors:
            return errors, False
        user.password_hash = hash_password(raw["password"])
        user.must_change_password = False
        audit.record_change(db, user.id, "account.password_changed", user.username)
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
    db.execute(delete(PendingLogin).where(PendingLogin.user_id == user_id))


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(LoginSession).where(LoginSession.id == token_fingerprint(token)))


# --- Two-factor login (issue #30) ------------------------------------------------------


def start_pending_login(db: Session, user: User) -> str:
    """Right password of a two-factor user; returns the token for the code step."""
    token = new_token()
    now = utcnow()
    db.execute(
        delete(PendingLogin).where(PendingLogin.created_at < now - two_factor.PENDING_LOGIN_TIMEOUT)
    )
    db.add(PendingLogin(id=token_fingerprint(token), user_id=user.id, created_at=now))
    return token


def _find_pending_login(db: Session, token: str | None) -> PendingLogin | None:
    if not token:
        return None
    pending = db.get(PendingLogin, token_fingerprint(token))
    if pending is None:
        return None
    if utcnow() - pending.created_at > two_factor.PENDING_LOGIN_TIMEOUT:
        db.delete(pending)
        return None
    return pending


def pending_login_exists(new_session: Callable[[], Session], token: str | None) -> bool:
    with new_session() as db, db.begin():
        return _find_pending_login(db, token) is not None


def _use_second_factor(db: Session, cipher: Cipher | None, user: User, code: str) -> str | None:
    """Checks an app code or a recovery code and uses it up, so it works only once.

    Returns "app" or "recovery", or None for a wrong code.
    """
    if user.totp_secret is None or len(code) > two_factor.MAX_CODE_LENGTH:
        return None
    if two_factor.is_app_code(code):
        secret = None if cipher is None else cipher.decrypt(user.totp_secret)
        if secret is None:
            return None
        step = two_factor.matching_step(secret, code, utcnow(), user.totp_last_step)
        if step is None:
            return None
        user.totp_last_step = step
        return "app"
    row = db.scalar(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == two_factor.recovery_code_hash(code),
        )
    )
    if row is None:
        return None
    db.delete(row)
    return "recovery"


def confirm_login_code(
    new_session: Callable[[], Session], cipher: Cipher | None, token: str | None, code: str
) -> tuple[str | None, bool]:
    """Second step of a two-factor login.

    Returns the session token (None when the code is wrong) and whether the login has to
    start again with the password (expired, locked or no pending login).
    """
    with _login_lock, new_session() as db, db.begin():
        pending = _find_pending_login(db, token)
        if pending is None:
            return None, True
        user = db.get(User, pending.user_id)
        if user is None or not user.active or user.totp_secret is None:
            db.delete(pending)
            return None, True
        if _is_locked(user):
            db.delete(pending)
            audit.record_login(db, user.id, "failed")
            return None, True
        kind = _use_second_factor(db, cipher, user, code)
        if kind is None:
            if _count_failure(db, user):
                audit.record_login(db, user.id, "locked")
                end_all_sessions(db, user.id)
                return None, True
            audit.record_login(db, user.id, "wrong_code")
            return None, False
        user.failed_logins = 0
        db.delete(pending)
        audit.record_login(db, user.id, "success" if kind == "app" else "recovery_code_used")
        return start_session(db, user), False


def recovery_codes_left(db: Session, user_id: int) -> int:
    return len(db.scalars(select(RecoveryCode.id).where(RecoveryCode.user_id == user_id)).all())


def _replace_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = two_factor.new_recovery_codes()
    db.add_all(
        RecoveryCode(user_id=user.id, code_hash=two_factor.recovery_code_hash(code))
        for code in codes
    )
    return codes


def start_two_factor_setup(
    new_session: Callable[[], Session], cipher: Cipher, user_id: int, password: str
) -> tuple[dict[str, str], bool]:
    """Checks the password and creates a new secret to show; returns errors, logged out."""
    with _login_lock, new_session() as db, db.begin():
        user = db.get(User, user_id)
        if user is None or _is_locked(user):
            end_all_sessions(db, user_id)
            return {}, True
        if user.totp_secret is not None:
            return {}, False
        if not _password_ok(user, password):
            locked = _reject(db, user, "wrong_current_password")
            return {"tfa_password": "account.error.current_wrong"}, locked
        user.failed_logins = 0
        user.totp_pending_secret = cipher.encrypt(two_factor.new_secret())
        return {}, False


def pending_setup_secret(
    new_session: Callable[[], Session], cipher: Cipher, user_id: int
) -> str | None:
    with new_session() as db:
        user = db.get(User, user_id)
        if user is None or user.totp_secret is not None or user.totp_pending_secret is None:
            return None
        return cipher.decrypt(user.totp_pending_secret)


def confirm_two_factor_setup(
    new_session: Callable[[], Session], cipher: Cipher, user_id: int, code: str
) -> list[str] | None:
    """Turns two-factor login on with the first code; returns the new recovery codes.

    A wrong code here does not count as a failure: the secret is new and not in use yet.
    """
    with _login_lock, new_session() as db, db.begin():
        user = db.get(User, user_id)
        if user is None or user.totp_secret is not None or user.totp_pending_secret is None:
            return None
        encrypted = user.totp_pending_secret
        secret = cipher.decrypt(encrypted)
        if secret is None or len(code) > two_factor.MAX_CODE_LENGTH:
            return None
        step = two_factor.matching_step(secret, code, utcnow(), None)
        if step is None:
            return None
        # Moved over as it is (still encrypted).
        user.totp_secret = encrypted
        user.totp_pending_secret = None
        user.totp_last_step = step
        audit.record_change(db, user.id, "account.2fa_enabled", user.username)
        return _replace_recovery_codes(db, user)


def disable_two_factor(
    new_session: Callable[[], Session], cipher: Cipher | None, user_id: int, raw: dict[str, str]
) -> tuple[dict[str, str], bool]:
    """Turns two-factor login off with password and code; returns errors, logged out."""
    with _login_lock, new_session() as db, db.begin():
        user = db.get(User, user_id)
        if user is None or _is_locked(user):
            end_all_sessions(db, user_id)
            return {}, True
        if user.totp_secret is None:
            return {}, False
        if not _password_ok(user, raw.get("off_password", "")):
            locked = _reject(db, user, "wrong_current_password")
            return {"off_password": "account.error.current_wrong"}, locked
        if _use_second_factor(db, cipher, user, raw.get("off_code", "")) is None:
            locked = _reject(db, user, "wrong_code")
            return {"off_code": "account.error.code_wrong"}, locked
        user.failed_logins = 0
        user.totp_secret = None
        user.totp_pending_secret = None
        user.totp_last_step = None
        db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        audit.record_change(db, user.id, "account.2fa_disabled", user.username)
        return {}, False


def renew_recovery_codes(
    new_session: Callable[[], Session], cipher: Cipher | None, user_id: int, code: str
) -> tuple[list[str] | None, dict[str, str], bool]:
    """New recovery codes after a valid code; returns codes, errors, logged out."""
    with _login_lock, new_session() as db, db.begin():
        user = db.get(User, user_id)
        if user is None or _is_locked(user):
            end_all_sessions(db, user_id)
            return None, {}, True
        if user.totp_secret is None:
            return None, {}, False
        if _use_second_factor(db, cipher, user, code) is None:
            locked = _reject(db, user, "wrong_code")
            return None, {"renew_code": "account.error.code_wrong"}, locked
        user.failed_logins = 0
        audit.record_change(db, user.id, "account.2fa_codes_renewed", user.username)
        return _replace_recovery_codes(db, user), {}, False


def two_factor_key_matches(db: Session, cipher: Cipher | None) -> bool:
    """True when every switched-on two-factor secret can be read with the current key.

    Pending (not yet confirmed) secrets do not matter: they are simply created again.
    """
    secrets_in_use = db.scalars(select(User.totp_secret).where(User.totp_secret.is_not(None)))
    return all(cipher is not None and cipher.decrypt(value) is not None for value in secrets_in_use)


def _clear_two_factor(user: User) -> None:
    user.totp_secret = None
    user.totp_pending_secret = None
    user.totp_last_step = None


def reset_two_factor(db: Session, user: User, actor_id: int | None) -> bool:
    """Turns two-factor login of one user off (lost phone, issue #39).

    Done by an admin (actor_id) or by the command on the server (actor_id None). The user is
    logged out everywhere. Returns False, changing nothing, when it was not switched on.
    """
    if user.totp_secret is None:
        return False
    _clear_two_factor(user)
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    end_all_sessions(db, user.id)
    audit.record_change(db, actor_id, "user.2fa_reset", user.username)
    return True


def admin_reset_two_factor(
    new_session: Callable[[], Session], actor_id: int, user_id: int
) -> tuple[str | None, str | None]:
    """An admin resets the two-factor login of another user; returns error key and name.

    The name is None for an unknown user.
    """
    # The same lock as the login, so a code step cannot run at the same moment.
    with _login_lock, new_session() as db, db.begin():
        user = db.get(User, user_id)
        if user is None:
            return None, None
        if user.id == actor_id:
            # The own two-factor login is turned off under "Mein Konto", with password and code.
            return "user.error.self_2fa", user.username
        reset_two_factor(db, user, actor_id)
        return None, user.username


def find_user(db: Session, username: str) -> User | None:
    """The user with this name; case does not matter, as at the login."""
    return db.scalar(select(User).where(User.username_key == name_key(username)))


def reset_all_two_factor(db: Session) -> int:
    """Turns two-factor login off for everybody (lost key); returns the number of users."""
    switched_on = 0
    for user in db.scalars(select(User)):
        switched_on += user.totp_secret is not None
        _clear_two_factor(user)
    db.execute(delete(RecoveryCode))
    db.execute(delete(PendingLogin))
    audit.record_change(db, None, "system.2fa_reset_all")
    return switched_on
