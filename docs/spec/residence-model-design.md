# 常駐モデル設計仕様書

> 監督との設計打ち合わせ（2026-05-14）の結論として、現状の「生成・破棄モデル」から「常駐モデル」へ切り替える設計仕様書。

## 概要

`MultiCaptionSystem` をアプリ起動時に即生成して常駐させ、ユーザー操作（開始/停止/デバイス変更/系統チェック）は既存インスタンスへの kick のみとする。

監督の認識:
> 「初期化で2つのtranlateのインスタンスが起動して、UIがそのインスタンスをKICKする。それだけのはず」

## 機能仕様

### 1. 常駐モデルの目的と利点

**現状（生成・破棄モデル）の問題:**
- 「開始」押下のたびに `MultiCaptionSystem()` 新規生成 → `CaptionSystem` × 2 + `RealtimeTranslator` × 2 生成
- 「停止」押下で完全破棄（`_konnyaku_system = None`）
- 再開時にすべて再生成 → PyAudio インスタンス生成競合リスク（`Assertion failed: hostApi->info.defaultOutputDevice` が再現しやすい）
- 個別 route の start/stop 手段がなく、B-14（系統チェック即時反映）・B-15（入力デバイス即時反映）が実装できない

**常駐モデルの利点:**
- `MultiCaptionSystem` を1度生成して生涯保持 → 生成・破棄コストゼロ
- UI イベントはすでに存在するインスタンスへの単純な呼び出しに集約 → 競合・リークの排除
- B-14/B-15 の「即時反映 API」が追加できる構造になる
- アプリ終了時に `terminate()` を1回呼ぶだけでリソースが確実に解放される

## インターフェース定義

### CaptionSystem の状態

```python
from enum import Enum

class RouteState(Enum):
    IDLE     = "idle"       # 未起動（WS 未接続、capture スレッドなし）
    STARTING = "starting"   # start() 呼び出し中（スレッド起動途中）
    RUNNING  = "running"    # 稼働中（WS 接続済み、capture スレッド稼働）
    STOPPING = "stopping"   # stop() 呼び出し中（シャットダウン処理中）
    ERROR    = "error"      # エラー状態
```

**状態遷移:**

```
IDLE ──start()──> STARTING ──接続成功──> RUNNING ──stop()──> STOPPING ──完了──> IDLE
                      │                     │
                  接続失敗               致命エラー
                      ▼                     ▼
                    ERROR                 ERROR
                      │
                  stop()/reset()
                      ▼
                    IDLE
```

- `STARTING` / `STOPPING` 中に `start()` / `stop()` を呼んでも no-op（冪等性保証）
- `ERROR` から `IDLE` への遷移は `reset()` または次回 `start()` 時に自動復帰

### CaptionSystem の API

```python
class CaptionSystem:
    @property
    def state(self) -> RouteState: ...

    def start(self) -> None:
        """
        IDLE / ERROR 状態から RUNNING へ遷移する。
        - _stop_event をリセット
        - asyncio イベントループスレッドを再生成して asyncio.run(self.run()) を実行
        - STARTING / RUNNING 中は no-op
        - API キー未設定の場合は state=ERROR に遷移して on_error を呼ぶ
          （ValueError を __init__ で投げるのではなく、start() 時に遅延チェック）
        """

    def stop(self) -> None:
        """
        RUNNING 状態から IDLE へ遷移する。
        - 現状の shutdown() ロジックをそのまま移植
        - IDLE / STOPPING / ERROR 中は no-op
        - インスタンスは破棄しない（常駐）
        - _stop_event / _loop / _capture_stream 等の内部状態をリセット
        """

    def set_input_device(self, device_info: dict) -> None:
        """
        入力デバイスを変更する。
        - RUNNING 中: stop() → _device_info 更新 → start() で再起動
        - IDLE / ERROR 中: _device_info を更新するのみ
        """

    def terminate(self) -> None:
        """
        アプリ終了時のみ呼ぶ。stop() を呼んだ後、PyAudio terminate 等の
        完全クリーンアップを行う。
        """

    def shutdown(self) -> None:
        """後方互換のために残す。内部的に stop() を呼ぶ thin wrapper。"""
```

