"""Channel and frequency utilities for sparrow_elastic.

Public API
----------
compute_occupied_set(primary, width_mhz, band) -> list[int]
band_for_frequency(frequency_mhz)              -> str
channel_for_frequency(frequency_mhz)           -> Optional[int]
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 5 GHz occupied-channel lookup tables
# Each sub-channel number maps to the full sorted group it belongs to.
# ---------------------------------------------------------------------------

# 80 MHz groups: UNII-1, UNII-2A, UNII-2C, UNII-3 + extended
_5GHZ_80MHZ_GROUPS: list = [
    [36, 40, 44, 48],
    [52, 56, 60, 64],
    [100, 104, 108, 112],
    [116, 120, 124, 128],
    [132, 136, 140, 144],
    [149, 153, 157, 161],
    [165, 169, 173, 177],
]

# 160 MHz groups (each is 8 consecutive 20-MHz sub-channels)
_5GHZ_160MHZ_GROUPS: list = [
    [36, 40, 44, 48, 52, 56, 60, 64],
    [100, 104, 108, 112, 116, 120, 124, 128],
    [149, 153, 157, 161, 165, 169, 173, 177],
]

# Build O(1) reverse-lookup dicts: sub-channel -> group
_5GHZ_80MHZ_MAP: dict = {}
for _grp in _5GHZ_80MHZ_GROUPS:
    for _ch in _grp:
        _5GHZ_80MHZ_MAP[_ch] = _grp

_5GHZ_160MHZ_MAP: dict = {}
for _grp in _5GHZ_160MHZ_GROUPS:
    for _ch in _grp:
        _5GHZ_160MHZ_MAP[_ch] = _grp


# ---------------------------------------------------------------------------
# Public: compute_occupied_set
# ---------------------------------------------------------------------------

def compute_occupied_set(primary: int, width_mhz: int, band: str) -> list:
    """Return the list of 20-MHz channel centers covered by a bonded channel.

    Args:
        primary:   Primary channel number (e.g. 36, 6, 1).
        width_mhz: Channel width in MHz (20, 40, 80, 160, 320).
        band:      Band identifier: "2_4ghz", "5ghz", "5_8ghz_isb", "6ghz",
                   or any other string (returns [primary]).

    Returns:
        Sorted list of 20-MHz sub-channel numbers that make up the bonded
        channel.  Falls back to ``[primary]`` when the primary channel is not
        in a known group for 80/160 MHz cases.
    """
    # 2.4 GHz: return only the primary channel regardless of width_mhz.
    # 40 MHz bonding in 2.4 GHz overlaps adjacent channels but we intentionally
    # do not expand to avoid confusing occupied-set semantics.
    if band == "2_4ghz":
        return [primary]

    # 5 GHz (and the overlapping ISB sub-range share the same channel numbers)
    if band in ("5ghz", "5_8ghz_isb"):
        if width_mhz == 20:
            return [primary]
        if width_mhz == 40:
            # Assume primary is the lower anchor; secondary is primary+4.
            return sorted([primary, primary + 4])
        if width_mhz == 80:
            grp = _5GHZ_80MHZ_MAP.get(primary)
            if grp is not None:
                return list(grp)
            logger.debug(
                "channel_utils: unknown 80 MHz group for 5 GHz primary %d; "
                "returning [%d]",
                primary, primary,
            )
            return [primary]
        if width_mhz >= 160:
            grp = _5GHZ_160MHZ_MAP.get(primary)
            if grp is not None:
                return list(grp)
            logger.debug(
                "channel_utils: unknown 160 MHz group for 5 GHz primary %d; "
                "returning [%d]",
                primary, primary,
            )
            return [primary]
        return [primary]

    # 6 GHz: 4-channel spacing (1, 5, 9, 13, ...)
    if band == "6ghz":
        if width_mhz == 20:
            return [primary]
        if width_mhz == 40:
            anchor = ((primary - 1) // 8) * 8 + 1
            return sorted([anchor, anchor + 4])
        if width_mhz == 80:
            anchor = ((primary - 1) // 16) * 16 + 1
            return sorted([anchor, anchor + 4, anchor + 8, anchor + 12])
        if width_mhz == 160:
            anchor = ((primary - 1) // 32) * 32 + 1
            return sorted([anchor + i * 4 for i in range(8)])
        if width_mhz == 320:
            anchor = ((primary - 1) // 64) * 64 + 1
            return sorted([anchor + i * 4 for i in range(16)])
        return [primary]

    # Unknown band -- best-effort
    return [primary]


# ---------------------------------------------------------------------------
# Public: band_for_frequency
# ---------------------------------------------------------------------------

def band_for_frequency(frequency_mhz: int) -> str:
    """Return a band label for the given frequency in MHz.

    Ranges (checked in priority order):
        2400-2500  -> "2_4ghz"
        5725-5875  -> "5_8ghz_isb"  (drone video ISB; takes precedence over 5ghz)
        5150-5900  -> "5ghz"
        5925-7125  -> "6ghz"
        < 1000     -> "sub_ghz"
        otherwise  -> "unknown"

    Note: 5725-5875 overlaps with the 5ghz range.  The ISB label takes
    precedence inside that sub-range because it is more specific.
    """
    if 2400 <= frequency_mhz <= 2500:
        return "2_4ghz"
    # Check ISB before general 5ghz so the more-specific label wins.
    if 5725 <= frequency_mhz <= 5875:
        return "5_8ghz_isb"
    if 5150 <= frequency_mhz <= 5900:
        return "5ghz"
    if 5925 <= frequency_mhz <= 7125:
        return "6ghz"
    if frequency_mhz < 1000:
        return "sub_ghz"
    return "unknown"


# ---------------------------------------------------------------------------
# Public: channel_for_frequency
# ---------------------------------------------------------------------------

def channel_for_frequency(frequency_mhz: int) -> Optional[int]:
    """Compute the primary channel number from a frequency in MHz.

    Formulae:
        2.4 GHz:  channel = (freq - 2412) // 5 + 1  (channels 1-14)
        5 GHz:    channel = (freq - 5000) // 5
        6 GHz:    channel = (freq - 5955) // 5 + 1

    The 5 GHz formula covers both plain "5ghz" and "5_8ghz_isb" since they
    share the same channel numbering.

    Args:
        frequency_mhz: Frequency in MHz.

    Returns:
        Integer channel number, or ``None`` for unknown bands or
        out-of-range frequencies.
    """
    if 2400 <= frequency_mhz <= 2500:
        return (frequency_mhz - 2412) // 5 + 1
    # Both ISB and plain 5ghz share the same channel numbering.
    if 5150 <= frequency_mhz <= 5900:
        return (frequency_mhz - 5000) // 5
    if 5925 <= frequency_mhz <= 7125:
        return (frequency_mhz - 5955) // 5 + 1
    return None
