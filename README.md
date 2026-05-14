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

> See [FAQ.md](FAQ.md) for cost and bandwidth estimates per translation mode.

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
| API キー設定 (API Key settings) | Enter keys and press 保存 (Save) to write them to `config.yaml`. |

**Setting API keys from the GUI:**

1. Open 詳細設定 (Advanced)
2. Enter your OpenAI and/or DeepL key in the respective field
3. Click 保存 (Save)

The key is saved to `config.yaml` in **Base64-obfuscated form** (`b64:...`).
This prevents casual plaintext exposure in screenshots or config file shares.

> **Important:** Base64 is **not encryption**. It is obfuscation only.
> Never commit `config.yaml` to version control, and avoid sharing it.
> The app reads both plain and `b64:` prefixed keys transparently.

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
| `openai_realtime.max_session_minutes` | Auto-stop after N minutes of Realtime usage (0 = unlimited) | `60` |
| `translation_konnyaku.route_a.target_language_code` | Route A target language (BCP-47: `ja`, `en`, …) | `ja` |
| `translation_konnyaku.route_a.audio_output_enabled` | Enable audio output on Route A | `false` |
| `translation_konnyaku.route_b.target_language_code` | Route B target language (BCP-47: `ja`, `en`, …) | `en` |
| `translation_konnyaku.route_b.audio_output_enabled` | Enable audio output on Route B (recommended for virtual mic) | `true` |
| `translation_konnyaku.route_b.output_device_keyword` | Route B output device name (partial match) | `CABLE Input` |

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

### Operating Modes

