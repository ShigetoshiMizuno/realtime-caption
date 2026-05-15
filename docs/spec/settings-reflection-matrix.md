# 設定変更反映タイミング仕様書 — マトリクス + 自己テスト強化

> Issue #102 Step 1 + Step 2 / 対象 master: ff4dece
> 策定日: 2026-05-15 by SPECちゃん

---

## 概要

各設定項目の「稼働中変更可否」「反映タイミング」「既存実装状況」「問題点」をコード読解で確定し、テスト強化（Step 4）への引き継ぎ情報と合わせてまとめたドキュメント。

---

## 1. 調査方法

コード読解のみ（実機実行なし）。調査対象ファイル:

- app.py — GUI コールバック群（行番号は master ff4dece 基準）
- main.py — CaptionSystem, MultiCaptionSystem, RouteConfig, RouteState
- realtime_translator.py — OpenAI Realtime 接続層

---

## 2. 反映タイミング分類（用語定義）

| 分類 | 定義 |
|---|---|
| 即時反映 | コールバック内で内部状態が即変更。API 接続への影響も即時 |
| 稼働中再起動 | コールバックが _restart_route_for_change を呼び stop_route -> start_route が自動実行される |
| 次回起動時反映 | _save_settings() のみ実行。稼働中の停止→開始操作まで反映されない |
| 再起動必須 | アプリ再起動が必要 |

---

## 3. 設定項目マトリクス（こんにゃくモード）

| # | 設定項目 | 稼働中変更可否 | 反映タイミング | コールバック | 既存実装状況 | 問題点 |
|---|---|---|---|---|---|---|
| C-01 | 入力デバイス（系統A） | 可 | 即時反映（内部 stop→start） | _on_route_a_device_change | set_input_device() 呼び出し。state==RUNNING 時のみ stop→start | 条件付き: STARTING/STOPPING 中は no-op |
| C-02 | 入力デバイス（系統B） | 可 | 即時反映（内部 stop→start） | _on_route_b_device_change | 同上 | 同上 |
| C-03 | 音声出力 ON/OFF（系統A） | 可 | **稼働中再起動** | _on_route_a_output_enable_change | _restart_route_for_change 呼び出し済み（W-COST-1 PR3） | なし |
| C-04 | 音声出力 ON/OFF（系統B） | 可 | **稼働中再起動** | _on_route_b_output_enable_change | 同上 | なし |
| C-05 | 出力デバイス（系統A） | 可 | 即時反映 | _on_route_a_output_device_change | set_output_device() 直接呼び出し | 出力ストリームのみ更新。RealtimeTranslator の接続には影響なし |
| C-06 | 出力デバイス（系統B） | 可 | 即時反映 | _on_route_b_output_device_change | 同上 | 同上 |
| C-07 | 原文表示 ON/OFF（系統A） | 可 | **稼働中再起動** | _on_route_a_source_transcript_change | _restart_route_for_change 呼び出し済み（W-COST-2） | なし |
| C-08 | 原文表示 ON/OFF（系統B） | 可 | **稼働中再起動** | _on_route_b_source_transcript_change | 同上 | なし |
| C-09 | 翻訳先言語（系統A） | 可 | **稼働中再起動** | _on_route_a_language_change | _restart_route_for_change 呼び出し済み（issue #102 Fix 2）。stop() で _realtime_translator=None リセット（Fix 1） | なし（実装済み） |
| C-10 | 翻訳先言語（系統B） | 可 | **稼働中再起動** | _on_route_b_language_change | 同上 | なし（実装済み） |
| C-11 | 出力音量（系統A） | 可 | 即時反映 | _on_route_a_volume_change | output_volume setter → AudioOutputStream.set_volume() 即時 | なし |
| C-12 | 出力音量（系統B） | 可 | 即時反映 | _on_route_b_volume_change | 同上 | なし |
| C-13 | 系統A 有効チェック | 可 | 即時反映 | _on_route_a_enable_change | start_route/stop_route 直接呼び出し（B-14） | なし |
| C-14 | 系統B 有効チェック | 可 | 即時反映（PTT 対応） | _on_route_b_enable_change_ptt_aware | PTT ON 時は PTT も連動 OFF（TBD-4 対応済） | なし |
| C-15 | VAD ON/OFF | 否（UI 未実装） | 次回起動時反映 | UI なし（settings.json のみ） | _save_settings で vad_enabled: False をハードコード | GUI ウィジェット未実装。常時 False が保存される（W-COST-3 設計段階で「次 PR で実装予定」と明記） |
| C-16 | PTT モード ON/OFF | 可 | 即時反映 | _on_ptt_enabled_change | _ptt_manager.start() / _cleanup_ptt_manager() 即時呼び出し | なし |
| C-17 | PTT ホットキー変更 | 可 | 即時反映 | _on_ptt_hotkey_change | _ptt_manager.change_hotkey() 即時呼び出し | なし |
| C-18 | API キー変更 | 否 | 次回起動時反映 | _on_save_api_keys | 稼働中は「次回起動時に反映されます」と GUI 表示 | 設計上の意図的制約。問題なし |

