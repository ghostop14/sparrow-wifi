"""Elasticsearch client implementation — backed by elasticsearch-py >= 8.

Import guard: this module imports ``elasticsearch`` at the top level.
If the package is not installed, importing this module will raise
``ModuleNotFoundError``.  The factory uses a lazy import to keep the
opensearch-only deployment path working without elasticsearch installed.
"""

from __future__ import annotations

import logging
from typing import Any

import elasticsearch
import elasticsearch.helpers

from .search_client import SearchClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level urllib3 warning suppression (idempotent)
# ---------------------------------------------------------------------------

_URLLIB3_WARNINGS_SUPPRESSED = False


def _suppress_urllib3_warnings() -> None:
    """Suppress InsecureRequestWarning once (idempotent, module-global)."""
    global _URLLIB3_WARNINGS_SUPPRESSED
    if not _URLLIB3_WARNINGS_SUPPRESSED:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _URLLIB3_WARNINGS_SUPPRESSED = True


# ---------------------------------------------------------------------------
# ElasticsearchClient
# ---------------------------------------------------------------------------

class ElasticsearchClient(SearchClient):
    """Concrete SearchClient backed by ``elasticsearch-py >= 8``."""

    def __init__(self, settings: dict) -> None:
        url: str = settings.get("url", "")
        username: str = settings.get("username", "")
        password: str = settings.get("password", "")
        api_key: str = settings.get("api_key", "")
        verify_certs: bool = settings.get("verify_certs", True)

        kwargs: dict[str, Any] = {
            "hosts": [url],
            "verify_certs": verify_certs,
            "request_timeout": 30,
        }

        if not verify_certs:
            _suppress_urllib3_warnings()
            kwargs["ssl_show_warn"] = False

        if api_key:
            kwargs["api_key"] = api_key
        elif username and password:
            kwargs["basic_auth"] = (username, password)

        self._client = elasticsearch.Elasticsearch(**kwargs)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except (elasticsearch.ConnectionError, elasticsearch.TransportError) as exc:
            logger.warning("ES ping failed: %s", exc)
            return False
        except Exception as exc:
            logger.warning("ES ping unexpected error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Lifecycle policy (ILM)
    # ------------------------------------------------------------------

    def ensure_policy(self, policy_name: str, policy_body: dict) -> None:
        """PUT ILM policy — idempotent upsert.

        elasticsearch-py 8.x: ``ilm.put_lifecycle(name=..., policy=...)``
        where *policy* is the inner "policy" dict from the ILM JSON body.
        """
        try:
            inner_policy = policy_body.get("policy", policy_body)
            self._client.ilm.put_lifecycle(name=policy_name, policy=inner_policy)
            logger.info("ES ILM policy '%s' applied", policy_name)
        except Exception as exc:
            logger.error("ES ensure_policy('%s') failed: %s", policy_name, exc)
            raise

    # ------------------------------------------------------------------
    # Index template
    # ------------------------------------------------------------------

    def ensure_template(self, template_name: str, template_body: dict) -> None:
        """PUT composable index template — idempotent upsert."""
        try:
            self._client.indices.put_index_template(
                name=template_name, body=template_body
            )
            logger.info("ES index template '%s' applied", template_name)
        except Exception as exc:
            logger.error("ES ensure_template('%s') failed: %s", template_name, exc)
            raise

    # ------------------------------------------------------------------
    # Initial index + write alias
    # ------------------------------------------------------------------

    def ensure_initial_index(self, alias: str, initial_index: str) -> None:
        """Ensure the write alias exists; create initial_index if not."""
        try:
            if self._client.indices.exists_alias(name=alias):
                logger.debug("ES alias '%s' already exists — skipping index create", alias)
                return
        except Exception as exc:
            logger.warning("ES exists_alias('%s') failed: %s", alias, exc)
            raise

        try:
            self._client.indices.create(
                index=initial_index,
                body={"aliases": {alias: {"is_write_index": True}}},
            )
            logger.info(
                "ES initial index '%s' with write alias '%s' created",
                initial_index, alias,
            )
        except elasticsearch.ApiError as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "meta", {})
            if hasattr(exc, "meta"):
                status = exc.meta.status  # type: ignore[union-attr]
            error_type = ""
            try:
                error_type = exc.body.get("error", {}).get("type", "")  # type: ignore[union-attr]
            except Exception:
                error_type = str(exc).lower()
            if status == 400 and "resource_already_exists" in error_type:
                logger.info(
                    "ES index '%s' already exists (race condition — OK)",
                    initial_index,
                )
                return
            logger.error(
                "ES ensure_initial_index('%s', '%s') failed: %s",
                alias, initial_index, exc,
            )
            raise
        except Exception as exc:
            logger.error(
                "ES ensure_initial_index('%s', '%s') unexpected error: %s",
                alias, initial_index, exc,
            )
            raise

    # ------------------------------------------------------------------
    # Bulk
    # ------------------------------------------------------------------

    def bulk(self, actions: list[dict]) -> tuple[int, list[dict]]:
        """Bulk index actions.  Returns (success_count, errors_list)."""
        success, errors = elasticsearch.helpers.bulk(
            self._client,
            actions,
            chunk_size=500,
            raise_on_error=False,
            raise_on_exception=False,
        )
        return success, errors if isinstance(errors, list) else []

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._client.transport.close()
        except Exception:
            pass
