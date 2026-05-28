"""
tests/test_audio_gate_b3.py

SPEC.md §15 バグ#3 回帰テスト。

バグ#3: openai/deepl/whisper モード（_realtime_mode=False）で
  audio_gate=False のとき _recorder.feed_audio() に音声が届いてしまう。
  realtimeモードにのみ _audio_gate ガードが存在し、else ブランチには未適用。

修正方針（TBD-2 確定）:
  _audio_gate チェックをモード分岐の外側に共通化する。

  変更前:
    if self._realtime_mode:
        if not self._audio_gate:
            continue
        if self._realtime_translator is not None:
            self._realtime_translator.feed_audio(pcm_bytes)
    else:
        if self._recorder is not None:
            self._recorder.feed_audio(pcm_bytes)   # ← ゲートなし

  変更後:
    if not self._audio_gate:
        continue
    if self._realtime_mode:
        if self._realtime_translator is not None:
            self._realtime_translator.feed_audio(pcm_bytes)
    else:
        if self._recorder is not None:
            self._recorder.feed_audio(pcm_bytes)

テストアプローチ:
  _capture_thread_body のループは PyAudio 依存のため直接テストが困難。
  代わりに、ループ内の音声ルーティングロジックを
  CaptionSystem._route_audio_chunk(pcm_bytes) メソッドに切り出し、
  そのメソッドを直接テストする。

  _route_audio_chunk の責務:
    - self._audio_gate が False なら False を返す（呼び出し元は continue）
    - self._audio_gate が True かつ realtime_mode なら
      self._realtime_translator.feed_audio(pcm_bytes) を呼ぶ
    - self._audio_gate が True かつ非 realtime_mode なら
      self._recorder.feed_audio(pcm_bytes) を呼ぶ
    - 戻り値 bool: True=音声を投入した / False=ゲートで破棄

テストケース:
  1. test_openai_mode_audio_gate_closed_drops_audio:
     openai モード（_realtime_mode=False）+ _audio_gate=False のとき
     _recorder.feed_audio が呼ばれないこと、False が返ること

  2. test_openai_mode_audio_gate_open_feeds_audio:
     openai モード（_realtime_mode=False）+ _audio_gate=True のとき
     _recorder.feed_audio が pcm_bytes で呼ばれること、True が返ること

  3. test_deepl_mode_audio_gate_closed_drops_audio:
     deepl モード（_realtime_mode=False、同じコードパス）+ _audio_gate=False のとき
     _recorder.feed_audio が呼ばれないこと
     （意図を明確にするための独立テスト）

  4. test_realtime_mode_audio_gate_closed_drops_audio:
     realtime モード（_realtime_mode=True）+ _audio_gate=False の既存動作を
     _route_audio_chunk 経由でも維持すること（回帰確認）

  5. test_realtime_mode_audio_gate_open_feeds_realtime_translator:
     realtime モード（_realtime_mode=True）+ _audio_gate=True のとき
     _realtime_translator.feed_audio が呼ばれること

  6. test_openai_mode_recorder_none_does_not_raise:
     openai モード + _audio_gate=True + _recorder=None でも例外が起きないこと

  7. test_realtime_mode_translator_none_does_not_raise:
     realtime モード + _audio_gate=True + _realtime_translator=None でも例外なし
"""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import CaptionSystem, RouteState, AudioStats


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_cs(
    realtime_mode: bool = False,
    audio_gate: bool = False,
    recorder=None,
    realtime_translator=None,
) -> CaptionSystem:
    """object.__new__ でバイパスして _route_audio_chunk テスト用の最小 CaptionSystem を作る。

    Args:
        realtime_mode: True = openai-realtime モード、False = openai/deepl/whisper モード
        audio_gate: True = ゲート開、False = ゲート閉（音声破棄）
        recorder: AudioToTextRecorder のモック（None は「存在しない」状態）
        realtime_translator: RealtimeTranslator のモック
    """
    cs = object.__new__(CaptionSystem)
    cs._audio_stats_lock = threading.Lock()
    cs._audio_stats = AudioStats()
    cs._stop_event = threading.Event()
    cs._realtime_translator = realtime_translator
    cs._cost_monitor = None
    cs._recorder = recorder
    cs._loop = None
    cs._stop_event_async = None
    cs._audio_stream = None
    cs._capture_stream = None
    cs._capture_thread = None
    cs._route_id = "test"
    cs._state = RouteState.IDLE
    cs._state_lock = threading.Lock()
    cs.verbose = False
    cs._verbose_log_path = None
    cs._verbose_lock = threading.Lock()
    cs._idle_monitor = None
    cs._audio_gate = audio_gate
    cs._realtime_mode = realtime_mode
    return cs