---

## 4. 重大問題の詳細分析

### 4.1 翻訳先言語（C-09 / C-10）が stop→start 後も反映されない

#### 根本原因1: 言語コンボの callback が _save_settings() のみ

app.py 行2754（系統A）:

    callback=lambda s, a, u: _save_settings()

_restart_route_for_change が呼ばれないため、稼働中に変更しても言語が変わらない。

#### 根本原因2: stop→start サイクル後も言語が反映されない（より深刻）

_restart_route_for_change が stop_route -> start_route を呼んだとしても、
start_route -> CaptionSystem.start() -> _create_realtime_translator() で以下の no-op 条件にヒットする:

    def _create_realtime_translator(self) -> None:
        if self._realtime_translator is not None:
            return  # stop() でも _realtime_translator は None にリセットされないため no-op

CaptionSystem.stop() の finally ブロック（main.py 行838-845）では
_loop, _stop_event, _capture_thread はリセットされるが _realtime_translator は None にされない。

結果として「停止→開始ボタンを押しても言語は反映されない」。アプリ再起動が唯一の確実な手段。

#### ユーザー体験への影響

監督の体感「停止→開始を押さないと反映されない」よりも深刻で、
「停止→開始を押しても反映されない」項目が翻訳先言語。

### 4.2 入力デバイス変更（C-01 / C-02）の条件付き動作

set_input_device() の実装（main.py 行939-966）:

    was_running = self.state == RouteState.RUNNING
    if was_running:
        self.stop()
    self._device_info = device_info
    if was_running:
        self.start()

STARTING/STOPPING 中は _device_info の書き換えのみで再起動しない（正常動作）。
STARTING 中に変更した場合、その後 RUNNING になっても古いデバイスで動作継続する可能性がある。

### 4.3 API キー変更（C-18）の仕様

_on_save_api_keys では稼働中の変更時に「次回起動時に反映されます」と GUI 表示。これは意図的な設計。

---

## 5. UX 改善対象リストと推奨案

| # | 設定項目 | 深刻度 | 推奨改善案 |
|---|---|---|---|
| C-09/C-10 | 翻訳先言語 | **高**（稼働中変更不可 + stop→start でも未反映） | 案A: stop() で _realtime_translator = None + 言語変更時に _restart_route_for_change 呼び出し |
| C-01/C-02 | 入力デバイス | 低（RUNNING 時は正常動作） | 案C: 仕様維持 |
| C-15 | VAD ON/OFF | 中（UI 未実装） | 案B: GUI ウィジェット追加後に再起動方式で対応（TBD-3） |
| C-18 | API キー | 低（メッセージ明示済み） | 案C: 仕様維持 |

