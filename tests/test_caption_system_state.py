"""
tests/test_caption_system_state.py

PR-1: RouteState enum + CaptionSystem.state プロパティ + stop()/shutdown() thin wrapper テスト。

TDD RED フェーズ: RouteState が存在しない段階で作成したテスト。
"""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_minimal_caption_system():
    """object.__new__ でバイパスして、状態管理テスト用の最小限インスタンスを作る。

    CaptionSystem の __init__ を呼ばずに state 関連フィールドだけ設定する。
    """
    from main import AudioStats, CaptionSystem, RouteState

    cs = object.__new__(CaptionSystem)
    # state 管理
    cs._state = RouteState.IDLE
    cs._state_lock = threading.Lock()
    # shutdown()/stop() が参照するフィールド
    cs._stop_event = threading.Event()
    cs._realtime_translator = None
    cs._cost_monitor = None
    cs._recorder = None
    cs._loop = None
    cs._stop_event_async = None
    cs._audio_stream = None
    cs._capture_stream = None
    cs._capture_thread = None
    # audio_stats（shutdown 内では参照されないが念のため）
    cs._audio_stats_lock = threading.Lock()
    cs._audio_stats = AudioStats()
    # route_id（ログ出力用）
    cs._route_id = "test"
    # verbose ログ（_set_state から _log_verbose が呼ばれるため必要）
    cs.verbose = False
    cs._verbose_log_path = None
    cs._verbose_lock = threading.Lock()
    cs._idle_monitor = None
    return cs


# ---------------------------------------------------------------------------
# RouteState enum テスト
# ---------------------------------------------------------------------------

class TestRouteStateEnum:
    """RouteState enum が main.py に存在し、期待する値を持つこと。"""

    def test_route_state_enum_exists(self):
        """RouteState が main モジュールから import できること。"""
        from main import RouteState
        assert RouteState is not None

    def test_route_state_has_idle(self):
        from main import RouteState
        assert RouteState.IDLE.value == "idle"

    def test_route_state_has_starting(self):
        from main import RouteState
        assert RouteState.STARTING.value == "starting"

    def test_route_state_has_running(self):
        from main import RouteState
        assert RouteState.RUNNING.value == "running"

    def test_route_state_has_stopping(self):
        from main import RouteState
        assert RouteState.STOPPING.value == "stopping"

    def test_route_state_has_error(self):
        from main import RouteState
        assert RouteState.ERROR.value == "error"


# ---------------------------------------------------------------------------
# CaptionSystem.state プロパティ テスト
# ---------------------------------------------------------------------------

class TestCaptionSystemStateProperty:
    """CaptionSystem が state プロパティを持ち、正しく動作すること。"""

    def test_initial_state_is_idle(self):
        """CaptionSystem の初期状態は IDLE であること。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        assert cs.state == RouteState.IDLE

    def test_state_property_returns_route_state(self):
        """state プロパティが RouteState 型を返すこと。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        assert isinstance(cs.state, RouteState)

    def test_state_property_is_thread_safe(self):
        """state プロパティを複数スレッドから同時にアクセスしても例外が起きないこと。"""
        cs = _make_minimal_caption_system()
        errors = []

        def read_state():
            try:
                for _ in range(100):
                    _ = cs.state
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_state) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"スレッドセーフアクセス中に例外: {errors}"

    def test_set_state_changes_state(self):
        """_set_state() が state を変更すること。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        cs._set_state(RouteState.RUNNING)
        assert cs.state == RouteState.RUNNING

    def test_set_state_logs_transition_on_change(self, capsys):
        """_set_state() が状態変更時にログを出力すること（同一状態では出力しない）。"""
        from main import RouteState
        cs = _make_minimal_caption_system()

        cs._set_state(RouteState.RUNNING)
        captured = capsys.readouterr()
        assert "idle" in captured.out
        assert "running" in captured.out

    def test_set_state_no_log_when_same(self, capsys):
        """_set_state() で同じ状態をセットしてもログを出力しないこと。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        cs._set_state(RouteState.IDLE)  # IDLE → IDLE
        captured = capsys.readouterr()
        assert captured.out == ""


# ---------------------------------------------------------------------------
# stop() メソッド テスト
# ---------------------------------------------------------------------------

