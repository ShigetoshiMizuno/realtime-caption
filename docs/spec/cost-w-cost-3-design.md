# W-COST-3 設計仕様書 — VAD 設定による無音区間 input トークン削減

> Issue #81 W-COST-3 / 対象 master: ce7f8eb（W-COST-1+2 マージ済み）
> 策定日: 2026-05-15 by SPECちゃん

---

## 概要

realtime_translator.py の session.update に turn_detection 設定を追加し、
API 側の Server VAD（音声区間検出）を有効化することで、無音区間の input トークン課金を削減する。
系統 A/B 同時稼働時はその効果が 2 倍になる。

---
## 機能仕様

### 1. OpenAI Realtime Translate API の VAD 仕様調査

#### 1.1 turn_detection フィールドの位置（SDK 型定義より確認済み）

openai Python SDK（同梱版）の型定義ファイル（
python/Lib/site-packages/openai/types/realtime/realtime_transcription_session_audio_input.py）より:

- RealtimeTranscriptionSessionAudioInput.turn_detection が
  Optional[RealtimeTranscriptionSessionAudioInputTurnDetection] として定義されている
- server_vad と semantic_vad の 2 種類が Union として定義されている

session.update で送信する JSON の想定配置（audio.input の直下）:

    {
      "type": "session.update",
      "session": {
        "audio": {
          "input": {
            "transcription": { "model": "gpt-realtime-whisper" },
            "turn_detection": {
              "type": "server_vad",
              "threshold": 0.5,
              "prefix_padding_ms": 300,
              "silence_duration_ms": 500
            }
          }
        }
      }
    }

**重要注意**: gpt-realtime-translate エンドポイントが turn_detection パラメータを実際に受け付けるかは
公式ドキュメントに明示なし。現行の session.update が audio.input.transcription.model を受け入れた
実績（PR #29 / handover-2026-05-13.md）に基づき同ブロックに配置されると推定するが、
**実機検証必須（TBD-3-1）**。

#### 1.2 server_vad の各パラメータ（SDK 型定義より）

| パラメータ | デフォルト | 型 | 説明 |
|---|---|---|---|
| type | — | string | "server_vad" 固定 |
| threshold | 0.5 | float 0.0-1.0 | VAD 起動音量閾値。高いほど大きな音でのみ発話検出 |
| prefix_padding_ms | 300 | int (ms) | 発話開始前に遡って含める音声バッファ |
| silence_duration_ms | 500 | int (ms) | この無音が続いたら発話終了と判定 |
| create_response | — | bool | VAD 停止時に自動レスポンス生成するか |
| interrupt_response | — | bool | VAD 開始時に進行中レスポンスを中断するか |

#### 1.3 VAD ON 時の audio commit タイミング変化

現状（VAD なし）:
- クライアントが session.input_audio_buffer.append で音声を連続送信
- サーバー側は音声を受け取り続け、バッファを常時処理 = 常時 input トークン課金

VAD ON 後:
- クライアントは引き続き session.input_audio_buffer.append で連続送信（変更なし）
- サーバーが VAD 判定を行い、発話区間のみを翻訳処理する
- 発話終了時に input_audio_buffer.speech_stopped イベントが届き、
  続いて input_audio_buffer.committed で翻訳が確定
- **無音区間の翻訳処理（= input トークン課金）が発生しない** ← W-COST-3 の核心

#### 1.4 コスト削減見込み

- 典型的な会議音声の無音区間比率: 40〜60%（相槌・考え中・話者交代）
- **想定削減: input トークンの 30〜50%**
- 系統 A/B 同時稼働時: 効果 2 倍

#### 1.5 既存 fallback timer との関係

現行 _recv_loop の fallback timer（_FALLBACK_TIMEOUT_SEC = 3.0）の変更は不要:

- VAD ON 時も session.output_transcript.done は引き続き送られるため fallback の基本動作は変わらない
- 無音区間では session.output_transcript.delta が送られないため fallback_timer は起動しない（正常動作）
- fallback_timer は done イベントが届かない異常系のセーフティネットとして維持する
- **変更不要**: コードの変更なし。_recv_loop 内の fallback_timer 付近にコメント追記のみ

---

### 2. 想定される効果と副作用

#### 2.1 効果

| 効果 | 説明 |
|---|---|
| input トークン課金削減 | 無音区間が課金対象から外れる。30〜50% 削減見込み |
| 翻訳精度の安定 | 発話単位でまとめて翻訳されるため細切れ翻訳が減る可能性 |
| API 負荷軽減 | 小さなチャンクが翻訳キューに積まれなくなる |

#### 2.2 副作用候補

