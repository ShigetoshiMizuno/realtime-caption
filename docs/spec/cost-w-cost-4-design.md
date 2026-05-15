# W-COST-4 設計仕様書 ― アイドル時セッション自動切断

> Issue #81 W-COST-4 / 対象 master: fc3af2d（W-COST-1/2/3 マージ済み）
> 策定日: 2026-05-16 by SPECちゃん

---

## 概要

アプリ起動中に長時間音声入力がない（アイドル状態）場合、OpenAI Realtime Translate API のセッションを自動切断して課金を停止する。
音声入力を再検知したとき、または手動操作によって自動再接続する。

---

## 1. W-COST-3 VAD との関係整理

### 1.1 階層比較表

| 機能 | 対象 | タイムスケール | 課金削減対象 |
|---|---|---|---|
| W-COST-3 VAD | 無音区間の翻訳処理（input トークン）を抑制 | 秒オーダー（200-2000ms） | input トークン課金の 30-50% 削減 |
| W-COST-4 アイドル切断 | 長時間無入力時にセッション全体を切断 | 分オーダー（5 分以上） | セッション保持コスト全体を 0 にする |

### 1.2 両者の共存関係

VAD（秒オーダー）と W-COST-4 アイドル切断（分オーダー）は独立した階層として動作し、互いを排除しない。

- VAD ON + W-COST-4 ON: 発話中は VAD が調整、5 分以上無発話で W-COST-4 がセッション切断、再接続後は VAD が再び有効
- VAD OFF + W-COST-4 ON: 5 分以上無入力で W-COST-4 が切断（VAD による input トークン削減なし）
- 重複判定なし: VAD は発話終了（秒）を検知、W-COST-4 は長時間無発話（分）を検知
- アイドルタイマーは再接続完了時にリセット

### 1.3 W-COST-1/2/3/4 の整合性まとめ

| Issue | フラグ | 制御対象 | 状態 |
|---|---|---|---|
| W-COST-1 | request_audio_output | audio.output の有無 | 実装済み |
| W-COST-2 | request_source_transcript | audio.input の有無 | 実装済み |
| W-COST-3 | vad_enabled | audio.input.turn_detection の有無 | 実装済み |
| W-COST-4 | idle_disconnect_enabled | セッション自動切断 | 本件 |

---
## 2. アイドル判定の定義

### 2.1 案の比較

| 案 | 判定方法 | 推奨度 |
|---|---|---|
| 案 A: PCM RMS（音量閾値） | _capture_thread_body で chunk ごとに計算済みの level_window_max（1 秒間ピーク、int16 絶対値 max）を監視。N 分間 level_window_max < IDLE_AUDIO_THRESHOLD なら idle 判定。chunk_max は既存コードで計算済みのため追加コスト最小 | 最推奨 |
| 案 B: audio_queue 投入監視 | _realtime_translator.feed_audio() の呼び出しがない状態を監視。RealtimeTranslator 側への hook 追加が必要 | 非推奨（案 A に包含） |
| 案 C: 翻訳結果（_on_transcript）監視 | _on_realtime_transcript が呼ばれない時間を監視。VAD OFF 時は無音でも翻訳が来ないため false positive 大 | 非推奨 |

### 2.2 推奨: 案 A（PCM RMS 監視）を採用

1. _capture_thread_body の 1267 行目付近で level_window_max（1 秒間のピーク値）がすでに計算されており、追加コストが最小
2. VAD の ON/OFF に依存しない（VAD は API 側の処理であり、クライアント側の音量判定は独立）
3. 入力デバイスから音声データが来ているかどうかを直接監視できる
4. ループバック音声（VB-CABLE 経由 Zoom 音声）でも適切に機能する

### 2.3 アイドル判定の具体的定義

    アイドル状態 = level_window_max（1 秒間ピーク）が
                  IDLE_AUDIO_THRESHOLD 未満の秒が IDLE_TIMEOUT_SEC 秒以上継続した状態

- IDLE_AUDIO_THRESHOLD: int 型、デフォルト 200（int16 絶対値 max、0-32767）
  - 0 dBFS = 32767 に対して、200 は約 -44 dBFS 相当
  - VB-CABLE のノイズフロアが通常 50-100 程度であることを考慮してマージンを設定
  - TBD-4-2: VB-CABLE 経由 Zoom 音声での適切な閾値は実機確認が必要
- IDLE_TIMEOUT_SEC: float 型、デフォルト 300.0（5 分）
  - TBD-4-1: 監督の会議翻訳ユースケースで 5 分が適切か確認が必要

