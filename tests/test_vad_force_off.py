"""
tests/test_vad_force_off.py

緊急修正 hotfix/vad-force-off-and-smoke-strict
TBD-3-1 再オープン: session.audio.input.turn_detection が API 拒否されることが
実機検証（2026-05-16）で確認された。

Fix 1: RouteConfig.__post_init__ で vad_enabled=True を受け取った場合、
       警告ログを出力し強制 False に変更する。

Fix 2: CaptionSystem.__init__ で vad_enabled=True を受け取った場合、
       警告ログを出力し強制 False に変更する。

Fix 3: app.py の _create_konnyaku_system で vad_enabled=True を受け取った場合、
       warning をステータスバーに通知し、settings.json を自動修正（vad_enabled=False で保存）する。

NOTE: 実 API キーは使わない。fixture はすべてフェイク値のみ。
      VAD の正しいパス修正は別 issue （#121）。本 PR は安全装置のみ実装。
"""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest

from main import CaptionSystem, RouteConfig
import app


# ---------------------------------------------------------------------------
# 1. RouteConfig.__post_init__ — vad_enabled=True を強制 False にする
# ---------------------------------------------------------------------------

class TestRouteConfigVadForceOff:
    """RouteConfig が vad_enabled=True を受け取った場合、強制 OFF する。"""

    def _make_route_config(self, vad_enabled: bool) -> RouteConfig:
        return RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            vad_enabled=vad_enabled,
        )

    def test_vad_enabled_true_is_forced_off(self):
        """RouteConfig(vad_enabled=True) を渡すと __post_init__ で False に強制される。"""
        rc = self._make_route_config(vad_enabled=True)
        assert rc.vad_enabled is False, (
            f"vad_enabled=True が渡されたとき、__post_init__ で False に強制されること。got={rc.vad_enabled}"
        )

    def test_vad_enabled_false_stays_false(self):
        """RouteConfig(vad_enabled=False) はそのまま False のままである。"""
        rc = self._make_route_config(vad_enabled=False)
        assert rc.vad_enabled is False

    def test_vad_enabled_default_stays_false(self):
        """RouteConfig(vad_enabled 省略) はデフォルト False のまま。"""
        rc = self._make_route_config(vad_enabled=False)
        assert rc.vad_enabled is False

    def test_vad_enabled_true_prints_warning(self, capsys):
        """RouteConfig(vad_enabled=True) を渡すと WARN メッセージが出力される。"""
        self._make_route_config(vad_enabled=True)
        captured = capsys.readouterr()
        # WARN または WARNING を含むこと
        combined = captured.out + captured.err
        assert "WARN" in combined or "warn" in combined.lower(), (
            f"vad_enabled=True 時に WARN ログが出力されること。captured={combined!r}"
        )

    def test_vad_enabled_true_warning_mentions_tbd31(self, capsys):
        """強制 OFF 警告に TBD-3-1 への言及が含まれること。"""
        self._make_route_config(vad_enabled=True)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "TBD-3-1" in combined, (
            f"警告メッセージに TBD-3-1 が含まれること。captured={combined!r}"
        )

    def test_vad_enabled_false_no_warning(self, capsys):
        """RouteConfig(vad_enabled=False) では WARN が出力されないこと。"""
        self._make_route_config(vad_enabled=False)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # WARN がない、もしくは vad に関係ない WARN のみであること
        vad_warn_lines = [l for l in combined.splitlines() if "WARN" in l and "vad" in l.lower()]
        assert len(vad_warn_lines) == 0, (
            f"vad_enabled=False では VAD 関連 WARN が出ないこと。warn_lines={vad_warn_lines}"
        )


# ---------------------------------------------------------------------------
# 2. CaptionSystem.__init__ — vad_enabled=True を強制 False にする
# ---------------------------------------------------------------------------

class TestCaptionSystemVadForceOff:
    """CaptionSystem が vad_enabled=True を受け取った場合、強制 OFF する。"""

    def _make_caption_system(self, vad_enabled: bool) -> CaptionSystem:
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
            vad_enabled=vad_enabled,
        )

    def test_caption_system_vad_enabled_true_is_forced_off(self):
        """CaptionSystem(vad_enabled=True) で _vad_enabled が False に強制される。"""
        cs = self._make_caption_system(vad_enabled=True)
        assert cs._vad_enabled is False, (
            f"CaptionSystem: vad_enabled=True が渡されたとき False に強制されること。got={cs._vad_enabled}"
        )

    def test_caption_system_vad_enabled_false_stays_false(self):
        """CaptionSystem(vad_enabled=False) では _vad_enabled が False のまま。"""
        cs = self._make_caption_system(vad_enabled=False)
        assert cs._vad_enabled is False

    def test_caption_system_vad_enabled_true_prints_warning(self, capsys):
        """CaptionSystem(vad_enabled=True) で WARN メッセージが出力される。"""
        self._make_caption_system(vad_enabled=True)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "WARN" in combined or "warn" in combined.lower(), (
            f"vad_enabled=True 時に WARN ログが出力されること。captured={combined!r}"
        )

    def test_caption_system_vad_enabled_true_warning_mentions_tbd31(self, capsys):
        """CaptionSystem 強制 OFF 警告に TBD-3-1 への言及が含まれること。"""
        self._make_caption_system(vad_enabled=True)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "TBD-3-1" in combined, (
            f"CaptionSystem: 警告メッセージに TBD-3-1 が含まれること。captured={combined!r}"
        )


