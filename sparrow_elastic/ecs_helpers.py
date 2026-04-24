"""ECS timestamp + shared helper utilities for sparrow_elastic."""

from datetime import datetime, timezone


# Day-of-week names indexed by datetime.weekday() (Monday=0 ... Sunday=6)
DAY_OF_WEEK = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)


def to_es_timestamp(dt: datetime) -> str:
    """Format a datetime as ISO 8601 Z with millisecond precision.

    Produces the pattern ``YYYY-MM-DDTHH:MM:SS.mmmZ`` required by ECS date
    fields.

    Rules:
    - Naive datetimes are assumed to be UTC and tagged as such.
    - Non-UTC timezone-aware datetimes are converted to UTC first.
    - The output always ends with ``Z`` (not ``+00:00``).

    Args:
        dt: A ``datetime`` object, aware or naive.

    Returns:
        ISO 8601 string with millisecond precision and ``Z`` suffix, e.g.
        ``"2024-03-15T14:30:00.123Z"``.
    """
    if dt.tzinfo is None:
        # Naive datetime -- assume UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # Convert any aware datetime to UTC
        dt = dt.astimezone(timezone.utc)

    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"
