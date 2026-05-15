"""
tests/test_issue_102_settings_reflection.py

Issue #102 Step 3+4: 翻訳先言語の稼働中反映 UX 改善テスト

テスト対象:
  Fix 1: CaptionSystem.stop() の finally ブロックで _realtime_translator / _cost_monitor を None にリセット
  Fix 2: app.py に _on_route_a_language_change / _on_route_b_language_change を追加し稼働中再起動
  Fix 3: CaptionSystem に set_target_language(code: str) 公開メソッドを追加

テスト分類:
  4-A: 組み合わせテスト（言語×系統、音声出力×デバイス、原文表示×VAD）
  4-B: 状態遷移テスト（IDLE→RUNNING、言語変更→再起動、stop→startで新Translator生成）
  4-C: 保存復元テスト（各設定値の save/load、不正値フォールバック）
"""

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest

from main import CaptionSystem, MultiCaptionSystem, RouteConfig, RouteState
import app


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _make_realtime_config(api_key: str = "sk-test-fake-issue102") -> dict:
    """テスト用 openai-realtime 設定 dict を返す。"""
    return {
        "translation": {"translation_model": "openai-realtime"},
        "openai": {"api_key": api_key},
        "openai_realtime": {
            "target_language_code": "ja",
            "model": "gpt-realtime-translate",
        },
        "output": {"log_dir": "."},
        "stt": {"model": "tiny"},
    }


def _make_caption_system(
    target_lang: str = "ja",
    output_device_index: int | None = None,
    request_source_transcript: bool = True,
    vad_enabled: bool = False,
    route_id: str = "a",
) -> CaptionSystem:
    """テスト用 CaptionSystem（openai-realtime モード）を返す。"""
    cfg = _make_realtime_config()
    cfg["openai_realtime"]["target_language_code"] = target_lang
    device_info = {"name": "FakeMic", "index": 0, "samplerate": 16000}
    return CaptionSystem(
        config=cfg,
        device_info=device_info,
        model_name="tiny",
        output_device_index=output_device_index,
        request_source_transcript=request_source_transcript,
        vad_enabled=vad_enabled,
        route_id=route_id,
    )


class FakeRouteSystem:
    """CaptionSystem の最小スタブ（state プロパティを返す）。"""

    def __init__(self, state: RouteState = RouteState.IDLE):
        self._state = state
        self.set_output_device = MagicMock()
        self.set_target_language = MagicMock()

    @property
    def state(self) -> RouteState:
        return self._state


class FakeMultiCaptionSystem:
    """MultiCaptionSystem の最小スタブ。"""

    def __init__(self, route_a_state=RouteState.IDLE, route_b_state=RouteState.IDLE):
        self.route_a_system = FakeRouteSystem(route_a_state)
        self.route_b_system = FakeRouteSystem(route_b_state)
        self.stop_route = MagicMock()
        self.start_route = MagicMock()


@pytest.fixture(autouse=True)
def reset_app_state():
    """各テスト前後に app モジュールのグローバル状態をリセットする。"""
    old_system = app._konnyaku_system
    old_running = app._konnyaku_running

    yield

    app._konnyaku_system = old_system
    app._konnyaku_running = old_running


# ---------------------------------------------------------------------------
# Fix 1: CaptionSystem.stop() が _realtime_translator / _cost_monitor をリセットする
# ---------------------------------------------------------------------------

