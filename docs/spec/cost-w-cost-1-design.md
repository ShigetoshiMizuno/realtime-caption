# W-COST-1 設計仕様書 — 音声出力 OFF 時の音声トークン課金問題

> Issue #81 / 対象 master: a9a984b（PTT 全 PR マージ済み）
> 策定日: 2026-05-15 by SPECちゃん

---

## 概要

音声出力 OFF 設定であっても OpenAI Realtime Translate API が音声トークンを生成・課金し続けている問題（W-COST-1）を解消する。
`main.py:608-625` で `request_audio_output=True` を常時設定していることが根本原因。

---

## 1. OpenAI Realtime Translate API — モダリティ動的切替の調査結果

### 1.1 API エンドポイント・仕様

- エンドポイント: `wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate`
- モデルリリース: 2026 年初頭（現時点で仕様変動の可能性あり）
- 公式参照:
  - https://platform.openai.com/docs/api-reference/realtime-sessions
  - https://developers.openai.com/cookbook/examples/voice_solutions/realtime_translation_guide

### 1.2 session.update による動的切替について

**調査情報（既存コードのコメントおよび実機挙動ログより）:**

| 試みた操作 | 結果 | 根拠 |
|---|---|---|
| session.update に audio.output.format=pcm16 を指定 | API が Unknown parameter エラーで session.update 全体を拒否 → 言語設定まで無効化 | realtime_translator.py:82-84 のコメント、PR #76 経緯 |
| session.update に audio.output.language のみ指定 | 受理される（現状の実装） | 同ファイル L278 |
| 接続中に audio.output キーを削除した session.update を送信 | 未検証（接続中の動的変更が効くかは不明） | — |
| modalities フィールドで text/audio を切り替え | 未検証（gpt-realtime-translate が modalities パラメータを受け付けるか不明） | — |

**結論（TBD-1）:**

- session.update で接続中に音声出力モダリティを ON/OFF できるかは公式ドキュメントに明示なし
- audio.output.format に関しては実機で Unknown parameter エラーが確認済み
- modalities: ["text"] にすれば音声生成を止められる可能性はあるが、gpt-realtime-translate 専用エンドポイントでこのパラメータが使えるかは未確認
- **監督への確認事項（TBD-1）: 「案 A を実装前に API 検証ブランチを作って試すか、それとも最初から案 B で進めるか」**

### 1.3 音声トークン課金の仕組み（現状の問題）

現状の _run_session では接続時に毎回以下を送信している:

    session.update -> session.audio.output = { "language": "<target_lang>" }

これにより API 側は音声出力を有効化し、session.output_audio.delta イベントで PCM16 音声チャンクを返す。
クライアント側で _audio_stream が None でも、**API 側は音声を生成・送信し続けるため課金が発生する**。
---

## 2. 3案の評価

### 評価軸

| 評価軸 | 説明 |
|---|---|
| コスト効果 | 「音声出力 OFF」設定時に音声トークン課金を実際に削減できるか |
| 実装複雑度 | 既存コードへの変更量・リスク |
| UX | ユーザーにとっての体験（遅延・表示など） |
| API リスク | 実際の API 挙動に依存するリスク |

---

### 案 A: session.update でモダリティ動的切替

**概要:**
接続を維持したまま、音声出力 OFF 時は session.update に audio.output を含めない
（または modalities: ["text"] を送る）ことで API 側の音声生成を停止する。

**前提条件（API 側が満たす必要がある）:**
- gpt-realtime-translate エンドポイントが、接続中の session.update で audio.output の有無を有効に解釈する
- または modalities フィールドで text/audio を切り替えられる

| 軸 | 評価 | 備考 |
|---|---|---|
| コスト効果 | 最大（API 側で音声生成自体を止める） | 検証成功が前提 |
| 実装複雑度 | 中（update_audio_output メソッド新規追加） | |
| UX | 最良（接続断なし） | |
| API リスク | 高（未検証、Unknown parameter エラー前例あり） | TBD-1 |

**判断: TBD-1 次第。API 検証が取れれば最優先案。検証なしに本実装に進む場合はリスクが高い。**

---

### 案 B: 再起動方式（音声出力 ON/OFF 変更時に stop_route -> start_route）

**概要:**
request_audio_output=True/False を RealtimeTranslator のコンストラクタ引数として取り、
音声出力 OFF/ON が切り替わるたびに CaptionSystem.stop() -> CaptionSystem.start() で系統全体を再起動する。
接続前に request_audio_output の値が決まるため、確実に API 側の音声モダリティを制御できる。

