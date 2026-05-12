"""
config_utils.decode_api_key / encode_api_key の単体テスト
"""
import base64
from config_utils import decode_api_key, encode_api_key  # まだ存在しないのでエラー


def test_decode_b64_valid():
    raw = "b64:" + base64.b64encode(b"sk-test-key").decode()
    assert decode_api_key(raw) == "sk-test-key"


def test_decode_plain():
    assert decode_api_key("sk-plaintext") == "sk-plaintext"


def test_decode_empty():
    assert decode_api_key("") == ""


def test_decode_none():
    assert decode_api_key(None) == ""


def test_decode_invalid_b64(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        result = decode_api_key("b64:!!!invalid!!!")
    assert result == "!!!invalid!!!"


def test_decode_b64_prefix_only():
    assert decode_api_key("b64:") == ""


def test_encode_plain():
    expected = "b64:" + base64.b64encode(b"sk-test-key").decode()
    assert encode_api_key("sk-test-key") == expected


def test_encode_empty():
    assert encode_api_key("") == ""


def test_encode_none():
    assert encode_api_key(None) == ""


def test_roundtrip():
    original = "FAKE-TEST-KEY-0000-0000-aaaaaaaaaaaa:fx"
    assert decode_api_key(encode_api_key(original)) == original
