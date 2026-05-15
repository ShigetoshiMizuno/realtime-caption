"""
tests/test_vad_validation.py

PR #97 W-2: VAD パラメータの境界値バリデーションテスト

対象:
- _clamp_with_warning ヘルパー関数（モジュールレベル）
- RealtimeTranslator.__init__ での境界値クランプ + 警告ログ

結合点:
  _clamp_with_warning (realtime_translator モジュールレベル)
      <- RealtimeTranslator.__init__ (vad_enabled=True のとき呼ばれる)

テストケース:
1. _clamp_with_warning の単体テスト
   - 範囲内: クランプなし・警告なし
   - 下限未満: 下限にクランプ + WARN ログ
   - 上限超過: 上限にクランプ + WARN ログ
   - 境界値ちょうど: クランプなし・警告なし
2. RealtimeTranslator.__init__ の統合テスト
   - vad_threshold: 範囲内 / 下限未満 / 上限超過
   - vad_prefix_padding_ms: 範囲内 / 下限未満 / 上限超過
   - vad_silence_duration_ms: 範囲内 / 下限未満 / 上限超過
   - vad_enabled=False のとき: クランプ・警告なし
   - 例外は投げない（後方互換維持）
"""

import io
import sys

import pytest

from realtime_translator import RealtimeTranslator, _clamp_with_warning


# ---------------------------------------------------------------------------
# ヘルパー: stdout キャプチャ
# ---------------------------------------------------------------------------

class _StdoutCapture:
    """print(..., flush=True) で出力される WARN ログをキャプチャするコンテキストマネージャ。"""

    def __enter__(self):
        self._buf = io.StringIO()
        self._old_stdout = sys.stdout
        sys.stdout = self._buf
        return self

    def __exit__(self, *args):
        sys.stdout = self._old_stdout

    @property
    def text(self) -> str:
        return self._buf.getvalue()


# ---------------------------------------------------------------------------
# 1. _clamp_with_warning の単体テスト
# ---------------------------------------------------------------------------

class TestClampWithWarning:
    """_clamp_with_warning ヘルパー関数の単体テスト。"""

    def test_value_within_range_returns_unchanged(self):
        """範囲内の値はそのまま返すこと。"""
        result = _clamp_with_warning(0.5, 0.0, 1.0, "test_param")
        assert result == pytest.approx(0.5)

    def test_value_within_range_no_warning(self):
        """範囲内の値は警告を出力しないこと。"""
        with _StdoutCapture() as cap:
            _clamp_with_warning(0.5, 0.0, 1.0, "test_param")
        assert "[WARN]" not in cap.text

    def test_value_below_lo_clamps_to_lo(self):
        """下限未満の値は下限にクランプされること。"""
        result = _clamp_with_warning(-0.1, 0.0, 1.0, "test_param")
        assert result == pytest.approx(0.0)

    def test_value_below_lo_emits_warn(self):
        """下限未満の値は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            _clamp_with_warning(-0.1, 0.0, 1.0, "test_param")
        assert "[WARN]" in cap.text

    def test_value_below_lo_warn_contains_name(self):
        """WARN ログにパラメータ名が含まれること。"""
        with _StdoutCapture() as cap:
            _clamp_with_warning(-0.1, 0.0, 1.0, "my_param_name")
        assert "my_param_name" in cap.text

    def test_value_below_lo_warn_contains_original(self):
        """WARN ログに元の値が含まれること。"""
        with _StdoutCapture() as cap:
            _clamp_with_warning(-0.1, 0.0, 1.0, "test_param")
        assert "-0.1" in cap.text

    def test_value_above_hi_clamps_to_hi(self):
        """上限超過の値は上限にクランプされること。"""
        result = _clamp_with_warning(1.5, 0.0, 1.0, "test_param")
        assert result == pytest.approx(1.0)

    def test_value_above_hi_emits_warn(self):
        """上限超過の値は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            _clamp_with_warning(1.5, 0.0, 1.0, "test_param")
        assert "[WARN]" in cap.text

    def test_value_at_lo_boundary_no_clamp(self):
        """下限ちょうどの値はクランプされないこと。"""
        result = _clamp_with_warning(0.0, 0.0, 1.0, "test_param")
        assert result == pytest.approx(0.0)

    def test_value_at_lo_boundary_no_warn(self):
        """下限ちょうどの値は警告を出力しないこと。"""
        with _StdoutCapture() as cap:
            _clamp_with_warning(0.0, 0.0, 1.0, "test_param")
        assert "[WARN]" not in cap.text

    def test_value_at_hi_boundary_no_clamp(self):
        """上限ちょうどの値はクランプされないこと。"""
        result = _clamp_with_warning(1.0, 0.0, 1.0, "test_param")
        assert result == pytest.approx(1.0)

    def test_value_at_hi_boundary_no_warn(self):
        """上限ちょうどの値は警告を出力しないこと。"""
        with _StdoutCapture() as cap:
            _clamp_with_warning(1.0, 0.0, 1.0, "test_param")
        assert "[WARN]" not in cap.text

    def test_integer_range_clamp(self):
        """整数範囲のクランプも動作すること（ms パラメータ想定）。"""
        result = _clamp_with_warning(30, 50, 5000, "prefix_padding_ms")
        assert result == 50

    def test_integer_range_large_value_clamp(self):
        """整数の上限超過クランプも動作すること。"""
        result = _clamp_with_warning(9999, 100, 10000, "silence_duration_ms")
        assert result == 9999  # 10000 以下なのでクランプなし

    def test_integer_range_exceeds_hi_clamp(self):
        """整数の上限超過（10001）が上限にクランプされること。"""
        result = _clamp_with_warning(10001, 100, 10000, "silence_duration_ms")
        assert result == 10000


