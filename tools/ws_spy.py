"""
ws_spy.py

overlay.html と等価な WebSocket クライアント。
AI 自動デバッグで字幕配信を検証するため、サーバーから受信したメッセージを
タイムスタンプ付きで stdout / ファイルに記録する。

使い方:
    python tools/ws_spy.py [URL] [--out FILE] [--duration SEC] [--connect-timeout SEC]

例:
    python tools/ws_spy.py ws://localhost:8765 --out ws_spy.log --duration 30
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

try:
    import websockets
except ImportError:
    print("[ws_spy] websockets パッケージが必要", flush=True)
    sys.exit(1)


async def _spy(url: str, output: Path, duration: float, connect_timeout: float) -> int:
    log = []

    def write_line(line: str) -> None:
        print(line, end="" if line.endswith("\n") else "\n", flush=True)
        log.append(line if line.endswith("\n") else line + "\n")

    write_line(f"[ws_spy] connecting to {url} (timeout={connect_timeout}s)")

    deadline = asyncio.get_event_loop().time() + duration if duration > 0 else None
    msg_count = 0

    while True:
        try:
            ws = await asyncio.wait_for(websockets.connect(url), timeout=connect_timeout)
            break
        except (OSError, asyncio.TimeoutError) as e:
            now = asyncio.get_event_loop().time()
            if deadline is not None and now >= deadline:
                write_line(f"[ws_spy] connect deadline reached: {e}")
                output.write_text("".join(log), encoding="utf-8")
                return 2
            write_line(f"[ws_spy] connect failed ({e}); retry in 1s")
            await asyncio.sleep(1.0)

    try:
        write_line(f"[ws_spy] connected at {datetime.now().isoformat()}")
        while True:
            now = asyncio.get_event_loop().time()
            remaining = (deadline - now) if deadline is not None else None
            if remaining is not None and remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(
                    ws.recv(),
                    timeout=remaining if remaining is not None else None,
                )
            except asyncio.TimeoutError:
                break
            except websockets.exceptions.ConnectionClosed:
                write_line(f"[ws_spy] connection closed by server")
                break
            ts = datetime.now().isoformat(timespec="milliseconds")
            write_line(f"[{ts}] {msg}")
            msg_count += 1
    finally:
        try:
            await ws.close()
        except Exception:
            pass

    write_line(f"[ws_spy] disconnected, received {msg_count} messages")
    output.write_text("".join(log), encoding="utf-8")
    return 0 if msg_count > 0 else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="overlay.html 等価 WebSocket クライアント")
    parser.add_argument("url", nargs="?", default="ws://localhost:8765",
                        help="WebSocket サーバー URL")
    parser.add_argument("--out", default="ws_spy.log", help="ログファイルパス")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="計測時間（秒、0 で無制限）")
    parser.add_argument("--connect-timeout", type=float, default=15.0,
                        help="接続試行タイムアウト（秒）")
    args = parser.parse_args()

    output = Path(args.out)
    return asyncio.run(_spy(args.url, output, args.duration, args.connect_timeout))


if __name__ == "__main__":
    sys.exit(main())
