# PTT GUI ボタン仕様書（issue #155 / #156 / #157）

> **対象 issue**: #155（GUI PTTで音声が文字化されない）、#156（F8押し直後に先頭が途切れる）、#157（固定チェックがONできない：系統A OFF・系統B ON 時）
> **対象ブランチ**: feat/ptt-gui-button
> **策定日**: 2026-05-21（SPECちゃん）
> **改訂日**: 2026-05-21（feat/ptt-gui-button ブランチの現実装を正確に反映）

---

## 概要

GUI PTT ボタン（`TAG_PTT_GUI_BTN`）の動作を整理し、F8 キーボード PTT と完全に同等な扱いとする。
加えて、ラッチ（固定）チェックボックスの正式仕様を定義し、
F8 とラッチの競合・「系統A OFF / 系統B ON 時に固定チェックが ON できない」バグ（#157）を解消する。

### 現ブランチの実装状況サマリー

`feat/ptt-gui-button` ブランチで以下がすでに実装済みであることを確認した（2026-05-21 時点）：

| コールバック | 実装状態 | 備考 |
|---|---|---|
| `_on_ptt_btn_pressed` | 実装済み（issue #154 修正後） | ラッチ解除ロジック付き |
| `_on_ptt_btn_released` | 実装済み | ラッチ中は停止しない制御あり |
| `_on_ptt_latch_changed` | 実装済み（issue #154 修正後） | `start_route('b')` を直接呼び出し |
| `_on_ptt_gui_button_click` | 実装済み | トグル動作 |
| `_update_ptt_visual_feedback` | 実装済み | GUI PTT ボタンのラベル・テーマ更新含む |

本仕様書の目的は：
1. 現実装でまだ残る issue #155 / #156 / #157 のギャップを特定する
2. 確定動作仕様として文書化する（QAちゃんのテスト基準とする）
3. PRGちゃんへ残実装を引き継ぐ

---
## 機能仕様

### G-1: GUI PTT ボタン = F8 の代替デバイス

GUI PTT ボタンのマウスダウン/マウスアップは、F8 ホットキーの押下/離脱と **完全に等価** な操作とする。

**G-1.1 マウスダウン時（`_on_ptt_btn_pressed`）**

1. `_konnyaku_running` が `False` なら no-op
2. `_konnyaku_system` または `route_b_system` が `None` なら no-op
3. ラッチ（`TAG_PTT_LATCH_CHECK`）が ON の場合：ラッチを OFF にして `stop_route('b')` をスレッドで実行し、return
4. Route B が `RUNNING` または `STARTING` でない場合：
   - `resume_from_idle()` を呼ぶ（W-COST-4 アイドル切断からの復帰）
   - `start_route('b')` を **スレッド経由で** 呼ぶ（F-2.1 との一貫性）
5. `_gui_queue.put({"cmd": "update_ptt_visual"})` で GUI を更新

**G-1.2 マウスアップ時（`_on_ptt_btn_released`）**

1. `_konnyaku_running` が `False` なら no-op
2. `_konnyaku_system` または `route_b_system` が `None` なら no-op
3. ラッチ（`TAG_PTT_LATCH_CHECK`）が ON の場合：停止しない（return）
4. Route B が `RUNNING` または `STARTING` の場合：`stop_route('b')` をスレッドで実行
   - **F8 との差異**: F8 は `PttHotkeyManager` 経由で 500ms 離脱デバウンスがかかる。
     GUI ボタンは DearPyGui の `item_handler_registry` からマウスアップを受け取るため、
     デバウンスタイマーを **app.py 側で別途実装する必要がある**。
     → **TBD-G1（監督確認）**: GUI ボタンにも 500ms 離脱デバウンスを実装するか、即時停止で許容するか。
     現実装は「即時 stop_route」。F8 との完全統一には 500ms タイマーが必要。
5. `_gui_queue.put({"cmd": "update_ptt_visual"})` で GUI を更新

**G-1.3 `_ptt_enabled` との独立性（issue #157 の核心）**

`_ptt_enabled` は F8 ホットキー PTT の有効/無効フラグであり、GUI ボタンの動作とは **無関係**。
GUI ボタンコールバック（`_on_ptt_btn_pressed`, `_on_ptt_btn_released`, `_on_ptt_latch_changed`）は
`_ptt_enabled` を参照してはならない。

