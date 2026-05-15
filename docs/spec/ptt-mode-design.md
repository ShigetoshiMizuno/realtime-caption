# Push-to-Talk (PTT) モード設計仕様書

> **issue #82** に対応する詳細仕様。
> 系統B（マイク → 英語翻訳）を **ホットキー押下中だけ起動** する PTT モードを導入し、
> 未使用時の OpenAI Realtime API コストを削減する。

## 改訂履歴

- **2026-05-15 (v1)**: SPECちゃん初版策定。

---

## 概要

監督の発話タイミングに合わせてのみ系統Bを稼働させる「Push-to-Talk」モードを追加する。
PTT モード有効時、系統Bの動作は「チェックボックスによる常時 ON/OFF」から
「グローバルホットキー押下中のみ ON」に切り替わる。

**想定コスト削減率**: 92%（1 時間の会議で発話時間が約 5 分 = 8% の場合）

---

## F-1: ホットキー仕様

### F-1.1 既定キー候補と推奨

| 候補 | 理由 | 問題点 |
|------|------|--------|
| **F8（推奨）** | F1〜F4 は Zoom 既定（ミュート・ビデオ等）と競合。F5〜F7 はブラウザ更新等で汎用性高い。F8 は空きが多い | 一部アプリで F8 に機能割り当てあり |
| F9 | F8 同様に空きが多い | Microsoft Word が F9 を「フィールド更新」に使用 |
| F10 | Zoom の PTT と同じキー（Zoom 標準）| Zoom アプリ側の PTT と干渉する可能性。Zoom 使用中は要確認 |

**推奨: F8**
Zoom 標準の PTT は F10 だが、本アプリと Zoom を同時使用するユースケースでは F10 は避けた方が安全。
F8 は主要 DAW・Zoom・ブラウザとの競合が最小。

### F-1.2 設定変更

- settings.json の route_b.ptt_hotkey キーでキーを設定可能とする
- デフォルト値: f8
- 対応フォーマット: keyboard ライブラリの key name 文字列（例: f8, f9, ctrl+shift+t）

### F-1.3 グローバルホットキー対応

システム全体に有効な（フォーカスを問わない）ホットキーが必要。
Zoom や OBS がフォアグラウンドにある状態でも本アプリが押下イベントを受け取る。

**ライブラリ選定: keyboard を推奨**（詳細は付録A参照）

### F-1.4 既存 requirements との整合

requirements.txt に keyboard は未記載。新規追加が必要。keyboard>=0.13.5 を追加する（PyPI 最新安定版）。

---

## F-2: 押下・離脱の動作

### F-2.1 ホットキー押下時（key down イベント）

1. PTT モードが無効（route_b.ptt_enabled = false）なら no-op
2. _konnyaku_running が False（アプリ停止中）なら no-op
3. 系統Bの RouteState が RUNNING ならすでに稼働中 → no-op（押しっぱなし repeat 対策）
4. デバウンス判定: 直前の押下から 200ms 未満なら no-op（F-4 参照）
5. _konnyaku_system.start_route(b) を呼ぶ
6. GUI に「PTT 押下中」フィードバックを表示（F-6 参照）
7. ログ: [PTT] 系統B 押下: start_route(b) 呼び出し

### F-2.2 ホットキー離脱時（key up イベント）

1. PTT モードが無効なら no-op
2. _konnyaku_running が False なら no-op
3. 離脱デバウンス待機: 500ms 後に stop_route を実行（タイマースレッドで遅延）
4. 500ms 以内に再押下されたらタイマーをキャンセル（連続発話対応）
5. タイマー満了後: _konnyaku_system.stop_route(b) を呼ぶ
6. GUI に「停止中...」フィードバックを表示（F-6 参照）
7. ログ: [PTT] 系統B 離脱: stop_route(b) 呼び出し（500ms デバウンス後）

### F-2.3 押下中のレベルメーター

- 押下中（系統Bが RUNNING）: _update_konnyaku_level_meters() の既存ロジックで更新される
- PTT モード有効時は route_b_enabled の判定を TAG_ROUTE_B_ENABLE チェックボックス値ではなく
  route_b.state == RouteState.RUNNING で行う（F-7.1 参照）

---
## F-3: コールドスタート遅延の扱い（最重要）

### 問題

CaptionSystem.start() → WebSocket 接続 → session.update 完了まで通常 **1〜2 秒** かかる。
この間に発話した音声は capture スレッドが存在しないため **ロスト** する。

