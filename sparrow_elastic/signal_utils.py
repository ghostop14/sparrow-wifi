"""Signal-strength utilities for sparrow_elastic.

Public API
----------
dbm_to_mw(dbm)          -> Optional[float]
quality_0_to_5(dbm)     -> Optional[int]
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public: dbm_to_mw
# ---------------------------------------------------------------------------

def dbm_to_mw(dbm: Optional[float]) -> Optional[float]:
    """Convert a dBm value to milliwatts.

    The conversion formula is:  ``mW = 10 ** (dBm / 10)``

    Examples:
        -70 dBm  ->  1e-7 mW  (0.0000001 mW)
          0 dBm  ->  1.0  mW
        +10 dBm  ->  (anomalous) clipped to 0 dBm -> 1.0 mW + WARN logged

    Args:
        dbm: Signal strength in dBm, or ``None``.

    Returns:
        Milliwatt value (float) or ``None`` if *dbm* is ``None``.
    """
    if dbm is None:
        return None

    if dbm > 0:
        logger.warning(
            "signal_utils: anomalous positive dBm value %.2f from passive sensor; "
            "clipping to 0 dBm before mW conversion",
            dbm,
        )
        dbm = 0.0

    return 10.0 ** (dbm / 10.0)


# ---------------------------------------------------------------------------
# Public: quality_0_to_5
# ---------------------------------------------------------------------------

def quality_0_to_5(dbm: Optional[float]) -> Optional[int]:
    """Map a dBm value to a 0-5 quality bar rating.

    Scale:
        >= -50 dBm  -> 5
        >= -60 dBm  -> 4
        >= -70 dBm  -> 3
        >= -80 dBm  -> 2
        >= -90 dBm  -> 1
        <  -90 dBm  -> 0

    Args:
        dbm: Signal strength in dBm, or ``None``.

    Returns:
        Integer quality rating 0-5, or ``None`` if *dbm* is ``None``.
    """
    if dbm is None:
        return None

    if dbm >= -50:
        return 5
    if dbm >= -60:
        return 4
    if dbm >= -70:
        return 3
    if dbm >= -80:
        return 2
    if dbm >= -90:
        return 1
    return 0