# ---------------------------------------------------------------------------
# 3. app.py _create_konnyaku_system — vad_enabled=True を強制 False に書き戻す
# ---------------------------------------------------------------------------

class TestAppVadForceOff:
    """app._create_konnyaku_system が saved settings の vad_enabled=True を
    強制 False に上書きして RouteConfig に渡すこと（Fix 2）。"""

    def setup_method(self):
        self._old_system = app._konnyaku_system
        self._old_running = app._konnyaku_running

    def teardown_method(self):
        app._konnyaku_system = self._old_system
        app._konnyaku_running = self._old_running

    def test_create_konnyaku_system_forces_vad_off_when_saved_true(self):
        """saved settings に vad_enabled=True があっても RouteConfig に False が渡されること。"""
        fake_devices = [{"name": "Mic1", "index": 0, "samplerate": 16000}]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": True,   # ON 設定（強制 OFF 対象）
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": True,   # ON 設定（強制 OFF 対象）
            },
        }

        app._konnyaku_system = None
        app._konnyaku_running = False

        created_route_a_vad = {}
        created_route_b_vad = {}

        def capturing_mcs(config, route_a, route_b, **kwargs):
            if route_a is not None:
                created_route_a_vad["vad_enabled"] = route_a.vad_enabled
            if route_b is not None:
                created_route_b_vad["vad_enabled"] = route_b.vad_enabled
            mock_mcs = MagicMock()
            mock_mcs.route_a_system = MagicMock()
            mock_mcs.route_b_system = MagicMock()
            return mock_mcs

        with patch("app._load_settings", return_value=fake_settings), \
             patch("app._devices", fake_devices), \
             patch("app.MultiCaptionSystem", side_effect=capturing_mcs), \
             patch("app.list_audio_devices", return_value=[]), \
             patch("app.find_device_by_name", return_value=None):
            app._create_konnyaku_system()

        assert created_route_a_vad.get("vad_enabled") is False, (
            f"saved vad_enabled=True でも route_a には False が渡されること。got={created_route_a_vad}"
        )
        assert created_route_b_vad.get("vad_enabled") is False, (
            f"saved vad_enabled=True でも route_b には False が渡されること。got={created_route_b_vad}"
        )

    def test_create_konnyaku_system_vad_false_stays_false(self):
        """saved settings に vad_enabled=False があれば RouteConfig に False が渡されること（変化なし）。"""
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

        created_route_a_vad = {}

        def capturing_mcs(config, route_a, route_b, **kwargs):
            if route_a is not None:
                created_route_a_vad["vad_enabled"] = route_a.vad_enabled
            mock_mcs = MagicMock()
            mock_mcs.route_a_system = MagicMock()
            mock_mcs.route_b_system = MagicMock()
            return mock_mcs

        with patch("app._load_settings", return_value=fake_settings), \
             patch("app._devices", fake_devices), \
             patch("app.MultiCaptionSystem", side_effect=capturing_mcs), \
             patch("app.list_audio_devices", return_value=[]), \
             patch("app.find_device_by_name", return_value=None):
            app._create_konnyaku_system()

        assert created_route_a_vad.get("vad_enabled") is False, (
            f"saved vad_enabled=False は False のまま渡されること。got={created_route_a_vad}"
        )

    def test_create_konnyaku_system_vad_true_prints_warning(self, capsys):
        """saved vad_enabled=True のとき _create_konnyaku_system が WARN を出力すること。"""
        fake_devices = [{"name": "Mic1", "index": 0, "samplerate": 16000}]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "vad_enabled": True,
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
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

        with patch("app._load_settings", return_value=fake_settings), \
             patch("app._devices", fake_devices), \
             patch("app.MultiCaptionSystem", side_effect=capturing_mcs), \
             patch("app.list_audio_devices", return_value=[]), \
             patch("app.find_device_by_name", return_value=None):
            app._create_konnyaku_system()

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "WARN" in combined or "warn" in combined.lower(), (
            f"vad_enabled=True が saved のとき WARN が出力されること。captured={combined!r}"
        )