---
## 3. アイドル時の動作フロー

### 3.1 切断フロー

    [RUNNING 状態]
      -> idle タイマーが IDLE_TIMEOUT_SEC を超過
      -> 1. ステータスバー: 「アイドル切断中（音が入ると自動再開）」を表示（_gui_queue 経由）
      -> 2. 課金ランプは IDLE 遷移後に自動で緑（none）になる（既存 _get_billing_state() で処理）
      -> 3. stop_route のみ呼ぶ（start_route は呼ばない）
      -> 4. _idle_disconnected フラグを True にセット
      -> 5. 系統は IDLE 状態に遷移（stop_route の既存動作）

### 3.2 再接続トリガー

| トリガー | 説明 |
|---|---|
| 入力レベル上昇（自動） | アイドル切断中に level_window_max >= IDLE_RECONNECT_THRESHOLD になったとき自動再接続 |
| 手動「再開」ボタン | UI に「再開」ボタンを表示し、クリックで再接続（TBD-4-3） |
| PTT 押下（PTT モード時） | PTT モード時は PTT 押下でトリガー（TBD-4-3） |

- IDLE_RECONNECT_THRESHOLD: int 型、デフォルト 300
  - ヒステリシスによるチャタリング防止（切断閾値 200 < 再接続閾値 300）

### 3.3 再接続フロー

    [_idle_disconnected = True 状態]
      -> 再接続トリガーを検知
      -> 1. _idle_disconnected を False にリセット
      -> 2. アイドルタイマーをリセット
      -> 3. IDLE_RECONNECT_COOLDOWN_SEC（デフォルト 10.0 秒）のクールダウン確認
      -> 4. start_route を呼ぶ（バックグラウンドスレッド）
      -> 5. ステータスバー: 「再接続中...」を表示
      -> 6. on_connected コールバック後、ステータスバーをクリア

- IDLE_RECONNECT_COOLDOWN_SEC: float 型、デフォルト 10.0 秒
  - 切断 -> 再接続 -> すぐ無音 -> 再切断 ... のループを防ぐ

---
## 4. パラメータ仕様

### 4.1 設定値一覧

| パラメータ名 | 型 | デフォルト | 単位 | 説明 |
|---|---|---|---|---|
| idle_disconnect_enabled | bool | False | — | アイドル切断機能の有効/無効。デフォルト OFF（既存挙動維持） |
| idle_timeout_sec | float | 300.0 | 秒 | アイドル判定タイムアウト（5 分）。TBD-4-1 |
| idle_audio_threshold | int | 200 | int16 絶対値 max | 無音とみなす音量上限。TBD-4-2 |
| idle_reconnect_threshold | int | 300 | int16 絶対値 max | 自動再接続をトリガーする音量下限（ヒステリシス） |
| idle_reconnect_cooldown_sec | float | 10.0 | 秒 | 再接続後の次回切断までの最短間隔 |

### 4.2 RouteConfig への追加フィールド（main.py）

    @dataclass
    class RouteConfig:
        # ... 既存フィールド省略 ...
        # W-COST-4 追加（デフォルト値付き、後方互換）
        idle_disconnect_enabled: bool = False
        idle_timeout_sec: float = 300.0
        idle_audio_threshold: int = 200
        idle_reconnect_threshold: int = 300
        idle_reconnect_cooldown_sec: float = 10.0

---
## インターフェース定義

### IdleDisconnectMonitor クラス（新規、cost_monitor.py に追記）

    class IdleDisconnectMonitor:
        def __init__(
            self,
            idle_timeout_sec: float = 300.0,
            idle_audio_threshold: int = 200,
            idle_reconnect_threshold: int = 300,
            on_idle_timeout: Callable[[], None] | None = None,
            on_activity_detected: Callable[[], None] | None = None,
            _timeout_override: float | None = None,   # テスト用 DI
        ) -> None: ...

        def report_audio_level(self, peak: int) -> None:
            # capture スレッドから 1 秒ごとに呼ぶ。peak は int16 絶対値 max
            ...

        def set_disconnected(self, disconnected: bool) -> None:
            # アイドル切断状態フラグをセット。切断後は on_activity_detected が有効
            ...

        def reset_idle_timer(self) -> None:
            # アイドルタイマーをリセット（再接続完了時に呼ぶ）
            ...

        def stop(self) -> None:
            # 監視を停止する（CaptionSystem.stop() から呼ぶ）
            ...