現実装（2026-05-21 確認）は `_ptt_enabled` を参照していない。仕様として明示的に禁止する。

**G-1.4 スレッドモデル統一**

| 操作 | F8 ホットキー | GUI ボタン |
|---|---|---|
| `start_route('b')` | スレッド経由（`PttStartRouteB`）| スレッド経由（`PttBtnStartRouteB` 推奨名）|
| `stop_route('b')` | スレッド経由（`PttStopRouteB`）| スレッド経由（`PttBtnRelease` 推奨名）|
| ラッチ ON 時の `start_route('b')` | 該当なし | **直接呼び出し**（issue #154 修正。変更禁止）|

ラッチ ON 時のみ直接呼び出しとする理由：
`_on_ptt_latch_changed` → `start_route('b')` → 直後に `_update_ptt_visual_feedback` の順で
RouteState を同期的に参照する必要があるため（`test_ptt_latch.py` の `test_latch_on_then_visual_feedback_does_not_reset` 参照）。

---

### G-2: コールドスタート遅延（issue #156）

**現状の動作確認：**

現実装では `start_route('b')` はスレッド経由で `CaptionSystem.start()` を呼ぶ。
`start()` は WebSocket 接続完了まで 1～2 秒かかる。この間の音声はキャプチャスレッドが
存在しないため **ロスト** する（issue #156 の根本原因）。

**採用方針（既存 ptt-mode-design.md F-3 の更新）：**

`ptt-mode-design.md` は **Case C（仕様割切りアナウンス）を推奨** していたが、
監督からの要件変更により Case C は却下。代替として **Case D: 接続維持 + 音声ゲート** を採用する。

**Case D 詳細仕様：**

- 系統B（Route B）の WebSocket セッションを **常時接続維持** する
- マイク音声の送信は `_audio_gate: bool` フラグで制御する
- `_audio_gate = True` の間のみ `RealtimeTranslator.feed_audio()` が実際に音声を送る
- `_audio_gate = False` の間は音声データを **サイレントに破棄**（`feed_audio` 内部で即 return）

**Case D の実装箇所：**

| ファイル | 追加/変更箇所 | 内容 |
|---|---|---|
| `main.py` | `CaptionSystem.__init__` | `self._audio_gate: bool = False` を追加 |
| `main.py` | `_capture_thread_body` | `feed_audio` 呼び出し前に `if not self._audio_gate: continue` を追加 |
| `main.py` | `RealtimeTranslator.on_connected` コールバック | 接続確立時に `self._audio_gate = True` をセット |
| `main.py` | stop_route('b') 相当の停止処理 | `self._audio_gate = False` をリセット |
| `app.py` | `_on_ptt_btn_pressed` | PTT 押下時に `open_audio_gate()` を呼ぶ（Case D 移行後）|
| `app.py` | `_on_ptt_btn_released` | PTT 離脱時に `close_audio_gate()` を呼ぶ（Case D 移行後）|

**TBD-A → 解消済み（2026-05-21 調査完了）: Case D を正式採用する。**

OpenAI 公式ドキュメント調査の結果：
- "There is no cost currently for network bandwidth or connections" — 接続維持は無料
- gpt-realtime-translate は "billed by audio duration"（音声を送った時間のみ課金）
- `feed_audio()` を呼ばない間は `input_audio_buffer.append` が送られないため課金なし
- PTT 押下中の合計音声時間が同じなら、現行の stop/start 方式と**課金額は同一**

Case D は実装着手可。

---
### G-3: ラッチ（固定）チェックボックスの仕様

#### G-3.1 ラッチ ON 時の動作

チェックボックス（`TAG_PTT_LATCH_CHECK`）を ON にすると：

1. Route B が `IDLE` または `ERROR` の場合：`start_route('b')` を **直接** 呼ぶ（スレッド経由 **不可**）
2. Route B が `STARTING` または `RUNNING` の場合：何もしない（二重起動防止）
3. `_gui_queue.put({"cmd": "update_ptt_visual"})` で GUI を更新

#### G-3.2 ラッチ OFF 時の動作

チェックボックスを OFF にすると：

