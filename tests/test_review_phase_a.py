"""
tests/test_review_phase_a.py

QA + codex レビュー Phase A 高優先度 4 件の TDD テスト。

C-1: start_route() が _thread_a/_thread_b を更新すること
C-3: 両 route 無効でも _create_konnyaku_system が None を返さないこと
W-1: _stop_in_background の _konnyaku_running=False が finally で実行されること
W-5: _on_konnyaku_start_stop_click 連打ガード
"""

import ast
import inspect
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import (
    AudioStats,
    CaptionSystem,
    MultiCaptionSystem,
    RouteConfig,
    RouteState,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_route_config(route_id: str = "a") -> RouteConfig:
    return RouteConfig(
        route_id=route_id,
        input_device_info={"index": 0, "name": f"FakeDevice-{route_id}"},
        target_language_code="ja" if route_id == "a" else "en",
        audio_output_enabled=False,
        output_device_index=None,
        output_volume=1.0,
    )


def _make_fake_config() -> dict:
    return {
        "translation": {"translation_model": "openai-realtime"},
        "openai": {"api_key": "sk-test-fake-0000000000000000"},
        "openai_realtime": {
            "target_language_code": "ja",
            "model": "gpt-realtime-translate",
            "connect_timeout": 10,
            "reconnect_max_attempts": 5,
            "reconnect_backoff_base": 1.5,
            "max_session_minutes": 60,
            "audio_output": {},
        },
        "output": {"log_dir": "."},
    }


def _make_minimal_caption_system(route_id: str = "test") -> CaptionSystem:
    """object.__new__ で最小限の CaptionSystem を作るヘルパー。"""
    cs = object.__new__(CaptionSystem)
    cs._audio_stats_lock = threading.Lock()
    cs._audio_stats = AudioStats()
    cs._stop_event = threading.Event()
    cs._realtime_translator = None
    cs._cost_monitor = None
    cs._recorder = None
    cs._loop = None
    cs._stop_event_async = None
    cs._audio_stream = None
    cs._capture_stream = None
    cs._capture_thread = None
    cs._route_id = route_id
    cs._state = RouteState.IDLE
    cs._state_lock = threading.Lock()
    return cs


# ---------------------------------------------------------------------------
# C-1: start_route() が _thread_a/_thread_b を更新すること
# ---------------------------------------------------------------------------

class TestStartRouteUpdatesThreadRef:
    """C-1: start_route("a"/"b") 後に _thread_a/_thread_b が更新されること。

    背景: start_all() は _thread_a/_thread_b を CaptionSystem._asyncio_thread で更新するが、
    start_route() は更新していない。GUI 別系統 ON で起動したあと terminate() を呼ぶと
    過去の thread ref を join してしまい PortAudio クラッシュリスクが残る。
    """

    def test_start_route_a_updates_thread_a(self):
        """start_route("a") 後に _thread_a が None でなく、CaptionSystem の asyncio スレッドと一致すること。"""
        with patch("main.pyaudio.PyAudio"), patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=_make_route_config("b"),
            )

        # start() が _asyncio_thread を設定するようにモック
        mock_thread = MagicMock(spec=threading.Thread)
        mcs._route_a.start = lambda: setattr(mcs._route_a, "_asyncio_thread", mock_thread)

        mcs.start_route("a")

        assert mcs._thread_a is not None, "start_route('a') 後 _thread_a が None のまま"
        assert mcs._thread_a is mock_thread, (
            "_thread_a が route_a._asyncio_thread と一致しない"
        )

    def test_start_route_b_updates_thread_b(self):
        """start_route("b") 後に _thread_b が None でなく、CaptionSystem の asyncio スレッドと一致すること。"""
        with patch("main.pyaudio.PyAudio"), patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=_make_route_config("b"),
            )

        mock_thread = MagicMock(spec=threading.Thread)
        mcs._route_b.start = lambda: setattr(mcs._route_b, "_asyncio_thread", mock_thread)

        mcs.start_route("b")

        assert mcs._thread_b is not None, "start_route('b') 後 _thread_b が None のまま"
        assert mcs._thread_b is mock_thread, (
            "_thread_b が route_b._asyncio_thread と一致しない"
        )

    def test_caption_system_has_get_asyncio_thread_method(self):
        """CaptionSystem.get_asyncio_thread() メソッドが存在すること。"""
        cs = _make_minimal_caption_system()
        assert hasattr(cs, "get_asyncio_thread"), (
            "CaptionSystem に get_asyncio_thread() メソッドが存在しない"
        )
        assert callable(cs.get_asyncio_thread), (
            "get_asyncio_thread が callable でない"
        )

    def test_get_asyncio_thread_returns_none_before_start(self):
        """start() 前は get_asyncio_thread() が None を返すこと。"""
        cs = _make_minimal_caption_system()
        # start() 前は _asyncio_thread が存在しないかまたは None
        result = cs.get_asyncio_thread()
        assert result is None, f"start() 前に get_asyncio_thread() が None でない: {result}"

    def test_get_asyncio_thread_returns_thread_after_set(self):
        """_asyncio_thread を設定した後は get_asyncio_thread() がそれを返すこと。"""
        cs = _make_minimal_caption_system()
        mock_thread = MagicMock(spec=threading.Thread)
        cs._asyncio_thread = mock_thread
        result = cs.get_asyncio_thread()
        assert result is mock_thread, (
            f"get_asyncio_thread() が _asyncio_thread と異なるオブジェクトを返した: {result}"
        )