### MultiCaptionSystem の API

```python
class MultiCaptionSystem:
    def __init__(self, config, route_a_config, route_b_config, ...) -> None:
        """
        アプリ起動時に1回だけ呼ぶ。
        - 両 CaptionSystem を IDLE 状態で即生成（WS 未接続）
        - 共有 PyAudio インスタンスを生成
        """

    # 新規追加 API（B-14 対応）
    def start_route(self, route_id: str) -> None: ...
    def stop_route(self, route_id: str) -> None: ...

    # 新規追加 API（B-15 対応）
    def set_input_device(self, route_id: str, device_info: dict) -> None: ...

    # 既存 start() / shutdown() の再設計
    def start_all(self) -> None:
        """有効（IDLE）な全系統を起動する。"""

    def stop_all(self) -> None:
        """全系統を停止する（IDLE に遷移）。インスタンスは破棄しない。"""

    def terminate(self) -> None:
        """アプリ終了時のみ呼ぶ。stop_all() + PyAudio terminate。"""

    # 後方互換
    def start(self) -> None: ...  # start_all() の thin wrapper
    def shutdown(self) -> None: ...  # terminate() の thin wrapper
```

### RealtimeTranslator の API

```python
class RealtimeTranslator:
    def connect(self, loop: asyncio.AbstractEventLoop) -> None:
        """現状の start(loop) と同じ。リネーム + 再接続可能化。"""

    def disconnect(self) -> None:
        """
        現状の stop() と同じ。
        次回 connect() が呼べるように内部状態をリセット。
        """

    def feed_audio(self, pcm16_bytes: bytes) -> None:
        """変更なし。IDLE 状態なら no-op。"""

    # 後方互換
    def start(self, loop) -> None: ...  # connect(loop) の thin wrapper
    def stop(self) -> None: ...  # disconnect() の thin wrapper
```

## 制約・前提条件

- **共有 PyAudio インスタンスは MultiCaptionSystem のライフサイクルに束縛**。アプリ起動から終了まで1つの `pyaudio.PyAudio()` が存在し続ける（現状と同じ）
- **`CaptionSystem.stop()` は内部スレッドを join してから返らなければならない**
- **`RouteConfig` の変更時（入力デバイス変更）は `CaptionSystem` を再生成せず、`_device_info` だけ更新してから restart**
- 翻訳モードは `openai-realtime` のみが対象（Whisper + DeepL モードは別途）
- `SubtitleBroadcaster`（WebSocket サーバー）は現状通り route_a が所有、アプリ起動から終了まで稼働し続ける

## 受け入れ条件

- [ ] アプリ起動後、ユーザー操作前の時点で `_konnyaku_system` が非 None
- [ ] 「開始」押下時に新しい `MultiCaptionSystem` が生成されない（既存の `start_all()` が呼ばれる）
- [ ] 「停止」押下時に `_konnyaku_system = None` にならない（`stop_all()` が呼ばれる）
- [ ] 停止後に再度「開始」を押しても正常稼働
- [ ] 系統チェック稼働中 OFF で対象系統の capture スレッドが停止（B-14）
- [ ] 系統チェック稼働中 ON で対象系統が新規起動（B-14）
- [ ] 稼働中の入力デバイス変更で新デバイス再起動（B-15）
- [ ] API キー未設定時、クラッシュではなく ERROR 状態 + ステータスバーにエラー表示
- [ ] アプリ終了時に `terminate()` が呼ばれ PyAudio が正常解放
- [ ] 既存テスト 300 件のうち、生成・破棄前提テストが新 API に対応

## 段階的実装計画

### PR-1（基盤: 状態管理の追加）
- `RouteState` enum を `main.py` に追加
- `CaptionSystem` に `state: RouteState` プロパティ追加
- `shutdown()` を `stop()` の thin wrapper にする
- `CaptionSystem.stop()` 完了後の内部状態リセット
- 既存テスト全パス確認

