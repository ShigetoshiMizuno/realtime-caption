"""
tests/test_settings_persistence.py

Issue #68: 経路1/2 の設定が保存・復元されない。

_save_settings() が route_a / route_b セクションを JSON に含むこと、
_load_settings() が保存済み値を正しく返すことを検証する。
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).parent.parent))

import app as _app_module


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_dpg_mock(widget_values: dict) -> MagicMock:
    """
    dpg のモック。
    - does_item_exist: widget_values にキーがあれば True
    - get_value: widget_values の値を返す
    """
    mock = MagicMock()
    mock.does_item_exist.side_effect = lambda tag: tag in widget_values
    mock.get_value.side_effect = lambda tag: widget_values.get(tag, "")
    return mock


# ---------------------------------------------------------------------------
# _save_settings() — route_a / route_b セクションが出力に含まれること
# ---------------------------------------------------------------------------

class TestSaveSettingsIncludesRoutes:
    """_save_settings() が route_a / route_b セクションを JSON に書き出す。"""

    def _run_save(self, widget_values: dict) -> dict:
        """
        dpg と open() をモック差し替えして _save_settings() を呼び、
        実際に書き込まれた JSON を dict で返す。
        """
        written_data = {}

        captured_content: list[str] = []

        class FakeFile:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def write(self, s):
                captured_content.append(s)

        def fake_open(path, mode="r", encoding=None):
            if "w" in mode:
                return FakeFile()
            raise FileNotFoundError(path)

        dpg_mock = _make_dpg_mock(widget_values)

        with patch.object(_app_module, "dpg", dpg_mock), \
             patch.object(_app_module, "_dpg_ready", True), \
             patch("builtins.open", fake_open), \
             patch.object(_app_module, "json") as json_mock:

            # json.dump の第1引数（data dict）をキャプチャ
            def capture_dump(data, f, **kwargs):
                written_data.update(data)

            json_mock.dump.side_effect = capture_dump

            _app_module._save_settings()

        return written_data

    def test_save_settings_includes_route_a(self):
        """_save_settings() の出力に route_a セクションが含まれる。"""
        widget_values = {
            _app_module.TAG_ROUTE_A_ENABLE: True,
            _app_module.TAG_ROUTE_A_DEVICE_COMBO: "LG 4K [Loopback]",
            _app_module.TAG_ROUTE_A_LANG_COMBO: "日本語",
            _app_module.TAG_ROUTE_A_OUTPUT_ENABLE: False,
            _app_module.TAG_ROUTE_A_OUTPUT_DEVICE_COMBO: "(なし)",
            _app_module.TAG_ROUTE_A_OUTPUT_VOLUME: 1.0,
        }
        data = self._run_save(widget_values)
        assert "route_a" in data, f"route_a が保存データに含まれない: {list(data.keys())}"
        ra = data["route_a"]
        assert ra["enabled"] is True
        assert ra["device"] == "LG 4K [Loopback]"
        assert ra["lang"] == "日本語"
        assert ra["output_enabled"] is False
        assert ra["output_device"] == "(なし)"
        assert ra["output_volume"] == 1.0

    def test_save_settings_includes_route_b(self):
        """_save_settings() の出力に route_b セクションが含まれる。"""
        widget_values = {
            _app_module.TAG_ROUTE_B_ENABLE: False,
            _app_module.TAG_ROUTE_B_DEVICE_COMBO: "マイク (USB)",
            _app_module.TAG_ROUTE_B_LANG_COMBO: "英語",
            _app_module.TAG_ROUTE_B_OUTPUT_ENABLE: True,
            _app_module.TAG_ROUTE_B_OUTPUT_DEVICE_COMBO: "CABLE Input (VB-Audio)",
            _app_module.TAG_ROUTE_B_OUTPUT_VOLUME: 0.8,
        }
        data = self._run_save(widget_values)
        assert "route_b" in data, f"route_b が保存データに含まれない: {list(data.keys())}"
        rb = data["route_b"]
        assert rb["enabled"] is False
        assert rb["device"] == "マイク (USB)"
        assert rb["lang"] == "英語"
        assert rb["output_enabled"] is True
        assert rb["output_device"] == "CABLE Input (VB-Audio)"
        assert rb["output_volume"] == 0.8

    def test_save_settings_includes_host_api(self):
        """host_api も保存される（既存機能の後方互換確認）。"""
        widget_values = {
            _app_module.TAG_HOST_API_COMBO: "wasapi",
        }
        data = self._run_save(widget_values)
        assert "host_api" in data, f"host_api が保存データに含まれない: {list(data.keys())}"
        assert data["host_api"] == "wasapi"


# ---------------------------------------------------------------------------
# _load_settings() — ファイルが存在しない場合と正常ロードを検証
# ---------------------------------------------------------------------------

class TestLoadSettings:
    """_load_settings() の基本動作テスト。"""

    def test_load_settings_returns_empty_dict_when_file_missing(self):
        """_load_settings() はファイル不存在時に空 dict を返す。"""
        with patch.object(_app_module, "_SETTINGS_PATH", Path("/nonexistent/settings.json")):
            result = _app_module._load_settings()
        assert result == {}, f"空 dict を期待したが {result!r} が返った"

    def test_load_settings_recovers_route_a(self):
        """保存した route_a の各キーが _load_settings() で読み込める。"""
        route_a_data = {
            "enabled": True,
            "device": "LG 4K [Loopback]",
            "lang": "日本語",
            "output_enabled": False,
            "output_device": "(なし)",
            "output_volume": 1.0,
        }
        payload = {"route_a": route_a_data, "route_b": {}}

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(payload, f, ensure_ascii=False)
            tmp_path = Path(f.name)

        try:
            with patch.object(_app_module, "_SETTINGS_PATH", tmp_path):
                result = _app_module._load_settings()
        finally:
            tmp_path.unlink(missing_ok=True)

        assert "route_a" in result
        ra = result["route_a"]
        assert ra["enabled"] is True
        assert ra["device"] == "LG 4K [Loopback]"
        assert ra["lang"] == "日本語"
        assert ra["output_enabled"] is False
        assert ra["output_device"] == "(なし)"
        assert ra["output_volume"] == 1.0

    def test_load_settings_recovers_route_b(self):
        """保存した route_b の各キーが _load_settings() で読み込める。"""
        route_b_data = {
            "enabled": False,
            "device": "マイク (USB)",
            "lang": "英語",
            "output_enabled": True,
            "output_device": "CABLE Input (VB-Audio)",
            "output_volume": 0.75,
        }
        payload = {"route_a": {}, "route_b": route_b_data}

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(payload, f, ensure_ascii=False)
            tmp_path = Path(f.name)

        try:
            with patch.object(_app_module, "_SETTINGS_PATH", tmp_path):
                result = _app_module._load_settings()
        finally:
            tmp_path.unlink(missing_ok=True)

        assert "route_b" in result
        rb = result["route_b"]
        assert rb["enabled"] is False
        assert rb["device"] == "マイク (USB)"
        assert rb["lang"] == "英語"
        assert rb["output_enabled"] is True
        assert rb["output_device"] == "CABLE Input (VB-Audio)"
        assert rb["output_volume"] == 0.75

    def test_load_settings_returns_empty_dict_on_corrupt_json(self):
        """JSON が壊れていても例外を出さず空 dict を返す。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write("{broken json!!!")
            tmp_path = Path(f.name)

        try:
            with patch.object(_app_module, "_SETTINGS_PATH", tmp_path):
                result = _app_module._load_settings()
        finally:
            tmp_path.unlink(missing_ok=True)

        assert result == {}, f"壊れた JSON: 空 dict を期待したが {result!r}"
