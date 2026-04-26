"""Tests for sparrow_elastic.opensearch_client.OpenSearchClient.

All tests use a patched opensearchpy.OpenSearch constructor — no live
cluster required.  Skipped entirely if the opensearch-py package is absent.
"""

from __future__ import annotations

import importlib.util
import logging
import pytest
from unittest.mock import MagicMock, patch

OS_AVAILABLE = importlib.util.find_spec("opensearchpy") is not None

pytestmark = pytest.mark.skipif(
    not OS_AVAILABLE, reason="opensearch-py package not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(settings: dict | None = None):
    """Return an OpenSearchClient with a patched underlying OpenSearch."""
    from sparrow_elastic.opensearch_client import OpenSearchClient
    base_settings = {"url": "http://localhost:9200", "verify_certs": True}
    if settings:
        base_settings.update(settings)
    with patch("opensearchpy.OpenSearch") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        client = OpenSearchClient(base_settings)
    client._client = mock_instance
    return client, mock_instance


# ---------------------------------------------------------------------------
# API key warning + fallback
# ---------------------------------------------------------------------------

class TestApiKeyFallback:
    def test_api_key_logs_warning_and_no_http_auth(self, caplog):
        """If api_key is set, a WARN is logged and http_auth is NOT passed."""
        with caplog.at_level(logging.WARNING, logger="sparrow_elastic.opensearch_client"):
            with patch("opensearchpy.OpenSearch") as mock_cls:
                mock_cls.return_value = MagicMock()
                from sparrow_elastic.opensearch_client import OpenSearchClient
                OpenSearchClient({
                    "url": "http://localhost:9200",
                    "api_key": "supersecret",
                    "username": "",
                    "password": "",
                    "verify_certs": True,
                })
            _, kwargs = mock_cls.call_args

        assert any("falling back to no auth" in r.message for r in caplog.records)
        assert "http_auth" not in kwargs


# ---------------------------------------------------------------------------
# ping()
# ---------------------------------------------------------------------------

class TestPing:
    def test_ping_returns_true(self):
        client, mock_os = _make_client()
        mock_os.ping.return_value = True
        assert client.ping() is True

    def test_ping_returns_false_on_connection_error(self):
        import opensearchpy
        client, mock_os = _make_client()
        mock_os.ping.side_effect = opensearchpy.ConnectionError("refused", {}, None)
        assert client.ping() is False


# ---------------------------------------------------------------------------
# ensure_policy() — GET-then-PUT pattern
# ---------------------------------------------------------------------------

class TestEnsurePolicy:
    def test_skips_put_when_policy_exists(self):
        """If GET returns 200 (no exception), PUT is not called."""
        client, mock_os = _make_client()
        # GET succeeds → policy already exists
        mock_os.transport.perform_request.return_value = {"policy": {}}

        client.ensure_policy("my-policy", {"policy": {"description": "test"}})

        # Only GET called, not PUT
        mock_os.transport.perform_request.assert_called_once_with(
            "GET", "/_plugins/_ism/policies/my-policy"
        )

    def test_puts_policy_when_not_found(self):
        """If GET raises NotFoundError, PUT is called."""
        import opensearchpy
        client, mock_os = _make_client()

        not_found = opensearchpy.NotFoundError(
            message="policy not found",
            meta=MagicMock(status=404),
            body={"error": "not found"},
        )
        put_response = {"policy": {"policy_id": "my-policy"}}

        mock_os.transport.perform_request.side_effect = [
            not_found,   # GET → not found
            put_response,  # PUT → success
        ]

        body = {"policy": {"description": "my policy"}}
        client.ensure_policy("my-policy", body)

        assert mock_os.transport.perform_request.call_count == 2
        calls = mock_os.transport.perform_request.call_args_list
        assert calls[0][0] == ("GET", "/_plugins/_ism/policies/my-policy")
        assert calls[1][0] == ("PUT", "/_plugins/_ism/policies/my-policy")
        assert calls[1][1]["body"] == body


# ---------------------------------------------------------------------------
# ensure_initial_index()
# ---------------------------------------------------------------------------

class TestEnsureInitialIndex:
    def test_skips_create_when_alias_exists(self):
        client, mock_os = _make_client()
        mock_os.indices.exists_alias.return_value = True

        client.ensure_initial_index("my-alias", "my-index-000001")

        mock_os.indices.create.assert_not_called()

    def test_calls_create_when_alias_absent(self):
        client, mock_os = _make_client()
        mock_os.indices.exists_alias.return_value = False
        mock_os.indices.create.return_value = {"acknowledged": True}

        client.ensure_initial_index("my-alias", "my-index-000001")

        mock_os.indices.create.assert_called_once_with(
            index="my-index-000001",
            body={"aliases": {"my-alias": {"is_write_index": True}}},
        )

    def test_does_not_reraise_on_400_resource_already_exists(self):
        import opensearchpy
        client, mock_os = _make_client()
        mock_os.indices.exists_alias.return_value = False

        exc = opensearchpy.RequestError(
            status_code=400,
            error="resource_already_exists_exception",
            info={"error": {"type": "resource_already_exists_exception"}},
        )
        mock_os.indices.create.side_effect = exc

        client.ensure_initial_index("my-alias", "my-index-000001")  # Should not raise


# ---------------------------------------------------------------------------
# bulk()
# ---------------------------------------------------------------------------

class TestBulk:
    def test_bulk_returns_helpers_result(self):
        client, mock_os = _make_client()
        actions = [{"_index": "test", "_id": "1", "_source": {"f": "v"}}]

        with patch("opensearchpy.helpers.bulk", return_value=(1, [])):
            success, errors = client.bulk(actions)

        assert success == 1
        assert errors == []

    def test_bulk_passes_chunk_size_and_flags(self):
        client, mock_os = _make_client()

        with patch("opensearchpy.helpers.bulk", return_value=(0, [])) as mock_bulk:
            client.bulk([])

        _, kwargs = mock_bulk.call_args
        assert kwargs.get("chunk_size") == 500
        assert kwargs.get("raise_on_error") is False
        assert kwargs.get("raise_on_exception") is False


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_does_not_raise_when_transport_close_succeeds(self):
        client, mock_os = _make_client()
        mock_os.transport.close.return_value = None
        client.close()

    def test_close_does_not_raise_when_transport_close_raises(self):
        client, mock_os = _make_client()
        mock_os.transport.close.side_effect = RuntimeError("no close")
        client.close()  # Should swallow