| 副作用 | リスク | 対策 |
|---|---|---|
| 短い発話の取りこぼし | 中 | prefix_padding_ms=300ms で発話頭を遡り取得 |
| 背景雑音による誤検知 | 中 | threshold を環境に合わせて調整（0.5〜0.7） |
| 翻訳の遅延増加 | 低〜中 | VAD 判定後に翻訳が始まるため最大 silence_duration_ms（500ms）追加遅延 |
| API が turn_detection を拒否 | 高 | TBD-3-1。拒否時は W-COST-3 を将来課題に棚上げ |
| input_audio_buffer.committed 未サポート | 中 | 2026 年リリース直後の API のため（TBD-3-1） |

---

### 3. 実装案の比較

| 評価軸 | 案 A: 常時 VAD ON | 案 B: UI 切替 | 案 C: 系統ごと切替 |
|---|---|---|---|
| コスト削減効果 | 最大・即時 | 最大（ON 時） | 部分的 |
| 実装複雑度 | 低（session.update 追記のみ） | 中（W-COST-1 相当） | 案 B に包含 |
| UX | 最良（設定不要） | 良（明示的 ON/OFF） | 最も柔軟 |
| API リスク対処 | 困難（一括 ON） | 容易（OFF 切替可） | 容易（片系統 OFF） |
| 後退リスク | 高 | 低 | 低 |
| 推奨 | API 検証後 | **主推奨** | 案 B に包含 |

推奨: 段階的アプローチ（API 検証ブランチ先行 → 案 A か案 B を決定）

1. API 検証リスクの分離: まず検証ブランチで audio.input.turn_detection を追加し、
   verbose ログで session.updated または RT_ERROR を確認する（TBD-3-1）。
2. 検証成功 → 案 A で先行リリース可能: PR1+2 のみ（UI 結線省略）。
3. API 拒否 → W-COST-3 を将来課題に棚上げ: W-COST-1 の audio.output.format と同様の前例に従う。
4. TBD-3-2（監督判断）: 案 A か案 B かは監督に確認すること。

---
## インターフェース定義

### RealtimeTranslator.__init__ の追加パラメータ

    def __init__(
        self,
        # ... 既存パラメータ省略 ...
        request_source_transcript: bool = True,    # 既存（W-COST-2）
        vad_enabled: bool = False,                  # W-COST-3 追加（監督判断: False を採用・安全側）
        vad_threshold: float = 0.5,                # W-COST-3 追加
        vad_prefix_padding_ms: int = 300,           # W-COST-3 追加
        vad_silence_duration_ms: int = 500,         # W-COST-3 追加
    ):

### _run_session の session.update 変更

変更前（現状 realtime_translator.py:284-294）:

    audio_section: dict = {}
    if self._request_source_transcript:
        audio_section["input"] = {
            "transcription": {"model": "gpt-realtime-whisper"}
        }
    if self._request_audio_output:
        audio_section["output"] = {"language": self._target_language_code}
    await ws.send(json.dumps({"type": "session.update", "session": {"audio": audio_section}}))

変更後（W-COST-3 追加後）:

    audio_section: dict = {}
    if self._request_source_transcript:
        input_cfg: dict = {"transcription": {"model": "gpt-realtime-whisper"}}
        # W-COST-3: VAD 有効時は turn_detection を追加して無音区間 input トークン課金を停止
        # VAD ON 時: 無音区間では delta が来ないため fallback_timer は起動しない（正常動作）
        if self._vad_enabled:
            input_cfg["turn_detection"] = {
                "type": "server_vad",
                "threshold": self._vad_threshold,
                "prefix_padding_ms": self._vad_prefix_padding_ms,
                "silence_duration_ms": self._vad_silence_duration_ms,
            }
        audio_section["input"] = input_cfg
    if self._request_audio_output:
        audio_section["output"] = {"language": self._target_language_code}
    await ws.send(json.dumps({"type": "session.update", "session": {"audio": audio_section}}))

### CaptionSystem.__init__ の追加パラメータ

    def __init__(
        self,
        # ... 既存パラメータ省略 ...
        request_source_transcript: bool = True,
        vad_enabled: bool = False,                 # 監督判断: False を採用（安全側・既存挙動維持）
        vad_threshold: float = 0.5,
        vad_prefix_padding_ms: int = 300,
        vad_silence_duration_ms: int = 500,
    ):

### RouteConfig の追加フィールド（dataclass）

    @dataclass
    class RouteConfig:
        route_id: str
        input_device_info: dict
        target_language_code: str
        audio_output_enabled: bool
        output_device_index: int | None
        output_volume: float
        request_source_transcript: bool = True    # W-COST-2（既存）
        vad_enabled: bool = False                 # W-COST-3 追加（監督判断: False を採用・安全側）
        vad_silence_duration_ms: int = 500        # W-COST-3 追加
        vad_threshold: float = 0.5                # W-COST-3 追加
        vad_prefix_padding_ms: int = 300          # W-COST-3 追加

