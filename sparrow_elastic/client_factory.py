"""Client factory for sparrow_elastic.

Returns the correct SearchClient subclass based on the ``engine`` key in the
settings dict.  Imports are lazy (inside the branch) so that a deployment with
only ``elasticsearch`` installed still works without ``opensearch-py``, and
vice versa.
"""

from __future__ import annotations

from .search_client import SearchClient


def build_client(settings: dict) -> SearchClient:
    """Construct and return the appropriate SearchClient.

    Args:
        settings: A settings dict as returned by ``load_settings()``.
            The ``engine`` key selects the backend; defaults to
            ``"elasticsearch"`` when absent.

    Returns:
        An :class:`~sparrow_elastic.search_client.SearchClient` instance
        ready to use (no connection is made at construction time).

    Raises:
        ValueError: If ``engine`` is not ``"elasticsearch"`` or
            ``"opensearch"``.
        ModuleNotFoundError: If the required client library is not installed
            (e.g. ``opensearch-py`` missing for the opensearch engine).
    """
    engine = settings.get("engine", "elasticsearch").lower()

    if engine == "elasticsearch":
        from .elasticsearch_client import ElasticsearchClient
        return ElasticsearchClient(settings)

    if engine == "opensearch":
        from .opensearch_client import OpenSearchClient
        return OpenSearchClient(settings)

    raise ValueError(
        f"Unknown engine: {engine!r}. Expected 'elasticsearch' or 'opensearch'."
    )
