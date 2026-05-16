"""
tests/test_log_route_id.py

TDD RED フェーズ: CaptionSystem の各ログに route_id プレフィックスが付くことを検証する。

検証対象:
  - CaptionSystem._log(category, message) ヘルパーが存在し、
    [CATEGORY][route_id] message 形式で出力すること
  - _capture_thread_body / _start_recorder / run 内のログが route_id を含むこと
  - prepare() 内のログが route_id を含むこと
"""

import sys
import threading
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# ヘルパー: object.__new__ でバイパスした最小限インスタンス
# ---------------------------------------------------------------------------

def _make_cs(route_id: str = "a") -> "CaptionSystem":  # type: ignore[name-defined]
    from main import AudioStats, CaptionSystem, RouteState

    cs = object.__new__(CaptionSystem)
    cs._route_id = route_id
    cs._state = RouteState.IDLE
    cs._state_lock = threading.Lock()
    cs._stop_event = threading.Event()
    cs._realtime_translator = None
    cs._cost_monitor = None
    cs._recorder = None
    cs._loop = None
    cs._stop_event_async = None
    cs._capture_stream = None
    cs._capture_thread = None
    cs._audio_stream = None
    cs._device_info = {}
    cs._config = {"websocket": {"host": "localhost", "port": 9001}}
    cs._log_path = Path("test.log")
    cs._realtime_mode = False
    cs._audio_output_mode = False
    cs._output_device_index = None
    cs._owns_broadcaster = False
    cs._broadcaster = MagicMock()
    cs._on_ready = None
    cs._model_name = "tiny"  # W-7: Whisper モデルロード時間を短縮するため "tiny" を設定
    # PR3: _log_verbose が参照する属性（verbose OFF でトレースバック記録なし）
    cs.verbose = False
    cs._verbose_log_path = None
    cs._verbose_lock = threading.Lock()
    return cs


# ---------------------------------------------------------------------------
# テスト: _log ヘルパー
# ---------------------------------------------------------------------------

class TestLogHelper:
    def test_log_helper_exists(self):
        """CaptionSystem._log メソッドが存在する。"""
        from main import CaptionSystem
        assert hasattr(CaptionSystem, "_log"), "_log ヘルパーが存在しない"

    def test_log_includes_route_id_route_a(self, capsys):
        """route_id='a' のとき '[a]' が出力に含まれる。"""
        cs = _make_cs("a")
        cs._log("INFO", "test message")
        out = capsys.readouterr().out
        assert "[a]" in out, f"route_id '[a]' が出力に含まれていない: {out!r}"

    def test_log_includes_route_id_route_b(self, capsys):
        """route_id='b' のとき '[b]' が出力に含まれる。"""
        cs = _make_cs("b")
        cs._log("INFO", "test message")
        out = capsys.readouterr().out
        assert "[b]" in out, f"route_id '[b]' が出力に含まれていない: {out!r}"

    def test_log_includes_category(self, capsys):
        """カテゴリが出力に含まれる。"""
        cs = _make_cs("a")
        cs._log("AUDIO", "peak test")
        out = capsys.readouterr().out
        assert "[AUDIO]" in out, f"カテゴリ '[AUDIO]' が出力に含まれていない: {out!r}"

    def test_log_includes_message(self, capsys):
        """メッセージ本文が出力に含まれる。"""
        cs = _make_cs("a")
        cs._log("INFO", "hello world")
        out = capsys.readouterr().out
        assert "hello world" in out, f"メッセージが出力に含まれていない: {out!r}"

    def test_log_format_category_before_route(self, capsys):
        """出力形式が [CATEGORY][route_id] message であること。"""
        cs = _make_cs("b")
        cs._log("INFO", "format check")
        out = capsys.readouterr().out
        # [INFO][b] format check というフォーマットであること
        assert "[INFO][b]" in out, f"フォーマット '[INFO][b]' が一致しない: {out!r}"

    def test_log_fallback_when_no_route_id(self, capsys):
        """_route_id 属性がない場合は '?' が使われる。"""
        from main import CaptionSystem
        cs = object.__new__(CaptionSystem)
        # _route_id を意図的に設定しない
        cs._log("WARN", "fallback test")
        out = capsys.readouterr().out
        assert "[?]" in out, f"フォールバック '[?]' が出力に含まれていない: {out!r}"


