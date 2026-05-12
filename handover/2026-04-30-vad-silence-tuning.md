# ハンドオーバー: 2026-04-30（夜の続き）/ VAD 無音待機チューニング

## 現状サマリ

- **ブランチ**: `master` クリーン（push 済み）
- **最新 HEAD**: `f722d68 feat: bump VAD post_speech_silence_duration default to 0.6s`
- **最新リリース**: `v0.1.1`（master はその先に進んでいる。次は v0.1.2 候補）
- 同日午前のセッションで verbose ロギング機能を追加 → 夕方に監督が verbose ログを取得 → 本セッションで分析・チューニング

## このセッションで判明したこと（重要）

### 「翻訳が抜ける」の正体は VAD による文分断だった

`C:\tmp\rc-install-test\2026-04-30-5_verbose.txt`（140 STT 件）を文章のつながり面から解析したところ、**翻訳 API 失敗ではなく、VAD が文の途中で切ってしまい、Whisper が冒頭・末尾を取りこぼしていた**ことが判明。

**観測された具体例:**
- `Thousand people gathered...` ← `A` 欠落
- `The month he was fatally stabbed at Primrose Hill.` ← 文頭の `Earlier this` 欠落
- `Since the first attack on four ambulances...` ← 文頭欠落
- `As the Surrey Day Ambassador for 2026, which takes place.` ← 主語欠落
- `Hasn't even had the common decency to.` ← 主語と末尾の動詞欠落
- `Of arson attacks and attempted arson attacks.` ← 前置詞だけで始まる
- `At night and her work promoting science in schools.` ← `BBC's Sky at Night` の番組名欠落
- 1つの自然な文が複数の STT イベントに分かれており、それぞれ独立に翻訳 → 文脈崩壊

verbose ログとしては STT == TRANS_REQ == TRANS_OK で「失敗 0 件」だが、**VAD 段階で既に欠落しているので可視化できなかった**。

### 対処（commit `f722d68`）

`VAD_DEFAULT_SILENCE` を **0.2 秒 → 0.6 秒** に上げた。

**根拠:** 0.6 秒は人間の自然な息継ぎを超える長さ。これ未満の沈黙では文を切らず、文末の本格的な無音で確定する。Whisper の冒頭/末尾欠落が減り、翻訳エンジンに完全な文単位で渡る。

**反映先:**
- `app.py` の `VAD_DEFAULT_SILENCE = 0.6`（新規ユーザー / クリーン settings.json 用）
- `C:\tmp\rc-install-test\settings.json`: 0.2 → 0.6
- `C:\tmp\rc-fresh\settings.json`: 0.1 → 0.6
- 監督は GUI スライダー（詳細設定 → 無音待機(秒)）で個別調整可能

### .gitignore 強化

監督の指摘で `*_verbose.txt` を `.gitignore` に追加。
GitHub に翻訳・verbose ログがコミットされていないことも確認済み（`git log --all --diff-filter=A --name-only` で走査）。
commit `b7eb97a chore: gitignore *_verbose.txt`。

## 次回再開時の候補タスク

### 1. VAD 0.6 秒の効果検証（最優先）
- 監督がアプリを再起動して BBC ニュース等で verbose ログを取り直す
- 比較ポイント:
  - 文の冒頭欠落（`Thousand people` 等）が減ったか
  - 1 STT イベントあたりの文字数が増えたか
  - 文の途中分断が減ったか
  - 体感の応答遅延（発話終了から字幕表示までの時間）

### 2. それでも残る取りこぼしへの追加対策
0.6 秒で十分でなければ:
- **B**: faster-whisper の `condition_on_previous_text=True` を有効化 → 前の発話を context として渡し、断片でも文脈推測しやすくする（RealtimeSTT 経由で設定できるか要確認）
- **C**: Deepgram など連続ストリーミング系に切替（issue #2）→ そもそも VAD で切らずに連続認識
- **D**: VAD 後の短いセグメントを次の翻訳までバッファして連結する独自ロジック

### 3. v0.1.2 リリース準備
v0.1.1 以降の master の変更:
- verbose ロギング機能（commit `a5c7442`）
- `*_verbose.txt` を gitignore（commit `b7eb97a`）
- VAD silence デフォルト 0.6（commit `f722d68`）

タグ・リリースノート作成。テスター（西崎様）に再案内するかどうか監督判断。

### 4. issue #2 STT 代替検証
取りこぼしが本質的に Whisper の VAD 限界だとすると、Deepgram 等のストリーミング転記の方が筋が良い可能性。PoC 実装の優先度を上げる。

## 運用メモ

- **テスト環境**:
  - `C:\tmp\rc-install-test\` ← 監督が実運用テストに使用
  - `C:\tmp\rc-fresh\` ← 開発時の動作確認
- **モデルキャッシュ**: ASCII パスなら `./models/`、非 ASCII なら `%LOCALAPPDATA%\rc-models`
- **verbose 機能**: GUI の「Verbose ●/○」ボタンで切替、settings.json に永続化
- **API キー**: rc-install-test / rc-fresh の `config.yaml` に実キー（リポジトリ非公開）
- **重要発見ログ**:
  - `C:\tmp\rc-install-test\2026-04-30-5_verbose.txt` （文分断の証拠、要保全）
  - `C:\tmp\rc-install-test\2026-04-30-3_translate.txt` （西崎様 PJ 会議、最初に「翻訳抜け」が報告されたログ）

## 関連 issue

| # | タイトル | 状態 |
|---|---|---|
| #1 | 音声の自動ノーマライズ | Gain 機能で完了相当 |
| #2 | STT 代替エンジン検討 | 取りこぼし対策として再評価候補 |
| #3 | QA 残課題（プリロード周辺・スレッド安全性） | 未着手 |
| #4 | WinError 6 ログ抑制 | suppression 実装済み |
