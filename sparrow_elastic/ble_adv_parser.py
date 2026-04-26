"""BLE advertising payload parser.

Parses raw HCI advertising data bytes into structured fields for the ECS
document. Today the sparrow agent does not expose raw adv bytes; this
module returns an empty dict in that case. When the agent is extended
to surface raw bytes (see agent backlog), the real parser goes here.
"""

from typing import Optional


def parse_adv_payload(hex_str: Optional[str]) -> dict:
    """Return a dict with optional keys:
        advertising.type, advertising.flags, advertising.appearance,
        advertising.tx_power_dbm, beacon.type, beacon.uuid,
        beacon.major, beacon.minor, beacon.eddystone_url,
        apple.continuity_type

    Empty dict when input is None/empty or unparseable.

    Args:
        hex_str: Hex-encoded raw HCI advertising payload, or ``None``.

    Returns:
        Parsed fields dict, currently always empty pending agent extension.
    """
    if not hex_str:
        return {}
    # TODO: Real parser deferred to agent-extension backlog.
    return {}