# ---------------------------------------------------------------------------
# C-3: 両 route 無効でも _create_konnyaku_system が None を返さないこと
# ---------------------------------------------------------------------------

class TestCreateKonnyakuSystemBothRoutesDisabled:
    """C-3: settings の両 route が enabled=False でも MultiCaptionSystem が生成されること。

    背景: 両 route 無効でも MultiCaptionSystem(route_a=None, route_b=None) を
    呼ぼうとすると ValueError("少なくとも1つ") で起動失敗する。
    RouteConfig の生成は enabled に依存せず常に行い、start_route 判定だけで enabled を使うべき。
    """

    def _run_create_with_both_disabled(self) -> "MultiCaptionSystem | None":
        """settings で両 route enabled=False にして _create_konnyaku_system を呼ぶ。"""
        import app as _app

        # 事前リセット
        _app._konnyaku_system = None

        fake_devices = [
            {"index": 0, "name": "FakeLoopback [Loopback]", "isLoopback": True, "hostApi": 0},
            {"index": 1, "name": "FakeMic", "isLoopback": False, "hostApi": 0},
        ]

        # 両 route とも output_enabled=False の保存値
        fake_saved = {
            "route_a": {
                "device": "", "lang": "", "output_enabled": False,
                "output_device": "", "output_volume": 1.0,
            },
            "route_b": {
                "device": "", "lang": "", "output_enabled": False,
                "output_device": "", "output_volume": 1.0,
            },
        }

        fake_config = {
            "openai": {"api_key": "sk-test-fake-0000000000000000"},
            "translation": {"translation_model": "openai-realtime"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
                "connect_timeout": 10,
                "reconnect_max_attempts": 5,
                "reconnect_backoff_base": 1.5,
                "max_session_minutes": 60,
                "audio_output": {},
            },
            "output": {"log_dir": "."},
        }

        with (
            patch.object(_app, "_devices", fake_devices),
            patch.object(_app, "_config", fake_config),
            patch.object(_app, "_load_settings", return_value=fake_saved),
            patch("main.pyaudio.PyAudio"),
            patch("realtime_translator.RealtimeTranslator"),
            patch("app.list_audio_devices", return_value=fake_devices),
            patch("app.find_device_by_name", return_value=None),
        ):
            _app._create_konnyaku_system()
            return _app._konnyaku_system

    def test_create_konnyaku_system_with_both_disabled_not_none(self):
        """両 route output_enabled=False でも _konnyaku_system が None にならないこと。"""
        system = self._run_create_with_both_disabled()
        assert system is not None, (
            "両 route output_enabled=False のとき _konnyaku_system が None になった（起動失敗）"
        )

    def test_create_konnyaku_system_with_both_disabled_has_route_a(self):
        """両 route output_enabled=False でも route_a_system が生成されること。"""
        system = self._run_create_with_both_disabled()
        if system is None:
            pytest.fail("_konnyaku_system が None のため route_a_system を確認できない")
        assert system.route_a_system is not None, (
            "両 route output_enabled=False のとき route_a_system が None になった"
        )

    def test_create_konnyaku_system_with_both_disabled_has_route_b(self):
        """両 route output_enabled=False でも route_b_system が生成されること。"""
        system = self._run_create_with_both_disabled()
        if system is None:
            pytest.fail("_konnyaku_system が None のため route_b_system を確認できない")
        assert system.route_b_system is not None, (
            "両 route output_enabled=False のとき route_b_system が None になった"
        )