class TestStopResetsRealtimeTranslator:
    """Fix 1: stop() の finally ブロックで _realtime_translator = None になること。"""

    def test_stop_resets_realtime_translator_to_none(self):
        """stop() 後に _realtime_translator が None になること（Fix 1）。"""
        cs = _make_caption_system()
        mock_rt = MagicMock()
        cs._realtime_translator = mock_rt

        cs.stop()

        assert cs._realtime_translator is None, (
            "stop() 後に _realtime_translator が None でない。"
            "Fix 1 (_realtime_translator リセット) が未実装。"
        )

    def test_stop_resets_cost_monitor_to_none(self):
        """stop() 後に _cost_monitor が None になること（Fix 1）。"""
        cs = _make_caption_system()
        mock_cm = MagicMock()
        cs._cost_monitor = mock_cm

        cs.stop()

        assert cs._cost_monitor is None, (
            "stop() 後に _cost_monitor が None でない。"
            "Fix 1 (_cost_monitor リセット) が未実装。"
        )

    def test_stop_calls_realtime_translator_stop_before_reset(self):
        """stop() が _realtime_translator.stop() を呼んでから None にリセットすること。"""
        cs = _make_caption_system()
        mock_rt = MagicMock()
        cs._realtime_translator = mock_rt

        cs.stop()

        mock_rt.stop.assert_called_once()
        assert cs._realtime_translator is None

    def test_stop_resets_to_idle_state(self):
        """stop() 後に状態が IDLE になること（既存挙動確認）。"""
        cs = _make_caption_system()
        cs._realtime_translator = MagicMock()

        cs.stop()

        assert cs.state == RouteState.IDLE

    def test_create_realtime_translator_creates_new_instance_after_stop(self):
        """stop() → _create_realtime_translator() で新しいインスタンスが生成されること。

        Fix 1 の効果確認: stop() 後に _realtime_translator が None になるため、
        _create_realtime_translator() が no-op にならず新インスタンスを生成する。
        """
        cs = _make_caption_system(target_lang="ja")

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.side_effect = [MagicMock(), MagicMock()]

            # 1回目の生成
            cs._create_realtime_translator()
            first_instance = cs._realtime_translator

            # stop() でリセット（Fix 1）
            cs.stop()
            assert cs._realtime_translator is None, "stop() 後に None になること"

            # 2回目の生成（no-op でなく新インスタンス）
            cs._create_realtime_translator()
            second_instance = cs._realtime_translator

        assert MockRT.call_count == 2, (
            f"stop() 後に _create_realtime_translator() を呼んでも新インスタンスが生成されること。"
            f"実際の呼び出し回数: {MockRT.call_count}"
        )

    def test_stop_start_cycle_uses_new_language(self):
        """stop() → _create_realtime_translator() で最新の target_language_code が使われること。

        Fix 1 の核心テスト: stop→start サイクルで言語が反映される。
        """
        cs = _make_caption_system(target_lang="ja")

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.side_effect = [MagicMock(), MagicMock()]

            cs._create_realtime_translator()
            first_kwargs = MockRT.call_args_list[0][1]
            assert first_kwargs.get("target_language_code") == "ja"

            # 言語を変更してから stop/start
            cs._config["openai_realtime"]["target_language_code"] = "en"
            cs.stop()

            cs._create_realtime_translator()
            second_kwargs = MockRT.call_args_list[1][1]
            assert second_kwargs.get("target_language_code") == "en", (
                f"stop→start 後に target_language_code='en' が使われること。"
                f"実際: {second_kwargs.get('target_language_code')}"
            )

    def test_stop_is_idempotent_with_none_realtime_translator(self):
        """_realtime_translator が None の状態で stop() を呼んでもクラッシュしないこと。"""
        cs = _make_caption_system()
        assert cs._realtime_translator is None

        # 例外が出ないことを確認
        cs.stop()
        assert cs._realtime_translator is None


# ---------------------------------------------------------------------------
# Fix 3: set_target_language() 公開メソッド
# ---------------------------------------------------------------------------

