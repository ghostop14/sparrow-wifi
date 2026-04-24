"""Settings loader for the sparrow_elastic package.

Reads configuration from an optional INI file (``[elastic]`` section) and
environment variable overrides.  Returns a plain dict — no class required.

Environment variables (all optional, override file values):
    SPARROW_ES_URL
    SPARROW_ES_USERNAME
    SPARROW_ES_PASSWORD
    SPARROW_ES_API_KEY
    SPARROW_ES_ENGINE
    SPARROW_FINGERBANK_API_KEY
"""

import configparser
import logging
import os
import socket
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict = {
    "engine": "elasticsearch",
    "url": "",
    "username": "",
    "password": "",
    "api_key": "",
    "verify_certs": True,
    "poll_interval_sec": 15.0,
    "flush_interval_sec": 5.0,
    "batch_size": 500,
    "fingerbank_api_key": "",
    "observer_id": "",          # filled at load time via socket.gethostname()
    "agent_host": "127.0.0.1",
    "agent_port": 8020,
    "wifi_interface": "",
    "wifi_alias": "sparrow-wifi",
    "bt_alias": "sparrow-bt",
}

# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

def _coerce(key: str, value: str) -> object:
    """Coerce *value* (a raw string from file or env) to the type implied by
    *key*'s default.  Returns the coerced value or raises ``ValueError``.
    """
    default = _DEFAULTS.get(key)
    if isinstance(default, bool):
        return value.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    # str (or unrecognised key — leave as str)
    return value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_settings(config_path: Optional[str] = None) -> dict:
    """Load settings from *config_path* (INI ``[elastic]`` section) and apply
    environment variable overrides.

    Args:
        config_path: Optional path to an INI config file.  Ignored if the file
            does not exist (a debug message is logged).

    Returns:
        A plain ``dict`` with all recognised settings keys populated.
    """
    cfg: dict = dict(_DEFAULTS)
    # Defer observer_id default to runtime so we get the real hostname.
    cfg["observer_id"] = socket.gethostname()

    # ------------------------------------------------------------------
    # 1. Read INI file
    # ------------------------------------------------------------------
    if config_path is not None:
        if os.path.isfile(config_path):
            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            section = "elastic"
            if parser.has_section(section):
                for key, raw in parser.items(section):
                    if key in cfg:
                        try:
                            cfg[key] = _coerce(key, raw)
                        except (ValueError, TypeError) as exc:
                            logger.warning(
                                "settings: ignoring bad value for '%s' in %s: %s",
                                key, config_path, exc,
                            )
                    else:
                        logger.debug(
                            "settings: unknown key '%s' in %s — ignored", key, config_path
                        )
            else:
                logger.debug(
                    "settings: no [elastic] section in %s", config_path
                )
        else:
            logger.debug("settings: config file not found: %s", config_path)

    # ------------------------------------------------------------------
    # 2. Environment variable overrides
    # ------------------------------------------------------------------
    env_map = {
        "SPARROW_ES_URL": "url",
        "SPARROW_ES_USERNAME": "username",
        "SPARROW_ES_PASSWORD": "password",
        "SPARROW_ES_API_KEY": "api_key",
        "SPARROW_ES_ENGINE": "engine",
        "SPARROW_FINGERBANK_API_KEY": "fingerbank_api_key",
    }
    for env_var, key in env_map.items():
        raw = os.environ.get(env_var)
        if raw is not None:
            try:
                cfg[key] = _coerce(key, raw)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "settings: ignoring bad env value for %s: %s", env_var, exc
                )

    return cfg
