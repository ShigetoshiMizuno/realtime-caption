# FAQ — realtime-caption

> **日本語は下部に記載しています。** / Japanese documentation follows below.

---

## English

Frequently asked questions about cost, bandwidth, and performance.

---

### Q1. How much does it cost to run for 1 minute?

Pricing per translation engine:

| Mode | Unit price | 1 minute | 1 hour |
|------|-----------|---------|--------|
| Whisper local (small/medium) | Local inference | $0.00 | $0.00 |
| DeepL Free (translation only) | 500k chars/month free tier | $0.00 | $0.00 |
| DeepL Pro (translation only) | $25 / 1M chars | ≈ $0.001 | ≈ $0.06 |
| OpenAI gpt-4o-mini translation + Whisper local | Token-based | ≈ $0.001–0.003 | ≈ $0.06–0.18 |
| **OpenAI gpt-realtime-translate** | $0.034 / min | **$0.034** | **$2.04** |

**Note:** `gpt-realtime-translate` is billed per minute and includes both audio input and output.
The same rate applies when `audio_output.enabled: true` (VB-CABLE simultaneous interpretation mode).

---

### Q2. Can I use it on a pocket Wi-Fi or mobile hotspot?

Estimated bandwidth consumption:

| Mode | Upload | Download | Total |
|------|--------|----------|-------|
| Whisper local + DeepL/OpenAI translation | Near zero (HTTPS only) | Near zero | KB-order |
| gpt-realtime-translate (text only) | ≈ 4 MB/min | ≈ 6 KB/min | **≈ 4 MB/min** |
| gpt-realtime-translate + audio output | ≈ 4 MB/min | ≈ 4 MB/min | **≈ 8 MB/min** |

**How the upload figure is calculated:**
- 24 kHz PCM16 mono = 24,000 samples × 2 bytes × 60 sec = 2.88 MB/min (raw)
- After base64 encoding (×1.33) = ≈ 3.84 MB/min
- Including WebSocket overhead ≈ 4 MB/min

**Mobile data estimate:** 1 GB of data lasts roughly 4 hours (text-only mode) or 2 hours (audio output mode).

---

### Q3. How much if I run it for 30 hours/month?

| Mode | Monthly cost | Monthly data |
|------|-------------|-------------|
| Whisper local + DeepL Free | $0.00 | Near zero |
| gpt-realtime-translate (text only) | **≈ $61** | ≈ 7.2 GB |
| gpt-realtime-translate + audio output (Zoom) | ≈ $61 | ≈ 14 GB |

---

### Q4. Which is faster — Whisper local or gpt-realtime-translate?

Latency comparison (from start of speech to caption display):

| Mode | First caption | Caption finalized |
|------|--------------|------------------|
| Whisper local small | 1.6–2.6 sec (after speech ends) | Same |
| Whisper local medium | 2.6–3.6 sec (after speech ends) | Same |
| **gpt-realtime-translate** | **200–500 ms (during speech)** | + 100–300 ms |

`gpt-realtime-translate` uses **streaming** — captions appear while you are still speaking.
Whisper local is batch-based: it waits for VAD silence detection (0.6 s) and then runs local inference.

---

### Q5. I'm worried about runaway costs from long sessions

PR #16 (cost protection, Issue #14) addresses this:

- Set `openai_realtime.max_session_minutes` in `config.yaml` to cap session length (default: 60 minutes, then auto-stop)
- Warning dialogs appear at configurable thresholds ($5 / $10 / $20)
- The GUI always shows elapsed time and estimated cost

---

### Q6. Can it work offline?

| Mode | Offline capable |
|------|----------------|
| Whisper local + DeepL | No (DeepL requires internet) |
| Whisper local + OpenAI translation | No (OpenAI requires internet) |
| Whisper local only (no translation) | Yes |
| gpt-realtime-translate | No (requires persistent WebSocket) |

For fully offline use, choose Whisper local without translation.

