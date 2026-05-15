"""
tests/test_measure_audio_noise.py

tools/measure_audio_noise.py の単体テスト。
calculate_rms / calculate_statistics / format_report の純関数をテストする。
list_input_devices / record_and_compute は pyaudio を mock で代替する。
"""
import json
import math
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# tools/ を sys.path に追加して measure_audio_noise をインポートできるようにする
_ROOT = Path(__file__).parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from measure_audio_noise import (
    calculate_rms,
    calculate_statistics,
    format_report,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_pcm_bytes(values: list[int], sample_width: int = 2) -> bytes:
    """16bit signed int のリストを PCM バイト列に変換するヘルパー。"""
    fmt = f"<{len(values)}h"  # little-endian signed short
    return struct.pack(fmt, *values)


def _sine_wave_rms(amplitude: int, num_samples: int, sample_width: int = 2) -> tuple[bytes, float]:
    """サイン波 PCM バイト列と理論 RMS を返すヘルパー。

    RMS = amplitude / sqrt(2) （純粋なサイン波の場合）
    """
    import math
    samples = [int(amplitude * math.sin(2 * math.pi * i / num_samples)) for i in range(num_samples)]
    pcm = _make_pcm_bytes(samples, sample_width)
    expected_rms = amplitude / math.sqrt(2)
    return pcm, expected_rms


# ---------------------------------------------------------------------------
# calculate_rms テスト
# ---------------------------------------------------------------------------

class TestCalculateRms:
    """calculate_rms(pcm_bytes: bytes, sample_width: int = 2) -> float のテスト"""

    def test_silence_returns_zero(self):
        """全ゼロ PCM バイト列 → RMS = 0.0"""
        pcm = b"\x00" * 100
        result = calculate_rms(pcm)
        assert result == pytest.approx(0.0)

    def test_constant_value_returns_that_value(self):
        """一定値バイト列（全 100）→ RMS = 100.0"""
        # 16bit signed = 100 の PCM 100 サンプル
        pcm = _make_pcm_bytes([100] * 100)
        result = calculate_rms(pcm)
        assert result == pytest.approx(100.0)

    def test_sine_wave_matches_theoretical_rms(self):
        """サイン波サンプル → 理論 RMS = amplitude / sqrt(2) に近い値"""
        amplitude = 1000
        num_samples = 512
        pcm, expected_rms = _sine_wave_rms(amplitude, num_samples)
        result = calculate_rms(pcm)
        # サイン波の理論 RMS に対して 5% 以内の精度
        assert abs(result - expected_rms) / expected_rms < 0.05

    def test_single_sample(self):
        """1 サンプルのみの PCM → RMS = abs(値)"""
        pcm = _make_pcm_bytes([200])
        result = calculate_rms(pcm)
        assert result == pytest.approx(200.0)

    def test_empty_bytes_returns_zero(self):
        """空バイト列 → RMS = 0.0（例外なし）"""
        result = calculate_rms(b"")
        assert result == pytest.approx(0.0)

    def test_odd_bytes_handled(self):
        """奇数バイト長（sample_width=2 で不完全なフレーム）→ 例外なしで処理"""
        pcm = b"\x00\x01\x02"  # 3 バイト = 1 完全フレーム + 1 余りバイト
        # クラッシュしないことを確認（戻り値は問わない）
        result = calculate_rms(pcm)
        assert isinstance(result, float)

    def test_returns_float(self):
        """戻り値が float 型である"""
        pcm = _make_pcm_bytes([50] * 10)
        result = calculate_rms(pcm)
        assert isinstance(result, float)

    def test_negative_values_contribute_to_rms(self):
        """負の PCM 値も RMS 計算に正しく寄与する"""
        pcm_pos = _make_pcm_bytes([100] * 100)
        pcm_neg = _make_pcm_bytes([-100] * 100)
        assert calculate_rms(pcm_pos) == pytest.approx(calculate_rms(pcm_neg))

    def test_sample_width_1_supported(self):
        """sample_width=1 (8bit) でも処理できる"""
        pcm = bytes([100] * 10)  # 8bit unsigned
        # 例外が出ないことを確認
        result = calculate_rms(pcm, sample_width=1)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# calculate_statistics テスト
# ---------------------------------------------------------------------------

class TestCalculateStatistics:
    """calculate_statistics(rms_values: list[float]) -> dict のテスト"""

    def test_single_element(self):
        """単一要素リスト → 平均・中央値・最小・最大が同じ値"""
        result = calculate_statistics([42.0])
        assert result["mean"] == pytest.approx(42.0)
        assert result["median"] == pytest.approx(42.0)
        assert result["min"] == pytest.approx(42.0)
        assert result["max"] == pytest.approx(42.0)

    def test_multiple_elements_mean(self):
        """複数要素リスト → 平均値が正確"""
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calculate_statistics(values)
        assert result["mean"] == pytest.approx(30.0)

    def test_multiple_elements_median(self):
        """複数要素リスト → 中央値が正確"""
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calculate_statistics(values)
        assert result["median"] == pytest.approx(30.0)

    def test_multiple_elements_min_max(self):
        """複数要素リスト → 最小値・最大値が正確"""
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calculate_statistics(values)
        assert result["min"] == pytest.approx(10.0)
        assert result["max"] == pytest.approx(50.0)

    def test_percentile_95(self):
        """95 パーセンタイルが計算される"""
        values = list(range(1, 101))  # 1..100
        result = calculate_statistics([float(v) for v in values])
        assert "p95" in result
        # 95%ile ≈ 95 (±2 程度の誤差を許容)
        assert 93.0 <= result["p95"] <= 97.0

    def test_percentile_5(self):
        """5 パーセンタイルが計算される"""
        values = list(range(1, 101))
        result = calculate_statistics([float(v) for v in values])
        assert "p5" in result
        assert 3.0 <= result["p5"] <= 7.0

    def test_std_dev(self):
        """標準偏差が計算される"""
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        result = calculate_statistics(values)
        assert "std" in result
        # 標準偏差 ≈ 2.0
        assert result["std"] == pytest.approx(2.0, abs=0.1)

    def test_empty_list_returns_zeros(self):
        """空リスト → 全 0 またはエラーなく処理（RMS = 0 の統計）"""
        result = calculate_statistics([])
        assert isinstance(result, dict)
        # 空の場合でも dict が返ることだけ確認（値は 0 or None）
        assert "mean" in result

    def test_returns_dict(self):
        """戻り値が dict 型である"""
        result = calculate_statistics([10.0, 20.0])
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        """必須キー（mean, median, min, max, p95, p5, std）が存在する"""
        result = calculate_statistics([10.0, 20.0, 30.0])
        for key in ("mean", "median", "min", "max", "p95", "p5", "std"):
            assert key in result, f"キー '{key}' が存在しない"

    def test_single_element_std_is_zero(self):
        """単一要素リスト → 標準偏差 = 0.0"""
        result = calculate_statistics([42.0])
        assert result["std"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# format_report テスト
# ---------------------------------------------------------------------------

class TestFormatReport:
    """format_report(stats, threshold, duration, device_info, output_json) -> str のテスト"""

    def _make_stats(self):
        return {
            "mean": 45.2,
            "median": 42.0,
            "min": 12.0,
            "max": 1234.5,
            "p5": 18.0,
            "p95": 89.0,
            "std": 156.3,
        }

    def _make_device_info(self):
        return {"index": 0, "name": "マイク (Realtek Audio)"}

    def test_text_contains_header(self):
        """テキスト出力にヘッダーが含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert "マイク RMS 統計" in report

    def test_text_contains_device_info(self):
        """テキスト出力にデバイス情報が含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert "Realtek Audio" in report

    def test_text_contains_statistics(self):
        """テキスト出力に統計値が含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert "45.2" in report or "45" in report  # 平均値

    def test_text_contains_threshold_line(self):
        """テキスト出力に閾値判定行が含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert "100" in report  # threshold=100

    def test_text_contains_duration(self):
        """テキスト出力に録音時間が含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert "30" in report

    def test_text_contains_recommended_threshold(self):
        """テキスト出力に推奨閾値セクションが含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert "推奨" in report

    def test_json_is_valid(self):
        """JSON 形式で valid な JSON 文字列を返す"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=True
        )
        data = json.loads(report)
        assert isinstance(data, dict)

    def test_json_contains_statistics(self):
        """JSON に統計情報キーが含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=True
        )
        data = json.loads(report)
        assert "statistics" in data

    def test_json_contains_device_info(self):
        """JSON にデバイス情報が含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=True
        )
        data = json.loads(report)
        assert "device" in data

    def test_json_contains_threshold(self):
        """JSON に threshold キーが含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=True
        )
        data = json.loads(report)
        assert "threshold" in data
        assert data["threshold"] == 100

    def test_json_contains_duration(self):
        """JSON に duration キーが含まれる"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=True
        )
        data = json.loads(report)
        assert "duration" in data
        assert data["duration"] == pytest.approx(30.0)

    def test_json_statistics_values(self):
        """JSON の統計値が正しい"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=True
        )
        data = json.loads(report)
        stats = data["statistics"]
        assert stats["mean"] == pytest.approx(45.2)
        assert stats["median"] == pytest.approx(42.0)

    def test_threshold_silent_ratio_in_text(self):
        """テキスト出力に無音判定比率情報が含まれる"""
        # threshold=100, rms_values は stats から間接的に判定されることを確認
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        # 「無音」または silent 関連のテキストが含まれる
        assert "無音" in report

    def test_returns_string(self):
        """戻り値が str 型である"""
        report = format_report(
            self._make_stats(), threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert isinstance(report, str)

    def test_recommended_threshold_high_is_p95(self):
        """推奨閾値（感度高）が p95 の値を含む"""
        stats = self._make_stats()  # p95 = 89.0
        report = format_report(
            stats, threshold=100, duration=30.0,
            device_info=self._make_device_info(), output_json=False
        )
        assert "89" in report


# ---------------------------------------------------------------------------
# list_input_devices テスト（pyaudio mock）
# ---------------------------------------------------------------------------

class TestListInputDevices:
    """list_input_devices() -> list[dict] のテスト（pyaudio mock）"""

    def test_returns_list_of_dicts(self):
        """戻り値が list[dict] 型である"""
        from measure_audio_noise import list_input_devices

        mock_pa = MagicMock()
        mock_pa.get_device_count.return_value = 3
        # デバイス 0 と 2 は入力あり、デバイス 1 は入力なし
        def _device_info(i):
            if i == 0:
                return {"index": 0, "name": "Microphone A", "maxInputChannels": 2}
            elif i == 1:
                return {"index": 1, "name": "Speaker B", "maxInputChannels": 0}
            else:
                return {"index": 2, "name": "Microphone C", "maxInputChannels": 1}

        mock_pa.get_device_info_by_index.side_effect = _device_info

        with patch("measure_audio_noise.pyaudio") as mock_pyaudio_module:
            mock_pyaudio_module.PyAudio.return_value = mock_pa
            result = list_input_devices()

        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)

    def test_filters_input_only(self):
        """入力チャンネルを持つデバイスのみ返す"""
        from measure_audio_noise import list_input_devices

        mock_pa = MagicMock()
        mock_pa.get_device_count.return_value = 3

        def _device_info(i):
            channels = [2, 0, 1][i]
            return {"index": i, "name": f"Device {i}", "maxInputChannels": channels}

        mock_pa.get_device_info_by_index.side_effect = _device_info

        with patch("measure_audio_noise.pyaudio") as mock_pyaudio_module:
            mock_pyaudio_module.PyAudio.return_value = mock_pa
            result = list_input_devices()

        # maxInputChannels > 0 のデバイスのみ
        assert len(result) == 2

    def test_contains_index_and_name(self):
        """各デバイス dict に index と name が含まれる"""
        from measure_audio_noise import list_input_devices

        mock_pa = MagicMock()
        mock_pa.get_device_count.return_value = 1
        mock_pa.get_device_info_by_index.return_value = {
            "index": 0, "name": "Test Mic", "maxInputChannels": 1
        }

        with patch("measure_audio_noise.pyaudio") as mock_pyaudio_module:
            mock_pyaudio_module.PyAudio.return_value = mock_pa
            result = list_input_devices()

        assert result[0]["index"] == 0
        assert result[0]["name"] == "Test Mic"


