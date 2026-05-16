"""
原文イベント (session.input_transcript.delta) 受信確認 smoke (issue #121 問題 A)。

設定した API キーで実接続し、30 秒間ダミー音声 (サイン波 or sample wav) を送り
`session.input_transcript.delta` イベントが来るかを確認する。

使用例:
    python tools/test_source_transcript_smoke.py
    python tools/test_source_transcript_smoke.py --duration 60
    python tools/test_source_transcript_smoke.py --wav-file sample.wav
"""

import argparse
import asyncio
import base64
import hashlib
import json
import math
import struct
import sys
import time
import wave
from pathlib import Path

# Windows cp932 環境で日本語 + 特殊文字を扱うため utf-8 に切替
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# プロジェクトルートを sys.path に追加（config_utils 等を使うため）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    import websockets
    from websockets.asyncio.client import connect as websockets_connect
except ImportError:
    print("[source-smoke] websockets パッケージが必要です", flush=True)
    sys.exit(1)

# 実 API エンドポイント
_REALTIME_URL = "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate"

# exit code 定義
EXIT_OK = 0
EXIT_NOT_RECEIVED = 2
EXIT_AUTH_ERROR = 3
EXIT_TIMEOUT = 4


# ---------------------------------------------------------------------------
# 純関数: generate_sine_wave_pcm
# ---------------------------------------------------------------------------

def generate_sine_wave_pcm(
    duration: float,
    freq_hz: int = 1000,
    sample_rate: int = 24000,
) -> bytes:
    """指定時間分の 16bit signed little-endian PCM サイン波を生成する（純関数）。

    Parameters
    ----------
    duration    : 生成する音声の長さ（秒）
    freq_hz     : サイン波の周波数（Hz）。デフォルト 1000Hz
    sample_rate : サンプリングレート（Hz）。デフォルト 24000Hz

    Returns
    -------
    16bit little-endian PCM バイト列（mono）
    """
    num_samples = int(duration * sample_rate)
    amplitude = 16000  # int16 最大値 (32767) の約半分
    samples = [
        int(amplitude * math.sin(2.0 * math.pi * freq_hz * i / sample_rate))
        for i in range(num_samples)
    ]
    return struct.pack(f"<{num_samples}h", *samples)


# ---------------------------------------------------------------------------
# 純関数: load_wav_file
# ---------------------------------------------------------------------------

def load_wav_file(path: str) -> tuple[bytes, int]:
    """WAV ファイルを読み込んで (PCM bytes, sample_rate) を返す（純関数）。

    Parameters
    ----------
    path : WAV ファイルのパス

    Returns
    -------
    (PCM バイト列, サンプリングレート) のタプル
    """
    with wave.open(path, "rb") as wf:
        sample_rate = wf.getframerate()
        pcm_data = wf.readframes(wf.getnframes())
    return pcm_data, sample_rate


# ---------------------------------------------------------------------------
# 純関数: count_events
# ---------------------------------------------------------------------------