class TestSetTargetLanguage:
    """Fix 3: CaptionSystem.set_target_language(code) メソッドのテスト。"""

    def test_set_target_language_updates_config(self):
        """set_target_language('en') が openai_realtime.target_language_code を更新すること。"""
        cs = _make_caption_system(target_lang="ja")
        assert cs._config["openai_realtime"]["target_language_code"] == "ja"

        cs.set_target_language("en")

        assert cs._config["openai_realtime"]["target_language_code"] == "en", (
            "set_target_language('en') 後に config が更新されること。"
            "Fix 3 (set_target_language メソッド) が未実装。"
        )

    def test_set_target_language_clears_realtime_translator(self):
        """set_target_language() が _realtime_translator を None にクリアすること。"""
        cs = _make_caption_system(target_lang="ja")
        cs._realtime_translator = MagicMock()

        cs.set_target_language("en")

        assert cs._realtime_translator is None, (
            "set_target_language() 後に _realtime_translator が None になること"
        )

    def test_set_target_language_ja_to_en(self):
        """ja → en への変更が正しく反映されること。"""
        cs = _make_caption_system(target_lang="ja")
        cs.set_target_language("en")
        assert cs._config["openai_realtime"]["target_language_code"] == "en"

    def test_set_target_language_en_to_ja(self):
        """en → ja への変更が正しく反映されること。"""
        cs = _make_caption_system(target_lang="en")
        cs.set_target_language("ja")
        assert cs._config["openai_realtime"]["target_language_code"] == "ja"

    def test_set_target_language_same_value(self):
        """同じ言語コードを再設定してもクラッシュしないこと。"""
        cs = _make_caption_system(target_lang="ja")
        cs.set_target_language("ja")
        assert cs._config["openai_realtime"]["target_language_code"] == "ja"

    def test_set_target_language_after_stop_and_create(self):
        """set_target_language() → _create_realtime_translator() で新しい言語が使われること。"""
        cs = _make_caption_system(target_lang="ja")
        cs.set_target_language("en")

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()

        _, kwargs = MockRT.call_args
        assert kwargs.get("target_language_code") == "en", (
            f"set_target_language('en') 後の _create_realtime_translator() で"
            f"target_language_code='en' が渡されること。実際: {kwargs}"
        )


# ---------------------------------------------------------------------------
# Fix 2: app.py の言語コンボ callback（_on_route_a/b_language_change）
# ---------------------------------------------------------------------------

class TestLanguageChangeCallbackExists:
    """Fix 2: _on_route_a_language_change / _on_route_b_language_change が存在すること。"""

    def test_on_route_a_language_change_function_exists(self):
        """app に _on_route_a_language_change 関数が存在すること。"""
        assert hasattr(app, "_on_route_a_language_change"), (
            "app._on_route_a_language_change が存在しない。"
            "Fix 2（言語コンボ callback の動的反映化）が未実装。"
        )

    def test_on_route_b_language_change_function_exists(self):
        """app に _on_route_b_language_change 関数が存在すること。"""
        assert hasattr(app, "_on_route_b_language_change"), (
            "app._on_route_b_language_change が存在しない。"
            "Fix 2（言語コンボ callback の動的反映化）が未実装。"
        )

    def test_on_route_a_language_change_is_callable(self):
        """_on_route_a_language_change が callable であること。"""
        assert callable(getattr(app, "_on_route_a_language_change", None))

    def test_on_route_b_language_change_is_callable(self):
        """_on_route_b_language_change が callable であること。"""
        assert callable(getattr(app, "_on_route_b_language_change", None))


class TestRouteALanguageChangeCallback:
    """Fix 2: _on_route_a_language_change の動作テスト。"""

    def test_saves_settings_when_called(self):
        """_on_route_a_language_change が _save_settings() を呼ぶこと。"""
        with patch.object(app, "_save_settings") as mock_save, \
             patch("app.dpg"):
            app._on_route_a_language_change("sender", "English", None)
            mock_save.assert_called_once()

    def test_does_not_restart_when_not_running(self):
        """_konnyaku_running=False のとき再起動スレッドを起動しないこと。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_a_language_change("sender", "English", None)

        # threading.Thread が起動されないことを確認
        mock_restart.assert_not_called()

    def test_does_not_restart_when_system_is_none(self):
        """_konnyaku_system が None のとき再起動しないこと。"""
        app._konnyaku_system = None
        app._konnyaku_running = True

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_a_language_change("sender", "English", None)

        mock_restart.assert_not_called()

    def test_does_not_restart_when_route_a_system_is_none(self):
        """route_a_system が None のとき再起動しないこと。"""
        fake_system = FakeMultiCaptionSystem()
        fake_system.route_a_system = None
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_a_language_change("sender", "English", None)

        mock_restart.assert_not_called()

    def test_does_not_restart_when_route_a_not_running(self):
        """route_a が RUNNING でないとき再起動しないこと（IDLE 状態）。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.IDLE)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_a_language_change("sender", "English", None)

        mock_restart.assert_not_called()

    def test_triggers_restart_when_running(self):
        """_konnyaku_running=True かつ route_a RUNNING のとき再起動スレッドが起動すること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        restart_called = threading.Event()
        original_restart = getattr(app, "_restart_route_for_change", None)

        def fake_restart(route_id, reason_label):
            restart_called.set()

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            app._on_route_a_language_change("sender", "English", None)
            restart_called.wait(timeout=2.0)

        assert restart_called.is_set(), (
            "_on_route_a_language_change が RUNNING 系統に対して"
            "_restart_route_for_change を呼び出さなかった。"
        )

    def test_restarts_with_correct_route_id_a(self):
        """再起動の際に route_id='a' が渡されること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        called_args = []
        restart_done = threading.Event()

        def fake_restart(route_id, reason_label):
            called_args.append((route_id, reason_label))
            restart_done.set()

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            app._on_route_a_language_change("sender", "English", None)
            restart_done.wait(timeout=2.0)

        assert called_args, "_restart_route_for_change が呼ばれること"
        assert called_args[0][0] == "a", (
            f"route_id='a' で再起動されること。実際: {called_args[0][0]}"
        )