### 対策案比較表

| 観点 | 案A: プリロール録音 | 案B: セッション常時保持 | 案C: 仕様割切りアナウンス |
|------|---------------------|-------------------------|--------------------------|
| **概要** | 押下と同時にローカル録音を開始。WS 接続完了後に録音バッファを追送 | WS セッションを常時接続維持。押下中だけ capture → feed_audio を ON | 「押下後 1〜2 秒は取りこぼし」と UI 表示して許容 |
| **コスト削減効果** | 高（接続中のみ課金）| なし（常時接続 = 常時課金）| 高（接続中のみ課金）|
| **実装複雑度** | 中（バッファ管理・追送ロジックが必要）| 高（モダリティ動的切替の API 仕様確認が必要、未検証）| 低 |
| **遅延ロスト量** | ほぼゼロ | ゼロ | 1〜2 秒 |
| **UX** | 良い | 最良 | 許容できる（運用でカバー）|
| **リスク** | バッファ追送の音声品質・順序保証が不明 | OpenAI Realtime API の audio.output モダリティ動的切替が未ドキュメント。検証コストが高い | 低い |
| **既存コードへの影響** | CaptionSystem に AudioBuffer と追送ロジックを追加 | RealtimeTranslator の大改修 | app.py の UI 表示追加のみ |

### 推奨: 案C（仕様割切りアナウンス）

**理由:**

- 監督の主目的は「コスト削減」であり、取りこぼし許容は要件を満たす
- 案Aの「バッファ追送」は、OpenAI Realtime API の input_audio_buffer.append での追送が
  セッション確立前に積んだデータを正しく処理するかどうか保証がなく、
  実装してもロスト問題が解決しない可能性がある
- 案Bは API の公式ドキュメントに記載のない動作に依存するため導入リスクが高い
- 案Cは「少し間を置いてから話し始める」という運用習慣で完全に吸収できる
- 将来的に案Aへのアップグレードは可能（インターフェースを汚さない）

**UI アナウンス仕様（案C）:**

- ステータスバーに「PTT: 接続中... 1〜2秒後に音声認識を開始します」を表示
- 系統Bの RouteState が RUNNING に遷移したタイミングで「PTT: 認識中」に更新

---

## F-4: チャタリング防御

### F-4.1 押下デバウンス（200ms）

- ホットキー押下 → 200ms 以内の再押下は無視
- 実装: _ptt_last_press_time を記録し、経過時間が 200ms 未満なら start_route を呼ばない
- 目的: OS のキーリピートによる多重 start_route 呼び出しを防ぐ
  （F-2.1 の RouteState ガードでも防げる。二重防衛）

### F-4.2 離脱デバウンス（500ms）

- ホットキー離脱 → 500ms 後に stop_route(b) を実行（threading.Timer で実装）
- 500ms 以内に再押下されたらタイマーをキャンセル
- 目的: 短い発話の間の「息継ぎ」で毎回接続・切断が発生するのを防ぐ

**TBD-1（監督確認）**: 500ms の離脱デバウンス値が適切か。
長すぎると「喋り終わっても翻訳が続く」感覚、短すぎると息継ぎのたびに再接続が発生。
200ms / 500ms / 1000ms から選択。**推奨は 500ms**。

### F-4.3 連打上限

- 10 秒間で 5 回以上の start_route 呼び出しを検出したら警告ログを出力する
- 
- アプリを停止はしない（警告のみ）

---

## F-5: モード切替 UI

### F-5.1 設定ファイル変更

settings.json の route_b オブジェクトに以下を追加:



**TBD-2（監督確認）**: ptt_enabled のデフォルトを false（従来動作保持）か true か。
推奨は **false**（既存ユーザーへの影響なし）。

### F-5.2 UI 配置

- 詳細設定の「折りたたみ」セクション内に「PTT 設定」グループを追加
- 既存の APIキー・デバイスフィルタの下に配置



### F-5.3 系統Bチェックボックスの表示切替

- **PTT OFF（従来動作）**: ラベルは「系統2 有効」のまま
- **PTT ON**: ラベルを「系統2 (PTT: F8 押下中)」に変更（ホットキー変更時はラベルも追従）
- 押下中は「系統2 [送信中]」に変更
- **TBD-4（監督確認）**: PTT ON 時にチェックを手動 OFF 可能にするか禁止するか