FAKE_PCM = b"\x00\x01" * 512  # 1024 バイトのダミー PCM データ


# ---------------------------------------------------------------------------
# TestAudioGateCaptureRoutingB3
# ---------------------------------------------------------------------------

class TestAudioGateCaptureRoutingB3:
    """バグ#3 修正の回帰テスト: _route_audio_chunk による音声ルーティング検証。

    現状（修正前）のコードでは以下のテストが FAIL（RED）になること:
      - test_openai_mode_audio_gate_closed_drops_audio
      - test_deepl_mode_audio_gate_closed_drops_audio

    _route_audio_chunk メソッドが main.py に実装されたとき（修正後）に
    全件 PASS（GREEN）になる。
    """

    # ------------------------------------------------------------------
    # テスト 1: openai モード + ゲート閉 → feed_audio 未呼び出し（バグ#3 の直接検出）
    # ------------------------------------------------------------------

    def test_openai_mode_audio_gate_closed_drops_audio(self):
        """openai モード（_realtime_mode=False）+ audio_gate=False のとき
        _recorder.feed_audio() が呼ばれないこと。

        バグ#3 の直接検出テスト。
        修正前の実装では _audio_gate チェックが else ブランチに存在しないため、
        _recorder.feed_audio() が呼ばれてしまい、このテストは FAIL する。

        修正後: _route_audio_chunk が _audio_gate=False のとき False を返し
        feed_audio は呼ばれない。
        """
        mock_recorder = MagicMock()
        cs = _make_cs(
            realtime_mode=False,
            audio_gate=False,
            recorder=mock_recorder,
        )

        result = cs._route_audio_chunk(FAKE_PCM)

        mock_recorder.feed_audio.assert_not_called(), (
            "openai モード + audio_gate=False なのに _recorder.feed_audio() が呼ばれた。"
            "バグ#3: _capture_thread_body の else ブランチに _audio_gate ガードが存在しない。"
        )
        assert result is False, (
            "_route_audio_chunk は audio_gate=False のとき False を返すべき（破棄を示す）。"
        )

    # ------------------------------------------------------------------
    # テスト 2: openai モード + ゲート開 → feed_audio 呼び出し（正常系）
    # ------------------------------------------------------------------

    def test_openai_mode_audio_gate_open_feeds_audio(self):
        """openai モード（_realtime_mode=False）+ audio_gate=True のとき
        _recorder.feed_audio(pcm_bytes) が呼ばれること。

        修正後の正常系確認テスト。ゲートが開いているとき音声が届くこと。
        """
        mock_recorder = MagicMock()
        cs = _make_cs(
            realtime_mode=False,
            audio_gate=True,
            recorder=mock_recorder,
        )

        result = cs._route_audio_chunk(FAKE_PCM)

        mock_recorder.feed_audio.assert_called_once_with(FAKE_PCM), (
            "openai モード + audio_gate=True なのに _recorder.feed_audio() が呼ばれなかった。"
        )
        assert result is True, (
            "_route_audio_chunk は音声を投入したとき True を返すべき。"
        )

    # ------------------------------------------------------------------
    # テスト 3: deepl モード + ゲート閉 → feed_audio 未呼び出し（バグ#3 の意図明示）
    # ------------------------------------------------------------------

    def test_deepl_mode_audio_gate_closed_drops_audio(self):
        """deepl モード（_realtime_mode=False、openai と同じコードパス）+ audio_gate=False のとき
        _recorder.feed_audio() が呼ばれないこと。

        deepl/whisper モードも _realtime_mode=False で同じ else ブランチを通る。
        バグ#3 の影響を受けるモードを明示するための独立テスト。

        修正前の実装ではこのテストは FAIL する（feed_audio が呼ばれてしまう）。
        """
        mock_recorder = MagicMock()
        cs = _make_cs(
            realtime_mode=False,  # deepl/whisper もこの値
            audio_gate=False,
            recorder=mock_recorder,
        )

        result = cs._route_audio_chunk(FAKE_PCM)

        mock_recorder.feed_audio.assert_not_called(), (
            "deepl モード + audio_gate=False なのに _recorder.feed_audio() が呼ばれた。"
            "deepl/whisper も openai と同じ else ブランチを通るため、バグ#3 の影響を受ける。"
        )
        assert result is False

    # ------------------------------------------------------------------
    # テスト 4: realtime モード + ゲート閉 → feed_audio 未呼び出し（既存動作の回帰確認）
    # ------------------------------------------------------------------

    def test_realtime_mode_audio_gate_closed_drops_audio(self):
        """realtime モード（_realtime_mode=True）+ audio_gate=False のとき
        _realtime_translator.feed_audio() が呼ばれないこと。

        既存動作（バグ#3 修正前から正しく動いていた動作）を
        _route_audio_chunk 経由でも維持することの回帰テスト。
        """
        mock_translator = MagicMock()
        cs = _make_cs(
            realtime_mode=True,
            audio_gate=False,
            realtime_translator=mock_translator,
        )

        result = cs._route_audio_chunk(FAKE_PCM)

        mock_translator.feed_audio.assert_not_called(), (
            "realtime モード + audio_gate=False なのに feed_audio() が呼ばれた。"
        )
        assert result is False

    # ------------------------------------------------------------------
    # テスト 5: realtime モード + ゲート開 → realtime_translator.feed_audio 呼び出し
    # ------------------------------------------------------------------

    def test_realtime_mode_audio_gate_open_feeds_realtime_translator(self):
        """realtime モード（_realtime_mode=True）+ audio_gate=True のとき
        _realtime_translator.feed_audio(pcm_bytes) が呼ばれること。

        既存動作の回帰テスト。
        """
        mock_translator = MagicMock()
        cs = _make_cs(
            realtime_mode=True,
            audio_gate=True,
            realtime_translator=mock_translator,
        )

        result = cs._route_audio_chunk(FAKE_PCM)

        mock_translator.feed_audio.assert_called_once_with(FAKE_PCM), (
            "realtime モード + audio_gate=True なのに _realtime_translator.feed_audio() が呼ばれなかった。"
        )
        assert result is True

    # ------------------------------------------------------------------
    # テスト 6: openai モード + ゲート開 + recorder=None → 例外なし
    # ------------------------------------------------------------------

    def test_openai_mode_recorder_none_does_not_raise(self):
        """openai モード + audio_gate=True + _recorder=None でも AttributeError が起きないこと。

        _recorder が None の場合のガードが壊れていないことを確認。
        """
        cs = _make_cs(
            realtime_mode=False,
            audio_gate=True,
            recorder=None,  # None
        )

        # 例外が発生しないこと
        result = cs._route_audio_chunk(FAKE_PCM)
        # recorder が None なので feed_audio は呼ばれないが例外も起きない
        # 返り値は実装依存（True or False どちらでもよい）

    # ------------------------------------------------------------------
    # テスト 7: realtime モード + ゲート開 + translator=None → 例外なし
    # ------------------------------------------------------------------

    def test_realtime_mode_translator_none_does_not_raise(self):
        """realtime モード + audio_gate=True + _realtime_translator=None でも例外なし。

        既存コードのガード（if self._realtime_translator is not None）が
        _route_audio_chunk にも適用されていることを確認。
        """
        cs = _make_cs(
            realtime_mode=True,
            audio_gate=True,
            realtime_translator=None,  # None
        )

        # 例外が発生しないこと
        result = cs._route_audio_chunk(FAKE_PCM)
