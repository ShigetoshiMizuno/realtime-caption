"""マイクの PCM RMS 統計を計測するツール。

W-COST-4 のアイドル切断閾値（audio_threshold）の妥当性判定材料。
指定デバイスから N 秒録音し、1 秒ごとの RMS を計算して統計を出力。

使用例:
    python tools/measure_audio_noise.py --device 0
    python tools/measure_audio_noise.py --device 0 --duration 60
    python tools/measure_audio_noise.py --list-devices

結合点:
    calculate_rms       <- record_and_compute
    calculate_statistics <- main()
    format_report       <- main()
    list_input_devices  <- main() (--list-devices 時)
    record_and_compute  <- main()
"""

import argparse
import json
import math
import struct
import sys
from pathlib import Path

# Windows cp932 環境でデバイス名に含まれる ® 等の文字を扱うため utf-8 に切替
# (実機検証で UnicodeEncodeError 検出 - 例: "Realtek(R) Audio")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# プロジェクトルートを sys.path に追加
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# pyaudio は遅延 import（--list-devices 不使用時にも import で例外が出るのを避ける）
pyaudio = None


def _import_pyaudio():
    """pyaudio を遅延 import する。"""
    global pyaudio
    if pyaudio is None:
        import pyaudio as _pa  # noqa: PLC0415
        pyaudio = _pa
    return pyaudio


def calculate_rms(pcm_bytes: bytes, sample_width: int = 2) -> float:
    """PCM バイト列から RMS 値を計算する。

    Args:
        pcm_bytes: PCM バイト列。
        sample_width: サンプル幅バイト数（1=8bit, 2=16bit）。

    Returns:
        RMS 値（float）。空バイト列は 0.0 を返す。
    """
    if not pcm_bytes:
        return 0.0

    if sample_width == 2:
        num_complete = len(pcm_bytes) // 2
        if num_complete == 0:
            return 0.0
        # little-endian signed short
        samples = struct.unpack(f"<{num_complete}h", pcm_bytes[: num_complete * 2])
    elif sample_width == 1:
        samples = struct.unpack(f"{len(pcm_bytes)}B", pcm_bytes)
    else:
        num_complete = len(pcm_bytes) // sample_width
        if num_complete == 0:
            return 0.0
        fmt = {3: "i", 4: "i"}.get(sample_width, "h")
        samples = []
        for i in range(num_complete):
            chunk = pcm_bytes[i * sample_width: (i + 1) * sample_width]
            val = int.from_bytes(chunk, byteorder="little", signed=True)
            samples.append(val)

    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / len(samples))


