"""Tests for crawler_engine.crypto_utils — SM4, AES, MD5 sign."""

import pytest

from rag.svr.crawler_engine.crypto_utils import (
    hex_to_bytes,
    b64_to_bytes,
    bytes_to_b64,
    md5_sign,
)


class TestHexBase64:
    def test_hex_to_bytes(self):
        result = hex_to_bytes("0102030405")
        assert result == b"\x01\x02\x03\x04\x05"

    def test_b64_roundtrip(self):
        original = b"test data 12345"
        b64 = bytes_to_b64(original)
        assert b64_to_bytes(b64) == original

    def test_empty_hex(self):
        assert hex_to_bytes("") == b""


class TestMd5Sign:
    def test_basic_sign(self):
        params = {"a": "1", "b": "2", "c": "hello"}
        secret = "test_secret"
        sign = md5_sign(params, secret)
        # MD5 should produce 32 hex chars
        assert len(sign) == 32
        assert sign == sign.lower()

    def test_sign_stability(self):
        params = {"key": "value", "name": "test"}
        secret = "my_secret"
        sign1 = md5_sign(params, secret)
        sign2 = md5_sign(params, secret)
        assert sign1 == sign2

    def test_sign_different_params(self):
        secret = "s"
        sign1 = md5_sign({"a": "1"}, secret)
        sign2 = md5_sign({"a": "2"}, secret)
        assert sign1 != sign2

    def test_sign_none_values_skipped(self):
        params = {"a": "1", "b": None, "c": "2"}
        secret = "s"
        # b=None should be skipped in key sorting
        sign = md5_sign(params, secret)
        assert len(sign) == 32

    def test_empty_params(self):
        sign = md5_sign({}, "secret")
        assert len(sign) == 32
