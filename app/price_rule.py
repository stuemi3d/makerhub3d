"""The price rules of a calculation position (issue #237)."""

AVERAGE = "average"
LAST = "last"
STANDARD = "standard"
SPOOL = "spool"
# The order of the choice; "spool" is one spool of Bambuddy (the way it was before filaments)
RULES = (AVERAGE, LAST, STANDARD, SPOOL)
FILAMENT_RULES = (AVERAGE, LAST, STANDARD)
DEFAULT = SPOOL
