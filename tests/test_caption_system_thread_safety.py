"""
tests/test_caption_system_thread_safety.py

CaptionSystem の共有状態（AudioStats）のスレッド安全性テスト。

frozen dataclass + threading.Lock によるスナップショット方式が、
- アトミックな読み書き
- 後方互換プロパティ
- 並行アクセス時のフィールド整合性
を保証することを検証する（PEP 703 free-threaded Python 対応）。
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import AudioStats, CaptionSystem


def _make_caption_system_minimal() -> CaptionSystem:
    """__init__ をバイパスして最小限のインスタンスを作る（スレッド安全性テスト専用）。

    CaptionSystem の __init__ は重い依存（OpenAI クライアント、設定読込）を持つため、
    object.__new__ で生成し、スレッド安全性テストに必要な属性だけ初期化する。
    """
    sys_obj = object.__new__(CaptionSystem)
    sys_obj._audio_stats_lock = threading.Lock()
    sys_obj._audio_stats = AudioStats()
    return sys_obj


# ---------------------------------------------------------------------------
# AudioStats 自体のテスト
# ---------------------------------------------------------------------------

class TestAudioStatsImmutable:
    """AudioStats が frozen な immutable オブジェクトであること。"""

    def test_audio_stats_is_frozen(self):
        s = AudioStats(peak=100, gain=2.0)
        with pytest.raises(Exception):
            s.peak = 200  # frozen=True なので例外

    def test_audio_stats_defaults(self):
        s = AudioStats()
        assert s.peak == 0
        assert s.peak_now == 0
        assert s.chunks_per_sec == 0
        assert s.gain == 1.0
        assert s.mode == "off"
        assert s.manual_gain == 1.0

    def test_audio_stats_replace_creates_new_instance(self):
        from dataclasses import replace
        s1 = AudioStats(peak=100)
        s2 = replace(s1, peak=200)
        assert s1.peak == 100  # 元オブジェクトは不変
        assert s2.peak == 200
        assert s1 is not s2


# ---------------------------------------------------------------------------
# CaptionSystem の共有状態アクセス
# ---------------------------------------------------------------------------

class TestCaptionSystemAudioStatsAtomic:
    """audio_stats プロパティと _update_audio_stats メソッドの基本動作。"""

    def test_update_replaces_specific_field(self):
        sys_obj = _make_caption_system_minimal()
        sys_obj._update_audio_stats(gain=2.5)
        assert sys_obj.audio_stats.gain == 2.5
        assert sys_obj.audio_stats.peak == 0  # 他フィールドは保持

    def test_update_multiple_fields_atomically(self):
        sys_obj = _make_caption_system_minimal()
        sys_obj._update_audio_stats(peak=1000, peak_now=2000, gain=3.0)
        stats = sys_obj.audio_stats
        assert stats.peak == 1000
        assert stats.peak_now == 2000
        assert stats.gain == 3.0


class TestCaptionSystemBackwardCompatibility:
    """既存コードからの属性アクセスがそのまま動くこと（プロパティ透過化）。"""

    def test_audio_peak_property(self):
        sys_obj = _make_caption_system_minimal()
        sys_obj._update_audio_stats(peak=12345)
        assert sys_obj.audio_peak == 12345

    def test_audio_peak_now_property(self):
        sys_obj = _make_caption_system_minimal()
        sys_obj._update_audio_stats(peak_now=678)
        assert sys_obj.audio_peak_now == 678

    def test_audio_chunks_per_sec_property(self):
        sys_obj = _make_caption_system_minimal()
        sys_obj._update_audio_stats(chunks_per_sec=42)
        assert sys_obj.audio_chunks_per_sec == 42

    def test_effective_gain_property(self):
        sys_obj = _make_caption_system_minimal()
        sys_obj._update_audio_stats(gain=4.2)
        assert sys_obj.effective_gain == 4.2

    def test_gain_mode_getter_and_setter(self):
        sys_obj = _make_caption_system_minimal()
        assert sys_obj.gain_mode == "off"
        sys_obj.gain_mode = "manual"
        assert sys_obj.gain_mode == "manual"
        assert sys_obj.audio_stats.mode == "manual"

    def test_manual_gain_getter_and_setter(self):
        sys_obj = _make_caption_system_minimal()
        assert sys_obj.manual_gain == 1.0
        sys_obj.manual_gain = 5.5
        assert sys_obj.manual_gain == 5.5
        assert sys_obj.audio_stats.manual_gain == 5.5


# ---------------------------------------------------------------------------
# 並行アクセス時の整合性
# ---------------------------------------------------------------------------

class TestCaptionSystemConcurrentAccess:
    """マルチスレッドからの読み書きでデータ競合が起きないことを検証。"""

    def test_concurrent_field_writes_do_not_lose_updates(self):
        """各フィールドを別スレッドが順次書き込み、最終値が期待通りであること。"""
        sys_obj = _make_caption_system_minimal()
        iterations = 500

        def writer(field: str, start_val: int):
            for i in range(iterations):
                sys_obj._update_audio_stats(**{field: start_val + i})

        threads = [
            threading.Thread(target=writer, args=("peak", 0)),
            threading.Thread(target=writer, args=("peak_now", 10000)),
            threading.Thread(target=writer, args=("chunks_per_sec", 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = sys_obj.audio_stats
        # 各 writer は順次書き込むため、最終値は iterations-1 を加えた値
        assert final.peak == iterations - 1
        assert final.peak_now == 10000 + iterations - 1
        assert final.chunks_per_sec == 100 + iterations - 1

    def test_snapshot_consistency_across_fields(self):
        """writer が全フィールドを同じ値で更新する間、reader が読む snapshot は
        必ず「全フィールドが同じ writer ステップの値」になる（混在しない）。"""
        sys_obj = _make_caption_system_minimal()
        stop_flag = threading.Event()
        violations = []
        iterations = 2000

        def writer():
            for i in range(1, iterations + 1):
                # 4 フィールドを 1 度の atomic 更新でセット
                sys_obj._update_audio_stats(
                    peak=i,
                    peak_now=i,
                    chunks_per_sec=i,
                    gain=float(i),
                )

        def reader():
            while not stop_flag.is_set():
                s = sys_obj.audio_stats
                # 全フィールドが同一値ならスナップショット整合性 OK
                if not (s.peak == s.peak_now == s.chunks_per_sec == int(s.gain)):
                    violations.append((s.peak, s.peak_now, s.chunks_per_sec, s.gain))

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        w.join()
        stop_flag.set()
        r.join()

        assert not violations, (
            f"snapshot 不整合を検出（先頭 5 件）: {violations[:5]}"
        )

    def test_concurrent_setter_writes(self):
        """gain_mode / manual_gain setter を複数スレッドから呼んでもデータ競合なし。"""
        sys_obj = _make_caption_system_minimal()
        iterations = 500

        def set_mode():
            for i in range(iterations):
                sys_obj.gain_mode = "manual" if i % 2 == 0 else "auto"

        def set_gain():
            for i in range(iterations):
                sys_obj.manual_gain = 1.0 + i * 0.01

        t1 = threading.Thread(target=set_mode)
        t2 = threading.Thread(target=set_gain)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # データ競合がなければエラーなく終わる。最終値は any of inputs
        final = sys_obj.audio_stats
        assert final.mode in ("manual", "auto")
        assert 1.0 <= final.manual_gain <= 1.0 + iterations * 0.01