def calculate_statistics(rms_values: list) -> dict:
    """RMS 値リストから統計情報を計算する。

    Args:
        rms_values: RMS 値のリスト（1 秒ごとの値）。

    Returns:
        統計情報 dict（mean, median, min, max, p95, p5, std）。
        空リストの場合は全値 0.0 の dict を返す。
    """
    if not rms_values:
        return {
            "mean": 0.0,
            "median": 0.0,
            "min": 0.0,
            "max": 0.0,
            "p95": 0.0,
            "p5": 0.0,
            "std": 0.0,
        }

    n = len(rms_values)
    sorted_values = sorted(rms_values)

    mean = sum(rms_values) / n

    # 中央値
    if n % 2 == 1:
        median = sorted_values[n // 2]
    else:
        median = (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2.0

    min_val = sorted_values[0]
    max_val = sorted_values[-1]

    # パーセンタイル（線形補間）
    def _percentile(sorted_vals: list, p: float) -> float:
        if len(sorted_vals) == 1:
            return sorted_vals[0]
        idx = p / 100.0 * (len(sorted_vals) - 1)
        lower = int(idx)
        upper = min(lower + 1, len(sorted_vals) - 1)
        frac = idx - lower
        return sorted_vals[lower] * (1.0 - frac) + sorted_vals[upper] * frac

    p95 = _percentile(sorted_values, 95)
    p5 = _percentile(sorted_values, 5)

    # 標準偏差（母標準偏差）
    variance = sum((v - mean) ** 2 for v in rms_values) / n
    std = math.sqrt(variance)

    return {
        "mean": mean,
        "median": median,
        "min": min_val,
        "max": max_val,
        "p95": p95,
        "p5": p5,
        "std": std,
    }


def format_report(
    stats: dict,
    threshold: int,
    duration: float,
    device_info: dict,
    output_json: bool = False,
    rms_values: list | None = None,
) -> str:
    """計測結果をフォーマットする。

    Args:
        stats: calculate_statistics() の戻り値。
        threshold: 無音判定閾値（RMS < threshold で無音とみなす）。
        duration: 録音時間（秒）。
        device_info: デバイス情報 dict（index, name）。
        output_json: True のとき JSON 形式で返す。
        rms_values: 秒ごとの RMS 値リスト（無音判定に使用）。

    Returns:
        フォーマットされた文字列（テキスト or JSON）。
    """
    if rms_values is None:
        rms_values = []

    silent_count = sum(1 for v in rms_values if v < threshold)
    total_secs = int(duration)

    device_name = device_info.get("name", "不明")
    device_index = device_info.get("index", "不明")

    # 推奨閾値計算
    threshold_high = round(stats["p95"])
    threshold_mid = round(stats["median"] + stats["std"])
    threshold_low = round(stats["max"] * 0.1)

    if output_json:
        data = {
            "device": {"index": device_index, "name": device_name},
            "duration": duration,
            "samplerate": device_info.get("samplerate", 16000),
            "threshold": threshold,
            "statistics": {k: round(v, 1) for k, v in stats.items()},
            "silent_seconds": silent_count,
            "total_seconds": total_secs,
            "silent_ratio": round(silent_count / total_secs, 3) if total_secs > 0 else 0.0,
            "recommended_thresholds": {
                "high_sensitivity": threshold_high,
                "mid_sensitivity": threshold_mid,
                "low_sensitivity": threshold_low,
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    # テキスト形式
    silent_ratio = silent_count / total_secs * 100 if total_secs > 0 else 0.0

    lines = [
        "=== マイク RMS 統計 ===",
        f"デバイス: {device_index} ({device_name})",
        f"録音時間: {duration} 秒",
        f"サンプルレート: {device_info.get('samplerate', 16000)} Hz",
        "",
        "統計:",
        f"  {'平均':<16} {stats['mean']:.1f}",
        f"  {'中央値':<15} {stats['median']:.1f}",
        f"  {'最小':<16} {stats['min']:.1f}",
        f"  {'最大':<16} {stats['max']:.1f}",
        f"  {'5 パーセンタイル':<12} {stats['p5']:.1f}",
        f"  {'95 パーセンタイル':<11} {stats['p95']:.1f}",
        f"  {'標準偏差':<14} {stats['std']:.1f}",
        "",
        f"無音判定 (RMS < {threshold}): {silent_count} / {total_secs} 秒 ({silent_ratio:.1f}%)",
        "",
        "推奨閾値:",
        f"  音検出感度高 (95%ile)              {threshold_high}",
        f"  音検出感度中 (中央値+標準偏差)      {threshold_mid}",
        f"  音検出感度低 (最大の 10%)           {threshold_low}",
    ]
    return "\n".join(lines)


def list_input_devices() -> list:
    """利用可能な入力デバイスの一覧を返す。

    Returns:
        入力チャンネルを持つデバイス情報の list[dict]。
        各 dict は index, name, maxInputChannels を含む。
    """
    pa = _import_pyaudio()
    pa_instance = pa.PyAudio()
    devices = []
    try:
        count = pa_instance.get_device_count()
        for i in range(count):
            info = pa_instance.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                devices.append({
                    "index": info["index"],
                    "name": info["name"],
                    "maxInputChannels": info["maxInputChannels"],
                })
    finally:
        pa_instance.terminate()
    return devices


def record_and_compute(
    device_index,
    duration: float,
    samplerate: int = 16000,
) -> list:
    """指定デバイスから録音して 1 秒ごとの RMS 値リストを返す。

    Args:
        device_index: 入力デバイス index（None = システムデフォルト）。
        duration: 録音時間（秒）。
        samplerate: サンプルレート（Hz）。

    Returns:
        1 秒ごとの RMS 値 list[float]。
    """
    pa = _import_pyaudio()
    pa_instance = pa.PyAudio()
    sample_width = 2  # 16bit
    frames_per_sec = samplerate

    stream = pa_instance.open(
        format=pa.paInt16,
        channels=1,
        rate=samplerate,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=frames_per_sec,
    )

    rms_values = []
    total_secs = int(duration)
    try:
        for _ in range(total_secs):
            chunk = stream.read(frames_per_sec)
            rms = calculate_rms(chunk, sample_width=sample_width)
            rms_values.append(rms)
    finally:
        stream.stop_stream()
        stream.close()
        pa_instance.terminate()

    return rms_values


def main() -> int:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="マイクの PCM RMS 統計を計測するツール（W-COST-4 閾値検証用）",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        metavar="INT",
        help="入力デバイス index（デフォルト: システムデフォルト）",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        metavar="FLOAT",
        help="録音時間秒（デフォルト: 30.0）",
    )
    parser.add_argument(
        "--samplerate",
        type=int,
        default=16000,
        metavar="INT",
        help="サンプルレート（デフォルト: 16000）",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="利用可能な入力デバイス一覧を表示して終了",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="JSON 形式で出力",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=100,
        metavar="INT",
        help="無音判定閾値 RMS 値（デフォルト: 100）",
    )
    args = parser.parse_args()

    if args.list_devices:
        devices = list_input_devices()
        if args.output_json:
            print(json.dumps(devices, ensure_ascii=False, indent=2))
        else:
            print("=== 入力デバイス一覧 ===")
            for d in devices:
                print(f"  [{d['index']}] {d['name']} (ch: {d['maxInputChannels']})")
        return 0

    # デバイス名を取得（表示用）
    device_name = "システムデフォルト"
    if args.device is not None:
        try:
            devices = list_input_devices()
            for d in devices:
                if d["index"] == args.device:
                    device_name = d["name"]
                    break
        except Exception:
            device_name = f"デバイス {args.device}"

    print(f"録音開始: {args.duration} 秒 (デバイス: {args.device}, {args.samplerate} Hz)")

    rms_values = record_and_compute(
        device_index=args.device,
        duration=args.duration,
        samplerate=args.samplerate,
    )

    stats = calculate_statistics(rms_values)

    device_info = {
        "index": args.device if args.device is not None else "default",
        "name": device_name,
        "samplerate": args.samplerate,
    }

    report = format_report(
        stats,
        threshold=args.threshold,
        duration=args.duration,
        device_info=device_info,
        output_json=args.output_json,
        rms_values=rms_values,
    )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
