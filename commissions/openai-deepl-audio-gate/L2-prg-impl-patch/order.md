---
commission_id: openai-deepl-audio-gate/L2-prg-impl-patch
parent_id: openai-deepl-audio-gate
mode: impl
enforce: strict
max_depth: 0
due_at: 2026-05-28T23:59:00+09:00
---

# 発注書: B-3 修正 — main.py への _route_audio_chunk 実装と _capture_thread_body への配線

## 目的

SPEC.md §15 バグ#3 を修正する。
openai/deepl/whisper モード（`_realtime_mode=False`）において、
`_audio_gate=False` のとき `_recorder.feed_audio()` が呼ばれてしまう不具合を解消する。

## スコープ

- **操作可能なファイル:** `main.py` のみ
- スコープ外のファイルは一切変更しないこと

## 仕様

### 1. `CaptionSystem._route_audio_chunk(pcm_bytes: bytes) -> bool` メソッドを新規追加

**責務:** `_capture_thread_body` 内のループから抽出した音声ルーティングロジック。

**シグネチャ:**
```python
def _route_audio_chunk(self, pcm_bytes: bytes) -> bool:
    ...
```

**ロジック（疑似コード）:**
```
if self._audio_gate is False:
    return False  # ゲート閉: 音声を破棄、呼び出し元は continue すべき

if self._realtime_mode:
    if self._realtime_translator is not None:
        self._realtime_translator.feed_audio(pcm_bytes)
else:
    if self._recorder is not None:
        self._recorder.feed_audio(pcm_bytes)

return True  # 音声を投入した
```

**配置場所:** `CaptionSystem` クラス内、`_capture_thread_body` の直前（行 1269 付近）に追加。

### 2. `_capture_thread_body` のルーティングブロックを `_route_audio_chunk` 呼び出しに差し替え

**変更前（main.py 1405-1414）:**
```python
                # モードに応じて音声データの投入先を切替
                if self._realtime_mode:
                    # Case D 音声ゲート: _audio_gate=False の間は音声をサイレント破棄（issue #156）
                    if not self._audio_gate:
                        continue
                    if self._realtime_translator is not None:
                        self._realtime_translator.feed_audio(pcm_bytes)
                else:
                    if self._recorder is not None:
                        self._recorder.feed_audio(pcm_bytes)
```

**変更後:**
```python
                # モードに応じて音声データの投入先を切替
                # Case D 音声ゲート: _audio_gate=False の間は音声をサイレント破棄
                # （openai/deepl/whisper モードも対象 — SPEC.md §15 バグ#3 修正）
                if not self._route_audio_chunk(pcm_bytes):
                    continue
```

### 3. コメントの更新

`_route_audio_chunk` の docstring には以下を含めること:
- 元の実装が `_capture_thread_body` にインラインで存在していたこと
- バグ#3 修正（TBD-2 確定）として openai/deepl モードにも音声ゲートを適用していること
- 戻り値の意味（True=投入済み / False=ゲート破棄）

## 受け入れ条件

以下のコマンドで 7 / 7 PASS になること:

```bash
python -m pytest tests/test_audio_gate_b3.py -v
```

また、既存テストへの影響がないこと:

```bash
python -m pytest tests/test_audio_gate_on_start.py tests/test_audio_gate_open_property.py -v
```

## テストケース一覧（全件 RED 確認済み）

テストファイル: `tests/test_audio_gate_b3.py`
クラス: `TestAudioGateCaptureRoutingB3`

| # | テスト名 | 確認内容 |
|---|----------|----------|
| 1 | `test_openai_mode_audio_gate_closed_drops_audio` | openai + gate=False → feed_audio 未呼び出し |
| 2 | `test_openai_mode_audio_gate_open_feeds_audio` | openai + gate=True → feed_audio 呼び出し |
| 3 | `test_deepl_mode_audio_gate_closed_drops_audio` | deepl + gate=False → feed_audio 未呼び出し |
| 4 | `test_realtime_mode_audio_gate_closed_drops_audio` | realtime + gate=False → feed_audio 未呼び出し（回帰） |
| 5 | `test_realtime_mode_audio_gate_open_feeds_realtime_translator` | realtime + gate=True → translator.feed_audio 呼び出し |
| 6 | `test_openai_mode_recorder_none_does_not_raise` | openai + recorder=None → 例外なし |
| 7 | `test_realtime_mode_translator_none_does_not_raise` | realtime + translator=None → 例外なし |

## 実装後の確認事項（配線チェック）

`_route_audio_chunk` は `_capture_thread_body` から呼ばれることを確認すること:

```bash
git diff HEAD | grep "_route_audio_chunk"
```

`_capture_thread_body` 内の `if not self._route_audio_chunk(pcm_bytes):` が含まれていること。

## 完了報告

`commissions/openai-deepl-audio-gate/L2-prg-impl-patch/report.md` に以下を記載:
- テスト PASS 件数
- 変更した行数（main.py の diff 統計）
- 配線チェック結果
- codex 利用: あり/なし
