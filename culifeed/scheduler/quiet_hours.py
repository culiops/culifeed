"""Quiet-hours window check for delivery suppression."""
from datetime import datetime


def in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    """Return True if `now.hour` falls within the quiet window [start, end).

    The window is half-open: `start` is inside, `end` is outside.
    If `start > end`, the window wraps midnight.
    If `start == end`, there are no quiet hours and this returns False for every hour.
    """
    if start == end:
        return False
    h = now.hour
    if start < end:
        return start <= h < end
    return h >= start or h < end
