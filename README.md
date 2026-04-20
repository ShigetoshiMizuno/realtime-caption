# Realtime Caption & Translation System

> **日本語は下部に記載しています。** / Japanese documentation follows below.

---

## English

Real-time audio captioning and translation desktop app for Windows + OBS.
Captures PC audio via WASAPI loopback, transcribes with Whisper (faster-whisper, CPU int8),
translates via OpenAI / DeepL, and streams the resulting captions to OBS Browser Source over WebSocket.

**No virtual audio cable required.**
Desktop audio is captured directly via WASAPI loopback — no VB-Audio Virtual Cable or similar needed.

### Requirements

- Windows 10 / 11
- OpenAI API key **or** DeepL API key (one of them)
- Internet connection (first launch only)
- ~3 GB of free disk, ~2 GB RAM

Python does not need to be installed — an embeddable Python 3.11.9 is downloaded and configured by `start.bat` on first launch.

---

### Setup

#### 1. Configure API key(s)

```bat
copy config.yaml.example config.yaml
```

Open `config.yaml` and set one (or both) of the API keys. `translation.translation_model` decides which engine is used (default `deepl`):

```yaml
openai:
  api_key: "sk-xxxxxxxxxxxxxxxxxxxx"   # used when translation_model: "openai"

deepl:
  api_key: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"   # used when translation_model: "deepl"

translation:
  translation_model: "deepl"   # "openai" or "deepl"
```