### MultiCaptionSystem 内の CaptionSystem 生成への追加引数

main.py の route_a 生成（L1605-1618 付近）と route_b 生成（L1627-1640 付近）に追加:

    CaptionSystem(
        # ... 既存引数省略 ...
        request_source_transcript=route_X.request_source_transcript,
        vad_enabled=route_X.vad_enabled,
        vad_threshold=route_X.vad_threshold,
        vad_prefix_padding_ms=route_X.vad_prefix_padding_ms,
        vad_silence_duration_ms=route_X.vad_silence_duration_ms,
    )

---
## 制約・前提条件

- 対象モード: openai-realtime のみ。Whisper + DeepL モードは対象外
- request_source_transcript=False のときは VAD 設定も無効（audio.input キー自体が除外される）
- API 側 VAD サポートの前提: gpt-realtime-translate エンドポイントが turn_detection を受け付けることが前提（TBD-3-1）
- 既存 fallback timer は維持: _FALLBACK_TIMEOUT_SEC タイマーは削除しない
- VAD ON 時も session.input_audio_buffer.append は継続送信: クライアント側の送信方式は変更しない
- テストのサンプル値: API キーには必ずフェイク値（"sk-test-fake-vad001" 形式）を使用すること。実 API キーはコードに絶対に含めない

---

## 受け入れ条件

- [ ] vad_enabled=True のとき、session.update の session.audio.input に turn_detection キーが含まれる（モック WS 検証）
- [ ] turn_detection.type が "server_vad" である
- [ ] turn_detection.threshold / prefix_padding_ms / silence_duration_ms が指定値通りに送信される
- [ ] vad_enabled=False のとき、session.update に turn_detection キーが含まれない（既存挙動）
- [ ] request_source_transcript=False のとき、VAD 設定に関係なく audio.input キー自体が存在しない（W-COST-2 の既存テスト維持）
- [ ] request_audio_output と vad_enabled の 2x2 組み合わせで session.update の構造が正しい（マトリクステスト）
- [ ] RealtimeTranslator のデフォルト値が vad_enabled=False、vad_threshold=0.5、vad_silence_duration_ms=500、vad_prefix_padding_ms=300 である（監督判断: False を採用）
- [ ] RouteConfig.vad_enabled のデフォルト値が False である（監督判断: False を採用・安全側・既存挙動維持）
- [ ] CaptionSystem._create_realtime_translator() が RouteConfig の VAD 設定を RealtimeTranslator に渡す（単体テスト）
- [ ] 案 B 採用時: vad_enabled チェックボックスが UI に表示され、変更が settings.json に永続化される（TBD-3-2 次第）
- [ ] 既存テスト（125 件）が全 PASS のまま維持される

---

## PR 分割案

### PR1: realtime_translator.py — VAD パラメータを session.update に追加

変更ファイル: realtime_translator.py

変更内容:
- __init__ に vad_enabled: bool = True 等 4 パラメータを追加（後方互換）
- _run_session の session.update 送信ロジックを変更（インターフェース定義参照）
- _recv_loop に input_audio_buffer.speech_started/stopped/committed イベントの verbose ログ追記
  （RT_VAD_SPEECH_STARTED / RT_VAD_SPEECH_STOPPED / RT_VAD_COMMITTED として記録）
- fallback timer 付近に VAD ON 時の動作説明コメントを追記

新規テスト（tests/test_realtime_translator.py）:
- test_session_update_includes_turn_detection_when_vad_enabled
- test_session_update_excludes_turn_detection_when_vad_disabled
- test_session_update_turn_detection_params
- test_session_update_2x2_matrix（request_audio_output x vad_enabled の 4 ケース）

影響範囲: realtime_translator.py のみ

---

### PR2: main.py — RouteConfig と CaptionSystem に VAD フィールド追加

変更ファイル: main.py

変更内容:
- RouteConfig に VAD フィールド 4 つを追加（デフォルト値付き、後方互換）
- CaptionSystem.__init__ に VAD パラメータを追加
- CaptionSystem._create_realtime_translator() で VAD パラメータを RealtimeTranslator に渡す
- MultiCaptionSystem.__init__ 内の route_a/b 生成コードに VAD 引数を追加

新規テスト:
- test_create_realtime_translator_vad_enabled
- test_create_realtime_translator_vad_disabled

影響範囲: main.py のみ

---

