"""SearchClient ABC — abstract base class for Elasticsearch and OpenSearch clients.

Both concrete implementations (ElasticsearchClient, OpenSearchClient) implement
this interface so the rest of sparrow_elastic never touches the underlying
library directly.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class SearchClient(ABC):
    """Thin abstraction over the Elasticsearch / OpenSearch Python clients.

    Subclasses handle import-time differences, ILM vs ISM, and auth
    mechanisms so that callers never touch the concrete client library.
    """

    @abstractmethod
    def ping(self) -> bool:
        """Return True if the cluster responds to a lightweight probe."""

    @abstractmethod
    def ensure_policy(self, policy_name: str, policy_body: dict) -> None:
        """Create or update a lifecycle policy idempotently.

        Elasticsearch: ILM via PUT _ilm/policy/{name}.
        OpenSearch: ISM via PUT _plugins/_ism/policies/{name}.

        This is a PUT (upsert) — safe to call repeatedly.
        """

    @abstractmethod
    def ensure_template(self, template_name: str, template_body: dict) -> None:
        """Create or update a composable index template via PUT _index_template.

        For OpenSearch, the caller is expected to have already inlined any
        component template content — OpenSearch does not reliably support
        component composition in older versions.
        """

    @abstractmethod
    def ensure_initial_index(self, alias: str, initial_index: str) -> None:
        """Ensure the write alias exists, creating the initial index if absent.

        Algorithm:
            1. If the alias already exists: return immediately (no-op).
            2. Otherwise: create *initial_index* with is_write_index=True on
               *alias*.
            3. A 400/resource_already_exists_exception is treated as success
               (multi-sensor race condition tolerance).
        """

    @abstractmethod
    def bulk(self, actions: list[dict]) -> tuple[int, list[dict]]:
        """Execute a bulk index request.

        Returns ``(success_count, errors_list)``.  Individual-document failures
        are returned in *errors_list* rather than raised as exceptions, so the
        caller can inspect and retry selectively.  A connection-level failure
        may still raise.
        """

    @abstractmethod
    def close(self) -> None:
        """Release any transport-level resources.

        Safe to call more than once and safe to call even if the client was
        never successfully connected.
        """
