"""Tests for sparrow_elastic.elasticsearch_client.ElasticsearchClient.

All tests use a patched elasticsearch.Elasticsearch constructor — no live
cluster required.  Skipped entirely if the elasticsearch package is absent.
"""

from __future__ import annotations

import importlib.util
import logging
import pytest
from unittest.mock import MagicMock, patch, call

ES_AVAILABLE = importlib.util.find_spec("elasticsearch") is not None

pytestmark = pytest.mark.skipif(
    not ES_AVAILABLE, reason="elasticsearch package not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(settings: dict | None = None):
    """Return an ElasticsearchClient with a patched underlying Elasticsearch."""
    from sparrow_elastic.elasticsearch_client import ElasticsearchClient
    base_settings = {"url": "http://localhost:9200", "verify_certs": True}
    if settings:
        base_settings.update(settings)
    with patch("elasticsearch.Elasticsearch") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        client = ElasticsearchClient(base_settings)
    # Replace the internal client with the mock instance for test assertions
    client._client = mock_instance
    return client, mock_instance


# ---------------------------------------------------------------------------
# ping()
# ---------------------------------------------------------------------------

class TestPing:
    def test_ping_returns_true_when_client_ping_true(self):
        client, mock_es = _make_client()
        mock_es.ping.return_value = True
        assert client.ping() is True

    def test_ping_returns_false_when_client_ping_false(self):
        client, mock_es = _make_client()
        mock_es.ping.return_value = False
        assert client.ping() is False

    def test_ping_returns_false_on_connection_error(self):
        import elasticsearch
        client, mock_es = _make_client()
        mock_es.ping.side_effect = elasticsearch.ConnectionError("refused", {}, None)
        assert client.ping() is False

    def test_ping_returns_false_on_transport_error(self):
        import elasticsearch
        client, mock_es = _make_client()
        mock_es.ping.side_effect = elasticsearch.TransportError("transport err", {}, None)
        assert client.ping() is False


# ---------------------------------------------------------------------------
# ensure_initial_index()
# ---------------------------------------------------------------------------

class TestEnsureInitialIndex:
    def test_skips_create_when_alias_exists(self):
        client, mock_es = _make_client()
        mock_es.indices.exists_alias.return_value = True

        client.ensure_initial_index("my-alias", "my-index-000001")

        mock_es.indices.exists_alias.assert_called_once_with(name="my-alias")
        mock_es.indices.create.assert_not_called()

    def test_calls_create_when_alias_absent(self):
        client, mock_es = _make_client()
        mock_es.indices.exists_alias.return_value = False
        mock_es.indices.create.return_value = {"acknowledged": True}

        client.ensure_initial_index("my-alias", "my-index-000001")

        mock_es.indices.create.assert_called_once_with(
            index="my-index-000001",
            body={"aliases": {"my-alias": {"is_write_index": True}}},
        )

    def test_does_not_reraise_on_400_resource_already_exists(self):
        import elasticsearch

        client, mock_es = _make_client()
        mock_es.indices.exists_alias.return_value = False

        # Build a realistic ApiError with resource_already_exists_exception
        meta = MagicMock()
        meta.status = 400
        exc = elasticsearch.ApiError(
            message="resource_already_exists_exception",
            meta=meta,
            body={"error": {"type": "resource_already_exists_exception"}},
        )
        mock_es.indices.create.side_effect = exc

        # Should not raise
        client.ensure_initial_index("my-alias", "my-index-000001")

    def test_reraises_on_unexpected_api_error(self):
        import elasticsearch

        client, mock_es = _make_client()
        mock_es.indices.exists_alias.return_value = False

        meta = MagicMock()
        meta.status = 500
        exc = elasticsearch.ApiError(
            message="internal server error",
            meta=meta,
            body={"error": {"type": "internal_server_error"}},
        )
        mock_es.indices.create.side_effect = exc

        with pytest.raises(elasticsearch.ApiError):
            client.ensure_initial_index("my-alias", "my-index-000001")


# ---------------------------------------------------------------------------
# bulk()
# ---------------------------------------------------------------------------

class TestBulk:
    def test_bulk_returns_helpers_result(self):
        client, mock_es = _make_client()
        actions = [{"_index": "test", "_id": "1", "_source": {"field": "val"}}]

        with patch("elasticsearch.helpers.bulk", return_value=(1, [])) as mock_bulk:
            success, errors = client.bulk(actions)

        assert success == 1
        assert errors == []
        mock_bulk.assert_called_once()

    def test_bulk_passes_chunk_size_and_flags(self):
        client, mock_es = _make_client()
        actions = [{"_index": "test", "_id": "1", "_source": {}}]

        with patch("elasticsearch.helpers.bulk", return_value=(1, [])) as mock_bulk:
            client.bulk(actions)

        _, kwargs = mock_bulk.call_args
        assert kwargs.get("chunk_size") == 500
        assert kwargs.get("raise_on_error") is False
        assert kwargs.get("raise_on_exception") is False

    def test_bulk_returns_empty_errors_list_when_helpers_returns_empty(self):
        client, mock_es = _make_client()

        with patch("elasticsearch.helpers.bulk", return_value=(5, [])):
            success, errors = client.bulk([])

        assert success == 5
        assert errors == []


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_does_not_raise_when_transport_close_succeeds(self):
        client, mock_es = _make_client()
        mock_es.transport.close.return_value = None
        client.close()  # Should not raise

    def test_close_does_not_raise_when_transport_close_raises(self):
        client, mock_es = _make_client()
        mock_es.transport.close.side_effect = RuntimeError("no close method")
        client.close()  # Should swallow the exception


# ---------------------------------------------------------------------------
# Auth construction
# ---------------------------------------------------------------------------

class TestAuthConstruction:
    def test_api_key_used_when_set(self):
        with patch("elasticsearch.Elasticsearch") as mock_cls:
            mock_cls.return_value = MagicMock()
            from sparrow_elastic.elasticsearch_client import ElasticsearchClient
            ElasticsearchClient({
                "url": "http://localhost:9200",
                "api_key": "mykey",
                "username": "",
                "password": "",
                "verify_certs": True,
            })
        _, kwargs = mock_cls.call_args
        assert kwargs.get("api_key") == "mykey"
        assert "basic_auth" not in kwargs

    def test_basic_auth_used_when_username_and_password_set(self):
        with patch("elasticsearch.Elasticsearch") as mock_cls:
            mock_cls.return_value = MagicMock()
            from sparrow_elastic.elasticsearch_client import ElasticsearchClient
            ElasticsearchClient({
                "url": "http://localhost:9200",
                "api_key": "",
                "username": "elastic",
                "password": "secret",
                "verify_certs": True,
            })
        _, kwargs = mock_cls.call_args
        assert kwargs.get("basic_auth") == ("elastic", "secret")
        assert "api_key" not in kwargs

    def test_no_auth_when_neither_set(self):
        with patch("elasticsearch.Elasticsearch") as mock_cls:
            mock_cls.return_value = MagicMock()
            from sparrow_elastic.elasticsearch_client import ElasticsearchClient
            ElasticsearchClient({
                "url": "http://localhost:9200",
                "api_key": "",
                "username": "",
                "password": "",
                "verify_certs": True,
            })
        _, kwargs = mock_cls.call_args
        assert "api_key" not in kwargs
        assert "basic_auth" not in kwargs