class TestRouteBLanguageChangeCallback:
    """Fix 2: _on_route_b_language_change の動作テスト。"""

    def test_saves_settings_when_called(self):
        """_on_route_b_language_change が _save_settings() を呼ぶこと。"""
        with patch.object(app, "_save_settings") as mock_save, \
             patch("app.dpg"):
            app._on_route_b_language_change("sender", "日本語", None)
            mock_save.assert_called_once()

    def test_does_not_restart_when_not_running(self):
        """_konnyaku_running=False のとき再起動しないこと。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_b_language_change("sender", "日本語", None)

        mock_restart.assert_not_called()

    def test_does_not_restart_when_route_b_not_running(self):
        """route_b が RUNNING でないとき再起動しないこと。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.IDLE)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_b_language_change("sender", "日本語", None)

        mock_restart.assert_not_called()

    def test_triggers_restart_when_running(self):
        """route_b が RUNNING のとき再起動スレッドが起動すること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        restart_called = threading.Event()

        def fake_restart(route_id, reason_label):
            restart_called.set()

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            app._on_route_b_language_change("sender", "日本語", None)
            restart_called.wait(timeout=2.0)

        assert restart_called.is_set(), (
            "_on_route_b_language_change が RUNNING 系統に対して"
            "_restart_route_for_change を呼び出さなかった。"
        )

    def test_restarts_with_correct_route_id_b(self):
        """再起動の際に route_id='b' が渡されること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        called_args = []
        restart_done = threading.Event()

        def fake_restart(route_id, reason_label):
            called_args.append((route_id, reason_label))
            restart_done.set()

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            app._on_route_b_language_change("sender", "日本語", None)
            restart_done.wait(timeout=2.0)

        assert called_args, "_restart_route_for_change が呼ばれること"
        assert called_args[0][0] == "b", (
            f"route_id='b' で再起動されること。実際: {called_args[0][0]}"
        )


# ---------------------------------------------------------------------------
# 4-A: 組み合わせテスト
# ---------------------------------------------------------------------------

class TestCombinationRouteAOnly:
    """系統A のみ ON の場合のテスト。"""

    def test_route_a_only_running_triggers_language_restart(self):
        """系統A が RUNNING、系統B が IDLE の状態で言語変更 → 系統A のみ再起動。"""
        fake_system = FakeMultiCaptionSystem(
            route_a_state=RouteState.RUNNING,
            route_b_state=RouteState.IDLE,
        )
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        restart_done = threading.Event()
        called_route = []

        def fake_restart(route_id, reason_label):
            called_route.append(route_id)
            restart_done.set()

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            app._on_route_a_language_change("sender", "English", None)
            restart_done.wait(timeout=2.0)

        assert "a" in called_route
        assert "b" not in called_route