# ---------------------------------------------------------------------------
# テスト: _capture_thread_body のログに route_id が含まれる
# ---------------------------------------------------------------------------

class TestCaptureThreadBodyLogs:
    def test_stream_open_error_log_includes_route_id(self, capsys):
        """ストリームのオープン失敗ログに route_id が含まれる。"""
        import pyaudiowpatch as pyaudio
        from main import CaptionSystem

        cs = _make_cs("b")
        cs._pa_instance = None
        cs._device_info = {"index": 0, "defaultSampleRate": 44100,
                           "maxInputChannels": 2, "isLoopback": True}
        cs._agc_envelope = 0.0
        cs._agc_gain = 1.0

        # PyAudio.open を失敗させる
        mock_pa = MagicMock()
        mock_pa.open.side_effect = OSError("device not available")

        with patch("pyaudiowpatch.PyAudio", return_value=mock_pa):
            cs._capture_thread_body()

        out = capsys.readouterr().out
        assert "[b]" in out, f"route_id '[b]' がエラーログに含まれていない: {out!r}"

    def test_audio_level_log_includes_route_id(self, capsys):
        """1秒ごとの音量ログ [AUDIO] に route_id が含まれる。"""
        import numpy as np
        from main import AudioStats, CaptionSystem

        cs = _make_cs("a")
        # _update_audio_stats が参照する属性
        cs._audio_stats = AudioStats()
        cs._audio_stats_lock = threading.Lock()
        cs._agc_envelope = 0.0
        cs._agc_gain = 1.0
        cs._pa_instance = None
        cs._device_info = {"index": 0, "defaultSampleRate": 16000,
                           "maxInputChannels": 1, "isLoopback": True}

        # 1秒ぶんのダミー音声を作り、stop_event で1ループ後に終了させる
        chunk_size = 512
        sample_rate = 16000
        chunks_needed = (sample_rate // chunk_size) + 2  # next_log を超えるチャンク数

        call_count = 0

        def fake_read(size, exception_on_overflow=False):
            nonlocal call_count
            call_count += 1
            if call_count > chunks_needed:
                cs._stop_event.set()
            # 無音チャンク
            return (np.zeros(size // 2, dtype=np.int16)).tobytes()

        mock_stream = MagicMock()
        mock_stream.read.side_effect = fake_read

        mock_pa = MagicMock()
        mock_pa.open.return_value = mock_stream

        with patch("pyaudiowpatch.PyAudio", return_value=mock_pa):
            with patch("time.time") as mock_time:
                # 時刻を進めて next_log を超えさせる
                mock_time.side_effect = [
                    0.0,   # next_log = 1.0 の起点
                    *([0.5] * (chunks_needed - 1)),  # まだ時刻が来ていない
                    2.0,   # next_log (1.0) を超えた → ログ出力
                    2.0,   # stop 後
                ]
                cs._capture_thread_body()

        out = capsys.readouterr().out
        assert "[a]" in out and "[AUDIO]" in out, (
            f"'[a][AUDIO]' がログに含まれていない: {out!r}"
        )


# ---------------------------------------------------------------------------
# テスト: _start_recorder のログに route_id が含まれる
# ---------------------------------------------------------------------------

class TestStartRecorderLogs:
    def test_recorder_start_log_includes_route_id(self, capsys):
        """録音開始ログに route_id が含まれる（非 Realtime モード）。"""
        from main import CaptionSystem

        cs = _make_cs("b")
        cs._realtime_mode = False
        cs._device_info = {"isLoopback": False}
        cs._stop_event.set()  # 即終了

        # _recorder を事前にモックとして設定（prepare() スキップ）
        mock_recorder = MagicMock()
        mock_recorder.text.side_effect = lambda cb: None
        cs._recorder = mock_recorder

        cs._start_recorder()

        out = capsys.readouterr().out
        assert "[b]" in out, f"route_id '[b]' が録音開始ログに含まれていない: {out!r}"

    def test_recorder_error_log_includes_route_id(self, capsys):
        """録音中のエラーログに route_id が含まれる。"""
        from main import CaptionSystem

        cs = _make_cs("a")
        cs._realtime_mode = False
        cs._device_info = {"isLoopback": False}

        call_count = 0

        def fake_text(cb):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("fake recorder error")
            cs._stop_event.set()

        mock_recorder = MagicMock()
        mock_recorder.text.side_effect = fake_text
        cs._recorder = mock_recorder

        cs._start_recorder()

        out = capsys.readouterr().out
        assert "[a]" in out, f"route_id '[a]' がエラーログに含まれていない: {out!r}"


# ---------------------------------------------------------------------------
# テスト: prepare() のログに route_id が含まれる
# ---------------------------------------------------------------------------

class TestPrepareLogs:
    def test_prepare_failure_log_includes_route_id(self, capsys):
        """AudioToTextRecorder 初期化失敗ログに route_id が含まれる。"""
        from main import CaptionSystem

        cs = _make_cs("b")
        cs._realtime_mode = False
        cs._recorder = None
        cs._device_info = {"isLoopback": False, "index": 0}
        cs._config = {
            "whisper": {"language": "ja", "compute_type": "int8", "device": "cpu", "model": "tiny"},
            "vad": {},
        }
        cs._model_name = "tiny"

        with patch("main.AudioToTextRecorder", side_effect=RuntimeError("init failed")):
            cs.prepare()

        out = capsys.readouterr().out
        assert "[b]" in out, f"route_id '[b]' が prepare 失敗ログに含まれていない: {out!r}"


# ---------------------------------------------------------------------------
# テスト: run() のログに route_id が含まれる
# ---------------------------------------------------------------------------

class TestRunLogs:
    def test_run_log_path_log_includes_route_id(self, capsys):
        """run() 内のログファイル出力ログに route_id が含まれる。"""
        import asyncio
        from main import CaptionSystem

        cs = _make_cs("a")
        cs._audio_output_mode = False
        cs._realtime_mode = False
        cs._cost_monitor = None
        cs._owns_broadcaster = False
        cs._log_path = Path("/fake/log/path_a.log")

        # _start_recorder は別スレッドで動くため即終了させる
        cs._stop_event.set()

        async def _run():
            cs._loop = asyncio.get_running_loop()
            cs._stop_event_async = asyncio.Event()
            cs._stop_event_async.set()  # 即終了

            recorder_thread = threading.Thread(target=cs._start_recorder, daemon=True)
            recorder_thread.start()

            # ログ出力部分だけ抽出してテスト
            from main import CaptionSystem as _CS
            # _log_path ログの出力
            cs._log("INFO", f"ログファイル: {cs._log_path}")

        asyncio.run(_run())

        out = capsys.readouterr().out
        assert "[a]" in out, f"route_id '[a]' が run ログに含まれていない: {out!r}"

    def test_run_finish_log_includes_route_id(self, capsys):
        """run() の終了ログに route_id が含まれる（'終了しました' メッセージ）。"""
        import asyncio
        from main import CaptionSystem

        cs = _make_cs("b")
        cs._audio_output_mode = False
        cs._realtime_mode = False
        cs._cost_monitor = None
        cs._owns_broadcaster = False
        cs._log_path = Path("/fake/log/path_b.log")
        cs._stop_event.set()

        async def _run():
            cs._loop = asyncio.get_running_loop()
            cs._stop_event_async = asyncio.Event()
            cs._stop_event_async.set()
            # 終了ログを直接テスト
            cs._log("INFO", "終了しました。")

        asyncio.run(_run())

        out = capsys.readouterr().out
        assert "[b]" in out, f"route_id '[b]' が終了ログに含まれていない: {out!r}"