def count_events(events: list) -> dict[str, int]:
    """イベントリストをイベント型別にカウントして返す（純関数）。

    Parameters
    ----------
    events : dict のリスト。各 dict に "type" キーが含まれる想定

    Returns
    -------
    {event_type: count} の dict
    """
    counts: dict[str, int] = {}
    for event in events:
        evt_type = event.get("type", "")
        if evt_type:
            counts[evt_type] = counts.get(evt_type, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 純関数: format_report
# ---------------------------------------------------------------------------

def format_report(result: dict, output_json: bool) -> str:
    """テスト結果を出力用文字列にフォーマットして返す（純関数）。

    Parameters
    ----------
    result      : run_smoke_test の戻り値（dict）
    output_json : True なら JSON 形式、False なら人間が読めるテキスト形式

    Returns
    -------
    フォーマット済み文字列
    """
    if output_json:
        return json.dumps(result, ensure_ascii=False, indent=2)

    status = result.get("status", "unknown")
    elapsed = result.get("elapsed", 0.0)
    duration = result.get("duration", 30.0)
    audio_source = result.get("audio_source", "sine wave 1kHz")
    event_counts = result.get("event_counts", {})
    input_count = result.get("input_transcript_count", 0)
    output_count = result.get("output_transcript_count", 0)
    request_source = result.get("request_source_transcript", True)
    error_message = result.get("error_message") or ""

    lines = []
    lines.append("")
    lines.append("=== 結果 ===")
    lines.append("受信イベント別件数:")

    # 主要イベントを見やすく出力
    priority_keys = [
        "session.input_transcript.delta",
        "session.input_transcript.done",
        "session.output_transcript.delta",
        "session.output_transcript.done",
        "session.output_audio.delta",
        "session.created",
        "session.updated",
    ]
    other_count = 0
    for key in priority_keys:
        cnt = event_counts.get(key, 0)
        lines.append(f"  {key}: {cnt} 件")
    for key, cnt in event_counts.items():
        if key not in priority_keys:
            other_count += cnt
    if other_count > 0:
        lines.append(f"  その他: {other_count} 件")

    lines.append("")

    if status == "input_transcript_received":
        lines.append(f"判定: API が原文イベントを送信している ({input_count} 件)")
        lines.append("-> issue #121 問題 A: クライアント側 callback 配線を再確認推奨")
    elif status == "input_transcript_missing":
        lines.append(f"判定: API が原文イベントを送信していない (0 件)")
        lines.append("-> issue #121 問題 A: gpt-realtime-translate が原文イベント未対応の可能性")
        lines.append("-> W-COST-2 機能の見直しが必要")
    elif status == "auth_error":
        lines.append(f"ERROR: 認証エラー -- {error_message}")
        lines.append("  API キーを確認してください")
    elif status == "timeout":
        lines.append(f"TIMEOUT: 接続タイムアウト ({duration}s)")
        lines.append("  ネットワーク接続と API エンドポイントを確認してください")
    else:
        lines.append(f"ERROR: 不明なエラー -- {error_message}")

    lines.append("")
    lines.append(f"総時間: {elapsed:.1f}s")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# run_smoke_test: 実 API 接続（mock 可能な設計）
# ---------------------------------------------------------------------------

async def run_smoke_test(
    api_key: str,
    duration: float = 30.0,
    audio_pcm: bytes = b"",
    target_lang: str = "ja",
    timeout: float = 60.0,
) -> dict:
    """実 API に接続して session.input_transcript.delta 受信を確認する。

    websockets_connect をモック可能に設計している（テスト時は patch で差し替え）。

    Parameters
    ----------
    api_key   : OpenAI API キー
    duration  : 音声送信時間（秒）
    audio_pcm : 送信する PCM バイト列（24kHz mono 16bit）
    target_lang : 翻訳先言語コード
    timeout   : テストタイムアウト秒（duration + マージン）

    Returns
    -------
    {
        "status": str,                     # "input_transcript_received" / "input_transcript_missing"
                                           # / "auth_error" / "timeout" / "error"
        "input_transcript_count": int,
        "output_transcript_count": int,
        "event_counts": dict[str, int],
        "elapsed": float,
        "duration": float,
        "audio_source": str,
        "request_source_transcript": bool,
        "error_message": str | None,
    }
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": hashlib.sha256(api_key.encode()).hexdigest(),
    }

    # session.update: request_source_transcript=True
    session_payload = json.dumps({
        "type": "session.update",
        "session": {
            "audio": {
                "input": {
                    "transcription": {"model": "gpt-realtime-whisper"},
                },
            },
        },
    })

    # 音声データをチャンク分割（100ms = 2400 samples = 4800 bytes）
    chunk_size = 4800  # 24000Hz * 0.1s * 2bytes
    audio_chunks = [
        audio_pcm[i:i + chunk_size]
        for i in range(0, len(audio_pcm), chunk_size)
    ] if audio_pcm else []

    all_events: list[dict] = []
    auth_error_msg: str | None = None
    start = time.monotonic()

    try:
        async with websockets_connect(
            _REALTIME_URL,
            additional_headers=headers,
            open_timeout=min(timeout, 15.0),
        ) as ws:
            # セッション設定送信
            await ws.send(session_payload)

            # 音声送信コルーチン
            async def _send_audio():
                for chunk in audio_chunks:
                    if not chunk:
                        continue
                    audio_b64 = base64.b64encode(chunk).decode("utf-8")
                    await ws.send(json.dumps({
                        "type": "session.input_audio_buffer.append",
                        "audio": audio_b64,
                    }))
                    await asyncio.sleep(0.1)  # 100ms 間隔

            # 受信コルーチン: ソケットから直接 recv し、TimeoutError/EOFError で終了
            async def _recv_events():
                nonlocal auth_error_msg
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        raw = await ws.recv()
                    except (asyncio.TimeoutError, StopAsyncIteration, EOFError):
                        # recv が枯渇した = モック終端 or 実サーバが切断
                        break
                    except Exception:
                        break
                    try:
                        data = json.loads(raw)
                        all_events.append(data)

                        # 認証エラーは早期終了
                        if data.get("type") == "error":
                            error_data = data.get("error", {})
                            code = error_data.get("code", "")
                            msg = error_data.get("message", "")
                            if code == "invalid_api_key" or "invalid_api_key" in msg:
                                auth_error_msg = msg
                                return
                    except Exception:
                        continue

                    # duration 経過したら音声送信終了を待たずに打ち切り
                    if time.monotonic() - start >= timeout:
                        break

            # 並走実行: 送信 + 受信
            send_task = asyncio.create_task(_send_audio())
            recv_task = asyncio.create_task(_recv_events())

            # timeout で両タスクを強制終了
            try:
                await asyncio.wait_for(
                    asyncio.gather(send_task, recv_task, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                pass

            # タスクのキャンセル（念のため）
            for task in [send_task, recv_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

            elapsed = time.monotonic() - start

            # 認証エラーが検出された場合
            if auth_error_msg is not None:
                return {
                    "status": "auth_error",
                    "input_transcript_count": 0,
                    "output_transcript_count": 0,
                    "event_counts": count_events(all_events),
                    "elapsed": elapsed,
                    "duration": duration,
                    "audio_source": "pcm_bytes",
                    "request_source_transcript": True,
                    "error_message": auth_error_msg,
                }

            # 集計
            counts = count_events(all_events)
            input_count = counts.get("session.input_transcript.delta", 0)
            output_count = counts.get("session.output_transcript.delta", 0)

            status = "input_transcript_received" if input_count > 0 else "input_transcript_missing"
            return {
                "status": status,
                "input_transcript_count": input_count,
                "output_transcript_count": output_count,
                "event_counts": counts,
                "elapsed": elapsed,
                "duration": duration,
                "audio_source": "pcm_bytes",
                "request_source_transcript": True,
                "error_message": None,
            }

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        return {
            "status": "timeout",
            "input_transcript_count": 0,
            "output_transcript_count": 0,
            "event_counts": count_events(all_events),
            "elapsed": elapsed,
            "duration": duration,
            "audio_source": "pcm_bytes",
            "request_source_transcript": True,
            "error_message": "接続タイムアウト",
        }
    except OSError as e:
        elapsed = time.monotonic() - start
        return {
            "status": "timeout",
            "input_transcript_count": 0,
            "output_transcript_count": 0,
            "event_counts": {},
            "elapsed": elapsed,
            "duration": duration,
            "audio_source": "pcm_bytes",
            "request_source_transcript": True,
            "error_message": f"接続失敗: {e}",
        }
    except Exception as e:
        err_str = str(e)
        elapsed = time.monotonic() - start
        if "401" in err_str or "403" in err_str or "Unauthorized" in err_str:
            return {
                "status": "auth_error",
                "input_transcript_count": 0,
                "output_transcript_count": 0,
                "event_counts": {},
                "elapsed": elapsed,
                "duration": duration,
                "audio_source": "pcm_bytes",
                "request_source_transcript": True,
                "error_message": err_str,
            }
        return {
            "status": "error",
            "input_transcript_count": 0,
            "output_transcript_count": 0,
            "event_counts": {},
            "elapsed": elapsed,
            "duration": duration,
            "audio_source": "pcm_bytes",
            "request_source_transcript": True,
            "error_message": err_str,
        }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _load_api_key_from_config() -> str:
    """config.yaml から API キーを読み込む。失敗時は空文字を返す。"""
    try:
        import yaml
        from config_utils import decode_api_key

        cfg_path = _PROJECT_ROOT / "config.yaml"
        if not cfg_path.exists():
            return ""
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        raw_key = cfg.get("openai", {}).get("api_key", "")
        return decode_api_key(raw_key)
    except Exception as e:
        print(f"[source-smoke] config.yaml 読み込み失敗: {e}", flush=True)
        return ""


def _status_to_exit_code(status: str) -> int:
    """status 文字列を exit code に変換する。"""
    return {
        "input_transcript_received": EXIT_OK,
        "input_transcript_missing": EXIT_NOT_RECEIVED,
        "auth_error": EXIT_AUTH_ERROR,
        "timeout": EXIT_TIMEOUT,
    }.get(status, EXIT_TIMEOUT)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="原文イベント受信確認 smoke テスト (issue #121 問題 A)"
    )
    parser.add_argument("--api-key", help="OpenAI API キー（省略時は config.yaml から読込）")
    parser.add_argument("--duration", type=float, default=30.0, help="音声送信時間（秒、デフォルト 30.0）")
    parser.add_argument("--wav-file", help="WAV ファイルパス（省略時は 1kHz サイン波）")
    parser.add_argument("--target-lang", default="ja", help="翻訳先言語コード（デフォルト ja）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 形式で出力")
    args = parser.parse_args()

    api_key = args.api_key or _load_api_key_from_config()
    if not api_key:
        print(
            "[source-smoke] API キーが設定されていません。--api-key または config.yaml を確認してください。",
            flush=True,
        )
        return EXIT_AUTH_ERROR

    # 音声データ準備
    if args.wav_file:
        try:
            audio_pcm, sample_rate = load_wav_file(args.wav_file)
            audio_source = f"wav: {args.wav_file}"
        except Exception as e:
            print(f"[source-smoke] WAV 読み込み失敗: {e}", flush=True)
            return EXIT_TIMEOUT
    else:
        audio_pcm = generate_sine_wave_pcm(
            duration=args.duration, freq_hz=1000, sample_rate=24000
        )
        audio_source = "sine wave 1kHz"

    if not args.output_json:
        print("=== 原文イベント受信 smoke test (#121 A) ===", flush=True)
        print("パラメータ:", flush=True)
        print(f"  request_source_transcript: True", flush=True)
        print(f"  duration: {args.duration} 秒", flush=True)
        print(f"  audio_source: {audio_source}", flush=True)
        print("", flush=True)
        print("接続中 ...", flush=True)

    # timeout = duration + 余裕 30 秒
    timeout = args.duration + 30.0

    result = asyncio.run(
        run_smoke_test(
            api_key=api_key,
            duration=args.duration,
            audio_pcm=audio_pcm,
            target_lang=args.target_lang,
            timeout=timeout,
        )
    )
    # audio_source を結果に反映
    result["audio_source"] = audio_source

    report = format_report(result, output_json=args.output_json)
    print(report, flush=True)

    return _status_to_exit_code(result["status"])


if __name__ == "__main__":
    sys.exit(main())