### CaptionSystem への追加パラメータ（main.py）

    def __init__(
        self,
        # ... 既存パラメータ省略 ...
        # W-COST-4 追加
        idle_disconnect_enabled: bool = False,
        idle_timeout_sec: float = 300.0,
        idle_audio_threshold: int = 200,
        idle_reconnect_threshold: int = 300,
        idle_reconnect_cooldown_sec: float = 10.0,
        _idle_timeout_override: float | None = None,   # テスト用 DI
        on_idle_timeout: Callable[[], None] | None = None,
        on_activity_detected: Callable[[], None] | None = None,
    ) -> None: ...

_capture_thread_body の 1 秒ログ処理（level_window_max を _update_audio_stats に渡す箇所の直後）に追加:

    if self._idle_disconnect_monitor is not None:
        self._idle_disconnect_monitor.report_audio_level(level_window_max)

### app.py のアイドル切断コールバック

    def _on_route_idle_timeout(route_id: str) -> None:
        # アイドルタイムアウト到達時。stop_route のみ呼ぶ。GUI 更新は _gui_queue 経由
        # _restart_locks を acquire(blocking=False) で取得して実行
        ...

    def _on_route_activity_detected(route_id: str) -> None:
        # アイドル切断後に入力レベル上昇を検知。クールダウン確認後 start_route を呼ぶ
        # _restart_locks を acquire(blocking=False) で取得して実行
        ...

### settings.json 保存キー（TBD-4-4 次第）

- route_a.idle_disconnect_enabled: bool
- route_a.idle_timeout_sec: float（TBD-4-4: UI 変更可能にするか config.yaml 直接編集のみか）
- route_a.idle_audio_threshold: int
- route_b.*（route_b も同様）

---
## 機能仕様

1. idle_disconnect_enabled=True のとき、_capture_thread_body の 1 秒ピーク（level_window_max）を IdleDisconnectMonitor.report_audio_level() に渡す
2. level_window_max < idle_audio_threshold の秒数が idle_timeout_sec を超えた場合、on_idle_timeout コールバックを一度だけ発火する（idempotent）
3. コールバック受信後、app.py は stop_route(route_id) のみ呼ぶ（start_route は呼ばない）
4. アイドル切断状態（_idle_disconnected=True）で level_window_max >= idle_reconnect_threshold になった場合、on_activity_detected コールバックを発火する
5. on_activity_detected 後、クールダウン期間中は同コールバックを抑制する
6. app.py は on_activity_detected 受信後 start_route(route_id) をバックグラウンドスレッドで実行する
7. 再接続完了（on_connected）時に IdleDisconnectMonitor.reset_idle_timer() を呼びアイドルタイマーをリセットする
8. idle_disconnect_enabled=False のとき、上記の処理はすべてスキップされる（既存挙動維持）

---

## 制約・前提条件

- 対象モード: openai-realtime のみ。Whisper + DeepL モードは対象外（CaptionSystem._realtime_mode=True の場合のみ有効）
- 既存 CostMonitor との関係: CostMonitor（最大稼働時間監視）は独立して維持する。アイドル切断後も CostMonitor はリセットしない（稼働時間の累積カウントは継続）
- 系統ごと独立: route_a と route_b は独立してアイドル判定・切断・再接続を行う
- _restart_locks との統合: アイドル切断トリガーも _restart_locks[route_id] を acquire(blocking=False) で取得してから実行する。連打防御は既存ロジックを流用する
- デフォルト OFF: idle_disconnect_enabled のデフォルト値は False（既存挙動を変えない・安全側）
- テストのサンプル値: API キーには必ずフェイク値（sk-test-fake-idle001 形式）を使用すること。実 API キーはコードに絶対に含めない

---
## 受け入れ条件

- [ ] idle_disconnect_enabled=False のとき、アイドルタイマーが動作せず切断が発生しない（既存挙動維持）
- [ ] idle_disconnect_enabled=True のとき、音量が idle_audio_threshold 未満の状態が idle_timeout_sec 秒続くと route が IDLE 状態に遷移する（_timeout_override DI でテスト）
- [ ] アイドル切断後に音量が idle_reconnect_threshold 以上になると on_activity_detected が発火し route が STARTING -> RUNNING に遷移する
- [ ] アイドル切断後のステータスバーに「アイドル切断中」相当の文字列が表示される
- [ ] アイドル切断後の課金ランプが緑（none）になる（既存 _get_billing_state() で IDLE 状態が none になることを確認）
- [ ] idle_reconnect_cooldown_sec 以内の連続再接続トリガーが無視される
- [ ] CostMonitor の稼働時間カウントがアイドル切断後もリセットされない（累積維持）
- [ ] RouteConfig.idle_disconnect_enabled のデフォルト値が False である
- [ ] IdleDisconnectMonitor.stop() が CaptionSystem.stop() 時に呼ばれる（モニタースレッドが終了する）
- [ ] 既存テスト（全 PASS）が W-COST-4 実装後も維持される
- [ ] 系統 A/B が独立してアイドル判定される（片方の切断がもう片方に影響しない）