1. Route B が `RUNNING` または `STARTING` の場合：`stop_route('b')` をスレッドで実行
2. Route B が `IDLE` または `ERROR` の場合：何もしない
3. `_gui_queue.put({"cmd": "update_ptt_visual"})` で GUI を更新

#### G-3.3 ラッチの自動 OFF 条件

`_update_ptt_visual_feedback` は以下の条件でラッチを自動 OFF する：

- Route B の状態が `IDLE` または `ERROR` になったとき（外部要因による停止を反映）
- Route B の状態が `RUNNING` または `STARTING` のときは **自動 OFF しない**

#### G-3.4 ラッチ中の F8 押下（共存ルール）

ラッチが ON の状態で F8 を押しても Route B の状態は変わらない（すでに RUNNING のため no-op）。
F8 を離した場合：

- **修正前（バグ）**: `_on_ptt_release` が `stop_route('b')` を呼び、ラッチが効かなくなる
- **修正後（要対応）**: `_on_ptt_release` の先頭にラッチガードを追加する

これは `_on_ptt_btn_released` にすでにある実装と同じパターン（後述の実装メモ参照）。

---

### G-4: 共存ルール一覧

| 操作 | ラッチ OFF | ラッチ ON |
|---|---|---|
| F8 押下 | Route B を開始（スレッド） | no-op（すでに RUNNING）|
| F8 離脱 | Route B を停止（500ms デバウンス）| **no-op（G-3.4 ガード追加が必要）**|
| GUI ボタン押下 | Route B を開始（スレッド）| ラッチを OFF + Route B を停止 |
| GUI ボタン離脱 | Route B を停止（即時 or デバウンス TBD-G1） | no-op |
| ラッチ ON | Route B を直接開始 | no-op（すでに RUNNING）|
| ラッチ OFF | — | Route B を停止（スレッド） |

---

### G-5: 系統A OFF / 系統B ON 時のラッチ有効化（issue #157）

**ステータス: 修正済み（2026-05-22）**

**根本原因：**

1. issue #154 と同じスレッド競合（`start_route("b")` をスレッド経由で呼んでいたため、visual feedback 時に Route B がまだ IDLE → ラッチ自動 OFF）  
2. Case D 実装後に `open_audio_gate()` の呼び出しが漏れていた（G-1 欠落）

**修正内容（feat/ptt-gui-button ブランチ）：**

- #154 fix: `start_route("b")` を直接呼び出しに変更（RUNNING 確定後に visual feedback）
- G-1 fix: `_on_ptt_latch_changed` ON 時に `open_audio_gate()` を追加（Case D 音声ゲート連携）

**テスト確認：**

```
tests/test_ptt_gui_button.py::TestIssuE157RouteBOnlyLatch  2 passed
```

#### G-4-NOTE: GUI トグルボタン停止分岐の STARTING 状態について

`_on_ptt_gui_button_click` の停止分岐は `state == RouteState.RUNNING` 時のみ `close_audio_gate()` を呼ぶ。
`_on_ptt_btn_released` は `RUNNING | STARTING` 両方で閉じるため非対称。

**意図:** GUI トグルボタンは RUNNING 状態のトグルに特化した操作であり、STARTING 中の停止操作は定義しない。
STARTING 中のユーザー操作は DearPyGui 側で disabled 扱いとなる（実運用での到達ケースは稀）。
将来的に STARTING 中停止が必要になった場合は `(RouteState.RUNNING, RouteState.STARTING)` に拡張すること。

---
## インターフェース定義

### 変更対象の関数

#### `_on_ptt_btn_pressed(sender, app_data, user_data) -> None`

- **DearPyGui コールバック**（`TAG_PTT_BTN_HANDLER` の `mvAll_MouseDownHandler`）
- 動作：G-1.1 参照
- 制約：`_ptt_enabled` を参照しないこと
- **変更点**: `start_route('b')` をスレッド経由に変更（現状は直接呼び出し）

#### `_on_ptt_btn_released(sender, app_data, user_data) -> None`

- **DearPyGui コールバック**（`TAG_PTT_BTN_HANDLER` の `mvAll_MouseReleaseHandler`）
- 動作：G-1.2 参照
- 制約：`_ptt_enabled` を参照しないこと
- **変更点**: TBD-G1 の判断次第でデバウンスタイマー追加

