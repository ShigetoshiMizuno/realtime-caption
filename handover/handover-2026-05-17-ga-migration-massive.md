# ハンドオーバー: 2026-05-16〜17 大規模 GA 移行 & QA 自動化

> 作成: 2026-05-17 (Claude Code 助監督ちゃん)
> 前回ハンドオーバー: `2026-04-30-vad-silence-tuning.md`

## このセッションの主要成果

### 🚨 最重要: OpenAI Realtime API GA 移行への対応

**2026-05-12 に OpenAI が Realtime API Beta を廃止 → GA 移行**。本アプリは Beta 時代の cookbook (2026-05-07) に準拠していたため、4 日間翻訳テキストが全く表示されない不具合が発生。

#### 真因と修正
- **Beta**: `audio.input.transcription.model: gpt-realtime-whisper` 必須
- **GA**: `audio.output.language` のみで OK、`turn_detection` は仕様外で送ると Unknown parameter エラー
- **実機検証で判明**: `audio.input.transcription` + `audio.input.noise_reduction` を**セット**で送ると `session.input_transcript.delta` が来る（noise_reduction なしだと来ない）
- → ただし Grok 回答では「output.language のみで input/output 両方発行」とのこと。**監督から最小構成（noise_reduction 不要）の Grok 回答が再共有された**（コピペ間違いと言われたが、内容は正）。実機で動作している現状は変えない判断。

関連 PR:
- #131 hotfix: VAD 強制 OFF + smoke 判定強化
- #133 GA 移行緊急修正
- #134 原文受信復活（noise_reduction セット）
- #135 VAD UI 完全削除
- #141 VAD パラメータ完全削除

### 🚨 緊急修正: dpg コールバック全滅バグ

PR #126 (`_verbose_callback` デコレータ) の副作用で **dpg が起動時に引数 0 個で callback を呼ぶケース**に未対応 → 全 callback で TypeError → 設定変更が一切効かない状態だった。

監督報告:
> 日本語選んだはずだけど英語で出るし、チェックボックス類が効いているか怪しい

→ **PR #143 で `inspect.signature` 経由の引数補完で完全修正**。

### 🛡️ QA 自動化: コントラクトテスト + 起動 smoke

監督指示:
> こういう不具合を自動で行うテスト、QA で見つけて修正してほしい

→ Issue #144 + **PR #145 で実装**:
- `tests/test_callback_contracts.py`: 全 `_on_*` callback を inspect で抽出、引数 0/1/2/3 個で TypeError を出さないことを assert
- `tests/test_app_startup_smoke.py`: subprocess で `python app.py --auto-konnyaku=2` を実起動、stderr に `TypeError:` が無いことを assert
- `pytest -m smoke` で分離実行、CI に専用 step 追加
- **PR #143 を revert すると新テストが必ず FAIL する**ことを実証済み

### 📐 GUI 縦長解消 (33 行 → 14 行)

| Phase | PR | 削減 |
|---|---|---|
| 第 1 弾 | #135 | VAD UI 削除 (-6 行) |
| 第 2 弾 | #136 | アイドル設定を詳細設定タブへ (-4 行) |
| 第 3 弾 | #137 | 系統 1/2 を TabBar 化 (-9 行) |

### 🧹 その他の主要成果

- **CI 拡張 PR #127**: pytest を GitHub Actions Windows runner で実行（従来は gitleaks のみ）
- **3 分割ログ PR #122**: `[USER]` / `[ACTION]` / `[RPC]` で「押したのに処理が走らない」自動検出可能に
- **VERBOSE 強化 PR #123/#126/#129**: WS 全 payload, state 遷移, GUI callback, RPC + traceback
- **ログ異常検出ツール PR #124**: `tools/log_anomaly_detector.py`
- **実機検証自動化 PR #130/#140**: `tools/auto_verify_source_transcript.py` (mtime 検出も対応)
- **flaky テスト解消 PR #138**: `_restart_locks` の autouse fixture
- **issue 10 件 close**: #80, #82, #99, #101, #102, #111, #121, #31, #81, #66

## 現在の状態