class TestCombinationRouteBOnly:
    """系統B のみ ON の場合のテスト。"""

    def test_route_b_only_running_triggers_language_restart(self):
        """系統B が RUNNING、系統A が IDLE の状態で言語変更 → 系統B のみ再起動。"""
        fake_system = FakeMultiCaptionSystem(
            route_a_state=RouteState.IDLE,
            route_b_state=RouteState.RUNNING,
        )
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        restart_done = threading.Event()
        called_route = []

        def fake_restart(route_id, reason_label):
            called_route.append(route_id)
            restart_done.set()

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            app._on_route_b_language_change("sender", "English", None)
            restart_done.wait(timeout=2.0)

        assert "b" in called_route
        assert "a" not in called_route


class TestTargetLanguageCombinations:
    """ターゲット言語の組み合わせテスト。"""

    def test_route_a_target_lang_ja(self):
        """系統A の言語を ja に設定して _create_realtime_translator() に渡ること。"""
        cs = _make_caption_system(target_lang="ja")
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("target_language_code") == "ja"

    def test_route_a_target_lang_en(self):
        """系統A の言語を en に設定して _create_realtime_translator() に渡ること。"""
        cs = _make_caption_system(target_lang="en")
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("target_language_code") == "en"

    def test_route_b_target_lang_ja(self):
        """系統B の言語を ja に設定して _create_realtime_translator() に渡ること。"""
        cs = _make_caption_system(target_lang="ja", route_id="b")
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("target_language_code") == "ja"

    def test_route_b_target_lang_en(self):
        """系統B の言語を en に設定して _create_realtime_translator() に渡ること。"""
        cs = _make_caption_system(target_lang="en", route_id="b")
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("target_language_code") == "en"


class TestAudioOutputAndDeviceCombinations:
    """音声出力 ON/OFF × 出力デバイスの組み合わせテスト。"""

    def test_audio_output_off_when_output_device_none(self):
        """output_device_index=None の場合 _audio_output_mode=False であること。"""
        cs = _make_caption_system(output_device_index=None)
        assert cs._audio_output_mode is False

    def test_audio_output_on_when_output_device_set(self):
        """output_device_index=2 の場合 _audio_output_mode=True であること。"""
        cs = _make_caption_system(output_device_index=2)
        assert cs._audio_output_mode is True

    def test_create_translator_request_audio_output_false_for_no_device(self):
        """デバイスなし → _create_realtime_translator() で request_audio_output=False が渡ること。"""
        cs = _make_caption_system(output_device_index=None)
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_audio_output") is False

    def test_create_translator_request_audio_output_true_for_device(self):
        """デバイスあり → _create_realtime_translator() で request_audio_output=True が渡ること。"""
        cs = _make_caption_system(output_device_index=3)
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_audio_output") is True


class TestSourceTranscriptAndVADCombinations:
    """原文表示 ON/OFF × VAD ON/OFF の組み合わせテスト。"""

    def test_source_transcript_on_vad_off(self):
        """原文表示 ON × VAD OFF の組み合わせが translator に渡ること。"""
        cs = _make_caption_system(request_source_transcript=True, vad_enabled=False)
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_source_transcript") is True
        assert kwargs.get("vad_enabled") is False

    def test_source_transcript_off_vad_off(self):
        """原文表示 OFF × VAD OFF の組み合わせが translator に渡ること。"""
        cs = _make_caption_system(request_source_transcript=False, vad_enabled=False)
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_source_transcript") is False
        assert kwargs.get("vad_enabled") is False

    def test_source_transcript_on_vad_on(self):
        """原文表示 ON × VAD ON の組み合わせが translator に渡ること。"""
        cs = _make_caption_system(request_source_transcript=True, vad_enabled=True)
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_source_transcript") is True
        assert kwargs.get("vad_enabled") is True

    def test_source_transcript_off_vad_on(self):
        """原文表示 OFF × VAD ON の組み合わせが translator に渡ること。"""
        cs = _make_caption_system(request_source_transcript=False, vad_enabled=True)
        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.return_value = MagicMock()
            cs._create_realtime_translator()
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_source_transcript") is False
        assert kwargs.get("vad_enabled") is True


# ---------------------------------------------------------------------------
# 4-B: 状態遷移テスト
# ---------------------------------------------------------------------------

