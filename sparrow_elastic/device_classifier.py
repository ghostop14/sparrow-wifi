"""Device classifier -- Step 5 will implement rule-table-driven logic.

Current placeholder always returns ("unknown", 0.0, []).
"""

from typing import Optional


def classify(evidence: dict) -> tuple:
    """Returns (class_guess, confidence, evidence_list).

    Evidence dict keys (Step 5 will use these):
        oui_vendor, bt_cod, bt_appearance, bt_name, bt_company,
        wifi_ssid, wifi_vendor_ies, service_uuids, apple_continuity_type

    Args:
        evidence: Dict of observable device characteristics.

    Returns:
        Tuple of (class_guess: str, confidence: float, evidence_list: list).
        Placeholder always returns ("unknown", 0.0, []).
    """
    return ("unknown", 0.0, [])
