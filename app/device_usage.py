"""Whether a device is in use (issue #163): a printer with productions must not be deleted.

This is the one place that answers the question. Productions do not point to a device yet;
issue #162 adds that link and must make `is_in_use` look at it.
"""

from sqlalchemy.orm import Session


def is_in_use(db: Session, device_id: int) -> bool:
    """True when at least one production hangs on the device."""
    return False
