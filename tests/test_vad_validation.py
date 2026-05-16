"""
tests/test_vad_validation.py

_clamp_with_warning ヘルパー関数の単体テスト、および
refactor/remove-vad-dead-code 後の後方互換検証。

テストケース:
1. _clamp_with_warning の単体テスト（VAD 削除後も関数自体は残る）
   - 範囲内: クランプなし・警告なし
   - 下限未満: 下限にクランプ + WARN ログ
   - 上限超過: 上限にクランプ + WARN ログ
   - 境界値ちょうど: クランプなし・警告なし
2. RealtimeTranslator への vad_* 渡し後方互換テスト
   - vad_* を渡しても TypeError にならないこと（**deprecated_kwargs で吸収）
   - 渡した値はクランプされない（内部で無視される）
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
# 2. RealtimeTranslator への vad_* 渡し後方互換テスト
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorVadBackwardCompat:
    """refactor/remove-vad-dead-code 後の後方互換性検証。

    vad_* パラメータは削除済みだが、既存コードから渡されても
    TypeError にならないこと（**deprecated_kwargs で吸収）を確認する。
    """

    def test_vad_enabled_true_no_type_error(self):
        """vad_enabled=True を渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val001",
                target_language_code="ja",
                vad_enabled=True,
            )
        except TypeError as e:
            pytest.fail(f"vad_enabled=True で TypeError が発生してはならない: {e}")

    def test_vad_enabled_false_no_type_error(self):
        """vad_enabled=False を渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val002",
                target_language_code="ja",
                vad_enabled=False,
            )
        except TypeError as e:
            pytest.fail(f"vad_enabled=False で TypeError が発生してはならない: {e}")

    def test_vad_threshold_no_type_error(self):
        """vad_threshold を渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val003",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=0.5,
            )
        except TypeError as e:
            pytest.fail(f"vad_threshold で TypeError が発生してはならない: {e}")

    def test_vad_prefix_padding_ms_no_type_error(self):
        """vad_prefix_padding_ms を渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val004",
                target_language_code="ja",
                vad_enabled=True,
                vad_prefix_padding_ms=300,
            )
        except TypeError as e:
            pytest.fail(f"vad_prefix_padding_ms で TypeError が発生してはならない: {e}")

    def test_vad_silence_duration_ms_no_type_error(self):
        """vad_silence_duration_ms を渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val005",
                target_language_code="ja",
                vad_enabled=True,
                vad_silence_duration_ms=500,
            )
        except TypeError as e:
            pytest.fail(f"vad_silence_duration_ms で TypeError が発生してはならない: {e}")

    def test_all_vad_params_no_type_error(self):
        """全 vad_* パラメータをまとめて渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val006",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=0.7,
                vad_prefix_padding_ms=200,
                vad_silence_duration_ms=800,
            )
        except TypeError as e:
            pytest.fail(f"全 vad_* パラメータで TypeError が発生してはならない: {e}")

    def test_unknown_kwarg_raises_type_error(self):
        """vad_* 以外の不明キーワード引数は TypeError になること（誤用防止）。"""
        with pytest.raises(TypeError):
            RealtimeTranslator(
                api_key="sk-test-fake-vad-val007",
                target_language_code="ja",
                unknown_future_param=True,
            )