---

## F-6: 視覚フィードバック

### F-6.1 押下中の表示

| 対象 | 変更内容 |
|------|---------|
| 系統B セクション枠 | 通常色 → アクセント色（橙）に変更。dpg.bind_item_theme でテーマ切替 |
| TAG_ROUTE_B_ENABLE ラベル | 「系統2 (PTT: F8 押下中)」→「系統2 [送信中]」 |
| ステータスバー (TAG_STATUS_STATE) | 「PTT: 認識中」 |
| 入力レベルメーター (TAG_LEVEL_METER_B_IN) | 通常通り更新 |

### F-6.2 離脱後の表示

| タイミング | 対象 | 表示 |
|-----------|------|------|
| key up 直後（デバウンス待機中）| 系統B ラベル | 「系統2 [停止待機中...]」 |
| stop_route 完了後 | 系統B ラベル | 「系統2 (PTT: F8 押下中)」（待機状態へ戻る）|
| stop_route 完了後 | ステータスバー | 「PTT: 待機中」 |
| stop_route 完了後 | 入力レベルメーター | 0% に戻る |

### F-6.3 接続中表示（案C 採用時）

- RouteState が STARTING の間: ステータスバーに「PTT: 接続中...」を表示

---
## F-7: 設計整合性

### F-7.1 常駐モデルとの整合

PTT 押下 = start_route("b") / 離脱 = stop_route("b") を呼ぶだけで OK。

既存の _on_route_b_enable_change コールバックと完全に同一の経路を使う。
PTT は「ユーザーがチェックボックスの代わりにキーボードで ON/OFF する操作」と等価。

CaptionSystem.start() / stop() の冪等性ガードがそのまま機能するため、
二重押下・二重離脱は自動的に no-op になる。

**レベルメーター修正（PR3 で対応）:**
_update_konnyaku_level_meters() の route_b_enabled 判定を PTT モード ON 時は
_konnyaku_system.route_b_system.state == RouteState.RUNNING に切り替える。

### F-7.2 B-15 動的入力デバイス切替との競合

- PTT 押下中に入力デバイスコンボを変更した場合、既存の _on_route_b_device_change が
  stop() → デバイス更新 → start() を内部で実行する（干渉なし）
- **TBD-3（監督確認）**: 押下中のデバイス変更を disable するかどうか。推奨は **disable**。

### F-7.3 系統A は対象外

PTT 機能は系統B（自分→相手）のみに適用する。
系統A（相手→自分）は常時稼働を前提とする（相手がいつ話すかわからないため）。

### F-7.4 開始ボタン未押下状態でのホットキー

- _konnyaku_running が False の場合、ホットキー押下を **無視する**
- PTT はアプリ起動トリガーではない

### F-7.5 アプリ終了時のホットキー登録解除

- アプリ終了フックで keyboard.unhook_all() または特定ホットキーの unhook を呼ぶ
- 解除しないと他アプリのキー入力に影響する可能性がある

---

## F-8: テスト方針

### F-8.1 ホットキーマネージャーの単体テスト（FakeKeyboardBackend 方式）

PttHotkeyManager クラスにコンストラクタ引数 backend を設け、テスト時は
FakeKeyboardBackend を注入することで実キーボードなしに on_press/on_release を発火できる。



### F-8.2 PTT 状態機械の単体テスト

- test_ptt_press_starts_route_b(): 押下 → start_route(b) が呼ばれること
- test_ptt_release_stops_route_b_after_debounce(): 離脱 → 500ms 後に stop_route(b) が呼ばれること
- test_ptt_release_cancelled_by_repress(): 離脱 → 300ms 後に再押下 → stop_route が呼ばれないこと
- test_ptt_noop_when_not_running(): _konnyaku_running=False 時は start_route が呼ばれないこと
- test_ptt_debounce_press(): 200ms 未満の連打は start_route が 1 回しか呼ばれないこと

threading.Timer 依存のテストは timer_factory 引数を mock 化して同期的に実行する。

### F-8.3 E2E テスト方針

- --inject-ptt-events フラグを新設し、GUI 起動後 N 秒後に PTT press/release を自動注入
- PttHotkeyManager.simulate_press() を直接呼ぶため実キーボード入力は不要
- --auto-konnyaku=N フラグと組み合わせて一連のフローを自動検証可能にする

---

## インターフェース定義

### 新規クラス: PttHotkeyManager（ptt_hotkey_manager.py）