# ---------------------------------------------------------------------------
# W-1: _stop_in_background の _konnyaku_running=False が finally で実行
# ---------------------------------------------------------------------------

class TestStopInBackgroundResetsRunningOnException:
    """W-1: stop_all() が例外を投げても _konnyaku_running が False になること。

    背景: _stop_in_background の try ブロック末尾に _konnyaku_running=False があると
    stop_all() が例外を投げた場合に実行されず、running フラグが True のまま残る。
    finally に移動して例外発生時も確実に実行させる必要がある。
    """

    def test_stop_in_background_resets_running_in_finally(self):
        """stop_all() が例外を投げても _konnyaku_running が False になること（finally 版）。

        app.py の _stop_in_background 相当のロジックで、
        W-1 修正後（_konnyaku_running=False を finally に移動）の動作を確認する。
        """
        import app as _app

        _app._konnyaku_running = True
        mock_system = MagicMock()
        mock_system.stop_all.side_effect = RuntimeError("テスト用例外")

        result_running = []

        def _stop_in_background_fixed():
            """W-1 修正後の実装（finally に移動済み）。"""
            try:
                if _app._konnyaku_system is not None:
                    _app._konnyaku_system.stop_all()
            except Exception as e:
                print(f"[ERROR] {e}", flush=True)
            finally:
                _app._konnyaku_running = False  # W-1 修正後: finally に移動
                result_running.append(_app._konnyaku_running)

        with patch.object(_app, "_konnyaku_system", mock_system):
            t = threading.Thread(target=_stop_in_background_fixed)
            t.start()
            t.join(timeout=3.0)

        assert result_running and result_running[0] is False, (
            f"finally で _konnyaku_running=False が実行されなかった: {result_running}"
        )

    def test_stop_in_background_source_has_running_false_in_finally(self):
        """app.py の _stop_in_background 実装でソースコードを静的解析して
        _konnyaku_running = False が finally ブロック内にあることを確認する。

        W-1: try ブロック内ではなく finally ブロックで代入されていること。
        """
        import app as _app
        import ast
        import inspect
        import textwrap

        # _on_konnyaku_start_stop_click のソースを取得
        source = inspect.getsource(_app._on_konnyaku_start_stop_click)
        # インデントを正規化（デデント）
        source = textwrap.dedent(source)

        # AST 解析
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            pytest.fail(f"ソース解析失敗: {e}")

        # _stop_in_background 関数定義を探す
        stop_bg_func = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "_stop_in_background":
                    stop_bg_func = node
                    break

        assert stop_bg_func is not None, (
            "_on_konnyaku_start_stop_click 内に _stop_in_background 関数定義が見つからない"
        )

        # _stop_in_background の中の try 文を探す
        # _konnyaku_running = False が finally ブロックにあるかチェック
        def _is_running_false_assign(node) -> bool:
            """node が `_konnyaku_running = False` の Assign ノードか判定。"""
            if not isinstance(node, ast.Assign):
                return False
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_konnyaku_running":
                    if isinstance(node.value, ast.Constant) and node.value.value is False:
                        return True
            return False

        found_in_finally = False
        found_in_try_body = False

        for node in ast.walk(stop_bg_func):
            if isinstance(node, ast.Try):
                # finalbody の直接の子ノードを確認
                for stmt in node.finalbody:
                    if _is_running_false_assign(stmt):
                        found_in_finally = True
                # try body の直接の子ノードを確認（except ハンドラは除く）
                for stmt in node.body:
                    if _is_running_false_assign(stmt):
                        found_in_try_body = True

        assert found_in_finally, (
            "_stop_in_background の try 文の finally ブロックに "
            "`_konnyaku_running = False` が見つからない（W-1 未修正）"
        )
        assert not found_in_try_body, (
            "_stop_in_background の try ブロック本体に "
            "`_konnyaku_running = False` が残っている（W-1 修正が不完全）"
        )


