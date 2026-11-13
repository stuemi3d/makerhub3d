"""Commands for the server, run inside the container (issues #30 and #39).

    docker compose run --rm app python -m app.cli reset-2fa [username]

reset-2fa: turns two-factor login off for every user, e.g. after the key in
MAKERHUB_2FA_KEY was lost. Nothing else changes; everybody can set it up again.

reset-2fa <username>: turns it off for this one user only, e.g. when the only admin lost the
phone and the recovery codes. Case does not matter, as at the login.
"""

import sys

from sqlalchemy.orm import Session

from app import auth
from app.config import Settings
from app.db import make_engine, run_migrations
from app.i18n import translate

# Exit codes: 0 done, 1 unknown user, 2 wrong call.
UNKNOWN_USER = 1
USAGE = 2


def _reset_all(db: Session, language: str) -> tuple[str, int]:
    count = auth.reset_all_two_factor(db)
    return translate(language, "cli.reset_2fa.done").format(count=count), 0


def _reset_one(db: Session, language: str, username: str) -> tuple[str, int]:
    """Resets one user; returns the message and the exit code."""
    user = auth.find_user(db, username)
    if user is None:
        key, name, exit_code = "cli.reset_2fa.unknown_user", username, UNKNOWN_USER
    elif auth.reset_two_factor(db, user, None):
        key, name, exit_code = "cli.reset_2fa.user_done", user.username, 0
    else:
        key, name, exit_code = "cli.reset_2fa.user_was_off", user.username, 0
    return translate(language, key).format(username=name), exit_code


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    settings = Settings.from_env()
    if not args or args[0] != "reset-2fa" or len(args) > 2:
        print(translate(settings.language, "cli.usage"), file=sys.stderr)
        return USAGE
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_path)
    try:
        run_migrations(engine)
        with Session(engine) as db, db.begin():
            if len(args) == 1:
                message, exit_code = _reset_all(db, settings.language)
            else:
                message, exit_code = _reset_one(db, settings.language, args[1])
    finally:
        engine.dispose()
    print(message, file=sys.stderr if exit_code else sys.stdout)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