推奨 PR 分割案:

- PR-A（最優先）: 翻訳先言語の稼働中反映
  - CaptionSystem.stop() で _realtime_translator = None にリセット
  - 言語コンボのコールバックで _restart_route_for_change を呼び出す
- PR-B（後続）: VAD GUI ウィジェット追加（W-COST-3 継続）

---

## 6. 監督への確認事項（TBD）

| TBD# | 内容 | 選択肢 |
|---|---|---|
| TBD-1 | 翻訳先言語変更時の挙動を「稼働中再起動」にするか「次回起動時まで仕様」とするか | A: 稼働中再起動化 / C: 仕様維持 |
| TBD-2 | CaptionSystem.stop() の finally ブロックで _realtime_translator = None を追加することへの同意（既存テストへの影響あり） | 同意 / 要検討 |
| TBD-3 | VAD GUI ウィジェット実装の優先度 | 次 PR か後回しか |

---

## 7. Step 4（自己テスト強化）への引き継ぎ

### 7.1 組み合わせテストでカバーすべきパターン

#### 7.1.1 稼働中変更 x 設定項目の 2 次元マトリクス

| 系統 | 設定項目 | 稼働中状態 | 期待動作 |
|---|---|---|---|
| A | 音声出力 ON→OFF | RUNNING | stop_route("a") -> start_route("a") が順序通り |
| A | 音声出力 OFF→ON | RUNNING | 同上 |
| A | 音声出力 ON→OFF | IDLE | 再起動が呼ばれないこと |
| A | 原文表示 ON→OFF | RUNNING | stop_route("a") -> start_route("a") が順序通り |
| A | 原文表示 OFF→ON | RUNNING | 同上 |
| A | 翻訳先言語 ja→en | RUNNING | TBD-1 により変わる。現状は _save_settings() のみ |
| B | 音声出力 ON→OFF | RUNNING | stop_route("b") -> start_route("b") が順序通り |
| B | 原文表示 ON→OFF | RUNNING | 同上 |
| A+B | 同時変更（A 音声出力, B 翻訳先言語） | 両方 RUNNING | 各系統が独立して処理されること |

#### 7.1.2 PTT x 系統B の組み合わせ

| PTT 状態 | 系統B 操作 | 期待動作 |
|---|---|---|
| PTT ON | 系統B チェック OFF | PTT も自動 OFF になること |
| PTT ON | 系統B チェック ON | 何も起きないこと（PTT が制御） |
| PTT OFF | 系統B チェック ON | start_route("b") が呼ばれること |
| PTT OFF | 系統B チェック OFF | stop_route("b") が呼ばれること |

#### 7.1.3 出力デバイス x 音声出力 ON/OFF の組み合わせ

| 音声出力状態 | 出力デバイス変更 | 期待動作 |
|---|---|---|
| OFF のとき | デバイス選択 | set_output_device が呼ばれるが _audio_output_mode=False のまま |
| ON のとき | (なし)→デバイス名 | set_output_device(index) が呼ばれること |
| ON のとき | デバイス名→(なし) | set_output_device(None) が呼ばれること |

### 7.2 状態遷移テストでカバーすべきシナリオ

| シナリオ | 初期状態 | 操作 | 終了状態 | 確認ポイント |
|---|---|---|---|---|
| 正常起動 | IDLE | start_route("a") | RUNNING | state が RUNNING になること |
| 正常停止 | RUNNING | stop_route("a") | IDLE | state が IDLE になること |
| 再起動サイクル | RUNNING | stop_route -> start_route | RUNNING | _realtime_translator が再生成されること（TBD-2 解決後） |
| STARTING 中の変更 | STARTING | 入力デバイス変更 | RUNNING | 変更が安全に処理されること（例外なし） |
| 連打防御 | RUNNING | _restart_route_for_change x2回同時 | RUNNING | ロックにより2回目がスキップされること |
| エラーからの復帰 | ERROR | start_route | RUNNING or ERROR | 有効 API キー → RUNNING、未設定 → ERROR |
| _konnyaku_running=False での変更 | IDLE | 音声出力 ON/OFF 変更 | IDLE | stop_route / start_route が呼ばれないこと |

