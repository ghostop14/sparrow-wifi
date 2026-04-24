"""OpenSearch client implementation — backed by opensearch-py >= 2.

Import guard: this module imports ``opensearchpy`` at the top level.
If the package is not installed, importing this module will raise
``ModuleNotFoundError``.  The factory uses a lazy import to keep the
elasticsearch-only deployment path working without opensearch-py installed.
"""

from __future__ import annotations

import logging
from typing import Any

import opensearchpy
import opensearchpy.helpers

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
# OpenSearchClient
# ---------------------------------------------------------------------------

class OpenSearchClient(SearchClient):
    """Concrete SearchClient backed by ``opensearch-py >= 2``."""

    def __init__(self, settings: dict) -> None:
        url: str = settings.get("url", "")
        username: str = settings.get("username", "")
        password: str = settings.get("password", "")
        api_key: str = settings.get("api_key", "")
        verify_certs: bool = settings.get("verify_certs", True)

        if api_key:
            logger.warning(
                "API Key auth not supported by OpenSearch — falling back to no auth"
            )

        kwargs: dict[str, Any] = {
            "hosts": [url],
            "verify_certs": verify_certs,
            "use_ssl": url.startswith("https"),
            "timeout": 30,
        }

        if not verify_certs:
            _suppress_urllib3_warnings()
            kwargs["ssl_show_warn"] = False

        if username and password:
            kwargs["http_auth"] = (username, password)

        self._client = opensearchpy.OpenSearch(**kwargs)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except (opensearchpy.ConnectionError, opensearchpy.TransportError) as exc:
            logger.warning("OS ping failed: %s", exc)
            return False
        except Exception as exc:
            logger.warning("OS ping unexpected error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Lifecycle policy (ISM)
    # ------------------------------------------------------------------

    def ensure_policy(self, policy_name: str, policy_body: dict) -> None:
        """Create ISM policy only if absent — GET-then-PUT to avoid version conflicts.

        OpenSearch ISM requires a sequence_number + primary_term for updates,
        so rather than implement a read-modify-write, we skip the PUT if the
        policy already exists.  This is appropriate for initial bootstrap.
        """
        endpoint = f"/_plugins/_ism/policies/{policy_name}"
        try:
            # Check whether the policy already exists.
            self._client.transport.perform_request("GET", endpoint)
            logger.info("OS ISM policy '%s' already exists — skipping", policy_name)
            return
        except opensearchpy.NotFoundError:
            pass  # Policy absent — create it below.
        except Exception as exc:
            # Log but proceed: a failed GET shouldn't block the PUT attempt.
            logger.warning(
                "OS ISM policy GET '%s' failed (will attempt PUT): %s",
                policy_name, exc,
            )

        try:
            self._client.transport.perform_request("PUT", endpoint, body=policy_body)
            logger.info("OS ISM policy '%s' created", policy_name)
        except Exception as exc:
            logger.error("OS ensure_policy('%s') PUT failed: %s", policy_name, exc)
            raise

    # ------------------------------------------------------------------
    # Index template
    # ------------------------------------------------------------------

    def ensure_template(self, template_name: str, template_body: dict) -> None:
        """PUT composable index template — idempotent upsert.

        The caller is expected to have inlined any component template content
        before calling this method; OpenSearch does not reliably support
        component composition in older versions.
        """
        try:
            self._client.indices.put_index_template(
                name=template_name, body=template_body
            )
            logger.info("OS index template '%s' applied", template_name)
        except Exception as exc:
            logger.error("OS ensure_template('%s') failed: %s", template_name, exc)
            raise

    # ------------------------------------------------------------------
    # Initial index + write alias
    # ------------------------------------------------------------------

    def ensure_initial_index(self, alias: str, initial_index: str) -> None:
        """Ensure the write alias exists; create initial_index if not."""
        try:
            if self._client.indices.exists_alias(alias):
                logger.debug("OS alias '%s' already exists — skipping index create", alias)
                return
        except Exception as exc:
            logger.warning("OS exists_alias('%s') failed: %s", alias, exc)
            raise

        try:
            self._client.indices.create(
                index=initial_index,
                body={"aliases": {alias: {"is_write_index": True}}},
            )
            logger.info(
                "OS initial index '%s' with write alias '%s' created",
                initial_index, alias,
            )
        except opensearchpy.RequestError as exc:
            error_type = ""
            try:
                error_type = exc.info.get("error", {}).get("type", "")  # type: ignore[union-attr]
            except Exception:
                error_type = str(exc).lower()
            status = getattr(exc, "status_code", 0)
            if status == 400 and "resource_already_exists" in error_type:
                logger.info(
                    "OS index '%s' already exists (race condition — OK)",
                    initial_index,
                )
                return
            logger.error(
                "OS ensure_initial_index('%s', '%s') failed: %s",
                alias, initial_index, exc,
            )
            raise
        except Exception as exc:
            logger.error(
                "OS ensure_initial_index('%s', '%s') unexpected error: %s",
                alias, initial_index, exc,
            )
            raise

    # ------------------------------------------------------------------
    # Bulk
    # ------------------------------------------------------------------

    def bulk(self, actions: list[dict]) -> tuple[int, list[dict]]:
        """Bulk index actions.  Returns (success_count, errors_list)."""
        success, errors = opensearchpy.helpers.bulk(
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