---
## TBD 一覧（監督判断事項）

| TBD | 内容 | 影響 |
|---|---|---|
| TBD-4-1 | アイドルタイムアウト 5 分（300 秒）は監督の会議翻訳ユースケースで妥当か。発話間が 5 分以上空くケースはあるか | idle_timeout_sec のデフォルト値および UI での変更可否 |
| TBD-4-2 | VB-CABLE 経由 Zoom 音声でのノイズフロア実測値。idle_audio_threshold=200 が適切か | 閾値が低いと Zoom の BGM/環境音でアイドルにならない。高いと小声を無音と誤判定 |
| TBD-4-3 | 「再開」ボタンを UI に追加するか、音量上昇のみで再接続するか。PTT モード時の特別扱い（PTT 解放からタイマー計測）が必要か | app.py の UI 実装量に影響 |
| TBD-4-4 | アイドル設定（timeout/threshold）を UI で変更可能にするか、config.yaml 直接編集のみか | PR3 の実装量に影響 |
| TBD-4-5 | 実装優先度: 今すぐ実装するか（完全実装）、仕様確定後に実装するか（保留）。TBD-4-1 から 4-4 の回答次第で PR 分割も変わる | プロジェクト全体のスケジュールに影響 |

---
## PR 分割案

### PR1: IdleDisconnectMonitor クラスの新規作成（cost_monitor.py）

変更ファイル: cost_monitor.py

変更内容:
- IdleDisconnectMonitor クラスを新規追加（既存 CostMonitor の直後に配置）
- report_audio_level() / set_disconnected() / reset_idle_timer() / stop() を実装
- 1 秒ポーリングループ（threading.Event.wait(timeout=1.0)）でタイマー管理
- クールダウン（idle_reconnect_cooldown_sec）の管理
- テスト用 _timeout_override DI パラメータ

新規テスト (tests/test_idle_disconnect_monitor.py):
- test_idle_timeout_fires_after_threshold: 音量が閾値以下の状態が継続するとコールバックが呼ばれる（_timeout_override 使用）
- test_idle_timeout_resets_on_loud_audio: 音量が閾値を超えるとタイマーがリセットされる
- test_no_callback_when_not_disconnected: set_disconnected(False) 状態で音量上昇しても on_activity_detected が呼ばれない
- test_activity_detected_fires_after_disconnect: set_disconnected(True) 後に音量上昇でコールバックが呼ばれる
- test_cooldown_prevents_immediate_reconnect: クールダウン中は on_activity_detected が呼ばれない
- test_stop_terminates_thread: stop() でポーリングスレッドが終了する

影響範囲: cost_monitor.py のみ

---

### PR2: CaptionSystem への IdleDisconnectMonitor 組み込み（main.py）

変更ファイル: main.py

変更内容:
- RouteConfig に W-COST-4 フィールド 5 つを追加（デフォルト値付き、後方互換）
- CaptionSystem.__init__ に W-COST-4 パラメータを追加
- CaptionSystem.start() で idle_disconnect_enabled=True のとき IdleDisconnectMonitor を生成・開始
- CaptionSystem.stop() で IdleDisconnectMonitor.stop() を呼ぶ（None チェック付き）
- _capture_thread_body の 1 秒ログ処理で report_audio_level(level_window_max) を呼ぶ
- _on_idle_timeout_internal() / _on_activity_detected_internal() を追加し、外部コールバックに伝達

新規テスト (tests/test_main_idle_disconnect.py):
- test_idle_monitor_created_when_enabled
- test_idle_monitor_not_created_when_disabled
- test_idle_monitor_stopped_on_stop
- test_on_idle_timeout_callback_called

影響範囲: main.py のみ

---

### PR3: app.py ― アイドル切断 UI 結線・設定保存（TBD-4-3/4-4 次第）

変更ファイル: app.py