# ---------------------------------------------------------------------------
# 2. RealtimeTranslator.__init__ の境界値バリデーション統合テスト
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorVadValidation:
    """RealtimeTranslator.__init__ でのVADパラメータ境界値バリデーション。"""

    # ---- vad_threshold (0.0 〜 1.0) ----

    def test_vad_threshold_within_range_no_clamp(self):
        """vad_threshold が 0.0〜1.0 の範囲内なら変更されないこと。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val001",
            target_language_code="ja",
            vad_enabled=True,
            vad_threshold=0.5,
        )
        assert t._vad_threshold == pytest.approx(0.5)

    def test_vad_threshold_below_zero_clamped_to_zero(self):
        """vad_threshold=-0.1 は 0.0 にクランプされること。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val002",
            target_language_code="ja",
            vad_enabled=True,
            vad_threshold=-0.1,
        )
        assert t._vad_threshold == pytest.approx(0.0)

    def test_vad_threshold_below_zero_emits_warn(self):
        """vad_threshold=-0.1 は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val003",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=-0.1,
            )
        assert "[WARN]" in cap.text
        assert "vad_threshold" in cap.text

    def test_vad_threshold_above_one_clamped_to_one(self):
        """vad_threshold=1.5 は 1.0 にクランプされること。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val004",
            target_language_code="ja",
            vad_enabled=True,
            vad_threshold=1.5,
        )
        assert t._vad_threshold == pytest.approx(1.0)

    def test_vad_threshold_above_one_emits_warn(self):
        """vad_threshold=1.5 は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val005",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=1.5,
            )
        assert "[WARN]" in cap.text

    def test_vad_threshold_boundary_zero_no_warn(self):
        """vad_threshold=0.0 はクランプ・警告なし。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val006",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=0.0,
            )
        assert "[WARN]" not in cap.text

    def test_vad_threshold_boundary_one_no_warn(self):
        """vad_threshold=1.0 はクランプ・警告なし。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val007",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=1.0,
            )
        assert "[WARN]" not in cap.text

    # ---- vad_prefix_padding_ms (50 〜 5000) ----

    def test_vad_prefix_padding_ms_within_range_no_clamp(self):
        """vad_prefix_padding_ms=300 は変更されないこと。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val010",
            target_language_code="ja",
            vad_enabled=True,
            vad_prefix_padding_ms=300,
        )
        assert t._vad_prefix_padding_ms == 300

    def test_vad_prefix_padding_ms_below_min_clamped(self):
        """vad_prefix_padding_ms=10 は 50 にクランプされること。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val011",
            target_language_code="ja",
            vad_enabled=True,
            vad_prefix_padding_ms=10,
        )
        assert t._vad_prefix_padding_ms == 50

    def test_vad_prefix_padding_ms_below_min_emits_warn(self):
        """vad_prefix_padding_ms=10 は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val012",
                target_language_code="ja",
                vad_enabled=True,
                vad_prefix_padding_ms=10,
            )
        assert "[WARN]" in cap.text
        assert "vad_prefix_padding_ms" in cap.text

    def test_vad_prefix_padding_ms_above_max_clamped(self):
        """vad_prefix_padding_ms=9999 は 5000 にクランプされること。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val013",
            target_language_code="ja",
            vad_enabled=True,
            vad_prefix_padding_ms=9999,
        )
        assert t._vad_prefix_padding_ms == 5000

    def test_vad_prefix_padding_ms_above_max_emits_warn(self):
        """vad_prefix_padding_ms=9999 は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val014",
                target_language_code="ja",
                vad_enabled=True,
                vad_prefix_padding_ms=9999,
            )
        assert "[WARN]" in cap.text

    def test_vad_prefix_padding_ms_boundary_50_no_warn(self):
        """vad_prefix_padding_ms=50 はクランプ・警告なし。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val015",
                target_language_code="ja",
                vad_enabled=True,
                vad_prefix_padding_ms=50,
            )
        assert "[WARN]" not in cap.text

    def test_vad_prefix_padding_ms_boundary_5000_no_warn(self):
        """vad_prefix_padding_ms=5000 はクランプ・警告なし。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val016",
                target_language_code="ja",
                vad_enabled=True,
                vad_prefix_padding_ms=5000,
            )
        assert "[WARN]" not in cap.text

    # ---- vad_silence_duration_ms (100 〜 10000) ----

    def test_vad_silence_duration_ms_within_range_no_clamp(self):
        """vad_silence_duration_ms=500 は変更されないこと。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val020",
            target_language_code="ja",
            vad_enabled=True,
            vad_silence_duration_ms=500,
        )
        assert t._vad_silence_duration_ms == 500

    def test_vad_silence_duration_ms_below_min_clamped(self):
        """vad_silence_duration_ms=50 は 100 にクランプされること。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val021",
            target_language_code="ja",
            vad_enabled=True,
            vad_silence_duration_ms=50,
        )
        assert t._vad_silence_duration_ms == 100

    def test_vad_silence_duration_ms_below_min_emits_warn(self):
        """vad_silence_duration_ms=50 は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val022",
                target_language_code="ja",
                vad_enabled=True,
                vad_silence_duration_ms=50,
            )
        assert "[WARN]" in cap.text
        assert "vad_silence_duration_ms" in cap.text

    def test_vad_silence_duration_ms_above_max_clamped(self):
        """vad_silence_duration_ms=99999 は 10000 にクランプされること。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val023",
            target_language_code="ja",
            vad_enabled=True,
            vad_silence_duration_ms=99999,
        )
        assert t._vad_silence_duration_ms == 10000

    def test_vad_silence_duration_ms_above_max_emits_warn(self):
        """vad_silence_duration_ms=99999 は WARN ログを出力すること。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val024",
                target_language_code="ja",
                vad_enabled=True,
                vad_silence_duration_ms=99999,
            )
        assert "[WARN]" in cap.text

    def test_vad_silence_duration_ms_boundary_100_no_warn(self):
        """vad_silence_duration_ms=100 はクランプ・警告なし。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val025",
                target_language_code="ja",
                vad_enabled=True,
                vad_silence_duration_ms=100,
            )
        assert "[WARN]" not in cap.text

    def test_vad_silence_duration_ms_boundary_10000_no_warn(self):
        """vad_silence_duration_ms=10000 はクランプ・警告なし。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val026",
                target_language_code="ja",
                vad_enabled=True,
                vad_silence_duration_ms=10000,
            )
        assert "[WARN]" not in cap.text

    # ---- vad_enabled=False のとき: クランプ・警告なし ----

    def test_vad_disabled_threshold_out_of_range_no_clamp(self):
        """vad_enabled=False のとき、範囲外の vad_threshold もクランプされないこと
        (VAD が無効なので送信されない)。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val030",
            target_language_code="ja",
            vad_enabled=False,
            vad_threshold=-0.1,
        )
        # vad_enabled=False ではクランプしない: 元の値が保持される
        assert t._vad_threshold == pytest.approx(-0.1)

    def test_vad_disabled_threshold_out_of_range_no_warn(self):
        """vad_enabled=False のとき、範囲外の値でも WARN ログを出力しないこと。"""
        with _StdoutCapture() as cap:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val031",
                target_language_code="ja",
                vad_enabled=False,
                vad_threshold=-0.1,
            )
        assert "[WARN]" not in cap.text

    def test_vad_disabled_prefix_padding_ms_out_of_range_no_clamp(self):
        """vad_enabled=False のとき、範囲外の vad_prefix_padding_ms もクランプされないこと。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val032",
            target_language_code="ja",
            vad_enabled=False,
            vad_prefix_padding_ms=1,
        )
        assert t._vad_prefix_padding_ms == 1

    def test_vad_disabled_silence_duration_ms_out_of_range_no_clamp(self):
        """vad_enabled=False のとき、範囲外の vad_silence_duration_ms もクランプされないこと。"""
        t = RealtimeTranslator(
            api_key="sk-test-fake-vad-val033",
            target_language_code="ja",
            vad_enabled=False,
            vad_silence_duration_ms=999999,
        )
        assert t._vad_silence_duration_ms == 999999

    # ---- 後方互換: 例外を投げないこと ----

    def test_no_exception_on_invalid_threshold(self):
        """範囲外の vad_threshold でも例外が発生しないこと（後方互換）。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val040",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=-99.9,
            )
        except Exception as e:
            pytest.fail(f"例外が発生してはならない: {e}")

    def test_no_exception_on_invalid_prefix_padding_ms(self):
        """範囲外の vad_prefix_padding_ms でも例外が発生しないこと（後方互換）。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val041",
                target_language_code="ja",
                vad_enabled=True,
                vad_prefix_padding_ms=0,
            )
        except Exception as e:
            pytest.fail(f"例外が発生してはならない: {e}")

    def test_no_exception_on_invalid_silence_duration_ms(self):
        """範囲外の vad_silence_duration_ms でも例外が発生しないこと（後方互換）。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val042",
                target_language_code="ja",
                vad_enabled=True,
                vad_silence_duration_ms=0,
            )
        except Exception as e:
            pytest.fail(f"例外が発生してはならない: {e}")