class TestStateTransitionForLanguageChange:
    """RUNNING → 言語変更 → 自動再起動が走る状態遷移テスト。"""

    def test_idle_to_running_no_language_restart(self):
        """IDLE 状態で言語変更 → 再起動は発生しないこと。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.IDLE)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_a_language_change("sender", "English", None)
            # スレッドが起動しない → 少し待っても呼ばれないことを確認
            time.sleep(0.1)

        mock_restart.assert_not_called()

    def test_running_language_change_triggers_restart(self):
        """RUNNING 状態で言語変更 → _restart_route_for_change が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        restart_done = threading.Event()

        def fake_restart(route_id, reason_label):
            restart_done.set()

        with patch.object(app, "_save_settings"), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = False
            app._on_route_a_language_change("sender", "English", None)
            assert restart_done.wait(timeout=2.0), "再起動が2秒以内に呼ばれること"

    def test_stop_then_idle_language_change_saves_only(self):
        """停止後（IDLE）に言語変更 → 保存のみ（再起動なし）。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.IDLE)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False  # 停止中

        with patch.object(app, "_save_settings") as mock_save, \
             patch.object(app, "_restart_route_for_change") as mock_restart, \
             patch("app.dpg"):
            app._on_route_a_language_change("sender", "English", None)
            time.sleep(0.1)

        mock_save.assert_called_once()
        mock_restart.assert_not_called()

    def test_stop_start_new_translator_created(self):
        """stop() → start_route で新しい RealtimeTranslator が生成されること（Fix 1 の効果確認）。"""
        cs = _make_caption_system(target_lang="ja")

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor"):
            MockRT.side_effect = [MagicMock(), MagicMock()]

            cs._create_realtime_translator()
            assert MockRT.call_count == 1

            # stop() → _realtime_translator が None にリセットされる
            cs.stop()
            assert cs._realtime_translator is None, (
                "stop() 後に _realtime_translator が None になること（Fix 1）"
            )

            # start() 相当: _create_realtime_translator() を呼ぶと新インスタンス生成
            cs._create_realtime_translator()
            assert MockRT.call_count == 2, (
                "stop() 後の _create_realtime_translator() で新インスタンスが生成されること"
            )


# ---------------------------------------------------------------------------
# 4-C: 保存復元テスト
# ---------------------------------------------------------------------------

class TestSettingsSaveRestoreLanguage:
    """翻訳先言語の保存・復元テスト。"""

    def _run_save(self, widget_values: dict) -> dict:
        """dpg と open() をモックして _save_settings() を呼び、書き込まれた dict を返す。"""
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

        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.side_effect = lambda tag: tag in widget_values
        mock_dpg.get_value.side_effect = lambda tag: widget_values.get(tag, "")

        with patch.object(app, "dpg", mock_dpg), \
             patch.object(app, "_dpg_ready", True), \
             patch("builtins.open", fake_open), \
             patch.object(app, "json") as json_mock:

            def capture_dump(data, f, **kwargs):
                written_data.update(data)

            json_mock.dump.side_effect = capture_dump
            app._save_settings()

        return written_data

    def test_route_a_lang_saved_as_japanese(self):
        """系統A の翻訳先言語「日本語」が settings.json に保存されること。"""
        data = self._run_save({
            app.TAG_ROUTE_A_LANG_COMBO: "日本語",
            app.TAG_ROUTE_B_LANG_COMBO: "English",
        })
        assert data.get("route_a", {}).get("lang") == "日本語", (
            f"route_a.lang が '日本語' で保存されること。実際: {data.get('route_a', {}).get('lang')}"
        )

    def test_route_b_lang_saved_as_english(self):
        """系統B の翻訳先言語「English」が settings.json に保存されること。"""
        data = self._run_save({
            app.TAG_ROUTE_A_LANG_COMBO: "日本語",
            app.TAG_ROUTE_B_LANG_COMBO: "English",
        })
        assert data.get("route_b", {}).get("lang") == "English", (
            f"route_b.lang が 'English' で保存されること。実際: {data.get('route_b', {}).get('lang')}"
        )

    def test_route_a_lang_saved_as_english(self):
        """系統A の翻訳先言語「English」が settings.json に保存されること。"""
        data = self._run_save({
            app.TAG_ROUTE_A_LANG_COMBO: "English",
        })
        assert data.get("route_a", {}).get("lang") == "English"

    def test_route_b_lang_saved_as_japanese(self):
        """系統B の翻訳先言語「日本語」が settings.json に保存されること。"""
        data = self._run_save({
            app.TAG_ROUTE_B_LANG_COMBO: "日本語",
        })
        assert data.get("route_b", {}).get("lang") == "日本語"


class TestSettingsLoadRestore:
    """設定値の load/restore テスト。"""

    def _load_settings_from_dict(self, data: dict) -> dict:
        """JSON データを一時ファイルに書いて _load_settings() で読み込む。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            tmp_path = f.name

        try:
            with patch.object(app, "_SETTINGS_PATH", tmp_path):
                return app._load_settings()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_load_restores_route_a_lang(self):
        """_load_settings() が route_a.lang を正しく復元すること。"""
        result = self._load_settings_from_dict({
            "route_a": {"lang": "English"},
            "route_b": {"lang": "日本語"},
        })
        assert result.get("route_a", {}).get("lang") == "English"

    def test_load_restores_route_b_lang(self):
        """_load_settings() が route_b.lang を正しく復元すること。"""
        result = self._load_settings_from_dict({
            "route_a": {"lang": "日本語"},
            "route_b": {"lang": "English"},
        })
        assert result.get("route_b", {}).get("lang") == "English"

    def test_load_missing_lang_key_returns_empty(self):
        """lang キーがない場合、デフォルト（空文字）を返すこと。"""
        result = self._load_settings_from_dict({"route_a": {}})
        assert result.get("route_a", {}).get("lang", "") == ""

    def test_load_missing_route_a_returns_empty_dict(self):
        """route_a キーがない場合、空 dict が返ること（既存テスト再確認）。"""
        result = self._load_settings_from_dict({})
        assert result.get("route_a") is None or result.get("route_a") == {}

    def test_load_output_volume_restored(self):
        """出力音量が復元されること（0.0〜2.0 の範囲）。"""
        result = self._load_settings_from_dict({
            "route_a": {"output_volume": 1.5},
        })
        assert result.get("route_a", {}).get("output_volume") == 1.5

    def test_load_vad_enabled_defaults_to_false(self):
        """vad_enabled が保存されていない場合デフォルト False になること。"""
        result = self._load_settings_from_dict({"route_a": {}})
        vad = result.get("route_a", {}).get("vad_enabled", False)
        assert vad is False

    def test_load_source_transcript_defaults_to_true(self):
        """source_transcript_enabled が保存されていない場合デフォルト True になること。"""
        result = self._load_settings_from_dict({"route_a": {}})
        src = result.get("route_a", {}).get("source_transcript_enabled", True)
        assert src is True

    def test_load_from_missing_file_returns_empty(self):
        """settings.json が存在しない場合 {} が返ること。"""
        with patch.object(app, "_SETTINGS_PATH", "/nonexistent/path/settings.json"):
            result = app._load_settings()
        assert result == {}


class TestRouteConfigLanguage:
    """RouteConfig target_language_code の伝播テスト。"""

    def test_route_config_target_language_code_ja(self):
        """RouteConfig(target_language_code='ja') が CaptionSystem に伝播すること。"""
        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            rc = RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
            )
            mcs = MultiCaptionSystem(
                config=_make_realtime_config(),
                route_a=rc,
                route_b=None,
            )
        assert mcs.route_a_system is not None
        assert mcs.route_a_system._config["openai_realtime"]["target_language_code"] == "ja"

    def test_route_config_target_language_code_en(self):
        """RouteConfig(target_language_code='en') が CaptionSystem に伝播すること。"""
        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            rc = RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="en",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
            )
            mcs = MultiCaptionSystem(
                config=_make_realtime_config(),
                route_a=rc,
                route_b=None,
            )
        assert mcs.route_a_system is not None
        assert mcs.route_a_system._config["openai_realtime"]["target_language_code"] == "en"
