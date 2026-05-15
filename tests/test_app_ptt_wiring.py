"""
tests/test_app_ptt_wiring.py

app.py の PTT 結線テスト (PR3)。

テスト対象:
- PTT 押下で start_route("b") が呼ばれること
- PTT 離脱で stop_route("b") が呼ばれること
- ptt_enabled=False 時は PttHotkeyManager.start() を呼ばないこと
- 設定読み込み (ptt_enabled, ptt_hotkey) のデフォルト値確認
- 設定保存 (ptt_enabled, ptt_hotkey)
- 終了時 _ptt_manager.stop() が呼ばれること
- _konnyaku_running=False の場合は start_route が呼ばれないこと
- チャタリング警告コールバックがログ出力すること

docs/spec/ptt-mode-design.md F-2 / F-5 / F-7 参照
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import app


# ---------------------------------------------------------------------------
# ヘルパー: FakeKeyboardBackend（ptt_hotkey_manager テストから再利用）
# ---------------------------------------------------------------------------

class FakeKeyboardBackend:
    """keyboard モジュールの代替。simulate_press/simulate_release でイベント発火。"""

    def __init__(self):
        self._press_handlers: dict[str, list] = {}
        self._release_handlers: dict[str, list] = {}
        self.unhook_all_called = 0

    def on_press_key(self, key: str, callback, suppress: bool = False):
        self._press_handlers.setdefault(key, []).append(callback)
        return (key, "press", callback)

    def on_release_key(self, key: str, callback, suppress: bool = False):
        self._release_handlers.setdefault(key, []).append(callback)
        return (key, "release", callback)

    def unhook_all(self):
        self.unhook_all_called += 1
        self._press_handlers.clear()
        self._release_handlers.clear()

    def simulate_press(self, key: str):
        event = MagicMock()
        event.name = key
        for cb in self._press_handlers.get(key, []):
            cb(event)

    def simulate_release(self, key: str):
        event = MagicMock()
        event.name = key
        for cb in self._release_handlers.get(key, []):
            cb(event)


# ---------------------------------------------------------------------------
# FakeTimerFactory: 離脱デバウンスタイマーを同期的に実行する
# ---------------------------------------------------------------------------

class FakeTimer:
    """threading.Timer 互換の同期タイマー。start() で即座にコールバックを呼ぶ。"""

    def __init__(self, interval, func):
        self._func = func
        self._cancelled = False

    def start(self):
        if not self._cancelled:
            self._func()

    def cancel(self):
        self._cancelled = True


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_app_ptt_state():
    """各テスト前後に app モジュールの PTT グローバル状態をリセットする。"""
    # テスト前: manager を停止してリセット
    old_manager = app._ptt_manager
    if old_manager is not None:
        try:
            old_manager.stop()
        except Exception:
            pass

    old_running = app._konnyaku_running
    old_system = app._konnyaku_system

    yield

    # テスト後: グローバルを元に戻す
    new_manager = app._ptt_manager
    if new_manager is not None and new_manager is not old_manager:
        try:
            new_manager.stop()
        except Exception:
            pass

    app._ptt_manager = old_manager
    app._konnyaku_running = old_running
    app._konnyaku_system = old_system


# ---------------------------------------------------------------------------
# テスト: PTT 押下 → start_route("b") 呼び出し
# ---------------------------------------------------------------------------

class TestPttPress:
    def test_press_calls_start_route_b(self):
        """PTT 押下時に start_route('b') が呼ばれること（F-2.1）。"""
        fake_kb = FakeKeyboardBackend()
        mock_system = MagicMock()

        app._konnyaku_system = mock_system
        app._konnyaku_running = True

        app._init_ptt_manager(
            ptt_enabled=True,
            ptt_hotkey="f8",
            keyboard_module=fake_kb,
            timer_factory=FakeTimer,
        )

        fake_kb.simulate_press("f8")

        # start_route はスレッドで呼ばれるため、少し待つ
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if mock_system.start_route.called:
                break
            time.sleep(0.05)

        mock_system.start_route.assert_called_once_with("b")

    def test_press_noop_when_not_running(self):
        """_konnyaku_running=False 時は start_route が呼ばれないこと（F-7.4）。"""
        fake_kb = FakeKeyboardBackend()
        mock_system = MagicMock()

        app._konnyaku_system = mock_system
        app._konnyaku_running = False

        app._init_ptt_manager(
            ptt_enabled=True,
            ptt_hotkey="f8",
            keyboard_module=fake_kb,
            timer_factory=FakeTimer,
        )

        fake_kb.simulate_press("f8")
        time.sleep(0.1)

        mock_system.start_route.assert_not_called()

    def test_press_noop_when_system_is_none(self):
        """_konnyaku_system=None 時は start_route が呼ばれないこと。"""
        fake_kb = FakeKeyboardBackend()

        app._konnyaku_system = None
        app._konnyaku_running = True

        app._init_ptt_manager(
            ptt_enabled=True,
            ptt_hotkey="f8",
            keyboard_module=fake_kb,
            timer_factory=FakeTimer,
        )

        # 例外が起きないことを確認
        fake_kb.simulate_press("f8")
        time.sleep(0.1)
        # assert_not_called は system が None なので呼べない。例外なく完了で OK。


# ---------------------------------------------------------------------------
# テスト: PTT 離脱 → stop_route("b") 呼び出し
# ---------------------------------------------------------------------------

class TestPttRelease:
    def test_release_calls_stop_route_b(self):
        """PTT 離脱時に stop_route('b') が呼ばれること（F-2.2, FakeTimer で即座に実行）。"""
        fake_kb = FakeKeyboardBackend()
        mock_system = MagicMock()

        app._konnyaku_system = mock_system
        app._konnyaku_running = True

        app._init_ptt_manager(
            ptt_enabled=True,
            ptt_hotkey="f8",
            keyboard_module=fake_kb,
            timer_factory=FakeTimer,
        )

        # 先に押下してから離脱（初回押下がないと離脱が無視される）
        fake_kb.simulate_press("f8")
        # start_route の呼び出しを待つ
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if mock_system.start_route.called:
                break
            time.sleep(0.05)

        fake_kb.simulate_release("f8")

        # stop_route はスレッドで呼ばれる（FakeTimer は即座に実行するが別スレッド）
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if mock_system.stop_route.called:
                break
            time.sleep(0.05)

        mock_system.stop_route.assert_called_once_with("b")

    def test_release_noop_when_not_running(self):
        """_konnyaku_running=False 時は stop_route が呼ばれないこと。"""
        fake_kb = FakeKeyboardBackend()
        mock_system = MagicMock()

        app._konnyaku_system = mock_system
        app._konnyaku_running = False

        app._init_ptt_manager(
            ptt_enabled=True,
            ptt_hotkey="f8",
            keyboard_module=fake_kb,
            timer_factory=FakeTimer,
        )

        # 押下→離脱の両方を行う（ただし running=False なので start_route は呼ばれない）
        fake_kb.simulate_press("f8")
        time.sleep(0.1)
        fake_kb.simulate_release("f8")
        time.sleep(0.1)

        mock_system.stop_route.assert_not_called()


# ---------------------------------------------------------------------------
# テスト: ptt_enabled=False 時は start() を呼ばない
# ---------------------------------------------------------------------------

class TestPttDisabled:
    def test_disabled_does_not_start_manager(self):
        """ptt_enabled=False の場合、PttHotkeyManager.start() が呼ばれないこと（F-5）。"""
        fake_kb = FakeKeyboardBackend()

        app._init_ptt_manager(
            ptt_enabled=False,
            ptt_hotkey="f8",
            keyboard_module=fake_kb,
            timer_factory=FakeTimer,
        )

        # running=False = start() が呼ばれていない
        assert app._ptt_manager is not None
        assert app._ptt_manager.running is False

    def test_disabled_press_does_not_fire_callback(self):
        """ptt_enabled=False のとき、ホットキーを押しても on_press が発火しないこと。"""
        fake_kb = FakeKeyboardBackend()
        mock_system = MagicMock()

        app._konnyaku_system = mock_system
        app._konnyaku_running = True

        app._init_ptt_manager(
            ptt_enabled=False,
            ptt_hotkey="f8",
            keyboard_module=fake_kb,
            timer_factory=FakeTimer,
        )

        # fake_kb に登録されていないため simulate_press しても何も起きない
        fake_kb.simulate_press("f8")
        time.sleep(0.1)

        mock_system.start_route.assert_not_called()


# ---------------------------------------------------------------------------
# テスト: 設定読み込み（_load_ptt_settings）
# ---------------------------------------------------------------------------

class TestPttSettingsLoad:
    def test_load_defaults_when_key_missing(self):
        """settings.json に ptt キーがない場合、デフォルト値が返ること。"""
        result = app._load_ptt_settings({})
        assert result["ptt_enabled"] is False
        assert result["ptt_hotkey"] == "f8"

    def test_load_ptt_enabled_true(self):
        """route_b.ptt_enabled=True が読み込まれること。"""
        saved = {"route_b": {"ptt_enabled": True, "ptt_hotkey": "f9"}}
        result = app._load_ptt_settings(saved)
        assert result["ptt_enabled"] is True
        assert result["ptt_hotkey"] == "f9"

    def test_load_ptt_hotkey_default(self):
        """route_b.ptt_hotkey が未設定の場合、デフォルト 'f8' になること。"""
        saved = {"route_b": {"ptt_enabled": True}}
        result = app._load_ptt_settings(saved)
        assert result["ptt_hotkey"] == "f8"

    def test_load_ptt_enabled_default_false(self):
        """route_b.ptt_enabled が未設定の場合、デフォルト False になること。"""
        saved = {"route_b": {"ptt_hotkey": "f9"}}
        result = app._load_ptt_settings(saved)
        assert result["ptt_enabled"] is False

    def test_load_ignores_top_level_route_b_without_ptt(self):
        """route_b に他のキーがあっても ptt キーがなければデフォルトになること。"""
        saved = {"route_b": {"enabled": True, "device": "some device"}}
        result = app._load_ptt_settings(saved)
        assert result["ptt_enabled"] is False
        assert result["ptt_hotkey"] == "f8"


# ---------------------------------------------------------------------------
# テスト: 設定保存（_build_ptt_settings_dict）
# ---------------------------------------------------------------------------

class TestPttSettingsSave:
    def test_build_ptt_settings_merges_into_route_b(self):
        """PTT 設定が route_b に正しくマージされること。"""
        existing = {
            "route_b": {
                "enabled": True,
                "device": "Microphone",
            }
        }
        result = app._build_ptt_settings_dict(
            existing_data=existing,
            ptt_enabled=True,
            ptt_hotkey="f9",
        )
        assert result["route_b"]["ptt_enabled"] is True
        assert result["route_b"]["ptt_hotkey"] == "f9"
        # 既存キーを壊していないこと
        assert result["route_b"]["enabled"] is True
        assert result["route_b"]["device"] == "Microphone"

    def test_build_ptt_settings_creates_route_b_if_missing(self):
        """route_b キーが存在しない場合でも PTT 設定を追加できること。"""
        result = app._build_ptt_settings_dict(
            existing_data={},
            ptt_enabled=False,
            ptt_hotkey="f8",
        )
        assert "route_b" in result
        assert result["route_b"]["ptt_enabled"] is False
        assert result["route_b"]["ptt_hotkey"] == "f8"

    def test_build_ptt_settings_false(self):
        """ptt_enabled=False が保存されること。"""
        result = app._build_ptt_settings_dict(
            existing_data={"route_b": {}},
            ptt_enabled=False,
            ptt_hotkey="f8",
        )
        assert result["route_b"]["ptt_enabled"] is False


# ---------------------------------------------------------------------------
# テスト: 終了時 _ptt_manager.stop() が呼ばれること
# ---------------------------------------------------------------------------

class TestPttCleanup:
    def test_cleanup_calls_stop(self):
        """_cleanup_ptt_manager() を呼ぶと _ptt_manager.stop() が呼ばれること（F-7.5）。"""
        mock_manager = MagicMock()
        app._ptt_manager = mock_manager

        app._cleanup_ptt_manager()

        mock_manager.stop.assert_called_once()

    def test_cleanup_noop_when_manager_is_none(self):
        """_ptt_manager が None の場合に _cleanup_ptt_manager() が例外を出さないこと。"""
        app._ptt_manager = None
        # 例外なく完了すればOK
        app._cleanup_ptt_manager()

    def test_cleanup_sets_manager_to_none(self):
        """_cleanup_ptt_manager() 後に _ptt_manager が None になること。"""
        mock_manager = MagicMock()
        app._ptt_manager = mock_manager

        app._cleanup_ptt_manager()

        assert app._ptt_manager is None


# ---------------------------------------------------------------------------
# テスト: チャタリング警告コールバック
# ---------------------------------------------------------------------------

class TestPttChatterWarning:
    def test_chatter_warning_logs_message(self, capsys):
        """on_chatter_warning が発火されると警告ログが出力されること（F-4.3）。"""
        app._on_ptt_chatter_warning()
        captured = capsys.readouterr()
        assert "[PTT]" in captured.out or "chatter" in captured.out.lower() or "連打" in captured.out


# ---------------------------------------------------------------------------
# テスト: _update_konnyaku_level_meters の PTT 対応
# ---------------------------------------------------------------------------

class TestLevelMeterPttMode:
    def test_level_meter_uses_route_state_when_ptt_enabled(self):
        """PTT モード ON 時は RouteState.RUNNING で route_b の有効判定をすること。

        チェックボックス (TAG_ROUTE_B_ENABLE) の値に依存せず、
        実際の RouteState.RUNNING で判定する関数を呼べることを確認する。
        """
        # _ptt_enabled=True のとき _is_route_b_active_for_meter() が
        # RouteState ベースで True を返すことをテスト
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.RUNNING

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b

        app._konnyaku_system = mock_system

        # PTT ON + RUNNING → True
        result = app._is_route_b_active_for_meter(ptt_enabled=True)
        assert result is True

    def test_level_meter_route_b_idle_when_ptt_enabled(self):
        """PTT モード ON + RouteState.IDLE → False を返すこと。"""
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.IDLE

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b

        app._konnyaku_system = mock_system

        result = app._is_route_b_active_for_meter(ptt_enabled=True)
        assert result is False