- master 最新: PR #145 マージ済（コミット未確認、git log で確認要）
- テスト: **1500+ 件 GREEN** / 80 件 SKIPPED（VAD 廃止関連）
- CI: gitleaks + pytest (Windows runner) + smoke test
- 未マージブランチ: なし

## 残課題（次セッション用）

### 監督確認待ち
1. **実機検証**: PR #143 修正後の master で「日本語選択」「チェックボックス」が効くか
2. **系統 B (マイク → 英語)** の動作確認（系統 A の LG 4K Loopback は動作確認済み）

### 残 issue (open)
- **#72**: 入力デバイス復元バグ（実機確認要）
- **#142**: UI 整理 包括的見直し（初心者/上級者モード等）
- **#57**: Rust 化検討（長期）
- **#5**: UI ブラッシュアップ（部分対応中）
- **#2**: STT エンジン検証（古い、廃止検討）
- **#144**: QA 自動化 Phase 3 (仕様書 + agent 定義更新)

### 設計負債
- W-COST-3 (VAD) 関連: GA で完全廃止、`vad_*` パラメータは `**deprecated_kwargs` で無視
- `_konnyaku_running` → `threading.Event` 化（Phase C S-2 残）
- `SubtitleBroadcaster.client_count` の lock (Phase C S-3 残)

## エージェント定義更新

新ルール（このセッションで実装）:
- **SPECちゃん**: 「結合点（Integration Point）」セクション必須化（PR #103 W-1 事案を受けて）
- **PRGちゃん**: 「配線チェック」を完了基準に追加
- **QAちゃん**: 「新規シンボル配線チェック」「テスト種別バランス」「秘密情報パターンスキャン」を必須化

すべて `~/.claude/agents/` に反映済み。

## 検証ツール一覧 (`tools/`)

| ツール | 用途 |
|---|---|
| `auto_verify_source_transcript.py` | 実機検証自動化（app.py --verbose --auto-konnyaku + TTS + verbose 解析） |
| `log_anomaly_detector.py` | `[USER]` → `[ACTION]` ペアリング等の異常パターン検出 |
| `measure_startup.py` | 起動シーケンス各ステップの所要時間計測 |
| `measure_audio_noise.py` | マイク 30 秒録音 + RMS 統計（TBD-4-2 解決用） |
| `test_vad_api_smoke.py` | 実 API への VAD smoke（GA で turn_detection 拒否を確認） |
| `test_source_transcript_smoke.py` | 30 秒音声送信 + input_transcript.delta 受信確認 |

## 重要な参照

### ドキュメント
- `CHANGELOG.md` - 2026-05-16 大規模変更のまとめ
- `docs/spec/cost-w-cost-2-design.md` - 原文表示の正しい session.update（GA 実機検証済）
- `docs/spec/cost-w-cost-3-design.md` - VAD 廃止経緯
- `docs/spec/settings-reflection-matrix.md` - 設定変更の反映タイミング
- `docs/spec/ptt-mode-design.md` - PTT 設計
- `docs/spec/residence-model-design.md` - 常駐モデル設計

### 重要コード
- `app.py:149` - `_verbose_callback` デコレータ（引数補完ロジック）
- `app.py:_log_user/_log_action/_log_rpc` - 3 分割ログヘルパー
- `realtime_translator.py:323` - GA 版 session.update payload（条件付き input transcription）
- `main.py:_create_realtime_translator` - VAD パラメータ受入無視

### 重要 PR 履歴
セッション中で **20 PR** マージ。
最重要: #131, #133, #134, #143, #145

## 監督タスク

1. **実機検証**: PR #143 後の動作確認（日本語選択、チェックボックス）
2. **系統 B (マイク)** の動作確認
3. **#142 UI 整理** の方針決定（初心者/上級者モード等）
4. **#72 入力デバイス復元バグ** の再現確認

## 申し送り事項

- このセッションで **4 時間自律動作 + 緊急対応** を実行
- 累計 PR 数: 20+
- ホットフィックス 2 件（#116 文字化け、#143 callback bug）
- 未着手の長期 issue: #57 (Rust 化), #2 (STT 検証)

Discord 連携は使用していないため、`#lobby` への完了通知はスキップ。
監督が GUI で実機検証を進める段階に入っている。