| Mode | Use case | Input | Output |
|------|----------|-------|--------|
| OBS caption mode | Subtitle overlay for OBS streaming | WASAPI loopback | overlay.html |
| Zoom simultaneous interpretation (one-way) | Send your voice to the meeting in the other language | Microphone | CABLE Input + overlay.html |
| **Translation Konnyaku Mode (bidirectional, Issue #38)** | Real-time two-way translation between you and a remote participant | Microphone + WASAPI loopback | CABLE Input + overlay.html |

---

### Zoom simultaneous interpretation via VB-CABLE

Use **OpenAI Realtime** mode to have translated speech played back into Zoom's interpreter channel.

#### Requirements

- [VB-CABLE Virtual Audio Device](https://vb-audio.com/Cable/) (free)
- OpenAI API key with `gpt-realtime-translate` access

#### 1. Install VB-CABLE

Download and run the VB-CABLE installer. After reboot, you should see:
- **CABLE Input** — a virtual speaker (output device)
- **CABLE Output** — a virtual microphone (input device)

#### 2. Configure this app

Open the GUI → 詳細設定 (Advanced), then:

1. Click **「Zoom 同時通訳プリセット」** — this will:
   - Switch the translation engine to `OpenAI Realtime`
   - Set the audio output to `CABLE Input (VB-Audio Virtual Cable)`
2. Click **開始** to start capture and translation.

Or configure manually:
- **翻訳エンジン** → `OpenAI Realtime`
- **音声出力先** → `CABLE Input (VB-Audio Virtual Cable)`

#### 3. Configure Zoom

1. Go to **Settings → Audio** → Microphone: select **CABLE Output (VB-Audio Virtual Cable)**
2. Join a meeting and go to **More → Language Interpretation → Add Language**
3. Set yourself as an interpreter for your target language
4. When interpretation is active, Zoom will broadcast audio from CABLE Output on the interpreter channel

> **Note:** Interpreted audio reaches Zoom listeners with ~1-3 second delay (realtime API latency).

---

### Translation Konnyaku Mode setup (bidirectional)

Translation Konnyaku Mode runs **two simultaneous translation sessions**:

- **Route A** — Captures the remote participant's audio (via WASAPI loopback), translates to your language, and displays subtitles.
- **Route B** — Captures your microphone audio, translates to the remote participant's language, and outputs to a virtual microphone (VB-CABLE).

#### Prerequisites

- [VB-CABLE Virtual Audio Device](https://vb-audio.com/Cable/) (free) — see the VB-CABLE install section above
- OpenAI API key with `gpt-realtime-translate` access

#### 1. Windows sound settings

After installing VB-CABLE:

1. Open **Sound Settings → Recording** and verify **CABLE Output** is enabled.
2. Open **Sound Settings → Playback** and verify **CABLE Input** is present.

#### 2. Launch the app and select Translation Konnyaku Mode

1. Double-click `start.bat` to open the GUI.
2. Open **詳細設定 (Advanced)** and click **「翻訳こんにゃくモード」** preset button.
   - Route A input: WASAPI loopback device (selects automatically)
   - Route B input: physical microphone
   - Route B output: `CABLE Input` (set by preset)
   - Route A target language: `ja` (Japanese) — change as needed
   - Route B target language: `en` (English) — change as needed
3. Click **開始** to start both routes simultaneously.

#### 3. Configure Zoom

1. Go to **Settings → Audio** → Microphone: select **CABLE Output (VB-Audio Virtual Cable)**.
2. Speaker: keep your normal output device.

With this setup, the remote participant hears your translated English voice, and their speech is displayed as Japanese subtitles on your screen via overlay.html.

#### Cost estimate

`gpt-realtime-translate` × 2 sessions ≈ **$0.068 / minute** (~$4 / 1-hour meeting, ~¥600).
The existing maximum session time guard (`openai_realtime.max_session_minutes`) applies independently to each route.

#### Adding languages in the future

Add one line to `SUPPORTED_LANGUAGES` in `constants.py` — the GUI language combo will reflect it automatically.
Language codes must conform to BCP-47 as supported by OpenAI Realtime Translate.

#### Troubleshooting (Konnyaku Mode)

| Symptom | Action |
|---------|--------|
| CABLE Input not selectable | Confirm VB-CABLE is installed and restart the app |
| Only one route works | Check device selection and level meter for the failing route in the GUI |
| Cost higher than expected | Check the `cost_monitor` log; confirm it correlates with actual meeting duration |

---

### Cost protection (OpenAI Realtime mode)

When `translation_model: "openai-realtime"` is active, the app monitors session cost
and provides safeguards:

- **Auto-stop** — The session ends automatically after `openai_realtime.max_session_minutes`
  minutes (default 60). Set to `0` to disable the limit. A message is shown in the GUI
  status bar and printed to the console: `最大稼働時間 60 分に達したため停止しました`.

- **Cost display** — The status bar shows elapsed time and estimated cost in real-time:
  `経過: 00:12:34 / 想定コスト: $0.43`. In CLI mode this appears as a `\r`-updated line
  in the terminal.

- **Warning thresholds** — Modal alerts appear (GUI) or warnings are printed (CLI) when
  the estimated cost first exceeds **$5**, **$10**, and **$20**. Each threshold fires only
  once per session.

Rate used for estimation: **$0.034 / minute** (≈ $2.04 / hour).

### CLI mode

Power users can run the legacy console flow (no GUI) via:

```bat
start.bat --cli
```

---

### Contributing

Before opening a pull request, read [CONTRIBUTING.md](./CONTRIBUTING.md).
In particular, use only the approved fake-key formats in test fixtures and docs
(`pre-commit install` will let gitleaks block real-looking keys locally).

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

### FAQ

**Q: In Translation Konnyaku Mode, the translated audio plays ~5 seconds before the on-screen caption appears. Is this a bug?**

No, this is the expected behavior of OpenAI's `gpt-realtime-translate` API:

- `session.output_audio.delta` is streamed chunk-by-chunk **as soon as the model starts generating audio**.
- `session.input_transcript.delta` / `session.output_transcript.delta` are emitted **once the model commits a sentence** (typically at sentence boundaries), which arrives later.

Verbose logs typically show a 3-7 second gap between the first audio chunk and the first transcript chunk. The app does not buffer audio to wait for transcripts because the primary use-case (simultaneous interpretation through VB-CABLE) prioritizes low latency. If you need synchronized captions for recording purposes, please file an issue.

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

> 各翻訳モードのコスト・帯域目安は [FAQ.md](FAQ.md) を参照してください。

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
| API キー設定 | キーを入力して「保存」を押すと `config.yaml` に書き込まれます |

**GUI から API キーを設定する手順:**

1. 詳細設定を開く
2. OpenAI または DeepL のキーを入力（「表示」ボタンで確認可）
3. 「保存」をクリック

キーは `config.yaml` に **Base64 難読化形式**（`b64:...`）で保存されます。
これにより画面共有やファイル共有時のうっかり漏洩を防ぎます。

> **注意:** Base64 は**暗号化ではなく難読化**です。
> `config.yaml` をバージョン管理に含めないでください（`.gitignore` で除外済み）。
> 平文キーと `b64:` 形式のキーは自動判定されるため、どちらでも動作します。

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
| `openai_realtime.max_session_minutes` | Realtime モードの自動停止時間（分）。0 で無制限 | `60` |
| `translation_konnyaku.route_a.target_language_code` | 経路A 翻訳先言語（BCP-47: `ja`, `en`, …） | `ja` |
| `translation_konnyaku.route_a.audio_output_enabled` | 経路A 音声出力を有効化 | `false` |
| `translation_konnyaku.route_b.target_language_code` | 経路B 翻訳先言語（BCP-47: `ja`, `en`, …） | `en` |
| `translation_konnyaku.route_b.audio_output_enabled` | 経路B 音声出力を有効化（仮想マイク推奨） | `true` |
| `translation_konnyaku.route_b.output_device_keyword` | 経路B 出力デバイス名（部分一致） | `CABLE Input` |

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

### 動作モード

| モード | 用途 | 入力 | 出力 |
|--------|------|------|------|
| OBS 字幕モード | OBS 配信に字幕オーバーレイ | WASAPI loopback | overlay.html |
| Zoom 同時通訳モード（片方向） | 自分の声を相手の言語で Zoom に流す | マイク | CABLE Input + overlay.html |
| **翻訳こんにゃくモード（双方向・Issue #38）** | 自分↔相手の双方向同時翻訳 | マイク + WASAPI loopback | CABLE Input + overlay.html |

---

### VB-CABLE を使った Zoom 同時通訳

**OpenAI Realtime** モードを使うと、翻訳音声を Zoom のインタープリターチャンネルに直接送ることができます。

#### 必要なもの

- [VB-CABLE Virtual Audio Device](https://vb-audio.com/Cable/)（無料）
- `gpt-realtime-translate` が使える OpenAI API キー

#### 手順 1: VB-CABLE をインストール

VB-CABLE のインストーラーを実行して再起動すると：
- **CABLE Input** — 仮想スピーカー（出力デバイス）
- **CABLE Output** — 仮想マイク（入力デバイス）

の 2 つのデバイスが追加されます。

#### 手順 2: このアプリの設定

GUI を開いて「詳細設定」を展開し：

1. **「Zoom 同時通訳プリセット」** ボタンをクリック。以下が自動設定されます：
   - 翻訳エンジン → `OpenAI Realtime`
   - 音声出力先 → `CABLE Input (VB-Audio Virtual Cable)`
2. **「開始」** ボタンで録音・翻訳を開始。

手動で設定する場合：
- **翻訳エンジン** → `OpenAI Realtime`
- **音声出力先** → `CABLE Input (VB-Audio Virtual Cable)`

#### 手順 3: Zoom の設定

1. Zoom の **設定 → オーディオ** → マイク を **「CABLE Output (VB-Audio Virtual Cable)」** に変更
2. ミーティングに参加したら **「詳細」→「言語通訳」→「言語を追加」** を選択
3. 自分を通訳者として設定し、ターゲット言語チャンネルを割り当てる
4. 通訳が有効になると、CABLE Output の音声が Zoom のインタープリターチャンネルで配信されます

> **注意:** 翻訳音声は Realtime API のレイテンシにより 1〜3 秒程度の遅延が生じます。

---

### 翻訳こんにゃくモードのセットアップ（双方向同時翻訳）

翻訳こんにゃくモードは **2つの翻訳セッションを同時に実行**します：

- **経路A** — 相手の音声（WASAPI loopback 経由）を取り込み、自分の言語に翻訳して字幕表示
- **経路B** — 自分のマイク音声を取り込み、相手の言語に翻訳して仮想マイク（VB-CABLE）へ出力

#### 前提条件

- [VB-CABLE Virtual Audio Device](https://vb-audio.com/Cable/)（無料）— 上記の VB-CABLE インストール手順を参照
- `gpt-realtime-translate` が使える OpenAI API キー

#### 手順 1: Windows サウンド設定

VB-CABLE インストール後：

1. **サウンド設定 → 録音** タブで **CABLE Output** が有効になっていることを確認。
2. **サウンド設定 → 再生** タブで **CABLE Input** が表示されていることを確認。

#### 手順 2: アプリ起動 → 翻訳こんにゃくモード

1. `start.bat` をダブルクリックして GUI を起動。
2. **「詳細設定」** を展開し、**「翻訳こんにゃくモード」** プリセットボタンをクリック。
   - 経路A 入力: WASAPI loopback デバイス（自動選択）
   - 経路B 入力: 物理マイク
   - 経路B 出力: `CABLE Input`（プリセットで自動設定）
   - 経路A 翻訳先: `ja`（日本語）— 必要に応じて変更
   - 経路B 翻訳先: `en`（English）— 必要に応じて変更
3. **「開始」** ボタンで2経路を同時起動。

#### 手順 3: Zoom の設定

1. Zoom の **設定 → オーディオ** → マイク を **「CABLE Output (VB-Audio Virtual Cable)」** に変更。
2. スピーカーは通常のデバイスのまま。

これで相手には自分の英語音声が届き、相手の英語は自分側で日本語字幕として表示されます。

#### コスト目安

`gpt-realtime-translate` × 2 セッション = 約 **$0.068 / 分**（1時間のビジネス会議で約 $4 ≈ 600 円）。
既存の `openai_realtime.max_session_minutes` による自動停止は各経路に独立して適用されます。

#### 言語の追加（将来）

`constants.py` の `SUPPORTED_LANGUAGES` リストに1行追加するだけで GUI のコンボに反映されます。
言語コードは OpenAI Realtime Translate の BCP-47 サポート言語に準拠してください。

#### トラブルシューティング（翻訳こんにゃくモード）

| 症状 | 対処 |
|------|------|
| CABLE Input が選べない | VB-CABLE インストールを確認してアプリを再起動 |
| 片方の経路だけ動かない | GUI のデバイス選択とレベルメーターで原因を切り分け |
| コストが想定より高い | `cost_monitor` のログを確認し、Zoom 会議時間と一致するか確認 |

---

### コスト保護機能（OpenAI Realtime モード）

`translation_model: "openai-realtime"` 使用時、以下の保護機能が有効になります：

- **自動停止** — `openai_realtime.max_session_minutes`（デフォルト 60 分）に達すると
  セッションを自動停止します。0 を設定すると無制限になります。
  停止時に GUI ステータスバーと CLI に「最大稼働時間 60 分に達したため停止しました」と表示されます。

- **コスト表示** — ステータスバーに経過時間と想定コストをリアルタイム表示：
  `経過: 00:12:34 / 想定コスト: $0.43`。CLI モードではターミナルに 1 秒ごとに更新表示されます。

- **警告閾値** — 想定コストが **$5 / $10 / $20** を初めて超えたとき、
  GUI ではモーダルアラート、CLI では警告メッセージを表示します（各閾値は 1 回のみ）。

コスト計算レート: **$0.034 / 分**（約 $2.04 / 時間）。

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

**翻訳こんにゃくモードで「CABLE Input が選べない」**
- VB-CABLE がインストールされているか確認し、アプリを再起動してください。

**翻訳こんにゃくモードで片方の経路だけ動かない**
- GUI のデバイス選択と各経路のレベルメーターで原因を切り分けてください。

### よくある質問（FAQ）

**Q: 翻訳こんにゃくモードで、翻訳音声が出てから 5 秒くらい遅れて字幕が出ます。バグですか？**

これは OpenAI の `gpt-realtime-translate` API の仕様です：

- `session.output_audio.delta` は **モデルが音声生成を始めた瞬間**から逐次ストリーミングされます。
- `session.input_transcript.delta` / `session.output_transcript.delta` は **文単位で確定したあと** に送られるため、後発になります。

verbose ログでは「最初の音声 chunk」と「最初の transcript chunk」の間に **3〜7 秒の差**が見えるのが普通です。本アプリは「同時通訳デバイス（VB-CABLE）」を主要用途として低レイテンシを優先しており、音声出力を transcript と同期するためのバッファリングは行いません。録画用途で字幕を同期させたい場合は issue を起票してください。

---

### コントリビューション

Pull request を出す前に [CONTRIBUTING.md](./CONTRIBUTING.md) を確認してください。
特にテスト fixture・ドキュメント例の API キー類は許可フォーマットのみ使用してください
（`pre-commit install` 後、gitleaks がローカルでブロックします）。