### PR-2（CaptionSystem の start() 追加）
- `CaptionSystem.start()` 新規追加（IDLE → STARTING → RUNNING）
- API キーチェックを `__init__` から `start()` に移動（`ValueError` を投げず `state = ERROR`）
- `test_multi_caption_system.py` の `test_multi_caption_system_can_be_instantiated` が API キー未設定でも通る

### PR-3（MultiCaptionSystem の start_route / stop_route 追加）
- `start_route(route_id)` / `stop_route(route_id)` 追加
- `start_all()` / `stop_all()` 追加（thin wrapper）
- `terminate()` 追加（PyAudio terminate 含む）
- app.py は変更しない

### PR-4（app.py を常駐モデルに切り替え）
- `main()` で `MultiCaptionSystem` 即生成
- `_on_konnyaku_start_stop_click` を `start_all()` / `stop_all()` に
- `_on_route_a_enable_change` callback で `start_route("a")` / `stop_route("a")`（B-14）
- `_on_route_a_device_change` callback 新規追加で `set_input_device("a", info)`（B-15）
- 終了フックで `terminate()`

### PR-5（RealtimeTranslator の connect/disconnect 整理）
- `connect(loop)` / `disconnect()` 追加（thin wrapper 化）
- `disconnect()` 後の内部状態リセットで再 `connect()` 可能
- 再接続テスト追加

## 既存テストへの影響範囲

**影響なし:**
- `TestMultiCaptionSystemShutdown` / `TestSafeShutdownOrdering` / `TestCaptureThreadJoinBeforeTerminate` / `TestCaptureStreamStopOnShutdown`
  - thin wrapper にした後も `shutdown()` → `terminate()` で動作

**修正必要:**
- `TestMultiCaptionSystemInstantiation.test_multi_caption_system_can_be_instantiated`: PR-2 で API キーチェックを `__init__` から除去 → 自動的に通る

**新規テスト:**
- `TestRouteState`: `state` プロパティ遷移
- `TestStartStopIdempotency`: 多重呼び出しが no-op
- `TestStartRouteStopRoute`: 個別 route の start/stop
- `TestSetInputDeviceRestart`: 稼働中の `set_input_device()` 後の restart

## WebSocket 再接続の仕様

既存の `RealtimeTranslator._connect_loop_async()` は再接続ループを持つ（指数バックオフ、最大 5 回再試行）。常駐モデルでの追加変更点:

- `disconnect()` を呼ぶと `_stop_event.set()` でループ即抜け（現状同じ）
- `disconnect()` 後に `_stop_event` / `_task` / `_future` / `_audio_queue` を `None` にリセットして再 `connect()` 可能に
- 再接続 flaky テスト（`test_reconnect_on_disconnect` 等）は asyncio のタイミング依存が高い。`asyncio.sleep` を mock 化してテスト

## リスク・懸念点

1. **`_stop_event` / `_loop` のリセット漏れ**
   - `stop()` 完了後にリセットしないと、次回 `start()` が即 IDLE に落ちる
   - `stop()` の末尾に明示的なリセット処理ブロックを設ける

2. **PyAudio 共有インスタンスと stop/start の競合**
   - `_pa` は `terminate()` まで生き続ける
   - `stop()` → `start()` の間に capture スレッドが完全終了していないと `pa.open()` 競合
   - `stop()` 内で capture スレッド join（timeout 5s）を厳守

3. **WebSocket サーバー（SubtitleBroadcaster）の扱い**
   - `stop_all()` を呼んでも WebSocket サーバーは落とさない
   - overlay.html のブラウザ接続を保持

4. **`--auto-konnyaku` フラグとの整合**
   - 新設計では `_konnyaku_system` が起動時から存在
   - `--auto-konnyaku` の「5秒待機 → 開始」フローは `start_all()` の呼び出しに置き換え

5. **`app.py` での `_konnyaku_system` 初期化タイミング**
   - `main()` の `_build_gui` より前に `MultiCaptionSystem()` を生成
   - callback はクロージャ経由で遅延バインド or setter プロパティで後設定

## 関連

- 仕様書: `docs/spec/ui-spec-konnyaku.md` (UI 仕様、B-14/B-15 を含む)
- Issue #57: Rust 化検討（将来）