# ---------------------------------------------------------------------------
# record_and_compute テスト（pyaudio mock）
# ---------------------------------------------------------------------------

class TestRecordAndCompute:
    """record_and_compute(device_index, duration, samplerate) -> list[float] のテスト"""

    def test_returns_list_of_floats(self):
        """戻り値が list[float] 型である"""
        from measure_audio_noise import record_and_compute

        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream

        # 1 秒分のフレーム = samplerate * 2 bytes (16bit)
        samplerate = 16000
        frames_per_sec = samplerate  # 1 チャンク = 1 秒分
        silence = b"\x00" * (frames_per_sec * 2)
        mock_stream.read.return_value = silence

        with patch("measure_audio_noise.pyaudio") as mock_pyaudio_module:
            mock_pyaudio_module.PyAudio.return_value = mock_pa
            mock_pyaudio_module.paInt16 = 8  # fake constant
            result = record_and_compute(
                device_index=0, duration=3.0, samplerate=samplerate
            )

        assert isinstance(result, list)
        for v in result:
            assert isinstance(v, float)

    def test_returns_correct_number_of_seconds(self):
        """duration 秒分の RMS 値リストを返す"""
        from measure_audio_noise import record_and_compute

        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream

        samplerate = 16000
        silence = b"\x00" * (samplerate * 2)
        mock_stream.read.return_value = silence

        with patch("measure_audio_noise.pyaudio") as mock_pyaudio_module:
            mock_pyaudio_module.PyAudio.return_value = mock_pa
            mock_pyaudio_module.paInt16 = 8
            result = record_and_compute(
                device_index=0, duration=5.0, samplerate=samplerate
            )

        # 5 秒分 → 5 要素
        assert len(result) == 5

    def test_silence_returns_zero_rms(self):
        """無音入力 → RMS = 0.0 のリストを返す"""
        from measure_audio_noise import record_and_compute

        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream

        samplerate = 16000
        silence = b"\x00" * (samplerate * 2)
        mock_stream.read.return_value = silence

        with patch("measure_audio_noise.pyaudio") as mock_pyaudio_module:
            mock_pyaudio_module.PyAudio.return_value = mock_pa
            mock_pyaudio_module.paInt16 = 8
            result = record_and_compute(
                device_index=None, duration=2.0, samplerate=samplerate
            )

        for rms in result:
            assert rms == pytest.approx(0.0)
