# W-COST-2 設計仕様書 — 原文表示 OFF 時の Whisper コスト削減

> Issue #81 W-COST-2 / 対象ブランチ: feat/ga-input-transcription-restore
> 策定日: 2026-05-15 by SPECちゃん
> 実機検証: 2026-05-16

---

## 概要

`request_source_transcript` フラグで session.update の `audio.input` 送信を制御し、
原文表示が不要な場合のコストを削減する。

---

## 実機検証（2026-05-16）で確定した仕様

### W-COST-2 は GA 移行後も動作することを確認

実機 verbose ログによる受信イベント（2026-05-16）:

| イベント | 件数 |
|---|---|
| `session.output_audio.delta` | 70 |
| `session.output_transcript.delta` | 17 |
| **`session.input_transcript.delta`** | **2 ✅** |
| `session.created` | 1 |
| `session.updated` | 1 |

`session.input_transcript.delta` の受信を確認（W-COST-2 動作確認）。

### 正しい payload

```json
{
  "type": "session.update",
  "session": {
    "audio": {
      "output": {"language": "en"},
      "input": {
        "transcription": {"model": "gpt-realtime-whisper"},
        "noise_reduction": {"type": "near_field"}
      }
    }
  }
}
```

### 重要: noise_reduction は必須

**noise_reduction なしでは input_transcript が来ない（実機検証で確定）。**

- `audio.input.transcription` のみ送信 → `session.input_transcript.delta` が発行されない
- `audio.input.transcription` + `audio.input.noise_reduction` のセット送信 → 発行される

---

## フラグ動作仕様

### request_source_transcript=True（デフォルト）

session.update に以下を送信:

```json
{
  "audio": {
    "output": {"language": "<target_language_code>"},
    "input": {
      "transcription": {"model": "gpt-realtime-whisper"},
      "noise_reduction": {"type": "near_field"}
    }
  }
}
```

- `session.input_transcript.delta` / `done` イベントを受信できる
- `on_source_transcript` コールバックで原文テキストを取得可能

### request_source_transcript=False

session.update に以下のみ送信（audio.input を省略）:

```json
{
  "audio": {
    "output": {"language": "<target_language_code>"}
  }
}
```

- `session.input_transcript.delta` は発行されない（コスト削減）
- `on_source_transcript` コールバックは呼ばれない

---

## GA 移行（2026-05-12）による変更点

### Beta 版との差異

| 項目 | Beta 版 | GA 版（実機検証確認済み） |
|---|---|---|
| transcription 指定 | audio.input.transcription のみでよかった | transcription + noise_reduction のセット必須 |
| turn_detection | audio.input.turn_detection で指定可能 | Unknown parameter エラーで拒否（送らない） |
| input_transcript 自動発行 | なし（明示指定が必要） | noise_reduction セット時のみ発行 |

### turn_detection（W-COST-3）は GA 仕様外

`audio.input.turn_detection` を送ると `Unknown parameter: 'session.audio.input.turn_detection'`
エラーで session 全体が拒否される。W-COST-3 は廃止（`cost-w-cost-3-design.md` 参照）。

---

## 実装箇所

`realtime_translator.py` の `_run_session` メソッド内:

```python
audio_section: dict = {
    "output": {"language": self._target_language_code},
}
if self._request_source_transcript:
    # 実機検証（2026-05-16）: transcription + noise_reduction をセットで指定しないと
    # input_transcript.delta が発行されない。noise_reduction は near_field を指定。
    audio_section["input"] = {
        "transcription": {"model": "gpt-realtime-whisper"},
        "noise_reduction": {"type": "near_field"},
    }
```

---

## 受け入れ条件（全件 PASS 確認済み）

- [x] request_source_transcript=True のとき audio.input.transcription + noise_reduction が送信される
- [x] request_source_transcript=False のとき audio.input が送信されない
- [x] audio.output.language は常に送信される
- [x] turn_detection は vad_enabled に関わらず送信されない（GA 仕様外）
- [x] 実機で session.input_transcript.delta が受信できる（2026-05-16 確認）

---

## テスト

- `tests/test_ga_migration.py` — GA ペイロード検証（request_source_transcript の分岐含む）
- `tests/test_w_cost_2_source_transcript.py` — W-COST-2 専用テスト
- `tests/test_w_cost_3_vad.py` — VAD + source transcript の組み合わせ検証
- `tests/test_realtime_translator.py` — audio.input 構造の単体テスト
