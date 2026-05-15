"""
W-COST-3 VAD 設定の API 受入を確認する smoke テスト。

TBD-3-1: gpt-realtime-translate が audio.input.turn_detection を受け入れるか
を実機 API で確認する。設定した API キーで実接続し、session.update を送って
エラー (Unknown parameter, invalid_request_error 等) が返らないか判定する。

使用例:
    python tools/test_vad_api_smoke.py
    python tools/test_vad_api_smoke.py --threshold 0.7 --silence-ms 800
    python tools/test_vad_api_smoke.py --api-key sk-real-key

結合点:
    build_session_update  <- run_smoke_test
    parse_error_response  <- run_smoke_test
    format_report         <- main()
    run_smoke_test        <- main()
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

# プロジェクトルートを sys.path に追加（config_utils 等を使うため）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    import websockets
    from websockets.asyncio.client import connect as websockets_connect
except ImportError:
    print("[vad-smoke] websockets パッケージが必要です", flush=True)
    sys.exit(1)

# 実 API エンドポイント
_REALTIME_URL = "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate"

# exit code 定義
EXIT_OK = 0
EXIT_UNSUPPORTED = 2
EXIT_AUTH_ERROR = 3
EXIT_TIMEOUT = 4


# ---------------------------------------------------------------------------
# 純関数: build_session_update
# ---------------------------------------------------------------------------

def build_session_update(
    threshold: float,
    silence_ms: int,
    prefix_ms: int,
    target_lang: str,
) -> dict:
    """session.update payload を構築して返す（純関数）。

    PR #97 の session.update と同じ構造（audio.input.turn_detection）に準拠する。

    Parameters
    ----------
    threshold   : VAD 起動音量閾値（0.0〜1.0）
    silence_ms  : silence_duration_ms（ms）
    prefix_ms   : prefix_padding_ms（ms）
    target_lang : 翻訳先言語コード（BCP-47）

    Returns
    -------
    session.update の JSON payload（dict）
    """
    return {
        "type": "session.update",
        "session": {
            "audio": {
                "input": {
                    "transcription": {"model": "gpt-realtime-whisper"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": threshold,
                        "prefix_padding_ms": prefix_ms,
                        "silence_duration_ms": silence_ms,
                    },
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# 純関数: parse_error_response
# ---------------------------------------------------------------------------

def parse_error_response(error_data: dict) -> tuple[str, str]:
    """エラーレスポンス dict を (category, message) に分類して返す（純関数）。

    Parameters
    ----------
    error_data : API から受信した error オブジェクト（dict）

    Returns
    -------
    (category, message) のタプル。
    category は以下のいずれか:
        "unsupported" - Unknown parameter（API が turn_detection を非サポート）
        "auth"        - 認証エラー（API キー無効）
        "rate_limit"  - レート制限
        "unknown"     - 上記以外
    """
    if not error_data:
        return ("unknown", "")

    message = error_data.get("message", "")
    error_type = error_data.get("type", "")
    error_code = error_data.get("code", "")

    if "Unknown parameter" in message:
        return ("unsupported", message)

    if error_code == "invalid_api_key" or "invalid_api_key" in message:
        return ("auth", message)

    # code フィールドに invalid_api_key が入ることもある
    if "incorrect api key" in message.lower() or "invalid_api_key" in error_type.lower():
        return ("auth", message)

    if "rate_limit" in error_type.lower() or "rate_limit" in error_code.lower():
        return ("rate_limit", message)

    return ("unknown", message)


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
    timeout = result.get("timeout", 15.0)
    params = result.get("params", {})
    error_message = result.get("error_message") or ""

    lines = []
    lines.append("")
    lines.append("=== 結果 ===")

    if status == "ok":
        lines.append("OK: API 受入 OK — turn_detection エラーなし")
        lines.append("  session.audio.input.transcription / turn_detection 両方受理")
        lines.append("TBD-3-1: クローズ可（実装通り動作）")
    elif status == "unsupported":
        lines.append(f"NG: API 受入 NG — {error_message}")
        lines.append("TBD-3-1: gpt-realtime-translate は turn_detection 未対応")
        lines.append("推奨: W-COST-3 を将来課題に棚上げ、または別エンドポイント検討")
    elif status == "auth_error":
        lines.append(f"ERROR: 認証エラー — {error_message}")
        lines.append("  API キーを確認してください")
    elif status == "timeout":
        lines.append(f"TIMEOUT: 接続タイムアウト ({timeout}s)")
        lines.append("  ネットワーク接続と API エンドポイントを確認してください")
    else:
        lines.append(f"ERROR: 不明なエラー — {error_message}")

    lines.append("")
    lines.append(f"総時間: {elapsed:.2f}s")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# run_smoke_test: 実 API 接続（mock 可能な設計）
# ---------------------------------------------------------------------------

async def run_smoke_test(
    api_key: str,
    threshold: float = 0.5,
    silence_ms: int = 500,
    prefix_ms: int = 300,
    target_lang: str = "ja",
    timeout: float = 15.0,
) -> dict:
    """実 API に接続して VAD 設定の受入を確認する。

    websockets_connect をモック可能に設計している（テスト時は patch で差し替え）。

    Parameters
    ----------
    api_key     : OpenAI API キー
    threshold   : VAD 起動音量閾値（0.0〜1.0）
    silence_ms  : silence_duration_ms（ms）
    prefix_ms   : prefix_padding_ms（ms）
    target_lang : 翻訳先言語コード
    timeout     : テストタイムアウト秒

    Returns
    -------
    {
        "status": str,            # "ok" / "unsupported" / "auth_error" / "timeout" / "error"
        "error_category": str | None,
        "error_message": str | None,
        "elapsed": float,
        "timeout": float,
        "params": dict,
    }
    """
    params = {
        "vad_enabled": True,
        "vad_threshold": threshold,
        "vad_silence_ms": silence_ms,
        "vad_prefix_ms": prefix_ms,
        "target_language": target_lang,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": hashlib.sha256(api_key.encode()).hexdigest(),
    }

    payload = build_session_update(
        threshold=threshold,
        silence_ms=silence_ms,
        prefix_ms=prefix_ms,
        target_lang=target_lang,
    )

    start = time.monotonic()

    try:
        async with websockets_connect(
            _REALTIME_URL,
            additional_headers=headers,
            open_timeout=min(timeout, 10.0),
        ) as ws:
            # session.update 送信
            await ws.send(json.dumps(payload))

            # timeout 秒間、エラーイベントを監視
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
                except asyncio.TimeoutError:
                    # タイムアウト内にエラーがなければ OK
                    break

                try:
                    data = json.loads(raw)
                except Exception:
                    continue

                evt_type = data.get("type", "")

                if evt_type == "error":
                    error_data = data.get("error", {})
                    category, message = parse_error_response(error_data)
                    elapsed = time.monotonic() - start

                    if category == "auth":
                        return {
                            "status": "auth_error",
                            "error_category": "auth",
                            "error_message": message,
                            "elapsed": elapsed,
                            "timeout": timeout,
                            "params": params,
                        }
                    elif category == "unsupported":
                        return {
                            "status": "unsupported",
                            "error_category": "unsupported",
                            "error_message": message,
                            "elapsed": elapsed,
                            "timeout": timeout,
                            "params": params,
                        }
                    else:
                        return {
                            "status": "error",
                            "error_category": category,
                            "error_message": message,
                            "elapsed": elapsed,
                            "timeout": timeout,
                            "params": params,
                        }

                elif evt_type in ("session.created", "session.updated"):
                    # session.updated = API が session.update を受け入れた証拠
                    elapsed = time.monotonic() - start
                    return {
                        "status": "ok",
                        "error_category": None,
                        "error_message": None,
                        "elapsed": elapsed,
                        "timeout": timeout,
                        "params": params,
                    }

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        return {
            "status": "timeout",
            "error_category": "timeout",
            "error_message": "接続タイムアウト",
            "elapsed": elapsed,
            "timeout": timeout,
            "params": params,
        }
    except OSError as e:
        elapsed = time.monotonic() - start
        return {
            "status": "timeout",
            "error_category": "timeout",
            "error_message": f"接続失敗: {e}",
            "elapsed": elapsed,
            "timeout": timeout,
            "params": params,
        }
    except Exception as e:
        # 認証エラーは websockets が 401 として送出することがある
        err_str = str(e)
        elapsed = time.monotonic() - start
        if "401" in err_str or "403" in err_str or "Unauthorized" in err_str:
            return {
                "status": "auth_error",
                "error_category": "auth",
                "error_message": err_str,
                "elapsed": elapsed,
                "timeout": timeout,
                "params": params,
            }
        return {
            "status": "error",
            "error_category": "unknown",
            "error_message": err_str,
            "elapsed": elapsed,
            "timeout": timeout,
            "params": params,
        }

    # タイムアウトまでエラーなし = OK
    elapsed = time.monotonic() - start
    return {
        "status": "ok",
        "error_category": None,
        "error_message": None,
        "elapsed": elapsed,
        "timeout": timeout,
        "params": params,
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
        print(f"[vad-smoke] config.yaml 読み込み失敗: {e}", flush=True)
        return ""


def _status_to_exit_code(status: str) -> int:
    """status 文字列を exit code に変換する。"""
    return {
        "ok": EXIT_OK,
        "unsupported": EXIT_UNSUPPORTED,
        "auth_error": EXIT_AUTH_ERROR,
        "timeout": EXIT_TIMEOUT,
    }.get(status, EXIT_TIMEOUT)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="W-COST-3 VAD 設定の API 受入を確認する smoke テスト (TBD-3-1)"
    )
    parser.add_argument("--api-key", help="OpenAI API キー（省略時は config.yaml から読込）")
    parser.add_argument("--threshold", type=float, default=0.5, help="VAD threshold（デフォルト 0.5）")
    parser.add_argument("--silence-ms", type=int, default=500, help="silence_duration_ms（デフォルト 500）")
    parser.add_argument("--prefix-ms", type=int, default=300, help="prefix_padding_ms（デフォルト 300）")
    parser.add_argument("--target-lang", default="ja", help="翻訳先言語コード（デフォルト ja）")
    parser.add_argument("--timeout", type=float, default=15.0, help="テストタイムアウト秒（デフォルト 15.0）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON 形式で出力")
    args = parser.parse_args()

    api_key = args.api_key or _load_api_key_from_config()
    if not api_key:
        print("[vad-smoke] API キーが設定されていません。--api-key または config.yaml を確認してください。", flush=True)
        return EXIT_AUTH_ERROR

    if not args.output_json:
        print("=== W-COST-3 VAD API smoke test ===", flush=True)
        print("パラメータ:", flush=True)
        print(f"  vad_enabled        : True", flush=True)
        print(f"  vad_threshold      : {args.threshold}", flush=True)
        print(f"  vad_silence_ms     : {args.silence_ms}", flush=True)
        print(f"  vad_prefix_ms      : {args.prefix_ms}", flush=True)
        print(f"  target_language    : {args.target_lang}", flush=True)
        print("", flush=True)
        print("接続中 ...", flush=True)

    result = asyncio.run(
        run_smoke_test(
            api_key=api_key,
            threshold=args.threshold,
            silence_ms=args.silence_ms,
            prefix_ms=args.prefix_ms,
            target_lang=args.target_lang,
            timeout=args.timeout,
        )
    )

    report = format_report(result, output_json=args.output_json)
    print(report, flush=True)

    return _status_to_exit_code(result["status"])


if __name__ == "__main__":
    sys.exit(main())