### 7.3 保存・復元テストでカバーすべきケース

| ケース | 保存内容 | 復元確認ポイント |
|---|---|---|
| route_a の全項目保存 | enabled, device, lang, output_enabled, output_device, output_volume, source_transcript_enabled, vad_enabled | _load_settings() で同じ値が返ること |
| route_b の全項目保存 | 上記 + ptt_enabled, ptt_hotkey | 同上 |
| vad_enabled の保存 | False ハードコード（W-COST-3 UI 未実装） | 常に False が保存されること |
| settings.json が存在しない | ファイルなし | {} が返ること（既存テスト済み） |
| settings.json が破損 | 不正 JSON 文字列 | {} が返ること（既存テスト済み） |
| アプリ再起動後の復元 | 全 route_a/b 項目 | _create_konnyaku_system() が保存値を使いデバイス・言語を復元すること |
| 翻訳先言語の保存・復元 | 日本語 / 英語 | 再起動後に同じ言語が選択されること |
| 出力音量の保存・復元 | 0.0 から 2.0 | CaptionSystem.output_volume に反映されること |
| ptt_enabled + ptt_hotkey の保存・復元 | True + f9 | _init_ptt_manager が正しいホットキーで起動すること |

### 7.4 既存テストとの重複回避

以下は既存テストでカバー済みのため、新規テストで重複を避けること:

| 既存テストファイル | カバー済み内容 |
|---|---|
| test_w_cost_1_pr3_restart.py | 音声出力 ON/OFF 時の stop/start 順序、停止中での no-op |
| test_w_cost_2_source_transcript.py | 原文表示 ON/OFF 時の再起動、RealtimeTranslator へのフラグ伝播 |
| test_w_cost_3_vad.py | VAD 設定の伝播 |
| test_settings_persistence.py | _save_settings / _load_settings の基本動作 |
| test_app_ptt_wiring.py | PTT ON/OFF 時のホットキー管理 |

---

## 8. サマリ（監督向け）

### 8.1 「停止→開始しないと反映されない」の最有力候補

翻訳先言語（C-09 / C-10）が最有力候補。さらに深刻なのは「停止→開始を押しても反映されない」点:

1. 言語コンボの callback が _save_settings() のみ → 稼働中に変更しても即時反映なし
2. CaptionSystem.stop() で _realtime_translator が None にリセットされない → 再起動後も _create_realtime_translator() が no-op → 古い言語で API 接続継続
3. アプリ再起動（プロセス終了 → 再起動）が唯一の確実な反映手段

その他の項目:

- 音声出力 ON/OFF・原文表示 ON/OFF は稼働中再起動済み（W-COST-1, W-COST-2 対応済み）
- VAD は UI 未実装のため settings.json 直接編集かつ再起動が必要（仕様未完成）
- API キーは意図的に次回起動時仕様（GUI でメッセージ表示済み）

### 8.2 推奨対応

| 優先度 | 対応 | PR |
|---|---|---|
| 最高 | stop() 時に _realtime_translator = None にリセットし、言語コンボ変更時に _restart_route_for_change を呼び出す | PR-A（新規） |
| 中 | VAD GUI ウィジェット追加（W-COST-3 継続） | PR-B（別途） |
| 低 | API キー稼働中反映（現状は意図的に次回起動時仕様） | 仕様維持推奨 |

---

## 機能仕様

（Step 3+4+5 実装で満たすべき仕様、TBD-1/TBD-2 承認後）

- 翻訳先言語変更時、稼働中の系統を自動再起動して即時反映する
- 再起動中はステータスバーに「系統N 翻訳先言語切替中...」を表示する
- _realtime_translator のリセットにより、stop→start サイクルで最新の言語設定が使われる