| 軸 | 評価 | 備考 |
|---|---|---|
| コスト効果 | 最大（API 側で音声生成を最初から有効化しない） | |
| 実装複雑度 | 中（main.py:623 の 1 行変更 + app.py の再起動フロー追加） | |
| UX | 良（再起動の間 2-5 秒だけ翻訳中断） | 音声出力 ON/OFF 切替時のみ |
| API リスク | なし（既存の接続時 session.update の仕様内） | |

**判断: 案 A が使えない場合の次点。実装リスクが低い。**

---

### 案 C: 「音声出力 OFF」機能を完全撤去

**概要:**
「音声出力チェックボックス」を廃止し、出力デバイス選択コンボのみを残す。
デバイスが「(なし)」= ミュート扱いとする。
API 側は常に音声を生成するが、音声チャンクの受信・再生を単純にクライアント側で捨てることで疑似 OFF とする。

| 軸 | 評価 | 備考 |
|---|---|---|
| コスト効果 | なし（API 側は音声生成し続けるので課金削減にならない） | W-COST-1 の解決にならない |
| 実装複雑度 | 低（チェックボックス削除のみ） | |
| UX | 最良（シンプルな UI） | |
| API リスク | なし | |

**判断: W-COST-1 を解決しないため却下。**

---

## 3. 推奨案

### 推奨: 案 B（再起動方式）を主案とし、案 A を将来オプション化

**理由:**

1. **API 不確実性**: gpt-realtime-translate の modalities 動的切替は公式ドキュメントに明示がなく、実機で audio.output.format が Unknown parameter エラーになった前例がある。案 A は API 検証なしでは実装リスクが高い。

2. **案 B の確実性**: request_audio_output は RealtimeTranslator.__init__ に既存パラメータとして存在し、接続時の session.update で audio.output の有無を制御する仕組みが既にある。main.py:623 の request_audio_output=True を動的な値に変えるだけで課金削減が実現する。

3. **再起動 UX の許容性**: 音声出力 ON/OFF 切替は頻繁には行わない操作（会議開始前に設定する想定）。再起動 2-5 秒は許容範囲。

4. **TBD-1 について**: 案 A の API 検証は将来的な改善として別 issue で追跡する。

---

## 4. 機能仕様（案 B ベース）

### 4.1 RealtimeTranslator._run_session の変更

現状の問題: _request_audio_output フラグは保持されているが（L97）、_run_session では無条件に audio.output を設定している（L278-288）。

**変更後の動作:**

_request_audio_output = False のとき、session.update の session.audio から output キーを除外する。

    [_request_audio_output=True の場合]
      session.audio.input.transcription.model = "gpt-realtime-whisper"
      session.audio.output.language = "<target_lang>"

    [_request_audio_output=False の場合]
      session.audio.input.transcription.model = "gpt-realtime-whisper"
      (audio.output キーなし -> API は音声生成しない想定)

### 4.2 CaptionSystem._create_realtime_translator() の変更

- **変更前**: request_audio_output=True（固定、main.py:623）
- **変更後**: request_audio_output=self._audio_output_mode（初期値から動的に決定）

self._audio_output_mode は CaptionSystem.__init__ で output_device_index is not None として初期化される（L445）。
MultiCaptionSystem が route_a.audio_output_enabled=False で生成した場合（L1579-1580）、
output_device_index=None -> _audio_output_mode=False の流れで正しく伝播する。

### 4.3 稼働中の音声出力 ON/OFF 切替フロー

音声出力チェックボックス callback（app.py 側）:

    [稼働中に音声出力 ON -> OFF の場合]
      1. route.set_output_device(None) を呼ぶ（音声再生を即座に停止）
      2. ステータスバーに「音声出力を切り替えています...」を表示
      3. バックグラウンドスレッドで以下を実行:
         a. _konnyaku_system.stop_route(route_id)
         b. _konnyaku_system.start_route(route_id)
      4. 完了後、ステータスバーを通常表示に戻す

    [稼働中に音声出力 OFF -> ON の場合]
      1. route.set_output_device(output_device_index) を呼ぶ
      2. ステータスバーに「音声出力を切り替えています...」を表示
      3. バックグラウンドスレッドで stop_route -> start_route を実行
      4. 完了後、ステータスバーを通常表示に戻す

    [停止中に変更された場合]
      set_output_device のみ（再起動不要）

### 4.4 ui-spec-konnyaku.md §3.3 との整合

