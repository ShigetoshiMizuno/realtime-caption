"""
_save_api_keys_to_config の単体テスト。
実際の config.yaml は一切触らず、temp ファイルを使う。
"""
import base64
import textwrap
import tempfile
from pathlib import Path
import yaml
import sys
import os

# プロジェクトルートを sys.path に追加してインポート可能にする
sys.path.insert(0, str(Path(__file__).parent.parent))

from config_utils import decode_api_key, encode_api_key


# ---------------------------------------------------------------------------
# テスト用ヘルパー: _save_api_keys_to_config の "純関数版"
# ---------------------------------------------------------------------------

import re as _re


def _save_api_keys_to_path(target_path: Path, openai_key_plain: str, deepl_key_plain: str) -> bool:
    """
    app._save_api_keys_to_config のロジックを config path 引数付きで複製。
    テスト専用: CONFIG_PATH の代わりに target_path を使う。
    """
    try:
        text = target_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        current_section = None
        out = []
        for line in lines:
            m = _re.match(r'^([a-zA-Z_]+):\s*(?:#.*)?$', line)
            if m:
                current_section = m.group(1)
                out.append(line)
                continue

            m = _re.match(r'^(\s+api_key:\s*)(.*)$', line)
            if m:
                indent_key = m.group(1)
                rest = m.group(2)
                comment_idx = rest.find('#')
                comment = rest[comment_idx:] if comment_idx >= 0 else ""

                if current_section == "openai" and openai_key_plain:
                    new_val = f'"{encode_api_key(openai_key_plain)}"'
                    line = (f"{indent_key}{new_val}  {comment}\n"
                            if comment else f"{indent_key}{new_val}\n")
                elif current_section == "deepl" and deepl_key_plain:
                    new_val = f'"{encode_api_key(deepl_key_plain)}"'
                    line = (f"{indent_key}{new_val}  {comment}\n"
                            if comment else f"{indent_key}{new_val}\n")

            out.append(line)

        target_path.write_text("".join(out), encoding="utf-8")
        return True
    except Exception:
        return False


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
        assert _save_api_keys_to_path(p, "sk-new-key", "")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["openai"]["api_key"]) == "sk-new-key"
    finally:
        os.unlink(p)


def test_deepl_key_replaced():
    """DeepL キーが b64: 形式で上書きされること。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        assert _save_api_keys_to_path(p, "", "new-deepl-key")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["deepl"]["api_key"]) == "new-deepl-key"
    finally:
        os.unlink(p)


def test_both_keys_replaced():
    """OpenAI と DeepL の両方が同時に置換されること。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        assert _save_api_keys_to_path(p, "sk-both-openai", "both-deepl")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["openai"]["api_key"]) == "sk-both-openai"
        assert decode_api_key(data["deepl"]["api_key"]) == "both-deepl"
    finally:
        os.unlink(p)


def test_empty_key_not_replaced():
    """空文字を渡したセクションはそのまま変更されないこと。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        assert _save_api_keys_to_path(p, "", "")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert data["openai"]["api_key"] == "sk-old-key"
        assert data["deepl"]["api_key"] == "old-deepl-key"
    finally:
        os.unlink(p)


def test_yaml_still_valid_after_write():
    """書き換え後も yaml.safe_load が成功すること（YAML 破壊なし）。"""
    p = _make_temp_config(SAMPLE_CONFIG)
    try:
        _save_api_keys_to_path(p, "sk-valid-test", "deepl-valid")
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
        _save_api_keys_to_path(p, "", "new-deepl-with-comment-test")
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
        _save_api_keys_to_path(p, "", original_key)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert decode_api_key(data["deepl"]["api_key"]) == original_key
    finally:
        os.unlink(p)
