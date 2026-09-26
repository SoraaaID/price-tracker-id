"""Tests for utility functions: parse_price, parse_sold, retry."""

from __future__ import annotations

import pytest

from src.utils import parse_price, parse_sold


class TestParsePrice:
    """Test Indonesian price string parsing."""

    def test_standard_format(self):
        assert parse_price("Rp 1.234.567") == 1_234_567.0

    def test_no_space_after_rp(self):
        assert parse_price("Rp1.234.567") == 1_234_567.0

    def test_without_prefix(self):
        assert parse_price("1.234.567") == 1_234_567.0

    def test_juta_shorthand_comma(self):
        assert parse_price("1,2jt") == 1_200_000.0

    def test_juta_shorthand_dot(self):
        assert parse_price("1.5jt") == 1_500_000.0

    def test_juta_whole(self):
        assert parse_price("3jt") == 3_000_000.0

    def test_ribu_shorthand(self):
        assert parse_price("850rb") == 850_000.0

    def test_ribu_with_decimal(self):
        assert parse_price("1,5rb") == 1_500.0

    def test_simple_number(self):
        assert parse_price("999000") == 999_000.0

    def test_formatted_with_comma_decimal(self):
        assert parse_price("Rp 1.234,50") == 1_234.5

    def test_empty_string(self):
        assert parse_price("") == 0.0

    def test_none_equivalent(self):
        assert parse_price("   ") == 0.0

    def test_rp_dot_prefix(self):
        assert parse_price("Rp. 500.000") == 500_000.0

    def test_small_number(self):
        assert parse_price("Rp 50.000") == 50_000.0

    def test_garbage_input(self):
        assert parse_price("not a price") == 0.0


class TestParseSold:
    """Test Indonesian sold-count string parsing."""

    def test_ribu_with_terjual(self):
        assert parse_sold("1,2rb terjual") == 1200

    def test_ribu_dot_with_terjual(self):
        assert parse_sold("1.2rb terjual") == 1200

    def test_plus_suffix(self):
        assert parse_sold("500+ terjual") == 500

    def test_ribu_only(self):
        assert parse_sold("12,5rb") == 12500

    def test_juta(self):
        assert parse_sold("3jt terjual") == 3_000_000

    def test_plain_number(self):
        assert parse_sold("150") == 150

    def test_empty(self):
        assert parse_sold("") == 0

    def test_none_equivalent(self):
        assert parse_sold("   ") == 0