「稼働中の場合は即時反映」の定義を以下に変更する（§9 の追記参照）:
- **音声再生の停止/開始**: 即時（set_output_device で _audio_stream を切り替え）
- **API 側の音声トークン課金停止**: 再起動後（stop_route -> start_route による接続再確立後）

---

## 5. インターフェース定義

### 5.1 realtime_translator.py:_run_session の変更

変更前（L278-289）:

```python
audio_output_cfg: dict = {"language": self._target_language_code}
await ws.send(json.dumps({
    "type": "session.update",
    "session": {
        "audio": {
            "input": {"transcription": {"model": "gpt-realtime-whisper"}},
            "output": audio_output_cfg
        }
    }
}))
```

変更後:

```python
audio_section: dict = {
    "input": {"transcription": {"model": "gpt-realtime-whisper"}}
}
if self._request_audio_output:
    audio_section["output"] = {"language": self._target_language_code}

await ws.send(json.dumps({
    "type": "session.update",
    "session": {"audio": audio_section}
}))
```

### 5.2 main.py:CaptionSystem._create_realtime_translator の変更

変更前（L623）:

```python
request_audio_output=True,
```

変更後:

```python
request_audio_output=self._audio_output_mode,
```

### 5.3 app.py の変更箇所（概念定義）

既存 callback _on_route_a_output_enable_change / _on_route_b_output_enable_change に以下のロジックを追加:

- route.state == RouteState.RUNNING のとき、バックグラウンドスレッドで stop_route -> start_route を実行する
- ステータスバーへの再起動中メッセージ表示
- 再起動中はボタンを一時 disabled にする（TBD-2: 監督判断）

---

## 6. 制約・前提条件

- **対象モード**: openai-realtime のみ。Whisper + DeepL モードは対象外
- **再起動 UX**: 稼働中の音声出力 ON/OFF 切替には 2-5 秒の再起動が発生する（翻訳中断あり）
- **audio.output なし挙動の前提**: session.update に audio.output キーを含めなかった場合、API がデフォルトで音声を生成しないことを前提とする。実機検証を推奨（TBD-1）
- **テスト環境**: 実 API には接続せず、モック WS サーバーで session.update の送信内容を検証する
- **テストのサンプル値**: API キーには必ずフェイク値（"sk-test-fake-xxxxx" 形式）を使用すること。実 API キーはコードに絶対に含めない

---

## 7. 受け入れ条件

- [ ] request_audio_output=False のとき、接続時の session.update に audio.output キーが含まれない（モックWS検証）
- [ ] request_audio_output=True のとき、session.update に audio.output.language が含まれる（既存テスト維持）
- [ ] CaptionSystem._create_realtime_translator() が self._audio_output_mode を request_audio_output に渡す（単体テスト）
- [ ] 音声出力 OFF の系統でも session.input_transcript.delta/done（原文字幕）が正常に届く
- [ ] 音声出力 OFF の系統で session.output_audio.delta を受信しても no-op（既存動作維持）
- [ ] 稼働中に音声出力チェックを OFF にすると、ステータスバーに再起動中メッセージが表示される
- [ ] 再起動後、対象系統の翻訳が正常に再開される
- [ ] 停止中に音声出力チェックを変更しても再起動は発生しない
- [ ] 既存テスト test_request_audio_output_true_does_not_include_format が引き続きパスする
- [ ] 既存テスト test_request_audio_output_false_does_not_include_format が引き続きパスする

---

## 8. PR 分割案

### PR1: realtime_translator.py — _request_audio_output を session.update に反映

**変更ファイル**: realtime_translator.py

**変更内容**:
- _run_session 内の session.update 送信ロジックを変更（§5.1 参照）
- _request_audio_output=False のとき audio.output キーを除外する

**新規テスト** (tests/test_realtime_translator.py に追加):
- test_session_update_excludes_audio_output_when_disabled: request_audio_output=False のとき session.update の session.audio に output キーが含まれないこと
- test_session_update_includes_audio_output_when_enabled: request_audio_output=True のとき audio.output.language が含まれること

**影響範囲**: realtime_translator.py のみ

---

### PR2: main.py — request_audio_output を _audio_output_mode から決定

**変更ファイル**: main.py

**変更内容**:
- CaptionSystem._create_realtime_translator() の request_audio_output=True を request_audio_output=self._audio_output_mode に変更（§5.2 参照）

**新規テスト** (既存テストファイルまたは新規ファイルに追加):
- test_create_realtime_translator_audio_output_false_when_no_device: output_device_index=None で CaptionSystem を生成したとき、_audio_output_mode が False で _realtime_translator._request_audio_output が False であること
- test_create_realtime_translator_audio_output_true_when_device_given: output_device_index=<int> で生成したとき _request_audio_output が True であること

