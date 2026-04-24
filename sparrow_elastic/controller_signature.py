"""Controller-candidate RF signature for sparrow_elastic.

Public API
----------
is_controller_candidate(rf_band, signal_dbm, device_class, mac_vendor) -> bool
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level compiled regex for known drone-controller OUI vendors
# ---------------------------------------------------------------------------

_CONTROLLER_VENDOR_RE = re.compile(
    r"^(dji|autel|skydio|parrot|yuneec|holy\s*stone)",
    re.IGNORECASE,
)

# Bands in which drone controllers typically operate
_CONTROLLER_BANDS = {"2_4ghz", "5_8ghz_isb"}

# Minimum signal strength to consider a device a nearby controller.
# Weaker signals are likely not a threat or are too far away to attribute.
_MIN_SIGNAL_DBM = -70.0


# ---------------------------------------------------------------------------
# Public: is_controller_candidate
# ---------------------------------------------------------------------------

def is_controller_candidate(
    rf_band: str,
    signal_dbm: Optional[float],
    device_class: str,
    mac_vendor: Optional[str],
) -> bool:
    """Return True iff the RF observation looks like a drone controller.

    Criteria (ALL must hold):
    1. Device class is "drone_controller" OR mac_vendor matches one of the
       known drone-manufacturer prefixes (DJI, Autel, Skydio, Parrot,
       Yuneec, Holy Stone).
    2. signal_dbm is not None and is stronger than -70 dBm (i.e. > -70.0).
    3. rf_band is in {"2_4ghz", "5_8ghz_isb"} -- the two bands used by
       consumer drone controllers.

    Args:
        rf_band:      Band label from ``channel_utils.band_for_frequency()``.
        signal_dbm:   Observed signal in dBm, or ``None`` when unavailable.
        device_class: Device class string (e.g. "drone_controller", "phone",
                      "unknown").
        mac_vendor:   OUI vendor string, or ``None`` when not resolved.

    Returns:
        bool
    """
    # Criterion 1 -- device class or vendor match
    is_known_class = (device_class == "drone_controller")
    is_known_vendor = bool(
        mac_vendor and _CONTROLLER_VENDOR_RE.match(mac_vendor)
    )
    if not (is_known_class or is_known_vendor):
        return False

    # Criterion 2 -- signal present and strong enough (strictly greater than -70)
    if signal_dbm is None or signal_dbm <= _MIN_SIGNAL_DBM:
        return False

    # Criterion 3 -- band must be one used by drone controllers
    if rf_band not in _CONTROLLER_BANDS:
        return False

    return True
