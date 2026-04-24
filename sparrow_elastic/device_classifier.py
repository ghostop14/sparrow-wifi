"""
Rule-table-driven device type classifier for Sparrow ES integration.

The classifier consumes a 9-key evidence dict assembled by the document
builder and returns a ``(class_guess, confidence, evidence_list)`` tuple.

Evidence keys (all always present, value is ``None`` when unknown):

    oui_vendor           str | None   — OUI vendor string from MAC lookup
    bt_cod               int | None   — Bluetooth Class of Device (24-bit)
    bt_appearance        int | None   — GAP Appearance value (16-bit)
    bt_name              str | None   — Bluetooth device name
    bt_company           str | None   — BT Company Identifier string
    wifi_ssid            str | None   — WiFi SSID
    wifi_vendor_ies      str | None   — concatenated vendor IE OUI strings
    service_uuids        list | None  — list of BLE service UUID strings
    apple_continuity_type str | None  — decoded Apple Continuity subtype

Public API
----------
classify(evidence)     -> (str, float, list[str])
reload_rules()         -> int
get_rule_count()       -> int
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Rule file location ────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_RULES_FILE = os.path.join(_DATA_DIR, "device_classifier_rules.json")

# ── Module-level rule cache ───────────────────────────────────────────────

# Each entry in _COMPILED_RULES is a dict with the raw rule fields plus:
#   "_compiled_re": compiled re.Pattern | None (for regex rules)
_COMPILED_RULES: List[Dict[str, Any]] = []
_RULES_LOADED: bool = False

# ── Match-type implementations ────────────────────────────────────────────

_VALID_MATCH_TYPES = frozenset(
    {"regex", "equals", "in_list", "cod_major", "appearance_category"}
)


def _match_regex(value: Any, compiled_re: re.Pattern) -> bool:
    """Return True if *value* (string) matches *compiled_re*."""
    if not isinstance(value, str) or not value:
        return False
    return bool(compiled_re.search(value))


def _match_equals(value: Any, pattern: str) -> bool:
    """Return True if *value* equals *pattern* exactly."""
    return value == pattern


def _match_in_list(value: Any, pattern: str) -> bool:
    """Return True if *pattern* appears in *value*.

    *value* may be a string (substring check) or a list (membership check).
    """
    if isinstance(value, list):
        return pattern in value
    if isinstance(value, str):
        return pattern in value
    return False


def _match_cod_major(value: Any, pattern: int) -> bool:
    """Return True if BT CoD major device class equals *pattern*.

    The Class of Device is a 24-bit integer.
    Major Device Class = bits 12-8 (i.e., ``(cod >> 8) & 0x1F``).
    """
    if not isinstance(value, int):
        return False
    major = (value >> 8) & 0x1F
    return major == pattern


def _match_appearance_category(value: Any, pattern: int) -> bool:
    """Return True if GAP Appearance category equals *pattern*.

    Appearance is a 16-bit value.
    Category = bits 15-6 (i.e., ``appearance >> 6``).
    """
    if not isinstance(value, int):
        return False
    category = value >> 6
    return category == pattern


# ── Rule loading ──────────────────────────────────────────────────────────

def _compile_rule(rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate and pre-compile a single rule dict.

    Returns the augmented rule dict on success, or None if the rule is
    malformed (will be skipped with a warning).
    """
    required = {"match_key", "match_type", "pattern",
                "class_guess", "confidence", "evidence_tag"}
    missing = required - rule.keys()
    if missing:
        logger.warning("Skipping rule missing fields %s: %r", missing, rule)
        return None

    match_type = rule["match_type"]
    if match_type not in _VALID_MATCH_TYPES:
        logger.warning("Skipping rule with unknown match_type %r: %r",
                       match_type, rule)
        return None

    compiled_re = None
    if match_type == "regex":
        try:
            compiled_re = re.compile(rule["pattern"])
        except re.error as exc:
            logger.warning("Skipping rule with invalid regex %r: %s",
                           rule["pattern"], exc)
            return None

    augmented = dict(rule)
    augmented["_compiled_re"] = compiled_re
    return augmented