**影響範囲**: main.py のみ

---

### PR3: app.py — 音声出力 ON/OFF 稼働中切替の再起動結線

**変更ファイル**: app.py

**変更内容**:
- _on_route_a_output_enable_change / _on_route_b_output_enable_change callback を変更
- 稼働中の場合に stop_route -> start_route をバックグラウンドスレッドで実行
- ステータスバーへの再起動メッセージ表示

**新規テスト** (tests/test_app_gui.py または新規ファイルに追加):
- test_output_enable_off_triggers_route_restart_when_running: 稼働中に音声出力 OFF で stop_route -> start_route が呼ばれること（モック）
- test_output_enable_change_no_restart_when_stopped: 停止中の切替では再起動が発生しないこと

**影響範囲**: app.py のみ

---

## 9. ui-spec-konnyaku.md への追記事項

### 追記 1: §3.3「系統1 音声出力チェックボックス」の変更

**変更前（§3.3）:**
> - ON 時の期待動作: 出力デバイスコンボで選択中のデバイスへ音声出力を開始する。稼働中の場合は即時反映（set_output_device(index) が呼ばれる）

**変更後:**
> - ON 時の期待動作:
>   - 出力デバイスコンボで選択中のデバイスへ音声出力を開始する
>   - **音声再生の停止/開始は即時**（set_output_device で _audio_stream を切り替え）
>   - **API 側の音声トークン課金停止は再起動後**（stop_route -> start_route による接続再確立後、約 2-5 秒）
>   - 稼働中の切替時は UI に「音声出力を切り替えています...」を一時表示
>   - 停止中の場合: 値を保持するのみ

同様の変更を §3.4「系統2 音声出力チェックボックス」にも適用すること。

### 追記 2: §11「現状の実装と差分」への追記

| # | 仕様 | 現状の実装 | 優先度 |
|---|---|---|---|
| **B-16** | 音声出力 OFF 設定時、API 側の音声生成（音声トークン課金）を停止すべき | request_audio_output=True が常時設定（main.py:623）。音声出力 OFF でも音声トークン課金が発生 | **最高（W-COST-1）** |

---

## 10. マイグレーション手順（既存ユーザーへの影響）

### 設定ファイル（config.yaml）への影響
- **なし**。既存の config.yaml 項目に変更・追加なし。

### UI 操作の変化
- 音声出力 OFF で稼働中に ON へ切り替えると一時的に翻訳が 2-5 秒停止する（再起動のため）
- これは既存の「入力デバイス変更時の再起動」（set_input_device）と同等の UX

### API 側の変化
- 音声出力 OFF で稼働している系統に対して session.output_audio.delta イベントが来なくなる（想定）
- session.input_transcript.delta/done および session.output_transcript.delta/done は引き続き受信する

---

## 実装メモ（PRGちゃんへの引き継ぎ）

### 修正の核心

main.py:623 の request_audio_output=True -> request_audio_output=self._audio_output_mode が本質的な変更。
これだけで「音声出力 OFF の設定で新規接続したとき」の課金は止まる。

### PR1 の重要ポイント

realtime_translator.py:278-289 を §5.1 の通り変更する。
既存テスト test_request_audio_output_false_does_not_include_format は format キーの有無を検証しているが、
**audio.output キー全体の有無は未検証**。
新規テスト test_session_update_excludes_audio_output_when_disabled を必ず追加すること。

### PR3 のスレッド注意点

stop_route -> start_route は blocking 処理のため、DearPyGui のメインスレッドからそのまま呼ぶと UI がフリーズする。
既存の B-14 実装（_on_route_a_enable_change での即時反映）と同じパターン（バックグラウンドスレッド）で実装すること。

### TBD-1 の取り扱い（案 A の将来化）

監督が案 A の検証を優先する場合:
- 検証用ブランチで session.update に modalities: ["text"] を追加して接続中に送信し、verbose ログで session.output_audio.delta が止まるか確認する
- 確認できれば案 A で実装し直す（PR1 の変更量は案 B と大差ない）
- 案 A 実装時は RealtimeTranslator に update_audio_output(enabled: bool) メソッドを追加する

### テストのサンプル値（秘密情報漏洩防止）

テストの api_key パラメータには必ずフェイク値を使用すること。
例: "sk-test-fake-cost-w1", "sk-test-fake-audio-mode"
実 API キーはコードに絶対に含めない。

---

## codex 利用
- codex 利用: なし
