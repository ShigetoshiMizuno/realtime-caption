# HANDOVER - 2026-05-12 (Session 2)

## What We Were Doing (1-3文)

OpenAI gpt-realtime-translate モードの実機 E2E テスト中に発覚した 3 連鎖バグ（訳文スペイン語化 → 原文空欄 → 翻訳重複表示）を順次修正・PR 化。
その後、監督から「2 時間留守にする」指示を受けた自律進行モードで QA 指摘の Suggestion 対応・既存 Issue 棚卸し・別 Issue 化までを実施。
さらに「どんどん進めて」指示を受け、flaky test 解消 PR #33 を作成、Issue #4 の現状確認・コメント追加まで進めた。
最終結果: 3 PR (#29, #32, #33) push 済み、2 Issue (#30, #31) 作成・1 Issue (#4) コメント追加。

## Current State (最重要！)

- Branch: `master` clean、3 つの feature ブランチが push 済み:
  - `fix/realtime-translate-format-and-streams` → PR #29
  - `refactor/unify-test-api-key-format` → PR #32
  - `fix/flaky-test-port-isolation` → PR #33
- Working / Functional:
  - **gpt-realtime-translate モードが完全動作**（監督実機確認済み、21:16 時点で独立ストリーム表示 OK）
  - JP（日本語訳）が正しく表示される
  - EN（原文）が `session.input_transcript.*` 経由で表示される
  - 翻訳の重複表示が解消（API の独立ストリーム性に合わせた UI 設計）
  - overlay.html も独立ストリーム対応
- Broken / Incomplete / Untested:
  - **PR #29 / PR #32 がレビュー・マージ待ち**（監督判断）
  - **VB-CABLE 経由の Zoom 音声出力 E2E は未確認**（format 削除で API のデフォルト音声形式になるため、Zoom に届くか実機検証必要）
  - **max_session_minutes 動作確認は未実施**（コスト保護 Phase E）
  - 既存 `test_reconnect_after_disconnect` が flaky（Issue #30 で記録）→ 本 PR と無関係
- Build / Test / Run status:
  - `pytest tests/`: PR #29 ブランチで 95 件 PASS、master ベースで 94 件 PASS
  - flaky: `test_reconnect_after_disconnect` が 5 回中 1 回程度 FAIL（suite 全体実行時のみ、単発 PASS）
  - pre-commit gitleaks: PR #29 / PR #32 とも Passed
- Recent commits (最新3件・本セッション分):
  - `e5535c1` test: unify test API key format to sk-test-fake-* (PR #32)
  - `641413b` fix(realtime): output format / input transcription / independent streams (PR #29)

## Key Decisions & Rationale

- **`audio.output.format` 削除（PR #29 修正 1）** → サーバが `Unknown parameter` で session.update 全体を拒否し、`language: "ja"` まで無効化されていた真因。format 指定はサーバデフォルトに任せる。
- **`audio.input.transcription.model: "gpt-realtime-whisper"` 明示指定（PR #29 修正 2）** → 公式 Cookbook 通り。これがないと input transcription が OFF になり原文イベント `session.input_transcript.*` が一切送られない。参照: <https://developers.openai.com/cookbook/examples/voice_solutions/realtime_translation_guide>
- **原文・翻訳の独立ストリーム化（PR #29 修正 3）** → API は原文（短い単位で頻繁）と翻訳（まとまった単位で遅延）を別レートでストリームするため 1:1 ペアリング不可能。`broadcast("", text)` / `broadcast(text, "")` で片側空送信に変更、UI / overlay 側で片側空対応。
- **flaky test を Issue 化して ship 続行** → 本 PR で変更していない既存テストの flaky なので独立対応。Critical 0 だった QA 判定を尊重。
- **`_request_audio_output` フラグの完全削除は Issue 化** → API シグネチャ破壊（破壊的変更）なので監督判断必要。自律モードでは触らず Issue #31 化のみ。
- **テスト API キーの統一は別 PR (#32)** → S3 対応。`.gitleaks.toml` allowlist regex `sk-test-fake-[A-Za-z0-9]+` に合わせるためハイフン無しサフィックス（`deltadone`, `feedaudio` 等）を採用。
- **handover-2026-05-12.md を `.done.md` にリネーム** → CLAUDE.md 規約通り。git では「削除」、`.gitignore` で `.done.md` は除外。PR #29 に含めた。

## Files Changed This Session (影響度順)

### PR #29 (`fix/realtime-translate-format-and-streams`)
- `realtime_translator.py`: session.update から `audio.output.format` 削除、`audio.input.transcription.model` 追加、docstring 更新
- `main.py`: `_on_realtime_transcript` を `broadcast("", text)`、`_on_realtime_source_transcript` を `broadcast(text, "")` に変更
- `app.py`: `_append_log_item` で original/translated 片側空に対応、`on_result` の `print` も空文字スキップ
- `overlay.html`: `showSubtitle` で片側空時は前回値維持
- `tests/test_realtime_translator.py`: `test_request_audio_output_true_does_not_include_format` で挙動反転、`test_session_update_includes_input_transcription` 新規追加（ポート 19779）
- `handover/handover-2026-05-12.md`: 削除（`.done.md` 化）

### PR #32 (`refactor/unify-test-api-key-format`)
- `tests/test_realtime_translator.py`: 5 箇所の `api_key="sk-test"` → `sk-test-fake-{suffix}`

### PR #33 (`fix/flaky-test-port-isolation`)
- `tests/test_realtime_translator.py`: 14 箇所のハードコードポート (19765-19778) を `_get_free_port()` による OS 動的割り当てに置換
- `_start_mock_server_in_thread` の戻り値を 3 タプル → 4 タプル化（`port` を返却）
- 検証: 5 回連続実行で 94 件全 PASS（修正前は約 80% PASS）
- Closes #30

## Blockers, Gotchas & Workarounds

- **session.update が無効パラメータ 1 つで全体拒否される** → サーバが寛容ではなく、`audio.output.format` のような未対応キーがあると `language` 含め session.update 全体を捨てる。**API ドキュメント未掲載のパラメータは送るな。**
- **input transcription はデフォルト OFF** → 明示的に `audio.input.transcription.model` を指定しないと `session.input_transcript.*` イベントは送られない。前 PR #26 では SPECちゃん類推で「自動で来る」と判断していたが誤り。Cookbook で確定。
- **原文・翻訳のストリームが独立** → 1:1 ペアリング設計は破綻する。原文は細切れに頻発、翻訳はまとまって遅延。UI はインタリーブ表示で対処。
- **flaky test_reconnect_after_disconnect** → 単発 PASS / suite 実行で 5/1 程度 FAIL。ポート競合（19772 → 19779 変更）でも解消せず別原因の可能性。Issue #30 で追跡。

## Key Learnings & Gotchas (長期記憶へ移行推奨)

- **OpenAI Realtime Translate API は session.update に厳密** → 未対応パラメータがあると全体拒否、エラーレスポンスは出るが session.updated が来ないので一見気付きにくい。verbose ログの `RT_ERROR` を見れば判明する。
- **gpt-realtime-translate は input/output 別レート設計** → 原文音声を細かい単位で書き起こし、翻訳出力はまとまった単位でストリーム。「翻訳と原文をペアにする」UI は機能しない。
- **Cookbook が SPEC より正確な場合あり** → 公式 API リファレンスに記載がなくても Cookbook には実用例が載っていることがある。WebFetch を Cookbook URL に向けて取得すると良い。
- **gitleaks allowlist regex の境界** → `sk-test-fake-[A-Za-z0-9]+` はハイフン非対応。テスト fixture 名はハイフン無し連結形式が安全。
- **空 string broadcast での片側更新パターン** → 独立ストリーム UI で `broadcast("", x)` / `broadcast(x, "")` は実装シンプルで読みやすい。受信側に `if x:` チェック必要。

## Next Steps (優先度順・具体的！)

### High（次セッション最優先）
- **PR #29 / PR #32 / PR #33 のレビュー・マージ判断**（監督）
  - 推奨マージ順: PR #29 (コア) → PR #33 (flaky test) → PR #32 (キー命名)
  - PR #33 / PR #32 は master ベースのため PR #29 マージ後にリベース可能性あり
- **VB-CABLE 経由の Zoom 音声出力 E2E**: format 削除後にサーバデフォルト形式（PCM16 か別形式か）で音声が `audio_output.py` の前提と合うか実機検証
- **OpenAI API キー revoke + 新規発行**（監督指示要、漏洩は前 PR #26 セッションから継続）
- **Issue #4 (WinError 6) の Close 判断**: 既に `main.py:82-95` で対処済み。実機で再現しないなら Close 可

### Medium（今週）
- **既存 Issue #1 音声入力自動ノーマライズ**: AGC 実装は未着手。GUI の入力ゲインスライダーで暫定対応中
- **既存 Issue #3 QA 残課題**: スレッド安全性（GUI/capture 間の atomic read/write）。free-threaded Python 3.13+ で必須化
- **既存 Issue #5 UI ブラッシュアップ**: 抽象的。PR #25/#27 で部分対応済、追加要望次第
- **GUI の Host API 変更を settings.json に永続化**（前ハンドオーバーから継続）
- **Issue #30 flaky test 解消**: → PR #33 で対処（マージ判断待ち）
- **Issue #31 `_request_audio_output` 削除リファクタ**

### Low
- **既存 Issue #2 STT エンジン追加検証**: OpenAI Realtime 統合で必要性低下。Whisper 利用継続派には依然有効
- **既存 Issue #4 WinError 6 ログ抑制**: 機能影響なし
- PyInstaller 実ビルド確認、強い暗号化への移行、NongSoft-LLC org の Team plan upgrade

## Risks & Warnings

- **OpenAI API キーは漏洩済み**（前セッション継続）。Usage Dashboard 監視推奨
- **VB-CABLE 出力の音声フォーマット確認未済** → format 指定削除でサーバデフォルト形式に依存。`audio_output.py` が PCM16 24kHz 前提なので、もし別形式が来たら音が出ない・歪む可能性
- **gpt-realtime-translate API 仕様変動継続**: 2026 年リリース直後、verbose ログ `RT_RAW_UNKNOWN` フォールバック維持
- **config.yaml を Read しないルール継続**: 過去 2 回事案、全エージェント定義に永続化済み
- **本 PR は 1 コミットにまとめている** → TDD の RED/GREEN コミット分割を遵守していない（QA W2 指摘、監督判断で許容）

## Context Gaps

- **CLEAR**:
  - 3 連鎖バグの原因と修正、95 件テスト PASS、PR #29/#32 push 済み
  - 監督による実機 GUI 動作確認（独立ストリーム表示）
  - QA レビュー結果（Critical 0、W1/S2 対処、W2/S1/S3 別対応）
  - Issue #30 (flaky) / #31 (deadcode) の Issue 化
- **FUZZY**:
  - VB-CABLE 経由の音声フォーマット整合性（実機検証未済）
  - flaky test_reconnect_after_disconnect の真因（ポート以外の原因）
  - 既存 Issue #1-#5 の優先度（監督判断）
- **GAPS / UNKNOWNS**:
  - max_session_minutes 到達時の shutdown 実機動作
  - PR #29/#32 のマージ順序・コンフリクト有無
  - 長時間稼働時のメモリ・パフォーマンス

## Next Session Instructions

このHANDOVER.mdを最初に全文読み込み、状態・ブランチ・未完了タスク・Gotchasを完全に把握してから作業続行。矛盾・不明点は即質問。secretsは絶対出力しない。

**監督への報告ポイント:**
- PR #29: コア修正、3 バグ修正、テスト 95 件 PASS
- PR #32: テストキー統一（QA Suggestion S3 対応）
- Issue #30: flaky test 別追跡
- Issue #31: `_request_audio_output` deadcode 別追跡

**特に注意:**
- `config.yaml` を Read しないこと（過去 2 回事案）
- secrets を絶対に raw_log / handover / commit / memory に書かない
- VB-CABLE 経由の Zoom 音声出力テストは監督の実機環境必要

**監督の状況:**
- 2026-05-12 19:00 頃から 22:00 頃の 3 時間で 1 セッション
- 後半 2 時間は留守、自律進行モード
- OpenAI Realtime モードの実機 E2E で 3 バグを順次発見・修正
- VB-CABLE インストール済み、Zoom 同時通訳デバイス化のフロー検証は次回

raw_log: `../raw_logs/2026-05-12_session.md`