def _load_rules() -> List[Dict[str, Any]]:
    """Read, validate, and compile the rules file.

    Returns an empty list on I/O or JSON parse failure so that
    :func:`classify` degrades gracefully without crashing the bridge.
    """
    try:
        with open(_RULES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        logger.warning(
            "Device classifier rules file not found: %s — "
            "classify() will return 'unknown' for all inputs",
            _RULES_FILE,
        )
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Failed to load device classifier rules from %s: %s — "
            "classify() will return 'unknown' for all inputs",
            _RULES_FILE, exc,
        )
        return []

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        logger.warning("Rules file %s: 'rules' is not a list", _RULES_FILE)
        return []

    compiled: List[Dict[str, Any]] = []
    for rule in raw_rules:
        result = _compile_rule(rule)
        if result is not None:
            compiled.append(result)

    logger.debug("Loaded %d device classifier rules from %s",
                 len(compiled), _RULES_FILE)
    return compiled


def _ensure_loaded() -> None:
    """Load rules on first use (lazy init, idempotent)."""
    global _COMPILED_RULES, _RULES_LOADED
    if not _RULES_LOADED:
        _COMPILED_RULES = _load_rules()
        _RULES_LOADED = True


# ── Public API ────────────────────────────────────────────────────────────

def reload_rules() -> int:
    """Re-read the JSON rules file and return the new rule count.

    Safe to call at runtime — e.g., after the operator edits the file or
    after a unit test that monkeypatches the file path.
    """
    global _COMPILED_RULES, _RULES_LOADED
    _COMPILED_RULES = _load_rules()
    _RULES_LOADED = True
    return len(_COMPILED_RULES)


def get_rule_count() -> int:
    """Return the number of currently loaded rules."""
    _ensure_loaded()
    return len(_COMPILED_RULES)


def classify(evidence: Dict[str, Any]) -> Tuple[str, float, List[str]]:
    """Classify a device based on the evidence dict.

    Parameters
    ----------
    evidence:
        Dict with keys ``oui_vendor``, ``bt_cod``, ``bt_appearance``,
        ``bt_name``, ``bt_company``, ``wifi_ssid``, ``wifi_vendor_ies``,
        ``service_uuids``, ``apple_continuity_type``.  Missing keys and
        ``None`` values are both treated as "absent evidence" — rules for
        absent keys are skipped cleanly.

    Returns
    -------
    (class_guess, confidence, evidence_list)
        *class_guess* is the winning device class string, or ``"unknown"``
        when no rules fire.
        *confidence* is in [0.0, 1.0], rounded to 3 decimal places.
        *evidence_list* is a deduplicated, ordered list of evidence tags
        that contributed to the winning class.
    """
    _ensure_loaded()

    if not _COMPILED_RULES:
        return ("unknown", 0.0, [])

    # per_class: {class_guess -> [(confidence, evidence_tag), ...]}
    per_class: Dict[str, List[Tuple[float, str]]] = {}

    for rule in _COMPILED_RULES:
        match_key: str = rule["match_key"]

        # Skip rules whose key is absent in the evidence dict
        if match_key not in evidence:
            continue

        value = evidence[match_key]

        # Skip rules when the value is None or empty string / empty list
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        if isinstance(value, list) and not value:
            continue

        match_type: str = rule["match_type"]
        pattern = rule["pattern"]
        fired = False

        if match_type == "regex":
            fired = _match_regex(value, rule["_compiled_re"])
        elif match_type == "equals":
            fired = _match_equals(value, pattern)
        elif match_type == "in_list":
            fired = _match_in_list(value, pattern)
        elif match_type == "cod_major":
            fired = _match_cod_major(value, int(pattern))
        elif match_type == "appearance_category":
            fired = _match_appearance_category(value, int(pattern))

        if fired:
            cls = rule["class_guess"]
            if cls not in per_class:
                per_class[cls] = []
            per_class[cls].append((rule["confidence"], rule["evidence_tag"]))

    if not per_class:
        return ("unknown", 0.0, [])

    # Combine confidences per class using the probabilistic OR formula:
    #   combined = 1 - product(1 - c_i)
    # This is non-commutative in floating point but gives a sensible
    # combined probability that caps gracefully near 1.0 when many rules fire.
    best_class: Optional[str] = None
    best_confidence: float = 0.0
    best_evidence: List[str] = []

    for cls, matches in per_class.items():
        product = 1.0
        for conf, _ in matches:
            product *= (1.0 - conf)
        combined = 1.0 - product

        if combined > best_confidence:
            best_confidence = combined
            best_class = cls
            # Collect deduplicated evidence tags, preserving order
            seen: set = set()
            tags: List[str] = []
            for _, tag in matches:
                if tag not in seen:
                    seen.add(tag)
                    tags.append(tag)
            best_evidence = tags

    if best_class is None:
        return ("unknown", 0.0, [])

    return (best_class, round(best_confidence, 3), best_evidence)
