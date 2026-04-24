"""sparrow_elastic.templates — template/component loading and OpenSearch inlining.

Public API
----------
load_template(name)             Load a top-level template JSON file by name.
load_component(base, name)      Load a component JSON file from a named sub-directory.
resolve_template(name, ...)     Return a template dict, optionally with components inlined
                                (required for OpenSearch which lacks reliable composition).
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package-root path resolution
# ---------------------------------------------------------------------------
# Use the directory that contains THIS __init__.py as the root for all file
# lookups.  This works from an editable source checkout AND from an installed
# package (where the JSON files are installed as package data next to the .py).
_TEMPLATES_DIR: Path = Path(__file__).parent


# ---------------------------------------------------------------------------
# Low-level loaders
# ---------------------------------------------------------------------------

def load_template(name: str) -> dict:
    """Load a composable index template from ``templates/{name}.json``.

    Args:
        name: Bare filename without the ``.json`` suffix, e.g.
            ``"sparrow-wifi-template"``.

    Returns:
        The parsed JSON dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    path = _TEMPLATES_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Template file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_component(base: str, name: str) -> dict:
    """Load a component JSON from ``templates/{base}-components/{name}.json``.

    Args:
        base: The index-family prefix, e.g. ``"sparrow-wifi"``.
        name: The component role, e.g. ``"settings"`` or ``"mappings"``.

    Returns:
        The parsed JSON dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    path = _TEMPLATES_DIR / f"{base}-components" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Component file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Deep-merge helper
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into *base* (in-place on *base*).

    Dict values at the same key are merged recursively; all other types are
    overwritten by the overlay value.  Returns *base* for convenience.
    """
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


# ---------------------------------------------------------------------------
# Template resolver
# ---------------------------------------------------------------------------

def resolve_template(name: str, for_opensearch: bool = False) -> dict:
    """Load a composable template and, optionally, inline its component bodies.

    For Elasticsearch the server handles component composition natively, so the
    template is returned unchanged (``composed_of`` intact).

    For OpenSearch, older versions do not reliably support the ``composed_of``
    composition mechanism.  When ``for_opensearch=True`` this function:

    1. Iterates the ``composed_of`` list in the template.
    2. Derives the component base-prefix from the template name
       (``sparrow-wifi-template`` → ``sparrow-wifi``).
    3. Strips that prefix from each component name to get the role filename
       (``sparrow-wifi-settings`` → ``settings``, ``sparrow-wifi-mappings`` → ``mappings``).
    4. Deep-merges each component's ``template.*`` subtree into the result.
    5. Drops the ``composed_of`` key from the returned dict.

    Args:
        name:           Bare template name (no ``.json``).
        for_opensearch: When True, inline component bodies and drop
                        ``composed_of``.

    Returns:
        A template dict ready to submit to the cluster.

    Raises:
        FileNotFoundError: If the template or any referenced component file is
            missing.
    """
    template = copy.deepcopy(load_template(name))

    if not for_opensearch:
        return template

    composed_of: list[str] = template.pop("composed_of", [])
    if not composed_of:
        # Nothing to inline — return as-is.
        return template

    # Derive the base prefix from the template file name.
    # e.g. "sparrow-wifi-template" -> "sparrow-wifi"
    base = name.rsplit("-template", 1)[0]

    # Ensure a top-level "template" key exists to merge into.
    if "template" not in template:
        template["template"] = {}

    for component_full_name in composed_of:
        # Strip "sparrow-wifi-" prefix to get the role ("settings", "mappings").
        prefix_to_strip = base + "-"
        if component_full_name.startswith(prefix_to_strip):
            role = component_full_name[len(prefix_to_strip):]
        else:
            # Fallback: use the full name as the filename.
            role = component_full_name

        component = load_component(base, role)

        # The component JSON wraps its content under "template".
        component_template: dict[str, Any] = component.get("template", {})
        _deep_merge(template["template"], component_template)

        logger.debug(
            "resolve_template: inlined component '%s' (role=%s) into '%s'",
            component_full_name, role, name,
        )

    return template