---

### Q7. Does it work behind a corporate proxy?

- `gpt-realtime-translate` requires a WebSocket connection to `wss://api.openai.com/v1/realtime/translations`
- If your corporate proxy blocks WebSocket, this mode will not work
- If regular HTTPS works but WebSocket does not, use Whisper local + DeepL/OpenAI translation mode instead

---

### Tips: Reducing costs

1. **Use the free tier**: DeepL Free gives 500k chars/month — enough for 1–2 meetings
2. **Set `max_session_minutes` low** (e.g., 30 min) to avoid forgetting to stop a session
3. **Text-only mode**: if VB-CABLE simultaneous interpretation is not needed, set `audio_output.enabled: false` to halve bandwidth
4. **Local-only is free**: Whisper local (small requires ≈ 1.5 GB RAM at startup, medium requires ≈ 3 GB)

---

### See also

- [PR #7 - gpt-realtime-translate integration](https://github.com/ShigetoshiMizuno/realtime-caption/pull/7)
- [PR #13 - VB-CABLE Zoom simultaneous interpretation](https://github.com/ShigetoshiMizuno/realtime-caption/pull/13)
- [PR #16 - Cost protection features](https://github.com/ShigetoshiMizuno/realtime-caption/pull/16)
- [Issue #14 - Cost protection requirements](https://github.com/ShigetoshiMizuno/realtime-caption/issues/14)

---
---

## 日本語

コスト・帯域・パフォーマンスに関するよくある質問。

---

### Q1. 1分動かしたらいくらかかる？

各翻訳エンジンの単価:

| モード | 単価 | 1 分 | 1 時間 |
|--------|------|------|--------|
| Whisper local（small/medium） | ローカル推論 | $0.00 | $0.00 |
| DeepL Free（翻訳のみ） | 500k 文字/月 無料枠 | $0.00 | $0.00 |
| DeepL Pro（翻訳のみ） | $25 / 1M 文字 | ≈ $0.001 | ≈ $0.06 |
| OpenAI gpt-4o-mini 翻訳 + Whisper local | トークン課金 | ≈ $0.001–0.003 | ≈ $0.06–0.18 |
| **OpenAI gpt-realtime-translate** | $0.034 / 分 | **$0.034** | **$2.04** |

**注意:** `gpt-realtime-translate` は音声入出力込みの分単価です。
`audio_output.enabled: true`（VB-CABLE 同時通訳モード）でも同じ単価が適用されます。

---

### Q2. ポケット Wi-Fi / テザリングで使える？

帯域消費の目安:

| モード | 上り | 下り | 合計 |
|--------|------|------|------|
| Whisper local + DeepL/OpenAI 翻訳 | ほぼゼロ（翻訳 HTTPS のみ） | ほぼゼロ | KB オーダー |
| gpt-realtime-translate（テキストのみ） | ≈ 4 MB/分 | ≈ 6 KB/分 | **約 4 MB/分** |
| gpt-realtime-translate + 音声出力 | ≈ 4 MB/分 | ≈ 4 MB/分 | **約 8 MB/分** |

**計算根拠（上り）:**
- 24 kHz PCM16 mono = 24,000 sample × 2 byte × 60 sec = 2.88 MB/分（raw）
- base64 エンコード後 1.33 倍 = 約 3.84 MB/分
- WebSocket オーバーヘッド込みで約 4 MB/分

**テザリング目安:** 1 GB プランで約 4 時間（テキストのみ）、約 2 時間（音声出力モード）。

---

### Q3. 30 時間/月使ったらいくら？

| モード | 月額 | 月間データ |
|--------|------|-----------|
| Whisper local + DeepL Free | $0.00 | ほぼゼロ |
| gpt-realtime-translate（テキストのみ） | **約 $61** | 約 7.2 GB |
| gpt-realtime-translate + 音声出力（Zoom） | 約 $61 | 約 14 GB |

---

### Q4. Whisper local と gpt-realtime-translate、どっちが速い？

レイテンシ比較（発話開始から字幕表示まで）:

| モード | 最初の字幕 | 字幕確定 |
|--------|-----------|---------|
| Whisper local small | 1.6–2.6 秒（発話後） | 同左 |
| Whisper local medium | 2.6–3.6 秒（発話後） | 同左 |
| **gpt-realtime-translate** | **200–500 ms（発話中）** | + 100–300 ms |

`gpt-realtime-translate` は**ストリーミング型**で発話中から字幕が表示されます。
Whisper local は VAD 無音検出（0.6 秒）＋ローカル推論待ちのバッチ処理です。

---

### Q5. 長時間動かして高額請求にならないか心配

PR #16（コスト保護機能、Issue #14）で対策済みです:

- `config.yaml` の `openai_realtime.max_session_minutes` で最大稼働時間を設定（デフォルト 60 分で自動停止）
- 警告閾値（$5 / $10 / $20）でモーダル表示
- GUI に経過時間と想定コストを常時表示

---

### Q6. オフライン環境で使える？

| モード | オフライン可 |
|--------|------------|
| Whisper local + DeepL | 不可（DeepL はネット必要） |
| Whisper local + OpenAI 翻訳 | 不可（OpenAI はネット必要） |
| Whisper local のみ（翻訳なし） | 可 |
| gpt-realtime-translate | 不可（WebSocket 常時接続必要） |

完全オフラインなら Whisper local のみ（翻訳なし）を選んでください。

---

### Q7. プロキシ・企業ネットワーク内で使える？

- `gpt-realtime-translate` は `wss://api.openai.com/v1/realtime/translations` への WebSocket 接続が必要です
- 企業プロキシで WebSocket がブロックされる場合は使用できません
- 通常の HTTPS は通るが WebSocket は通らない環境では Whisper local + DeepL/OpenAI 翻訳モードを推奨します

---

### Tips: コスト最適化

1. **無料枠を活用**: DeepL Free は 500k 文字/月 無料。会議 1〜2 回なら余裕で収まります
2. **`max_session_minutes` を短く設定**（例 30 分）して切り忘れを防止
3. **テキストのみモード**: VB-CABLE 同時通訳が不要なら `audio_output.enabled: false` で帯域を半減
4. **ローカルのみは無料**: Whisper local（small で起動時メモリ約 1.5 GB、medium で約 3 GB）

---

### Tips: 設定変更の反映について

稼働中に設定を変更したとき、各項目の反映タイミングは以下のとおりです。

| 設定項目 | 反映タイミング |
|---|---|
| 翻訳先言語（系統A/B） | **即時反映**（稼働中は系統を自動再起動） |
| 音声出力 ON/OFF | 即時反映（自動再起動） |
| 原文表示 ON/OFF | 即時反映（自動再起動） |
| 入力デバイス | 即時反映（内部 stop→start） |
| 出力デバイス・音量 | 即時反映 |
| API キー | 次回起動時（稼働中変更は「次回起動時に反映」と GUI 表示） |

稼働中に翻訳先言語を変更すると、ステータスバーに「系統N 翻訳先言語切替中...」と表示され、
バックグラウンドで自動的に再起動して新しい言語が反映されます。
停止中に変更した場合は settings.json に保存のみ行われ、次回起動時に反映されます。

---

### 関連リンク

- [PR #7 - gpt-realtime-translate 統合](https://github.com/ShigetoshiMizuno/realtime-caption/pull/7)
- [PR #13 - VB-CABLE Zoom 同時通訳](https://github.com/ShigetoshiMizuno/realtime-caption/pull/13)
- [PR #16 - コスト保護機能](https://github.com/ShigetoshiMizuno/realtime-caption/pull/16)
- [Issue #14 - コスト保護要件](https://github.com/ShigetoshiMizuno/realtime-caption/issues/14)
