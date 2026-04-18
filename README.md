# Realtime Caption & Translation System

> **日本語は下部に記載しています。** / Japanese documentation follows below.

---

## English

Real-time audio transcription and translation system for OBS.  
Captures desktop audio via WASAPI loopback, transcribes with Whisper, translates with the OpenAI API, and streams captions to OBS Browser Source over WebSocket.

**No virtual audio cable required.**  
Audio is captured directly from the desktop using WASAPI loopback — no VB-Audio Virtual Cable or similar software needed.

### Requirements

- Windows 10 / 11
- OpenAI API key
- Internet connection (initial setup only)

Python does not need to be installed. An Embeddable Python runtime is downloaded and configured automatically on first launch.

---

### Setup & Usage

#### 1. Configure your API key

Copy `config.yaml.example` to `config.yaml` and set your OpenAI API key:

```bat
copy config.yaml.example config.yaml
```

```yaml
openai:
  api_key: "sk-xxxxxxxxxxxxxxxxxxxx"  # replace with your API key
```

Get your API key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys).  
Usage is billed per request (approximately $0.001 per translated sentence).

#### 2. Double-click `start.bat`

On first launch, setup runs automatically:

- Downloads Python 3.11 (~10 MB)
- Installs required packages (~2 GB)
- Downloads Whisper and Silero VAD models (~500 MB)

> **Note:** First launch downloads approximately **2.5 GB** in total. A stable internet connection is recommended. Estimated time: 10–30 minutes. From the second launch onward, it starts immediately.

---

### Usage

Double-click `start.bat`.

You will be prompted to:

1. **Select an audio input device** — enter the device number from the list
2. **Select the Whisper model** — `small` (faster) or `medium` (more accurate); press Enter to use the default

Transcription and translation results are printed to the console.  
Press `Ctrl+C` to stop.

---

### Selecting the right input device

```
Available input devices:
  [25] Realtek HD Audio
  [26] LG HDR 4K [Loopback]   ← desktop audio (all system sound)
  [0]  Microphone Array        ← microphone only
```

To capture all desktop audio (video playback, meetings, etc.), select a **[Loopback]** device.  
If your PC outputs audio via HDMI, choose the loopback device corresponding to your monitor.

> Audio is captured directly via WASAPI loopback — no virtual audio cable required.

---

### Configuration reference (`config.yaml`)

| Key | Description | Default |
|-----|-------------|---------|
| `openai.api_key` | OpenAI API key | `your-api-key-here` |
| `translation.target_language` | Translation target language | `日本語` |
| `whisper.model` | Whisper model (`small` / `medium`) | `small` |
| `whisper.language` | Input language code (`en` / `null` for auto-detect) | `en` |
| `websocket.port` | WebSocket port | `8765` |
| `output.log_dir` | Directory for translation logs | `.` (current folder) |

Translation logs are saved as `YYYY-MM-DD-n_translate.txt`.

---

### OBS Browser Source setup

#### 1. Add overlay.html as a local file source

Add a **Browser** source to your OBS scene:

| Setting | Value |
|---------|-------|
| Local file | ✔ checked |
| File path | `<this folder>\overlay.html` |
| Width | `1920` |
| Height | `1080` |
| Shutdown source when not visible | ✔ recommended |

#### 2. Verify captions

With `start.bat` running, preview the scene in OBS — captions should appear:

- **Top line** (small, grey): original transcript
- **Bottom line** (large, white): translation

---

### Troubleshooting

**No audio recognized**
- Make sure you selected a `[Loopback]` device.
- Verify that audio is actually being output through that device (e.g., if using HDMI audio, ensure the monitor is the active output).

**Captions not appearing**
- Confirm `start.bat` is running.
- Check the file path in OBS Browser Source.
- Ensure port 8765 is not blocked by your firewall.

**Translation errors**
- Verify the API key in `config.yaml` is correct.
- Check your OpenAI account credit balance.

**Setup fails on first launch**
- Check your internet connection and run `start.bat` again (setup resumes from where it left off).

---
---

## 日本語

デスクトップ音声を Whisper で文字起こしし、OpenAI API で翻訳して、OBS Browser Source に字幕表示するシステムです。

**VB-Audio Virtual Cable などの仮想ケーブルは不要です。**  
WASAPI ループバックを使ってデスクトップ音声全体を直接キャプチャします。

### 動作環境

- Windows 10/11
- OpenAI API キー
- インターネット接続（初回セットアップ時のみ）