class TestCaptionSystemStop:
    """CaptionSystem.stop() が正しく動作すること。"""

    def test_shutdown_transitions_to_idle(self):
        """shutdown() を呼ぶと state が IDLE のままであること（IDLE → shutdown は no-op に近い）。

        既存の shutdown() ロジック: _stop_event.is_set() なら即返る。
        IDLE 状態では _stop_event は未セット → stop() が実行されて IDLE に遷移。
        """
        from main import RouteState
        cs = _make_minimal_caption_system()
        cs.shutdown()
        assert cs.state == RouteState.IDLE

    def test_stop_transitions_to_idle(self):
        """stop() を呼ぶと state が IDLE になること。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        cs.stop()
        assert cs.state == RouteState.IDLE

    def test_stop_alias_for_shutdown(self):
        """stop() と shutdown() は同じ動作（state IDLE）であること。"""
        from main import RouteState

        cs1 = _make_minimal_caption_system()
        cs1.stop()

        cs2 = _make_minimal_caption_system()
        cs2.shutdown()

        assert cs1.state == RouteState.IDLE
        assert cs2.state == RouteState.IDLE

    def test_stop_idempotent(self):
        """stop() を多重呼び出ししても no-op（2回目は例外なく返る）。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        cs.stop()
        cs.stop()  # 2回目も正常に返ること
        assert cs.state == RouteState.IDLE

    def test_stop_idempotent_already_stopping(self):
        """stop() 呼び出し中（STOPPING 状態）に再度 stop() を呼んでも no-op。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        cs._state = RouteState.STOPPING
        cs._stop_event.set()  # stop_event もセットして整合性確保
        cs.stop()  # no-op であること（例外なし）
        # 状態は IDLE または STOPPING のいずれか（no-op なので変化なし）
        assert cs.state in (RouteState.IDLE, RouteState.STOPPING)

    def test_stop_resets_internal_state(self):
        """stop() 後に _stop_event / _loop がリセットされること。

        リセット漏れがあると次回 start() が即 IDLE に落ちる（仕様書 §リスク 1）。
        """
        cs = _make_minimal_caption_system()
        # 事前に _loop をセット（稼働中を模擬）
        cs._loop = MagicMock()
        cs.stop()
        # _loop は None にリセットされていること
        assert cs._loop is None, "_loop が stop() 後にリセットされていない"
        # _stop_event は新しい Event（再生成）or セットされた Event ではないこと
        assert not cs._stop_event.is_set(), "_stop_event が stop() 後にリセットされていない"

    def test_stop_resets_stop_event_async(self):
        """stop() 後に _stop_event_async が None にリセットされること。"""
        cs = _make_minimal_caption_system()
        cs._stop_event_async = MagicMock()
        cs.stop()
        assert cs._stop_event_async is None, "_stop_event_async が None にリセットされていない"

    def test_stop_resets_capture_thread_ref(self):
        """stop() 後に _capture_thread が None にリセットされること。"""
        cs = _make_minimal_caption_system()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        cs._capture_thread = mock_thread
        cs.stop()
        assert cs._capture_thread is None, "_capture_thread が None にリセットされていない"

    def test_stop_resets_capture_stream_ref(self):
        """stop() 後に _capture_stream が None にリセットされること。"""
        cs = _make_minimal_caption_system()
        mock_stream = MagicMock()
        cs._capture_stream = mock_stream
        cs.stop()
        assert cs._capture_stream is None, "_capture_stream が None にリセットされていない"


# ---------------------------------------------------------------------------
# shutdown() thin wrapper テスト
# ---------------------------------------------------------------------------

class TestShutdownThinWrapper:
    """shutdown() が stop() を呼ぶ thin wrapper であること。"""

    def test_shutdown_calls_stop(self):
        """shutdown() は内部で stop() を呼ぶこと（thin wrapper）。"""
        from main import CaptionSystem
        cs = _make_minimal_caption_system()

        stop_called = []
        original_stop = cs.stop

        def mock_stop():
            stop_called.append(True)
            original_stop()

        cs.stop = mock_stop
        cs.shutdown()

        assert stop_called, "shutdown() が stop() を呼んでいない"

    def test_shutdown_is_idempotent_via_stop(self):
        """shutdown() を複数回呼んでも no-op（stop() の冪等性を継承）。"""
        from main import RouteState
        cs = _make_minimal_caption_system()
        cs.shutdown()
        cs.shutdown()
        assert cs.state == RouteState.IDLE


# ---------------------------------------------------------------------------
# PR-2: CaptionSystem.start() テスト
# ---------------------------------------------------------------------------

def _make_realtime_caption_config(api_key: str = "sk-test-fake-0000000000000000") -> dict:
    """openai-realtime モード用の最小限設定 dict（フェイク値のみ使用）。"""
    return {
        "translation": {
            "translation_model": "openai-realtime",
        },
        "openai": {
            "api_key": api_key,
        },
        "openai_realtime": {
            "target_language_code": "ja",
            "model": "gpt-realtime-translate",
            "connect_timeout": 10,
            "reconnect_max_attempts": 5,
            "reconnect_backoff_base": 1.5,
            "max_session_minutes": 60,
            "audio_output": {},
        },
        "output": {
            "log_dir": ".",
        },
        "websocket": {
            "host": "127.0.0.1",
            "port": 18765,
        },
    }


def _make_fake_device_info() -> dict:
    return {
        "index": 0,
        "name": "FakeDevice",
        "defaultSampleRate": 44100,
        "maxInputChannels": 2,
    }


class TestCaptionSystemStartMethod:
    """PR-2: CaptionSystem.start() の動作テスト。"""

    def test_init_does_not_check_api_key(self):
        """__init__ で API キー未設定でも ValueError が発生しないこと（PR-2 仕様）。

        PR-2 以前: api_key 未設定で ValueError が上がっていた。
        PR-2 以降: __init__ ではチェックせず、start() 時に遅延チェックする。
        """
        config = _make_realtime_caption_config(api_key="")
        device_info = _make_fake_device_info()

        with patch("realtime_translator.RealtimeTranslator"):
            # ValueError が発生しないこと
            from main import CaptionSystem
            cs = CaptionSystem(
                config=config,
                device_info=device_info,
                model_name="tiny",
            )
        assert cs is not None

    def test_realtime_translator_lazy_init(self):
        """__init__ 直後は _realtime_translator が None であること（lazy init）。

        start() を呼ぶ前は RealtimeTranslator インスタンスが生成されていないことを確認。
        """
        config = _make_realtime_caption_config(api_key="sk-test-fake-0000000000000000")
        device_info = _make_fake_device_info()

        from main import CaptionSystem
        with patch("realtime_translator.RealtimeTranslator"):
            cs = CaptionSystem(
                config=config,
                device_info=device_info,
                model_name="tiny",
            )

        # __init__ 直後は lazy init のため None
        assert cs._realtime_translator is None, (
            "__init__ 直後に _realtime_translator が None でない（lazy init が実装されていない）"
        )

    def test_start_with_no_api_key_transitions_to_error(self):
        """start() で API キー未設定なら state=ERROR + on_error コールバック呼び出し。"""
        from main import CaptionSystem, RouteState

        config = _make_realtime_caption_config(api_key="")
        device_info = _make_fake_device_info()
        error_messages = []

        with patch("realtime_translator.RealtimeTranslator"):
            cs = CaptionSystem(
                config=config,
                device_info=device_info,
                model_name="tiny",
                on_realtime_error_external=lambda msg: error_messages.append(msg),
            )

        cs.start()

        assert cs.state == RouteState.ERROR, (
            f"API キー未設定時に state が ERROR にならない（actual: {cs.state}）"
        )
        assert len(error_messages) == 1, (
            f"on_realtime_error_external が1回呼ばれるはずが {len(error_messages)} 回呼ばれた"
        )

    def test_start_idempotent_when_running(self):
        """start() を RUNNING 状態で呼んでも no-op（state は RUNNING のまま）。"""
        from main import CaptionSystem, RouteState

        # _make_minimal_caption_system を使って RUNNING 状態を模擬
        cs = _make_minimal_caption_system()
        cs._state = RouteState.RUNNING
        cs._realtime_mode = False  # 非 realtime モードで最小化

        # start() が定義されていない場合は AttributeError で fail する
        cs.start()

        assert cs.state == RouteState.RUNNING, (
            f"RUNNING 状態で start() を呼んだ後に state が変化した（actual: {cs.state}）"
        )

    def test_start_idempotent_when_starting(self):
        """start() を STARTING 状態で呼んでも no-op（state は STARTING のまま）。"""
        from main import CaptionSystem, RouteState

        cs = _make_minimal_caption_system()
        cs._state = RouteState.STARTING
        cs._realtime_mode = False

        cs.start()

        assert cs.state == RouteState.STARTING, (
            f"STARTING 状態で start() を呼んだ後に state が変化した（actual: {cs.state}）"
        )