### app.py への追加（新規グローバル変数・関数）



### settings.json スキーマ追加（キー名のみ）



---

## 付録A: ライブラリ選定詳細

### 候補比較

| 観点 | keyboard | pynput | global_hotkeys |
|------|----------|--------|----------------|
| グローバルホットキー | あり（Windows/Mac/Linux）| あり | あり（Windows/Mac）|
| key down / key up 分離 | あり | あり | なし（press のみ）|
| Windows 通常ユーザーで動作 | はい | はい | はい |
| PyPI メンテナンス状態 | 緩やかな更新（2022〜）| 2024 更新中 | 更新低調 |
| テスト容易性 | 高（simulator 内蔵）| 中 | 低 |
| API の単純さ | 高 | 中 | 高 |

**推奨: keyboard**

PTT は key down / key up の分離が必須。global_hotkeys は press のみのため除外。
keyboard は API が単純で、テスト用シミュレーターが組み込まれており、
Windows ファーストのアプリに適している。

**注意事項:**

- Windows では Raw Input フックを使用するため、一部のセキュリティソフトが誤検知する場合がある
- UAC 昇格アプリがフォアグラウンドの場合、ロー レベルフックが効かないケースがある
- 上記制約は仕様制約として README に明記する

requirements.txt 追記内容:



---

## 付録B: PR 分割案（TDD 実装時）

### PR1: ライブラリ導入 + PttHotkeyManager 基盤

- requirements.txt に keyboard>=0.13.5 追加
- ptt_hotkey_manager.py 新規作成（PttHotkeyManager クラス + FakeKeyboardBackend）
- tests/test_ptt_hotkey_manager.py 新規作成（F-8.1 の単体テスト）
- この時点では app.py / main.py を変更しない

### PR2: デバウンス・チャタリング防御

- PttHotkeyManager にデバウンスロジックを実装（timer_factory 注入で mock 可能）
- tests/test_ptt_hotkey_manager.py にデバウンステストを追加（F-8.2 の 5 テスト）
- 連打カウンターと警告ログを追加

### PR3: app.py 結線

- _on_ptt_press / _on_ptt_release / _init_ptt_manager / _cleanup_ptt_manager を app.py に追加
- settings.json の読み書き対応（ptt_enabled, ptt_hotkey）
- _update_konnyaku_level_meters() の route_b_enabled 判定を PTT 対応に修正
- アプリ起動時・終了時の init/cleanup フック追加

### PR4: UI（設定タブ + 視覚フィードバック）

- 詳細設定に「PTT 設定」グループ追加（TAG_PTT_ENABLED / TAG_PTT_HOTKEY）
- 系統Bチェックボックスのラベル動的切替
- 系統B区画のテーマ切替（押下中: 橙枠、通常: 標準色）
- ステータスバーへの PTT 状態表示

---

## 受け入れ条件

- [ ] PTT モード OFF の場合、ホットキーを押しても何も起きない
- [ ] PTT モード OFF の場合、系統Bの従来動作（チェックボックスで ON/OFF）が維持される
- [ ] PTT モード ON でアプリ稼働中に F8 を押すと系統B が起動する
- [ ] PTT モード ON で F8 を離すと 500ms 後に系統B が停止する
- [ ] F8 を 200ms 未満の間隔で連打しても start_route(b) は 1 回しか呼ばれない
- [ ] F8 離脱後 300ms で再押下すると stop_route(b) はキャンセルされる
- [ ] アプリ未稼働中（開始ボタン未押下）に F8 を押しても系統Bは起動しない
- [ ] PTT 押下中、系統B 区画の枠色が橙に変わる
- [ ] PTT 離脱後、系統B 区画の枠色が通常色に戻る
- [ ] ステータスバーに PTT 状態（接続中 → 認識中 → 待機中）が表示される
- [ ] settings.json に ptt_enabled / ptt_hotkey が保存・復元される
- [ ] アプリ終了時にホットキー登録が解除され、他アプリへの干渉がない
- [ ] PttHotkeyManager の全単体テスト（F-8.2 の 5 テスト）が実キーボードなしでパスする
- [ ] PTT 機能追加後も既存テスト 300 件が全パスする

---

## 監督確認事項（TBD 一覧）

以下の項目は仕様書上「TBD」とし、監督の判断を得てから仕様確定とする:

