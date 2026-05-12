"""
_save_api_keys_to_config の単体テスト。
実際の config.yaml は一切触らず、temp ファイルを使う。
"""
import os
import tempfile
import textwrap
from pathlib import Path

import sys
import yaml

# プロジェクトルートを sys.path に追加してインポート可能にする
sys.path.insert(0, str(Path(__file__).parent.parent))

from config_utils import decode_api_key
from app import _save_api_keys_to_config


# ---------------------------------------------------------------------------
# テスト用サンプル config.yaml テキスト
# ---------------------------------------------------------------------------

SAMPLE_CONFIG = textwrap.dedent("""\
    openai:
      api_key: "sk-old-key"

    deepl:
      api_key: "old-deepl-key"  # 例: "xxxxxxxx-xxxx-xxxx"

    translation:
      target_language: "日本語"
""")


def _make_temp_config(content: str) -> Path:
    """一時ファイルに content を書き込んで Path を返す。"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# テストケース
# ---------------------------------------------------------------------------

def test_openai_key_replaced():
    """OpenAI キーが b64: 形式で上書きされること。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        assert _save_api_keys_to_config("sk-new-key", "", target_path=p)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["openai"]["api_key"]) == "sk-new-key"
    finally:
        os.unlink(p)


def test_deepl_key_replaced():
    """DeepL キーが b64: 形式で上書きされること。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        assert _save_api_keys_to_config("", "new-deepl-key", target_path=p)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["deepl"]["api_key"]) == "new-deepl-key"
    finally:
        os.unlink(p)


def test_both_keys_replaced():
    """OpenAI と DeepL の両方が同時に置換されること。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        assert _save_api_keys_to_config("sk-both-openai", "both-deepl", target_path=p)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["openai"]["api_key"]) == "sk-both-openai"
        assert decode_api_key(data["deepl"]["api_key"]) == "both-deepl"
    finally:
        os.unlink(p)


def test_empty_key_not_replaced():
    """空文字を渡したセクションはそのまま変更されないこと。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        assert _save_api_keys_to_config("", "", target_path=p)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert data["openai"]["api_key"] == "sk-old-key"
        assert data["deepl"]["api_key"] == "old-deepl-key"
    finally:
        os.unlink(p)


def test_yaml_still_valid_after_write():
    """書き換え後も yaml.safe_load が成功すること（YAML 破壊なし）。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        _save_api_keys_to_config("sk-valid-test", "deepl-valid", target_path=p)
        # 例外なくロードできれば OK
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert "openai" in data
        assert "deepl" in data
        assert "translation" in data  # 無関係セクションが消えていないこと
    finally:
        os.unlink(p)


def test_comment_preserved():
    """既存のインラインコメントが保持されること。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        _save_api_keys_to_config("", "new-deepl-with-comment-test", target_path=p)
        content = p.read_text(encoding="utf-8")
        # コメント文字列が残っていること
        assert "# 例:" in content
    finally:
        os.unlink(p)


def test_b64_roundtrip_in_yaml():
    """書き込まれたキーが encode → decode のラウンドトリップを通ること。"""
    original_key = "FAKE-TEST-KEY-0000-0000-aaaaaaaaaaaa:fx"
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        _save_api_keys_to_config("", original_key, target_path=p)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["deepl"]["api_key"]) == original_key
    finally:
        os.unlink(p)
