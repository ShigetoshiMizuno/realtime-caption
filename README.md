# リアルタイム字幕・翻訳システム

デスクトップ音声を Whisper で文字起こしし、OpenAI API で翻訳して、OBS Browser Source に字幕表示するシステムです。

**VB-Audio Virtual Cable などの仮想ケーブルは不要です。**
WASAPI ループバックを使ってデスクトップ音声全体を直接キャプチャします。

## 動作環境

- Windows 10/11
- インターネット接続（初回セットアップ時のみ）
- OpenAI API キー

Python のインストールは不要です（Embeddable Python を同梱）。

---

## セットアップ（初回のみ）

### 1. config.yaml に API キーを設定

`config.yaml` を開き、`api_key` に OpenAI API キーを入力してください。

```yaml
openai:
  api_key: "sk-xxxxxxxxxxxxxxxxxxxx"  # ここを書き換える
```

### 2. setup.bat を実行

`setup.bat` をダブルクリックします。以下が自動でインストールされます。

- Python 3.11（`python/` フォルダへ）
- 必要パッケージ一式
- Whisper・Silero VAD モデル（`models/` フォルダへ）

完了まで数分〜10分程度かかります。

---

## 起動方法

`start.bat` をダブルクリックします。

起動後、以下を選択します。

1. **入力デバイス選択** — 一覧から音声デバイスの番号を入力
2. **Whisper モデル選択** — `small`（速い）または `medium`（精度高い）を選択（Enter でデフォルト使用）

録音が開始され、コンソールに文字起こし・翻訳結果が表示されます。
終了するには `Ctrl+C` を押してください。

---

## 入力デバイスの選び方

起動すると利用可能なデバイスが一覧表示されます。

```
利用可能な入力デバイス一覧:
  [25] Realtek HD Audio
  [26] LG HDR 4K [Loopback]   ← デスクトップ音声全体を取得する場合
  [0]  マイク配列 (Realtek)    ← マイクで話した音声のみの場合
  ...
```

**デスクトップの音声（動画・会議など）を翻訳したい場合**は `[Loopback]` と表示されているデバイスを選択してください。
PC の音声が HDMI モニター経由で出力されている場合は、モニター名のついた `[Loopback]` デバイスが有効です。

> VB-Audio Virtual Cable 等の仮想ケーブルを使わずに、WASAPI ループバックでデスクトップ音声を直接取得しています。

---

## config.yaml の設定項目

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

## OBS Browser Source の設定

### 1. overlay.html をローカルファイルとして追加

OBS Studio のシーンに「ブラウザ」ソースを追加します。

| 設定項目 | 値 |
|---------|-----|
| ローカルファイル | チェックを入れる |
| ファイルパス | `（このフォルダ）\overlay.html` |
| 幅 | `1920` |
| 高さ | `1080` |
| シャットダウン非表示時 | チェックを入れる（推奨） |

### 2. 字幕の確認

`start.bat` を起動した状態で OBS をプレビューすると字幕が表示されます。

- 上段（小文字・グレー）: 原文
- 下段（大文字・白）: 翻訳

---

## トラブルシューティング

### 音声が認識されない

- `[Loopback]` と表示されているデバイスを選択しているか確認してください
- PC の音声が実際にそのデバイスから出力されているか確認してください
  （例: HDMI モニターに音声が出ていない場合は別のデバイスを試してください）

### 字幕が表示されない

- `start.bat` が起動しているか確認してください
- OBS Browser Source のファイルパスが正しいか確認してください
- WebSocket ポート（デフォルト 8765）がファイアウォールでブロックされていないか確認してください

### 翻訳エラーが出る

- `config.yaml` の API キーが正しく設定されているか確認してください
- OpenAI アカウントのクレジット残高を確認してください

### setup.bat でエラーが出る

- インターネット接続を確認してください
- 再度 `setup.bat` を実行してください（途中から再開されます）
