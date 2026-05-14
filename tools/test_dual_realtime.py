"""
test_dual_realtime.py

OpenAI Realtime Translate API に 2 セッション並列で接続できるか検証する。
仮説: 経路A 側だけ翻訳が返ってこない真因が「tier 制限で 2 セッション同時不可」かどうかを切り分け。

使い方:
    python tools/test_dual_realtime.py
"""

import asyncio
import json
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config.yaml の API キーを decode して読み込む
import yaml
from config_utils import decode_api_key

try:
    import websockets
except ImportError:
    print("[test] websockets パッケージが必要", flush=True)
    sys.exit(1)


URL = "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate"


async def session(name: str, target_lang: str, api_key: str, duration: float = 12.0) -> dict:
    """1 つの Realtime Translate セッションを張り、session.created / session.updated 等を観測。"""
    result = {
        "name": name,
        "target_lang": target_lang,
        "connected": False,
        "session_created": False,
        "session_updated": False,
        "events": [],
        "error": None,
        "duration": 0.0,
    }

    start = asyncio.get_event_loop().time()
    import hashlib
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": hashlib.sha256(api_key.encode()).hexdigest(),
    }
    print(f"[{name}] connecting (target_lang={target_lang}) ...", flush=True)

    try:
        async with websockets.connect(URL, additional_headers=headers, max_size=None) as ws:
            result["connected"] = True
            print(f"[{name}] connected", flush=True)

            # session.update で target_language を設定
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "audio": {
                        "input": {"transcription": {"model": "gpt-realtime-whisper"}},
                        "output": {"language": target_lang},
                    }
                }
            }))

            deadline = asyncio.get_event_loop().time() + duration
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                try:
                    data = json.loads(msg)
                except Exception:
                    continue
                evt_type = data.get("type", "unknown")
                result["events"].append(evt_type)
                if evt_type == "session.created":
                    result["session_created"] = True
                    print(f"[{name}] session.created", flush=True)
                elif evt_type == "session.updated":
                    result["session_updated"] = True
                    print(f"[{name}] session.updated", flush=True)
                elif evt_type == "error":
                    print(f"[{name}] ERROR event: {data.get('error', {})}", flush=True)
                    result["error"] = data.get("error", {})
                    break
    except Exception as e:
        print(f"[{name}] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        result["error"] = f"{type(e).__name__}: {e}"

    result["duration"] = asyncio.get_event_loop().time() - start
    print(f"[{name}] disconnected (duration={result['duration']:.1f}s, events={len(result['events'])})", flush=True)
    return result


async def main() -> int:
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        print("[test] config.yaml が見つかりません", flush=True)
        return 1
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    api_key = decode_api_key(cfg.get("openai", {}).get("api_key", ""))
    if not api_key:
        print("[test] openai.api_key が空", flush=True)
        return 1
    print(f"[test] api_key length={len(api_key)}", flush=True)
    print(f"[test] === シナリオ1: 2 セッション並列接続 ===", flush=True)

    # 2 セッションを並列に起動 (経路A: ja / 経路B: en)
    results = await asyncio.gather(
        session("route_a", "ja", api_key, duration=10.0),
        session("route_b", "en", api_key, duration=10.0),
    )

    print("\n[test] === 結果 ===", flush=True)
    for r in results:
        print(f"  {r['name']}: connected={r['connected']}, "
              f"session.created={r['session_created']}, "
              f"session.updated={r['session_updated']}, "
              f"event_count={len(r['events'])}, "
              f"error={r['error']}", flush=True)

    print("\n[test] === シナリオ2: 5 秒間隔で順次接続（参考） ===", flush=True)
    r1 = await session("seq_a", "ja", api_key, duration=8.0)
    await asyncio.sleep(2)
    r2 = await session("seq_b", "en", api_key, duration=8.0)
    print(f"  seq_a: connected={r1['connected']}, events={len(r1['events'])}, error={r1['error']}", flush=True)
    print(f"  seq_b: connected={r2['connected']}, events={len(r2['events'])}, error={r2['error']}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
