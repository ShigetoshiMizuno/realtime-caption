"""
tests/test_vad_force_off.py

refactor/remove-vad-dead-code 後の後方互換検証。

vad_* パラメータは削除済み。以下を確認する:
- RouteConfig に vad_enabled 等を渡しても TypeError にならないこと
- CaptionSystem に vad_enabled 等を渡しても TypeError にならないこと
- app._create_konnyaku_system が saved settings に vad_enabled があっても起動エラーにならないこと

NOTE: 実 API キーは使わない。fixture はすべてフェイク値のみ。
"""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest

from main import CaptionSystem, RouteConfig
import app


# ---------------------------------------------------------------------------
# 1. RouteConfig — vad_enabled を渡しても TypeError にならないこと
# ---------------------------------------------------------------------------

class TestRouteConfigVadBackwardCompat:
    """RouteConfig に vad_* を渡しても TypeError にならないこと（後方互換）。"""

    def test_vad_enabled_true_no_type_error(self):
        """RouteConfig(vad_enabled=True) を渡しても TypeError にならないこと。"""
        try:
            RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_enabled=True,
            )
        except TypeError as e:
            pytest.fail(f"vad_enabled=True で TypeError が発生してはならない: {e}")

    def test_vad_enabled_false_no_type_error(self):
        """RouteConfig(vad_enabled=False) を渡しても TypeError にならないこと。"""
        try:
            RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_enabled=False,
            )
        except TypeError as e:
            pytest.fail(f"vad_enabled=False で TypeError が発生してはならない: {e}")

    def test_vad_threshold_no_type_error(self):
        """RouteConfig に vad_threshold を渡しても TypeError にならないこと。"""
        try:
            RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_threshold=0.7,
            )
        except TypeError as e:
            pytest.fail(f"vad_threshold で TypeError が発生してはならない: {e}")

    def test_vad_prefix_padding_ms_no_type_error(self):
        """RouteConfig に vad_prefix_padding_ms を渡しても TypeError にならないこと。"""
        try:
            RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_prefix_padding_ms=200,
            )
        except TypeError as e:
            pytest.fail(f"vad_prefix_padding_ms で TypeError が発生してはならない: {e}")

    def test_vad_silence_duration_ms_no_type_error(self):
        """RouteConfig に vad_silence_duration_ms を渡しても TypeError にならないこと。"""
        try:
            RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_silence_duration_ms=800,
            )
        except TypeError as e:
            pytest.fail(f"vad_silence_duration_ms で TypeError が発生してはならない: {e}")


# ---------------------------------------------------------------------------
# 2. CaptionSystem — vad_enabled を渡しても TypeError にならないこと
# ---------------------------------------------------------------------------

class TestCaptionSystemVadBackwardCompat:
    """CaptionSystem に vad_* を渡しても TypeError にならないこと（後方互換）。"""

    def _make_caption_system_with_vad(self, **vad_kwargs) -> CaptionSystem:
        config = {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-force-off-0000000000000"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
            },
            "output": {"log_dir": "."},
            "stt": {"model": "tiny"},
        }
        device_info = {"name": "FakeMic", "index": 0, "samplerate": 16000}
        return CaptionSystem(
            config=config,
            device_info=device_info,
            model_name="tiny",
            **vad_kwargs,
        )

    def test_vad_enabled_true_no_type_error(self):
        """CaptionSystem(vad_enabled=True) を渡しても TypeError にならないこと。"""
        try:
            self._make_caption_system_with_vad(vad_enabled=True)
        except TypeError as e:
            pytest.fail(f"vad_enabled=True で TypeError が発生してはならない: {e}")

    def test_vad_enabled_false_no_type_error(self):
        """CaptionSystem(vad_enabled=False) を渡しても TypeError にならないこと。"""
        try:
            self._make_caption_system_with_vad(vad_enabled=False)
        except TypeError as e:
            pytest.fail(f"vad_enabled=False で TypeError が発生してはならない: {e}")

    def test_all_vad_params_no_type_error(self):
        """全 vad_* パラメータをまとめて渡しても TypeError にならないこと。"""
        try:
            self._make_caption_system_with_vad(
                vad_enabled=True,
                vad_threshold=0.7,
                vad_prefix_padding_ms=200,
                vad_silence_duration_ms=800,
            )
        except TypeError as e:
            pytest.fail(f"全 vad_* パラメータで TypeError が発生してはならない: {e}")


# ---------------------------------------------------------------------------
# 3. app.py _create_konnyaku_system — 旧 settings に vad_enabled があっても起動エラーにならない
# ---------------------------------------------------------------------------

class TestAppVadBackwardCompat:
    """app._create_konnyaku_system が saved settings に vad_enabled キーがあっても
    起動エラーにならないこと（後方互換: 旧 settings.json との互換）。"""

    def setup_method(self):
        self._old_system = app._konnyaku_system
        self._old_running = app._konnyaku_running

    def teardown_method(self):
        app._konnyaku_system = self._old_system
        app._konnyaku_running = self._old_running

    def test_create_konnyaku_system_with_saved_vad_enabled_true_no_error(self):
        """saved settings に vad_enabled=True があっても _create_konnyaku_system が正常に動作すること。"""
        fake_devices = [{"name": "Mic1", "index": 0, "samplerate": 16000}]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": True,   # 旧 settings.json に存在するキー
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": True,
            },
        }

        app._konnyaku_system = None
        app._konnyaku_running = False

        called = {"mcs": False}

        def capturing_mcs(config, route_a, route_b, **kwargs):
            called["mcs"] = True
            mock_mcs = MagicMock()
            mock_mcs.route_a_system = MagicMock()
            mock_mcs.route_b_system = MagicMock()
            return mock_mcs

        try:
            with patch("app._load_settings", return_value=fake_settings), \
                 patch("app._devices", fake_devices), \
                 patch("app.MultiCaptionSystem", side_effect=capturing_mcs), \
                 patch("app.list_audio_devices", return_value=[]), \
                 patch("app.find_device_by_name", return_value=None):
                app._create_konnyaku_system()
        except Exception as e:
            pytest.fail(f"旧 vad_enabled=True があっても起動エラーにならないこと: {e}")

    def test_create_konnyaku_system_with_saved_vad_enabled_false_no_error(self):
        """saved settings に vad_enabled=False があっても _create_konnyaku_system が正常に動作すること。"""
        fake_devices = [{"name": "Mic1", "index": 0, "samplerate": 16000}]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": False,
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": False,
            },
        }

        app._konnyaku_system = None
        app._konnyaku_running = False

        def capturing_mcs(config, route_a, route_b, **kwargs):
            mock_mcs = MagicMock()
            mock_mcs.route_a_system = MagicMock()
            mock_mcs.route_b_system = MagicMock()
            return mock_mcs

        try:
            with patch("app._load_settings", return_value=fake_settings), \
                 patch("app._devices", fake_devices), \
                 patch("app.MultiCaptionSystem", side_effect=capturing_mcs), \
                 patch("app.list_audio_devices", return_value=[]), \
                 patch("app.find_device_by_name", return_value=None):
                app._create_konnyaku_system()
        except Exception as e:
            pytest.fail(f"旧 vad_enabled=False があっても起動エラーにならないこと: {e}")

    @pytest.mark.skip(reason="W-COST-3 UI 廃止: _create_konnyaku_system の VAD 警告コードを削除 — refactor/remove-vad-ui-ga-cleanup")
    def test_create_konnyaku_system_vad_true_prints_warning(self, capsys):
        """saved vad_enabled=True のとき _create_konnyaku_system が WARN を出力すること（廃止済み）。"""
        pass