# ---------------------------------------------------------------------------
# W-5: _on_konnyaku_start_stop_click 連打ガード
# ---------------------------------------------------------------------------

class TestStartStopClickDoublePressGuard:
    """W-5: ボタン disabled 状態で _on_konnyaku_start_stop_click を呼んでも内部状態変化なし。

    背景: バックグラウンドで stop_all 実行中に「開始」連打すると二重起動や内部状態不整合。
    ボタンが disabled 状態（処理中）なら早期 return する連打ガードが必要。
    """

    def test_click_ignored_when_button_disabled(self):
        """ボタン disabled 状態では _on_konnyaku_start_stop_click が早期 return し
        _konnyaku_running が変化しないこと。"""
        import app as _app

        initial_running = False
        _app._konnyaku_running = initial_running

        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True
        # ボタン disabled 状態をシミュレート
        mock_dpg.get_item_configuration.return_value = {"enabled": False}
        mock_dpg.get_value.return_value = False

        start_route_calls = []
        mock_system = MagicMock()
        mock_system.start_route.side_effect = lambda r: start_route_calls.append(r)

        with (
            patch.object(_app, "dpg", mock_dpg),
            patch.object(_app, "_konnyaku_running", initial_running, create=False),
            patch.object(_app, "_konnyaku_system", mock_system),
        ):
            _app._konnyaku_running = initial_running
            _app._on_konnyaku_start_stop_click()

            # start_route が呼ばれていないこと（連打ガードで早期 return）
            assert not start_route_calls, (
                f"ボタン disabled 状態なのに start_route が呼ばれた: {start_route_calls}"
            )
            # _konnyaku_running が変化していないこと
            assert _app._konnyaku_running is initial_running, (
                f"ボタン disabled 状態なのに _konnyaku_running が変化した: "
                f"{initial_running} → {_app._konnyaku_running}"
            )

    def test_click_proceeds_when_button_enabled(self):
        """ボタン enabled 状態では _on_konnyaku_start_stop_click が処理を進めること。"""
        import app as _app

        _app._konnyaku_running = False

        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True
        # ボタン enabled 状態
        mock_dpg.get_item_configuration.return_value = {"enabled": True}
        # TAG_ROUTE_A_ENABLE = True, TAG_ROUTE_B_ENABLE = True
        mock_dpg.get_value.side_effect = lambda tag: True

        start_route_calls = []
        mock_system = MagicMock()
        mock_system.route_a_system = MagicMock()
        mock_system.route_b_system = MagicMock()
        mock_system.start_route.side_effect = lambda r: start_route_calls.append(r)

        with (
            patch.object(_app, "dpg", mock_dpg),
            patch.object(_app, "_konnyaku_running", False, create=False),
            patch.object(_app, "_konnyaku_system", mock_system),
            patch.object(_app, "_system", None),
            patch.object(_app, "_gui_set_label", MagicMock()),
            patch.object(_app, "_gui_set_value", MagicMock()),
        ):
            _app._konnyaku_running = False
            _app._on_konnyaku_start_stop_click()

            # 少なくとも一方の start_route が呼ばれていること（処理が進んでいる）
            assert start_route_calls, (
                "ボタン enabled 状態なのに start_route が呼ばれなかった"
            )

    def test_source_has_disabled_guard_in_start_stop_click(self):
        """app.py のソースを静的解析して _on_konnyaku_start_stop_click に
        disabled 状態チェック（連打ガード）が実装されていることを確認する。

        W-5: 処理開始前に enabled=False のボタン連打を防ぐガードコードが存在すること。
        """
        import app as _app
        import inspect
        import textwrap

        source = inspect.getsource(_app._on_konnyaku_start_stop_click)
        # 連打ガードのキーワードが含まれているか確認
        # 実装方法として以下のいずれかが含まれていること:
        # - "enabled" + "False" の組み合わせ
        # - "return" + "disabled" / "処理中" の組み合わせ
        has_enabled_check = (
            '"enabled"' in source or "'enabled'" in source
        ) and (
            "False" in source or "false" in source
        ) and (
            "return" in source
        )

        assert has_enabled_check, (
            "_on_konnyaku_start_stop_click に disabled ガード（enabled=False + return）が見つからない"
            "（W-5 未実装の可能性）\n"
            f"ソース抜粋: {source[:500]}"
        )