Python のインストールは不要です（初回起動時に自動でダウンロード・設定されます）。

---

### セットアップ＆起動

#### 1. config.yaml を作成して API キーを設定

`config.yaml.example` をコピーして `config.yaml` にリネームし、`api_key` に OpenAI API キーを入力してください。

```bat
copy config.yaml.example config.yaml
```

```yaml
openai:
  api_key: "sk-xxxxxxxxxxxxxxxxxxxx"  # ここを自分の API キーに書き換える
```

OpenAI API キーは [platform.openai.com/api-keys](https://platform.openai.com/api-keys) から取得できます。  
API の利用には従量課金が発生します（翻訳1文あたり約 $0.001 程度）。

#### 2. `start.bat` をダブルクリック

**初回のみ**、セットアップが自動で実行されます。

- Python 3.11 をダウンロード（約10 MB）
- 必要パッケージをインストール（約2 GB）
- Whisper・Silero VAD モデルをダウンロード（約500 MB）

> **注意:** 初回起動のダウンロード総量は約 **2.5 GB** です。安定したインターネット接続を推奨します。回線速度によって完了まで 10〜30 分程度かかります。2回目以降はすぐに起動します。

---

### 起動方法

`start.bat` をダブルクリックします。

起動後、以下を選択します。

1. **入力デバイス選択** — 一覧から音声デバイスの番号を入力
2. **Whisper モデル選択** — `small`（速い）または `medium`（精度高い）を選択（Enter でデフォルト使用）

録音が開始され、コンソールに文字起こし・翻訳結果が表示されます。  
終了するには `Ctrl+C` を押してください。

---

### 入力デバイスの選び方

```
利用可能な入力デバイス一覧:
  [25] Realtek HD Audio
  [26] LG HDR 4K [Loopback]   ← デスクトップ音声全体を取得する場合
  [0]  マイク配列 (Realtek)    ← マイクで話した音声のみの場合
```

**デスクトップの音声（動画・会議など）を翻訳したい場合**は `[Loopback]` と表示されているデバイスを選択してください。  
PC の音声が HDMI モニター経由で出力されている場合は、モニター名のついた `[Loopback]` デバイスが有効です。

> VB-Audio Virtual Cable 等の仮想ケーブルを使わずに、WASAPI ループバックでデスクトップ音声を直接取得しています。

---

### config.yaml の設定項目

| 項目 | 説明 | デフォルト値 |
|------|------|-------------|
| `openai.api_key` | OpenAI API キー | `your-api-key-here` |
| `translation.target_language` | 翻訳先言語 | `日本語` |
| `whisper.model` | Whisper モデル（`small` / `medium`） | `small` |
| `whisper.language` | 入力言語コード（`en` 固定 / `null` で自動検出） | `en` |
| `websocket.port` | WebSocket ポート番号 | `8765` |
| `output.log_dir` | 翻訳ログの保存先ディレクトリ | `.`（起動フォルダ） |

翻訳ログは `YYYY-MM-DD-n_translate.txt` の形式で自動保存されます。

---

### OBS Browser Source の設定

#### 1. overlay.html をローカルファイルとして追加

OBS Studio のシーンに「ブラウザ」ソースを追加します。

| 設定項目 | 値 |
|---------|-----|
| ローカルファイル | チェックを入れる |
| ファイルパス | `（このフォルダ）\overlay.html` |
| 幅 | `1920` |
| 高さ | `1080` |
| シャットダウン非表示時 | チェックを入れる（推奨） |

#### 2. 字幕の確認

`start.bat` を起動した状態で OBS をプレビューすると字幕が表示されます。

- 上段（小文字・グレー）: 原文
- 下段（大文字・白）: 翻訳

---

### トラブルシューティング

**音声が認識されない**
- `[Loopback]` と表示されているデバイスを選択しているか確認してください
- PC の音声が実際にそのデバイスから出力されているか確認してください

**字幕が表示されない**
- `start.bat` が起動しているか確認してください
- OBS Browser Source のファイルパスが正しいか確認してください
- WebSocket ポート（デフォルト 8765）がファイアウォールでブロックされていないか確認してください

**翻訳エラーが出る**
- `config.yaml` の API キーが正しく設定されているか確認してください
- OpenAI アカウントのクレジット残高を確認してください

**初回起動のセットアップでエラーが出る**
- インターネット接続を確認してください
- 再度 `start.bat` を実行してください（途中から再開されます）
