"""Tests for sparrow_elastic.client_factory.build_client.

All tests are mocked — no live cluster required.  If elasticsearch or
opensearch-py is not installed the relevant tests are skipped.
"""

from __future__ import annotations

import importlib.util
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Skip markers
# ---------------------------------------------------------------------------

ES_AVAILABLE = importlib.util.find_spec("elasticsearch") is not None
OS_AVAILABLE = importlib.util.find_spec("opensearchpy") is not None

skip_no_es = pytest.mark.skipif(
    not ES_AVAILABLE, reason="elasticsearch package not installed"
)
skip_no_os = pytest.mark.skipif(
    not OS_AVAILABLE, reason="opensearch-py package not installed"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_no_es
def test_build_client_elasticsearch_returns_es_instance():
    """build_client with engine='elasticsearch' returns ElasticsearchClient."""
    from sparrow_elastic.elasticsearch_client import ElasticsearchClient

    with patch("elasticsearch.Elasticsearch") as mock_es_cls:
        mock_es_cls.return_value = MagicMock()
        from sparrow_elastic.client_factory import build_client
        client = build_client({"engine": "elasticsearch", "url": "http://localhost:9200"})

    assert isinstance(client, ElasticsearchClient)


@skip_no_os
def test_build_client_opensearch_returns_os_instance():
    """build_client with engine='opensearch' returns OpenSearchClient."""
    from sparrow_elastic.opensearch_client import OpenSearchClient

    with patch("opensearchpy.OpenSearch") as mock_os_cls:
        mock_os_cls.return_value = MagicMock()
        from sparrow_elastic.client_factory import build_client
        client = build_client({"engine": "opensearch", "url": "http://localhost:9200"})

    assert isinstance(client, OpenSearchClient)


def test_build_client_bogus_raises_value_error():
    """build_client with an unknown engine name raises ValueError."""
    from sparrow_elastic.client_factory import build_client

    with pytest.raises(ValueError, match="bogus"):
        build_client({"engine": "bogus"})


@skip_no_es
def test_build_client_default_engine_is_elasticsearch():
    """build_client with no engine key defaults to elasticsearch."""
    from sparrow_elastic.elasticsearch_client import ElasticsearchClient

    with patch("elasticsearch.Elasticsearch") as mock_es_cls:
        mock_es_cls.return_value = MagicMock()
        from sparrow_elastic.client_factory import build_client
        client = build_client({"url": "http://localhost:9200"})

    assert isinstance(client, ElasticsearchClient)
