# CHANGELOG

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] — 2026-05-16

### 🚨 OpenAI Realtime API GA 移行対応 (Critical)

OpenAI が 2026-05-12 に Realtime API Beta を廃止し GA 版に移行。本アプリは Beta 時代の仕様に依存していたため、4 日間翻訳テキストが表示されない不具合が発生していた。GA 仕様に追従。

- **GA 仕様**: `session.update` で `audio.output.language` のみが必須。
- **原文受信**: `audio.input.transcription` + `audio.input.noise_reduction` を**セット**で指定すると `session.input_transcript.delta` イベントが発火する（実機検証 2026-05-16 で確認）。
- **VAD 廃止**: `audio.input.turn_detection` は GA で仕様外（Unknown parameter エラー）。W-COST-3 機能を実質廃止。
- 関連 PR: #131 (緊急修正), #133 (GA 移行), #134 (原文復活), #135 (VAD UI 削除)

### Added

- **PTT (Push-to-Talk) モード**: F8 押下中だけ系統 B を起動してコスト削減 (#83-86)
- **クォータ確認 Web ボタン**: 詳細設定タブから OpenAI Usage ページを開く (#87)
- **課金状態ランプ**: 🟢/🟡/🔴 で両系統の API 課金状態を可視化 (#100)
- **起動進捗ログ**: `[STARTUP]` プリフィックスで 14 ステップを計測 (#105, #120)
- **アイドル切断**: 5 分無発話で自動的に API 切断 (#108-110)
- **3 分割ログ**: `[USER]` / `[ACTION]` / `[RPC]` で「押したのに処理が走らない」を自動検出可能 (#122)
- **VERBOSE モード徹底ログ**: WS 全 payload / state 遷移 / GUI callback / RPC + traceback (#123, #126, #129)
- **ログ異常検出ツール** `tools/log_anomaly_detector.py` (#124)
- **実機検証自動化ツール** `tools/auto_verify_source_transcript.py` (#130)
- **GitHub Actions CI**: `pytest` を Windows runner で実行 (#127)
- **多数の半自動化ツール群**: `measure_startup.py` / `measure_audio_noise.py` / `test_vad_api_smoke.py` / `test_ptt_e2e.py` (#112-115)

### Changed

- **GUI 縦長を当初の 42% に圧縮** (33 行 → 14 行):
  - 第 1 弾 (#135): VAD UI 削除 (-6 行)
  - 第 2 弾 (#136): アイドル設定を詳細設定タブへ (-4 行)
  - 第 3 弾 (#137): 系統 1/2 を TabBar 化 (-9 行)
- **言語切替の動的反映** (#103): 稼働中に翻訳先言語を変更すると自動で再起動して即反映
- **設定変更の再起動結線共通化**: `_restart_route_for_change(route_id, reason)` ヘルパー導入 (#94)
- **W-COST-1 音声出力 OFF 時の API 課金停止** (#89-91): `audio.output` を条件付き送信
- **W-COST-2 原文表示 ON/OFF** (#92-93, #134): GA 版でも `transcription` + `noise_reduction` セット指定で動作
- **W-COST-4 audio_threshold** デフォルト 100 → 200 (実機 RMS 統計に基づく) (#119)
- 日本語フォント表示修正: `add_font_range_hint(Japanese)` 明示追加 (#116)

### Fixed

- **GUI 全体日本語文字化け** (hotfix #116): DearPyGui で日本語が `?` 化していた問題
- 入力デバイス列挙時の cp932 UnicodeEncodeError (`Realtek(R)` 等) (#117)
- `subprocess.PIPE` で子プロセスがブロックする問題 (実機検証ツール内) (#117)
- VAD パラメータ境界値の API 拒否 → クランプ + WARN 警告 (#107)
- flaky テスト: `test_reconnect_after_disconnect` 安定化 (#96)
- flaky テスト: `_restart_locks` のテスト間干渉解消 (#138)
- 課金ランプの起動直後表示が固まる問題 (#104)
- 翻訳先言語変更が稼働中に反映されない問題 (#103 W-1)

### Removed

- VAD UI (W-COST-3): GA で仕様外のため GUI ウィジェット削除 (#135)
- `_restart_route_for_audio_output_change` 廃止ラッパー (#95)

### Deprecated

- `RealtimeTranslator.vad_enabled` / `vad_threshold` / `vad_silence_duration_ms` / `vad_prefix_padding_ms` パラメータは GA 版で効果なし。互換性のため属性は残置。将来の breaking change で削除予定。

### Security

- gitleaks workflow を全 PR で実行 (継続)
- API キー redact 関数 `_redact_secrets` を RPC verbose ログに適用 (#129)
- gpt-realtime-translate の Bearer 認証ヘッダーから API キーが漏洩しない設計を維持

### テスト

- 555 件 → **1452 件** (約 2.6 倍に拡充)
- スキップ件数: 80 件 (W-COST-3 廃止関連)
- GitHub Actions CI で全件実行 (#127)

### 既知の制約

- W-COST-3 (VAD): GA 移行で完全に動作不能。将来 OpenAI が turn_detection を別パスでサポートした場合に再実装検討。
- アイドル切断の audio_threshold は環境依存。実マイクで `tools/measure_audio_noise.py` で測定推奨。

### 関連ドキュメント

- `docs/spec/cost-w-cost-2-design.md`: 原文表示の正しい session.update payload
- `docs/spec/cost-w-cost-3-design.md`: VAD 廃止経緯
- `docs/spec/settings-reflection-matrix.md`: 設定変更の反映タイミング
- `docs/spec/ptt-mode-design.md`: PTT 設計
- `docs/spec/residence-model-design.md`: 常駐モデル設計
- `docs/spec/ui-spec-konnyaku.md`: UI 仕様