#### `_on_ptt_release(event) -> None`（変更必要）

- 既存の F8 離脱コールバック（`PttHotkeyManager` から呼ばれる）
- **変更箇所**: 先頭にラッチガードを追加（G-3.4 参照）

#### `_on_ptt_latch_changed(sender, app_data, user_data) -> None`（変更禁止）

- **DearPyGui コールバック**（`TAG_PTT_LATCH_CHECK` の `callback`）
- 動作：G-3.1～G-3.2 参照
- **変更禁止**: `start_route('b')` の直接呼び出しは issue #154 修正の核心

### Case D 実装時の追加インターフェース（TBD-A 確認後）

```
# main.py: CaptionSystem に追加予定
# _audio_gate: bool  （False: 音声破棄、True: 音声送信）
# open_audio_gate() -> None  音声ゲートを開く
# close_audio_gate() -> None  音声ゲートを閉じる
```

---

## 結合点（Integration Point）

| メソッド/関数 | 呼び出し元（ファイル:位置） | トリガー条件 |
|---|---|---|
| `_on_ptt_btn_pressed()` | DearPyGui `item_handler_registry` | `TAG_PTT_BTN_HANDLER` の `mvAll_MouseDownHandler`、対象ボタン `TAG_PTT_GUI_BTN` がホバー中にマウスダウン |
| `_on_ptt_btn_released()` | DearPyGui `item_handler_registry` | `TAG_PTT_BTN_HANDLER` の `mvAll_MouseReleaseHandler`、同上 |
| `_on_ptt_latch_changed()` | DearPyGui callback | ウィジェット `TAG_PTT_LATCH_CHECK` のチェック状態変更時 |
| `_on_ptt_release()` | `PttHotkeyManager._fire_release` | F8 キーアップから 500ms タイマー満了後 |
| `_update_ptt_visual_feedback()` | `app._drain_queue()` | `_gui_queue` に `update_ptt_visual` コマンドが積まれたとき（メインスレッドで実行）|
| `CaptionSystem.open_audio_gate()` ※Case D | `app._on_ptt_btn_pressed` / `app._on_ptt_press` | PTT 押下時（TBD-A 確認後に配線）|
| `CaptionSystem.close_audio_gate()` ※Case D | `app._on_ptt_btn_released` / `app._on_ptt_release` | PTT 離脱時（TBD-A 確認後に配線）|

---

## 制約・前提条件

- DearPyGui の `item_handler_registry` コールバックはレンダリングスレッドから呼ばれる。重い処理（WebSocket 接続）は必ずスレッド経由にすること。
- `_on_ptt_latch_changed` における `start_route('b')` の直接呼び出しは変更禁止（issue #154 の修正方針。`test_ptt_latch.py` がリグレッション検出する）。
- Case D の実装前は TBD-A の監督確認を必須とする。
- `_ptt_enabled` は F8 ホットキー PTT の有効/無効を制御するフラグであり、GUI ボタン系のコールバックは参照しないこと。
- `realtime_translator.py` の `feed_audio()` はスレッドセーフである前提で実装する。

---
## 受け入れ条件

### issue #155（GUI PTTで音声が文字化されない）

- [ ] GUI PTT ボタン（`TAG_PTT_GUI_BTN`）を押している間、Route B が `RUNNING` 状態になること
- [ ] `RUNNING` 状態で発話した音声が `RealtimeTranslator.feed_audio()` に渡されること
- [ ] GUI ボタンを離すと Route B が `STOPPING` → `IDLE` に遷移すること
- [ ] `_ptt_enabled = False`（F8 PTT 無効）の状態でも GUI ボタンが機能すること

### issue #156（F8/GUI押し直後に先頭が途切れる）

- [ ] **TBD-A 監督確認後**: Case D 採用の場合、F8 または GUI PTT 押下直後（押下から 100ms 以内）に話した音声が翻訳結果に反映されること
- [ ] **TBD-A が却下の場合**: Case A の仕様書を別途作成すること（本仕様書のスコープ外）

### issue #157（固定チェックがONできない：系統A OFF・系統B ON 時）