### PR3（案 B 採用時のみ）: app.py — VAD ON/OFF の UI 結線（TBD-3-2）

変更ファイル: app.py

変更内容:
- TAG_ROUTE_A_VAD_ENABLE / TAG_ROUTE_B_VAD_ENABLE チェックボックスの追加
- _create_konnyaku_system() で settings.json から vad_enabled を読み込み RouteConfig に反映
- _save_settings() で vad_enabled を保存

TBD-3-2: PR3 の実施判断は監督。案 A（常時 ON）で進める場合は PR3 不要。

---

## 設定パラメータ推奨値（会議翻訳 / 監督用途向け）

| パラメータ | 推奨値 | 理由 |
|---|---|---|
| silence_duration_ms | 500ms（デフォルト） | 会議での話者交代は 0.5 秒前後の間が多い |
| threshold | 0.5（デフォルト） | 静かな会議室ならデフォルト、雑音が多い環境は 0.6〜0.7 |
| prefix_padding_ms | 300ms（デフォルト） | 発話頭の取りこぼしを防ぐ |

VB-CABLE 経由 Zoom 音声ではループバック音声が常に音が入っている状態に近くなるため、
実際の Zoom セッションで verbose ログ（RT_VAD_SPEECH_STARTED/STOPPED）を確認しながら調整すること。

---

## TBD 一覧（監督判断事項）

| TBD | 内容 | 影響 |
|---|---|---|
| ~~TBD-3-1~~ | ~~gpt-realtime-translate エンドポイントが audio.input.turn_detection を受け入れるか実機検証が必要~~ | **✅ クローズ済み（2026-05-16 実機検証）。tools/test_vad_api_smoke.py で audio.input.turn_detection を受理・接続成功を確認。API エラーなし。** |
| TBD-3-2 | 案 A（常時 ON、PR1+2 のみ）か案 B（UI 切替、PR1+2+3）か | PR3 の要否。初期値 True/False の選択 |
| ~~TBD-3-3~~ | ~~vad_enabled のデフォルト値: True（即時効果）か False（既存挙動維持・安全側）か~~ | **監督判断: False を採用（安全側・既存挙動維持）[クローズ]** |

---

## 実装メモ（PRGちゃんへの引き継ぎ）

### 最優先確認事項

TBD-3-1（API 検証）が最重要。実装前に検証ブランチを作ることを強く推奨する。

確認手順:
1. PR1 相当の変更（vad_enabled=True で turn_detection を追加）をブランチで実装
2. 実際の API に接続し verbose ログを確認
3. RT_RAW_UNKNOWN に type: session.updated が現れるか確認（= API が turn_detection を受け入れた証拠）
4. RT_ERROR に Unknown parameter が出たら W-COST-3 は API 非対応として撤退

### PR1 の変更ポイント

- __init__ シグネチャ変更は後方互換（デフォルト引数のみ追加）
- audio_section["input"] への turn_detection 追加は _request_source_transcript=True ブランチ内のみ
- _request_source_transcript=False のパスは W-COST-2 と同じで変更不要
- verbose ログ用 elif 分岐で input_audio_buffer.speech_started/stopped/committed を個別に記録する

### PR2 の変更ポイント

- RouteConfig はデータクラス。デフォルト値付きフィールドの追加は後方互換（既存コード破壊なし）
- MultiCaptionSystem.__init__ の route_a 生成（main.py:L1605-1618 付近）と
  route_b 生成（main.py:L1627-1640 付近）に VAD 引数を追加する
- テストは object.__new__(CaptionSystem) パターン（既存の手法）で重い依存をバイパスして記述する

### テストのフェイク API キー形式

追加するテストの api_key は "sk-test-fake-vad001" 等の形式で統一。
.gitleaks.toml allowlist パターン sk-test-fake-[A-Za-z0-9]+ に準拠する（ハイフン無しサフィックス）。

### W-COST-1/2/3 の整合性

| Issue | フラグ | 制御対象 | 状態 |
|---|---|---|---|
| W-COST-1 | request_audio_output | audio.output の有無 | 実装済み |
| W-COST-2 | request_source_transcript | audio.input の有無 | 実装済み |
| W-COST-3 | vad_enabled | audio.input.turn_detection の有無 | 本件 |

3 フラグは独立しており互いに干渉しない設計。

### fallback timer について

削除禁止。_recv_loop の fallback timer 付近にコメント追記のみ（コードの変更なし）:

    # VAD ON 時: 無音区間では session.output_transcript.delta が送られないため
    # fallback_timer は起動しない（正常動作）。
    # fallback_timer は done イベントが届かない異常系のセーフティネットとして維持する。

---

## codex 利用
- codex 利用: なし
