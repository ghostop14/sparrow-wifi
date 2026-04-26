"""MAC address utilities for sparrow_elastic.

Public API
----------
canonicalize_mac(mac)   -> str
mac_flags(mac, ...)     -> dict
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Strip every non-hex character so we can normalise the raw address string.
_NON_HEX = re.compile(r"[^0-9a-fA-F]")


def _strip_to_hex(mac: str) -> str:
    """Return only the hex digits from *mac* (all separators removed)."""
    return _NON_HEX.sub("", mac)


# ---------------------------------------------------------------------------
# Public: canonicalize_mac
# ---------------------------------------------------------------------------

def canonicalize_mac(mac: str) -> str:
    """Normalise *mac* to ``AA:BB:CC:DD:EE:FF`` (uppercase, colon-separated).

    Accepted input formats:
    - ``AA:BB:CC:DD:EE:FF``  (colon-separated, any case)
    - ``AA-BB-CC-DD-EE-FF``  (hyphen-separated, any case)
    - ``AABBCCDDEEFF``       (bare hex, any case)
    - ``aabb.ccdd.eeff``     (Cisco dot notation, any case)

    Args:
        mac: Raw MAC address string.

    Returns:
        Canonical ``AA:BB:CC:DD:EE:FF`` string, or empty string if *mac* is
        empty.

    Raises:
        ValueError: If the input is non-empty but malformed (wrong length after
            stripping separators, or contains non-hex characters).
    """
    if not mac:
        return ""

    stripped = _strip_to_hex(mac)

    if len(stripped) != 12:
        raise ValueError(
            f"MAC address must contain exactly 12 hex digits; got {len(stripped)!r} "
            f"from input {mac!r}"
        )

    # Validate all characters are hex (should already be by _strip_to_hex, but
    # we also want to catch the case where the original string contained
    # characters that happened to be stripped — e.g. all non-hex — leaving
    # fewer than 12 valid digits among non-hex noise).
    try:
        int(stripped, 16)
    except ValueError:
        raise ValueError(f"MAC address contains non-hex characters: {mac!r}")

    return ":".join(
        stripped[i : i + 2].upper() for i in range(0, 12, 2)
    )


# ---------------------------------------------------------------------------
# Public: mac_flags
# ---------------------------------------------------------------------------

_BLE_RANDOM_SUBTYPES = {
    0b11: "random_static",
    0b01: "random_resolvable",
    0b00: "random_nonresolvable",
    0b10: "random_static",   # reserved — map to least harmful default
}


def mac_flags(
    mac: str,
    is_ble: bool = False,
    ble_addr_type: Optional[int] = None,
) -> dict:
    """Return a dict describing the addressing flags encoded in *mac*.

    Args:
        mac:           MAC address (any accepted format; empty/malformed → safe
                       defaults returned without raising).
        is_ble:        True when this MAC was observed in a BLE context.
        ble_addr_type: HCI address type byte (0 = public, 1 = random).  Only
                       meaningful when *is_ble* is True.

    Returns:
        dict with keys:
            ``locally_administered`` (bool)
            ``randomized`` (bool)
            ``addr_type`` (str): ``universal`` | ``random_static`` |
                ``random_resolvable`` | ``random_nonresolvable`` | ``unknown``
    """
    _unknown = {"locally_administered": False, "randomized": False, "addr_type": "unknown"}

    if not mac:
        return _unknown

    try:
        canonical = canonicalize_mac(mac)
    except ValueError:
        return _unknown

    first_byte = int(canonical[:2], 16)
    la_bit = bool(first_byte & 0x02)

    if is_ble and ble_addr_type is not None:
        if ble_addr_type == 0:
            # Public address
            return {
                "locally_administered": False,
                "randomized": False,
                "addr_type": "universal",
            }
        else:
            # Random address — examine top 2 bits of first byte per BLE spec
            top2 = (first_byte >> 6) & 0x03
            addr_type = _BLE_RANDOM_SUBTYPES[top2]
            return {
                "locally_administered": la_bit,
                "randomized": True,
                "addr_type": addr_type,
            }
    else:
        # Non-BLE (or BLE without addr type info)
        addr_type = "locally_administered" if la_bit else "universal"
        return {
            "locally_administered": la_bit,
            "randomized": la_bit,
            "addr_type": addr_type,
        }