- [ ] 系統A が無効・系統B が有効の状態でラッチチェックボックスを ON にできること
- [ ] ON にした後 Route B が `STARTING` → `RUNNING` に遷移すること
- [ ] F8 を押した後ラッチを ON にしても、F8 を離した後も Route B が `RUNNING` を維持すること
- [ ] ラッチ ON 中に GUI ボタンを押すとラッチが解除され Route B が停止すること
- [ ] ラッチ ON 中、Route B が外部要因で `IDLE` になったらラッチが自動 OFF されること

---

## 実装メモ（PRGちゃんへの引き継ぎ）

### 1. ラッチ関連（優先度: 高）

`_on_ptt_release`（F8 離脱）にラッチガードを追加すること（G-3.4）。
数行の追加で済む。`test_ptt_latch.py` にテストケースを追加してから実装すること（TDD）。

追加するガードの概要（`_konnyaku_running` チェックの後に追加）：
- `_dpg_ready` が True かつ `TAG_PTT_LATCH_CHECK` が存在し True の場合
- `update_ptt_visual` を gui_queue に積んで return する

### 2. issue #157 の原因調査（優先度: 高）

`_konnyaku_running` の定義を確認すること。
「系統A または系統B のどちらかが起動中」であれば `True` になるべきだが、
「開始ボタンが押された」フラグであれば系統A OFF / 系統B ON の組み合わせで `False` になりうる。

調査方法：`app.py` で `_konnyaku_running = True` になる箇所を grep し、
系統B のみ有効な場合にも `True` になるパスがあるかを確認すること。

### 3. `_on_ptt_btn_pressed` のスレッド化（優先度: 中）

現実装（2026-05-21 確認）では Route B が非アクティブな場合に `start_route('b')` を直接呼んでいる。
これはレンダリングスレッドをブロックするリスクがある。
`PttBtnStartRouteB` という名前のデーモンスレッドで呼ぶよう修正すること。

ただし、`_on_ptt_latch_changed` の `start_route('b')` 直接呼び出しは **変更禁止**。

### 4. Case D 実装（優先度: TBD-A 確認後）

TBD-A の監督確認なしに Case D を実装しないこと。
Case D を実装する場合、`_capture_thread_body` の変更は `main.py` の大改修になるため、
別 PR で対応することを推奨する。

### 5. 既存テストの維持

`tests/test_ptt_latch.py` の全テストが PASS し続けることを確認しながら実装すること。
特に `TestLatchRaceConditionFix::test_latch_on_then_visual_feedback_does_not_reset` は
issue #154 の核心であり、失敗した場合は実装方針が誤っている。

### 6. `_ptt_gui_release_timer` の要否

TBD-G1（GUI ボタン 500ms 離脱デバウンス）の監督判断待ち。
現実装（即時 stop_route）で issue #155 が解消するなら追加不要。

### 7. 新規テストファイル

`tests/test_ptt_gui_button.py` を新規作成し、以下のケースをカバーすること：

| # | テストケース | 確認内容 |
|---|---|---|
| 1 | `_on_ptt_btn_pressed` 通常押下 | `start_route('b')` がスレッドで呼ばれること |
| 2 | `_on_ptt_btn_pressed` 既に RUNNING | `start_route` が呼ばれないこと |
| 3 | `_on_ptt_btn_pressed` + ラッチ ON | ラッチが OFF になり `stop_route('b')` が呼ばれること |
| 4 | `_on_ptt_btn_released` 通常離脱 | `stop_route('b')` が呼ばれること |
| 5 | `_on_ptt_btn_released` + ラッチ ON | `stop_route` が呼ばれないこと |
| 6 | `_on_ptt_release` + ラッチ ON | `stop_route` が呼ばれないこと（G-3.4 修正確認）|
| 7 | 系統A OFF / 系統B ON でラッチ ON | `start_route('b')` が呼ばれること（#157 修正確認）|
| 8 | Route B が外部停止 + ラッチ自動 OFF | `_update_ptt_visual_feedback` でラッチが外れること |
| 9 | `_ptt_enabled=False` で GUI ボタン押下 | `start_route` が呼ばれること（GUI は F8 フラグ非依存）|

---

## codex 利用
- codex 利用: なし