| # | 確認事項 | 選択肢 | 推奨 |
|---|---------|--------|------|
| TBD-1 | 離脱デバウンス時間 | 200ms / 500ms / 1000ms | **500ms** |
| TBD-2 | ptt_enabled のデフォルト値 | true / false | **false**（既存動作保持）|
| TBD-3 | PTT 押下中のデバイスコンボを disable するか | する / しない | **する** |
| TBD-4 | PTT ON 時に系統Bチェックを手動 OFF 可能にするか | 可能 / 禁止 | 可能（OFF = PTT も無効扱い）|

---

## 実装メモ（PRGちゃんへの引き継ぎ）

1. **start_route("b") / stop_route("b") はそのまま使ってよい**
   - CaptionSystem.start() の冪等性ガード（RouteState.RUNNING なら no-op）が PTT の連打を自然に吸収する
   - CaptionSystem.stop() の冪等性ガード（RouteState.IDLE なら no-op）も同様

2. **レベルメーター表示の修正が必要（PR3 で対応）**
   - _update_konnyaku_level_meters() の route_b_enabled 判定を変更する
   - PTT モード有効時: dpg.get_value(TAG_ROUTE_B_ENABLE) の代わりに
     _konnyaku_system.route_b_system.state == RouteState.RUNNING で判定する
   - PTT モード無効時: 従来通りチェックボックスの値で判定する

3. **keyboard ライブラリのスレッド問題**
   - keyboard.on_press_key はバックグラウンドスレッドでコールバックを発火する
   - GUI 更新は既存の _gui_set_value() / _gui_set_label() ラッパー経由とすること

4. **threading.Timer の mock 化（テスト実装用）**
   - PttHotkeyManager 内で timer_factory: Callable = threading.Timer を引数で受け取る設計にする
   - テスト例: mock_timer = MagicMock() を注入 → mock_timer.return_value.start.assert_called_once()

5. **設定ファイルとの後方互換**
   - 既存の settings.json に ptt_enabled / ptt_hotkey キーがない場合:
     dict.get("ptt_enabled", False) / dict.get("ptt_hotkey", "f8") でデフォルト値を使う

6. **ホットキー登録タイミング**
   - _init_ptt_manager() は _build_gui() 完了後に呼ぶ
   - _cleanup_ptt_manager() は DearPyGui の on_close または atexit フックで呼ぶ
   - 既存の終了処理（terminate() → dpg.destroy_context()）の前に挿入すること

7. **B-15 との干渉なし**
   - PTT 押下中に set_input_device() が呼ばれても CaptionSystem の stop/start で安全に処理される
   - TBD-3（押下中の disable）が採用されれば UI 操作自体を禁止できる

8. **セキュリティ制約（README への明記事項）**
   - keyboard ライブラリは UAC 昇格アプリがフォアグラウンドの場合にフックが効かないケースがある
   - 一部のセキュリティソフトが誤検知する場合がある

---

## 既存仕様書への追記事項

### ui-spec-konnyaku.md への追記

**§3.2 系統2 有効チェックボックス** に PTT モード時の挙動を追記:

- PTT モード有効時（route_b.ptt_enabled=true）:
  - ラベルを「系統2 (PTT: <hotkey> 押下中)」に変更する
  - 押下中は「系統2 [送信中]」に変更する
  - TBD-4 の判断後に disabled にするか否かを確定する

**§7. 状態遷移** テーブルに PTT 状態行を追加:

| 状態 | 開始/停止ボタン | 系統チェックの効果 | レベルメーター |
|---|---|---|---|
| 稼働中（PTT モード、キー離脱中） | ラベル「停止」、押下可 | 系統Aは即時反映。系統Bチェックは PTT 連動 | 系統Aのみ動的更新、系統Bは 0% |
| 稼働中（PTT モード、キー押下中） | ラベル「停止」、押下可 | 同上 | 系統A・系統B ともに動的更新 |

### residence-model-design.md への追記

**§リスク・懸念点** に以下を追加:

> 6. PTT と start_route/stop_route の競合
>    PTT ホットキースレッドとチェックボックス callback が同時に start_route(b) を呼ぶ可能性がある。
>    CaptionSystem.start() の冪等性ガードが存在するため実害はないが、
>    ログに重複呼び出しが出る可能性がある。
>    PTT モード ON 時に TAG_ROUTE_B_ENABLE チェックボックスを disabled にすることで回避（推奨）。

---

## codex 利用

- codex 利用: なし