変更内容:
- _create_konnyaku_system() で settings.json から idle 設定を読み込み RouteConfig に反映
- _save_settings() で idle 設定を保存
- _on_route_a_idle_timeout() / _on_route_b_idle_timeout() を実装（stop_route のみ + GUI 更新）
- _on_route_a_activity_detected() / _on_route_b_activity_detected() を実装（start_route + GUI 更新）
- ステータスバーの「アイドル切断中」表示（_gui_queue 経由）
- TBD-4-3: 「再開」ボタンの要否は監督判断

新規テスト (tests/test_app_idle_disconnect.py):
- test_idle_timeout_calls_stop_route
- test_activity_detected_calls_start_route
- test_billing_lamp_none_after_idle_disconnect

影響範囲: app.py のみ

---
## UX 仕様

### ステータスバー表示

| 状態 | TAG_STATUS_STATE 表示 | 課金ランプ |
|---|---|---|
| RUNNING（アイドルタイマー作動中） | 通常表示（変化なし） | 既存通り（黄/赤） |
| IDLE（アイドル切断完了） | 「アイドル切断中 ― 音が入ると自動再開」 | 緑（none） |
| STARTING（再接続中） | 「再接続中...」 | 黄（single）/ 赤（both） |

### 課金ランプとの連動

- _get_billing_state() は STARTING/RUNNING を「課金中」とみなす（既存ロジック）
- アイドル切断後は IDLE 状態になるため、既存ロジックで自動的に緑（none）になる
- _get_billing_state() の変更は不要

### PTT モードとの関係

- PTT モード時は PTT 解放後に無音になることが多く、IDLE_AUDIO_THRESHOLD=200 では誤アイドル判定のリスクがある
- PTT モード時の特別扱い（「PTT 最後解放時刻からタイマー計測」）が必要か TBD-4-3 で確認

---

## 実装メモ（PRGちゃんへの引き継ぎ）

### 最重要: TBD 確認待ち

TBD-4-1 から 4-5 の監督判断が出るまで実装を開始しないこと。
特に TBD-4-1（5 分タイムアウトの妥当性）と TBD-4-5（実装優先度）は PR 分割と実装の根幹に関わる。

### IdleDisconnectMonitor の設計ポイント

- タイマー管理は threading.Event の wait(timeout=1.0) ループで実装すること（CostMonitor._watcher_loop と同パターン）
- report_audio_level() は _capture_thread_body から呼ばれるためロックなしで呼べるように設計すること（atomic な _idle_seconds カウンタ更新のみ）
- アイドル状態フラグ（_is_disconnected）は threading.Lock で保護すること
- コールバック内で blocking 処理（stop_route/start_route）を行わないこと。app.py 側でバックグラウンドスレッドに委ねること

### アイドル切断と _restart_locks の統合

- _on_route_a_idle_timeout は stop_route のみを呼ぶ（_restart_route_for_change は stop + start の両方を呼ぶため使わない）
- _on_route_a_activity_detected は start_route のみを呼ぶ
- どちらも _restart_locks を acquire(blocking=False) で取得し、取得できなかった場合はスキップすること
- 専用ヘルパー _idle_stop_route(route_id) / _idle_start_route(route_id) として切り出すことを推奨

### PR1-3 の依存関係

- PR1 は独立（cost_monitor.py のみ、他に依存なし）
- PR2 は PR1 に依存（IdleDisconnectMonitor を import して使う）
- PR3 は PR2 に依存（CaptionSystem のコールバック経由で通知を受ける）

### テスト用 DI パラメータの使い方

長時間稼働テストを回避するため _timeout_override: float | None = None を設ける。
テストでは _timeout_override=0.1 等の短い値を渡し、0.1 秒後にタイムアウトが発火することを確認する。
本番では _timeout_override=None のとき idle_timeout_sec が使われる。

### 既存 _BILLING_LAMP_COLORS との整合

アイドル切断後は route が IDLE 状態になるため、既存の _get_billing_state() が自動的に none を返す。
課金ランプの更新ロジックは変更不要。

### W-COST-3 VAD との共存（実装上の注意）

VAD が ON のとき、API 側で発話検知するが、クライアント側では引き続き音声チャンクを送り続ける。
W-COST-4 のアイドル判定はクライアント側の level_window_max を見るため、VAD の ON/OFF に関係なく動作する。
これは仕様上正しい（VAD はサーバー側の「翻訳処理」抑制、W-COST-4 はクライアント側の「音声入力有無」判定）。

### テストのフェイク API キー形式

追加するテストの api_key は sk-test-fake-idle001 等の形式で統一。
.gitleaks.toml allowlist パターン sk-test-fake-[A-Za-z0-9]+ に準拠（ハイフン無しサフィックス）。

---

## codex 利用
- codex 利用: なし