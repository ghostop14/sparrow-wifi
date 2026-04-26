"""Tests for sparrow_elastic.ble_adv_parser (scaffold / stub)."""

import pytest
from sparrow_elastic.ble_adv_parser import parse_adv_payload


class TestParseAdvPayload:
    """Stub tests -- real parser is pending agent extension."""

    def test_none_returns_empty(self):
        assert parse_adv_payload(None) == {}

    def test_empty_string_returns_empty(self):
        assert parse_adv_payload("") == {}

    def test_valid_hex_returns_empty_until_parser_implemented(self):
        # Parser is a stub: any non-empty hex input also returns {}
        assert parse_adv_payload("deadbeef") == {}

    def test_longer_payload_returns_empty(self):
        assert parse_adv_payload("0201061a09" + "41" * 20) == {}