---

## インターフェース定義

変更が必要なシグネチャ（TBD-1/TBD-2 承認後に PRG が実装）。

変更前（app.py 言語コンボのコールバック）:

    callback=lambda s, a, u: _save_settings()

変更後（TBD-1 承認後）:

    callback=_on_route_a_language_change  # 新規関数として定義

新規コールバック関数の雛形（系統A）:

    def _on_route_a_language_change(sender, app_data, user_data) -> None:
        _save_settings()
        if (
            _konnyaku_running
            and _konnyaku_system is not None
            and _konnyaku_system.route_a_system is not None
            and _konnyaku_system.route_a_system.state == RouteState.RUNNING
        ):
            if dpg.does_item_exist(TAG_STATUS_STATE):
                try:
                    dpg.set_value(TAG_STATUS_STATE, "系統1 翻訳先言語切替中...")
                except Exception:
                    pass
            threading.Thread(
                target=_restart_route_for_change,
                args=("a", "翻訳先言語切替"),
                daemon=True,
                name="RestartRouteAForLanguageChange",
            ).start()

main.py CaptionSystem.stop() の finally ブロックへの追加（TBD-2 承認後）:

    self._realtime_translator = None
    self._cost_monitor = None

系統B も同様に _on_route_b_language_change を新規作成する。

---

## 制約・前提条件

- コード変更なし（仕様書のみ）。実装は PRG が担当
- 翻訳エンジンは常時 openai-realtime（こんにゃくモード固定）
- こんにゃくモード以外（旧単独モード）のパスは調査対象外
- 実機実行テストは QA の担当

---

## 受け入れ条件

- [ ] docs/spec/settings-reflection-matrix.md が作成されていること
- [ ] 全設定項目（C-01 から C-18）の反映タイミングが仕様書に明記されていること
- [ ] 翻訳先言語の問題（根本原因2つ）が文書化されていること
- [ ] 監督への TBD 確認事項（TBD-1 から TBD-3）が明記されていること
- [ ] Step 4 テスト強化の引き継ぎ表（§7）が記載されていること

---

## 実装メモ（PRGちゃんへの引き継ぎ）

### Step 3+4+5 着手前に監督に確認すること

1. TBD-1: 翻訳先言語の「稼働中再起動化」を行うか（PR-A 実装の前提）
2. TBD-2: CaptionSystem.stop() の finally ブロックで self._realtime_translator = None を追加することへの同意。
   影響するテスト:
   - test_w_cost_2_source_transcript.py の _create_realtime_translator no-op を検証するテストがあれば修正が必要
   - test_realtime_translator_reconnect.py の再接続テストへの影響確認が必要
3. TBD-3: VAD GUI ウィジェット実装の優先度

### PRG 実装時の注意点

1. _restart_route_for_change の呼び出し条件:
   _konnyaku_running and state == RUNNING の条件チェックを必ず入れること
   （既存の音声出力・原文表示コールバックと同じパターン）

2. _realtime_translator の None リセット場所:
   stop() の finally ブロック末尾（main.py 行845付近）に
   self._realtime_translator = None と self._cost_monitor = None を追加。
   _cost_monitor も lazy init のため同時にリセット。

3. 連打防御ロック:
   _restart_locks を使うこと（_restart_route_for_change が内部で処理済み）

4. GUI ステータス表示:
   再起動中は「系統N 翻訳先言語切替中...」と表示すること

5. テスト追加:
   Step 4 で tests/test_settings_reflection_matrix.py（仮称）を新規作成し、§7 のカバレッジを追加すること

### 既存 API の破壊的変更について

CaptionSystem.stop() への _realtime_translator = None 追加は破壊的変更の可能性あり。
PRG が事前に以下を確認すること:

    grep -rn "_realtime_translator" tests/

---

## codex 利用
- codex 利用: なし