- OpenAI key: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- DeepL key: [deepl.com/pro-api](https://www.deepl.com/pro-api) (free tier keys end with `:fx`)

The GUI will only enable engines whose API key is filled in.

#### 2. Double-click `start.bat`

On first launch, setup runs automatically:

- Downloads Python 3.11.9 (~10 MB)
- Installs required packages (~2 GB)
- Downloads Whisper `small` + Silero VAD (~500 MB)

> First launch downloads approximately **2.5 GB**. Estimated time: 10–30 minutes.
> From the second launch onward the GUI starts in seconds.

Once setup finishes, the GUI window (`リアルタイム字幕・翻訳`) opens.

---

### Using the GUI

![GUI layout](docs/gui.png) *(if a screenshot is available)*

**Main toolbar (always visible):**

| Control | Purpose |
|---------|---------|
| 音声入力 (Device) | Audio device. Pick a `[Loopback]` item to capture desktop sound. |
| 開始 / 停止 (Start / Stop) | Start / stop capture & recognition. |
| 入力ゲイン (Gain) | `off` / `manual` (1x-20x slider) / `auto` (AGC targeting 60% peak). |
| 音量 (Level meter) | Real-time input meter, color graded (green / yellow / red). Shows applied gain as overlay. |
| ログクリア (Clear log) | Clears the on-screen caption log. |

**Status bar:**

- `■ 待機中` / `● 録音中` — overall state
- `認識 ○` / `認識 ●` — STT lamp (Whisper busy indicator)
- `翻訳 ○` / `翻訳 ●` — translation lamp (OpenAI / DeepL request in flight)
- `OBS接続` — number of connected WebSocket clients
- `RPC` — local RPC endpoint URL

**Collapsible 詳細設定 (Advanced) section:**

| Control | Purpose |
|---------|---------|
| 認識モデル (Model) | `small` (fast) / `medium` (more accurate) |
| 翻訳エンジン (Engine) | `openai` / `deepl` (only engines with a valid key are selectable) |
| 発話検出感度 (VAD sensitivity) | Lower = more permissive (picks up quieter speech). |
| 無音待機 (Post-speech silence, sec) | Silence duration to decide a sentence has ended. |
| 既定値に戻す (Reset) | Restores VAD defaults. |

### Selecting the right loopback device

```
音声入力:
  [25] スピーカー (Realtek Audio) [Loopback]
  [26] LG HDR 4K (NVIDIA HDMI) [Loopback]   ← monitor speakers via HDMI
  [27] CABLE In 16ch (VB-Audio Virtual Cable) [Loopback]
```

Pick the loopback that matches your actual audio output. If you're hearing sound through HDMI monitor speakers, choose the monitor loopback; if through the PC's speakers/headphones, choose the onboard (Realtek etc.) loopback.

The level meter should light up while audio is playing. If it stays at 0%, the app is listening to the wrong endpoint — try another `[Loopback]` entry.

---

### `config.yaml` reference

| Key | Description | Default |
|-----|-------------|---------|
| `openai.api_key` | OpenAI API key (used when `translation_model` = `openai`) | `your-api-key-here` |
| `deepl.api_key` | DeepL API key (used when `translation_model` = `deepl`) | — |
| `translation.translation_model` | `"openai"` or `"deepl"` | `deepl` |
| `translation.target_language` | Target language name (used in the OpenAI system prompt) | `日本語` |
| `translation.system_prompt` | OpenAI system prompt template | (see example) |
| `whisper.model` | `small` / `medium` | `small` |
| `whisper.language` | Input language (`"en"`, `"ja"`, `null` = auto) | `null` |
| `whisper.compute_type` | `int8` (CPU, fast) / `float16` (GPU) / `float32` | `int8` |
| `whisper.device` | `cpu` / `cuda` / `auto` | `cpu` |
| `vad.silero_sensitivity` | Silero VAD threshold (0-1, **lower = more sensitive**) | `0.4` |
| `vad.post_speech_silence_duration` | Seconds of silence required to end an utterance | `0.6` |
| `websocket.host` / `websocket.port` | Caption broadcast endpoint for OBS | `localhost:8765` |
| `rpc.port` | Local HTTP RPC port (status / remote start-stop) | `8767` |
| `output.log_dir` | Directory for per-session translation logs | `.` |

GUI-side overrides (device / model / engine / gain / VAD sliders) are persisted to `settings.json` and override the `config.yaml` values at runtime.

Translation logs are saved as `YYYY-MM-DD-N_translate.txt`.

---

### OBS Browser Source setup

Add a **Browser** source to your OBS scene:

| Setting | Value |
|---------|-------|
| Local file | ✔ |
| File path | `<this folder>\overlay.html` |
| Width | `1920` |
| Height | `1080` |
| Shutdown source when not visible | ✔ recommended |

Start capture in the GUI and preview the scene in OBS — captions should appear (top line: original in grey, bottom line: translation in white).

---

### Local HTTP RPC (optional)

For external tooling / automation, a small JSON API listens on `localhost:8767`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | state, device, model, ws_clients, audio_peak, gain |
| GET | `/api/audio` | real-time peak / gain_mode / chunks_per_sec |
| GET | `/api/log` | last 100 transcription entries |
| GET | `/api/devices` | list of available audio devices |
| POST | `/api/start` | `{"device_index": N, "model": "small"}` (both optional) |
| POST | `/api/stop` | stop capture |

### CLI mode

Power users can run the legacy console flow (no GUI) via:

```bat
start.bat --cli
```

---

### Notes for Japanese / non-ASCII project paths

If the project folder contains non-ASCII characters, models are cached under
`%LOCALAPPDATA%\rc-models` instead of `.\models\` to avoid Windows `fopen` limitations
in PyTorch / ctranslate2. This is transparent but the first launch in such a location
will re-download models.

---

### Troubleshooting

**No audio recognized**
- Check the 音量 (Level) meter — if it's 0% while sound is playing, the selected loopback is the wrong endpoint. Try another `[Loopback]`.
- Lower 発話検出感度 (VAD sensitivity) — values near 0.1 are much more permissive.
- Try turning 入力ゲイン to `auto` so small-volume sources are amplified.

**Captions not appearing in OBS**
- Confirm the GUI shows `● 録音中` and `OBS接続: 1` after OBS loads the page.
- Check the `overlay.html` path in OBS Browser Source.
- Ensure port `8765` is not blocked by Windows firewall.

**Translation errors**
- OpenAI: verify the API key and account credit.
- DeepL: free-tier keys must end with `:fx`. Paid keys do not have that suffix.

**Installer fails during first run**
- Check your internet connection and re-run `start.bat` — setup resumes where it left off.

**`WinError 6` noise at stop**
- Known upstream race in RealtimeSTT shutdown ([#4](../../issues/4)). Functionality is unaffected.

---
---

## 日本語

Windows デスクトップ向けのリアルタイム字幕・翻訳アプリです。PC の再生音声を WASAPI ループバックでキャプチャ →
Whisper (faster-whisper, CPU int8) で文字起こし → OpenAI / DeepL で翻訳 → WebSocket 経由で OBS Browser Source に字幕配信します。

**VB-Audio Virtual Cable などの仮想ケーブルは不要です。**

### 動作環境

- Windows 10 / 11
- OpenAI API キー **または** DeepL API キー（どちらか一方でOK）
- インターネット接続（初回セットアップ時のみ）
- ディスク空き約 3 GB、メモリ約 2 GB

Python のインストールは不要です（`start.bat` が初回起動時に embeddable Python 3.11.9 を自動取得）。

---

### セットアップ

#### 1. API キーを設定

```bat
copy config.yaml.example config.yaml
```

`config.yaml` を開いて、使う方のキーを入れます。`translation.translation_model` で使用エンジンを切替（デフォルト `deepl`）：

```yaml
openai:
  api_key: "sk-xxxxxxxxxxxxxxxxxxxx"

deepl:
  api_key: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"

translation:
  translation_model: "deepl"   # "openai" または "deepl"
```

- OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)（従量課金）
- DeepL: [deepl.com/pro-api](https://www.deepl.com/pro-api)（無料枠キーは末尾 `:fx`）

GUI では **API キーが入っているエンジンのみ選択可能** になります。

#### 2. `start.bat` をダブルクリック

初回のみ以下が自動実行されます：

- Python 3.11.9 ダウンロード（約 10 MB）
- パッケージインストール（約 2 GB）
- Whisper `small` + Silero VAD ダウンロード（約 500 MB）

> 初回ダウンロード総量は約 **2.5 GB**。回線状況により 10〜30 分。2回目以降は数秒で起動します。

セットアップ完了後に GUI ウィンドウ「リアルタイム字幕・翻訳」が開きます。

---

### GUI の使い方

**常時表示のツールバー:**

| コントロール | 説明 |
|-------------|------|
| 音声入力 | 入力デバイス。`[Loopback]` 付きを選ぶと PC の再生音声を取得 |
| 開始 / 停止 | 録音開始・停止 |
| 入力ゲイン | `off` / `manual`（1〜20倍スライダー）/ `auto`（AGC: 60% をターゲットに自動追従） |
| 音量メーター | リアルタイム入力レベル、緑/黄/赤 で色分け。gain 倍率もオーバーレイ表示 |
| ログクリア | 画面上の字幕ログをクリア |

**ステータスバー:**

- `■ 待機中` / `● 録音中`
- `認識 ○` / `認識 ●` — STT ランプ（Whisper 稼働中）
- `翻訳 ○` / `翻訳 ●` — 翻訳ランプ（API 呼び出し中）
- `OBS接続` — 接続中の WebSocket クライアント数
- `RPC` — ローカル RPC エンドポイント

**折りたたみ「詳細設定」:**

| コントロール | 説明 |
|-------------|------|
| 認識モデル | `small`（速い）/ `medium`（精度高） |
| 翻訳エンジン | `openai` / `deepl` |
| 発話検出感度 | 値が小さいほど敏感（小音量でも検知） |
| 無音待機（秒） | この秒数の無音で「発話終了」と判定 |
| 既定値に戻す | VAD をデフォルトへ |

### ループバックデバイスの選び方

```
音声入力:
  [25] スピーカー (Realtek Audio) [Loopback]
  [26] LG HDR 4K (NVIDIA HDMI) [Loopback]   ← HDMI モニタ経由のとき
  [27] CABLE In 16ch (VB-Audio Virtual Cable) [Loopback]
```

音の実際の経路に合わせて選んでください。HDMI モニタのスピーカーから鳴っているならモニタの loopback、PC 内蔵スピーカーやヘッドホンなら Realtek などの loopback が正解です。

音が鳴っているのに 音量メーターが 0% のままなら、**別のデバイスを試してください**。

---

### `config.yaml` 設定項目

| 項目 | 説明 | デフォルト |
|------|------|---------|
| `openai.api_key` | OpenAI API キー | `your-api-key-here` |
| `deepl.api_key` | DeepL API キー | — |
| `translation.translation_model` | `"openai"` または `"deepl"` | `deepl` |
| `translation.target_language` | 翻訳先言語（OpenAI プロンプト用） | `日本語` |
| `translation.system_prompt` | OpenAI システムプロンプト | 例参照 |
| `whisper.model` | `small` / `medium` | `small` |
| `whisper.language` | 入力言語 (`"en"`, `"ja"`, `null`=自動) | `null` |
| `whisper.compute_type` | `int8` (CPU推奨) / `float16` (GPU) / `float32` | `int8` |
| `whisper.device` | `cpu` / `cuda` / `auto` | `cpu` |
| `vad.silero_sensitivity` | Silero VAD 閾値（**値が小さいほど敏感**） | `0.4` |
| `vad.post_speech_silence_duration` | 発話終了判定の無音秒数 | `0.6` |
| `websocket.host` / `port` | OBS 向け字幕配信エンドポイント | `localhost:8765` |
| `rpc.port` | ローカル HTTP RPC のポート（状態取得・遠隔制御） | `8767` |
| `output.log_dir` | 翻訳ログ保存先 | `.` |

GUI 側で変更した設定（デバイス / モデル / 翻訳エンジン / ゲイン / VAD）は `settings.json` に保存され、次回起動時に復元されます（`config.yaml` の値より優先）。

翻訳ログは `YYYY-MM-DD-N_translate.txt` 形式で自動保存されます。

---

### OBS Browser Source の設定

OBS のシーンに「ブラウザ」ソースを追加：

| 設定項目 | 値 |
|---------|-----|
| ローカルファイル | ✔ |
| ファイルパス | `（このフォルダ）\overlay.html` |
| 幅 | `1920` |
| 高さ | `1080` |
| シャットダウン非表示時 | ✔ 推奨 |

GUI で録音を開始した状態で OBS をプレビューすると字幕が表示されます（上: 原文グレー小、下: 翻訳白大）。

---

### ローカル HTTP RPC（任意）

外部ツールから状態取得や遠隔制御ができます（`localhost:8767`）。

| Method | Path | 説明 |
|--------|------|------|
| GET | `/api/status` | 状態、デバイス、モデル、ws_clients、audio_peak、gain |
| GET | `/api/audio` | リアルタイム peak / gain_mode / chunks_per_sec |
| GET | `/api/log` | 直近 100 件の字幕ログ |
| GET | `/api/devices` | 入力デバイス一覧 |
| POST | `/api/start` | `{"device_index": N, "model": "small"}`（両方省略可） |
| POST | `/api/stop` | 録音停止 |

### CLI モード（上級者向け）

旧来のコンソール版（GUI なし）で起動するには：

```bat
start.bat --cli
```

---

### プロジェクトパスに日本語を含む場合の注意

プロジェクトフォルダのパスに非 ASCII 文字が含まれる場合、モデルキャッシュは `.\models\` ではなく
`%LOCALAPPDATA%\rc-models` に配置されます（PyTorch / ctranslate2 の `fopen` 制約対策）。
動作は変わりませんが、**その場所での初回起動時はモデルが再ダウンロードされます**。

---

### トラブルシューティング

**音声が認識されない**
- 音量メーターが 0% の場合、選んだ Loopback と実際の出力経路が違います。別の `[Loopback]` を試してください。
- 発話検出感度を下げる（0.1〜0.2 付近が敏感）。
- 入力ゲインを `auto` にすると小音量でも増幅されます。

**OBS に字幕が出ない**
- GUI で `● 録音中` かつ `OBS接続: 1` になっているか確認。
- OBS Browser Source の `overlay.html` パスを再確認。
- ポート `8765` が Windows ファイアウォールでブロックされていないか確認。

**翻訳エラーが出る**
- OpenAI: API キーとアカウントクレジット残高を確認。
- DeepL: 無料キーは末尾が `:fx` でなければなりません（有料キーには付きません）。

**初回セットアップが失敗する**
- インターネット接続を確認して `start.bat` を再実行してください（途中から再開します）。

**停止時に `WinError 6` がログに出る**
- RealtimeSTT 側の既知レース（[#4](../../issues/4)）。機能には影響ありません。
