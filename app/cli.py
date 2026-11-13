"""Commands for the server, run inside the container (issue #30).

    docker compose run --rm app python -m app.cli reset-2fa

reset-2fa: turns two-factor login off for every user, e.g. after the key in
MAKERHUB_2FA_KEY was lost. Nothing else changes; everybody can set it up again.
"""

import sys

from sqlalchemy.orm import Session

from app import auth
from app.config import Settings
from app.db import make_engine, run_migrations
from app.i18n import translate


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    settings = Settings.from_env()
    if args != ["reset-2fa"]:
        print(translate(settings.language, "cli.usage"), file=sys.stderr)
        return 2
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_path)
    try:
        run_migrations(engine)
        with Session(engine) as db, db.begin():
            count = auth.reset_all_two_factor(db)
    finally:
        engine.dispose()
    print(translate(settings.language, "cli.reset_2fa.done").format(count=count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
