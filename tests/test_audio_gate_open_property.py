"""
tests/test_audio_gate_open_property.py

CaptionSystem.audio_gate_open プロパティの存在と動作を確認するテスト。

バグ: app.py の api_status エンドポイント（3206 行）が
  _konnyaku_system.route_b_system.audio_gate_open
を参照しているが、CaptionSystem に audio_gate_open プロパティが存在しないため
AttributeError: 'CaptionSystem' object has no attribute 'audio_gate_open'
が発生し、/api/status が 500 を返す。

修正: CaptionSystem に以下を追加する
    @property
    def audio_gate_open(self) -> bool:
        return self._audio_gate

テストケース:
  1. test_audio_gate_open_default_false: 初期化直後は False
  2. test_audio_gate_open_after_open_returns_true: open_audio_gate() 後に True
  3. test_audio_gate_open_after_close_returns_false: close_audio_gate() 後に False
"""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import CaptionSystem, RouteState, AudioStats


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_minimal_caption_system() -> CaptionSystem:
    """object.__new__ でバイパスして最小限 CaptionSystem を作る。

    audio_gate 関連のプロパティ／メソッドのテストに必要なフィールドのみ初期化する。
    """
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
    cs._route_id = "test"
    cs._state = RouteState.IDLE
    cs._state_lock = threading.Lock()
    cs.verbose = False
    cs._verbose_log_path = None
    cs._verbose_lock = threading.Lock()
    cs._idle_monitor = None
    # issue #156 で追加された音声ゲートフラグ（デフォルト=閉）
    cs._audio_gate = False
    return cs


# ---------------------------------------------------------------------------
# audio_gate_open プロパティのテスト（RED フェーズ: 全件 FAIL を期待）
# ---------------------------------------------------------------------------

class TestAudioGateOpenProperty:
    """CaptionSystem.audio_gate_open プロパティの存在と動作を確認する。

    RED フェーズ: audio_gate_open プロパティが実装されていない状態で作成。
    全件 FAIL（AttributeError）になることを期待する。
    """

    def test_audio_gate_open_default_false(self):
        """CaptionSystem 生成直後（_audio_gate=False）は audio_gate_open が False を返すこと。

        _audio_gate は __init__ で False で初期化される（issue #156）。
        audio_gate_open プロパティが _audio_gate を正しく公開していることを確認する。
        """
        cs = _make_minimal_caption_system()
        assert cs.audio_gate_open is False, (
            "CaptionSystem 生成直後に audio_gate_open が False でない。"
            "_audio_gate の初期値は False（issue #156 設計）。"
            "audio_gate_open プロパティが存在しない場合は AttributeError で FAIL する。"
        )

    def test_audio_gate_open_after_open_returns_true(self):
        """open_audio_gate() 呼び出し後、audio_gate_open が True を返すこと。

        open_audio_gate() は _audio_gate を True にセットする。
        audio_gate_open プロパティはそれを反映して True を返すべき。
        """
        cs = _make_minimal_caption_system()
        cs.open_audio_gate()
        assert cs.audio_gate_open is True, (
            "open_audio_gate() 後に audio_gate_open が True でない。"
            "open_audio_gate() は _audio_gate = True をセットするため、"
            "audio_gate_open プロパティは True を返すべき。"
        )

    def test_audio_gate_open_after_close_returns_false(self):
        """close_audio_gate() 呼び出し後、audio_gate_open が False を返すこと。

        open_audio_gate() で True にした後に close_audio_gate() を呼んだ場合、
        audio_gate_open は False に戻るべき。
        """
        cs = _make_minimal_caption_system()
        cs.open_audio_gate()   # まず開く
        cs.close_audio_gate()  # 閉じる
        assert cs.audio_gate_open is False, (
            "close_audio_gate() 後に audio_gate_open が False でない。"
            "close_audio_gate() は _audio_gate = False をセットするため、"
            "audio_gate_open プロパティは False を返すべき。"
        )
