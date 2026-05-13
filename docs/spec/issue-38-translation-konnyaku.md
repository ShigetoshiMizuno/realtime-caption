# Issue #38 詳細仕様 — 双方向同時翻訳「翻訳こんにゃくモード」

関連: [Issue #38](https://github.com/ShigetoshiMizuno/realtime-caption/issues/38)

## 1. CaptionSystem 2系統並列化

### 拡張 vs 新設の比較

| 観点 | 既存 CaptionSystem 拡張 | MultiCaptionSystem 新設 |
|---|---|---|
| コード再利用 | 既存テストへの影響大 | 既存 CaptionSystem をそのまま再利用可 |
| テスト互換性 | `test_caption_system_thread_safety.py` が壊れる可能性あり | 既存テスト無影響 |
| 責務分離 | 1クラスが肥大化 | 薄いオーケストレーター層として分離 |
| 実装工数 | 中 | 小（インスタンス2つ起動するだけ） |

**判断: `MultiCaptionSystem` 新設を推奨。** `CaptionSystem` は1系統の責務を維持し、既存テストへの影響ゼロ。

### クラス構造

```
MultiCaptionSystem
  ├── route_a: CaptionSystem  （config + RouteConfig で生成）
  ├── route_b: CaptionSystem  （config + RouteConfig で生成）
  ├── start() → route_a.start(), route_b.start() を順次呼び出し
  ├── shutdown() → route_a.shutdown(), route_b.shutdown()
  └── route_a / route_b をプロパティで公開（GUI からレベルメーター取得用）
```

### インターフェース定義

```python
@dataclass
class RouteConfig:
    route_id: str            # "a" | "b"
    input_device_info: dict
    target_language_code: str       # "ja" | "en"（言語テーブルから選択）
    audio_output_enabled: bool
    output_device_index: int | None
    output_volume: float            # 0.0〜2.0

class MultiCaptionSystem:
    def __init__(
        self,
        config: dict,
        route_a: RouteConfig,
        route_b: RouteConfig,
        on_result_a: Callable[[str, str], None] | None = None,
        on_result_b: Callable[[str, str], None] | None = None,
        on_ready: Callable[[], None] | None = None,
    ) -> None: ...

    def start(self) -> None: ...
    def shutdown(self) -> None: ...

    @property
    def route_a_system(self) -> CaptionSystem: ...

    @property
    def route_b_system(self) -> CaptionSystem: ...

    @property
    def total_estimated_cost_usd(self) -> float: ...
```

### スレッド安全性

- `CaptionSystem` の `AudioStats`（frozen dataclass + Lock）は既存のまま流用
- 各系統は独立した `asyncio` イベントループスレッドを持つ
- `MultiCaptionSystem` 自体はスレッドを持たず、2インスタンスのライフサイクルを管理するのみ
- `test_caption_system_thread_safety.py` は `CaptionSystem` 単体テストのため無影響

## 2. WebSocket メッセージ仕様

### 既存フォーマット

```json
{"original": "You are absolutely right.", "translated": "あなたの言う通りです。"}
```

`overlay.html` は `data.original` / `data.translated` の2フィールドを参照。

### 拡張案: `route` フィールド追加

```json
{"original": "...", "translated": "...", "route": "a"}
{"original": "...", "translated": "...", "route": "b"}
```

- 既存の片方向モードは `route` を持たない（または省略）
- overlay.html 側で `route` 未定義時は経路Aと同一扱い

### 後方互換性

| ケース | `route` フィールド | overlay.html の動作 |
|---|---|---|
| 既存片方向モード | なし（省略） | `route ?? "a"` でフォールバック |
| 翻訳こんにゃくモード A | `"a"` | 経路Aエリアに表示 |
| 翻訳こんにゃくモード B | `"b"` | 経路Bエリアに表示 |

## 3. 言語テーブル定義

### `constants.py`（新規）

```python
# 言語テーブル: (コード, 表示名) のリスト。1行追加で言語追加可能。
# 将来 zh/ko/es を追加するときはここにタプルを1行追記するだけ。
# OpenAI Realtime Translate の BCP-47 コードに準拠すること。
SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("ja", "日本語"),
    ("en", "English"),
    # ("zh", "中文"),      # 将来: 中国語
    # ("ko", "한국어"),    # 将来: 韓国語
    # ("es", "Español"),   # 将来: スペイン語
]

def get_language_display_name(code: str) -> str:
    return next((name for c, name in SUPPORTED_LANGUAGES if c == code), code)

def get_language_codes() -> list[str]:
    return [code for code, _ in SUPPORTED_LANGUAGES]

def get_language_display_names() -> list[str]:
    return [name for _, name in SUPPORTED_LANGUAGES]
```

### 各コンポーネントでの利用

| 利用箇所 | 使い方 |
|---|---|
| `app.py` GUI コンボ | `get_language_display_names()` でアイテムリスト生成 |
| `RouteConfig.target_language_code` | `get_language_codes()` の値を保持 |
| `RealtimeTranslator` | `target_language_code` にそのまま渡す |
| `config.yaml` 読み込み | `get_language_codes()` でバリデーション |

## 4. cost_monitor の 2倍化

### 方針

**`CostMonitor` 自体は変更しない。呼び出し側（`MultiCaptionSystem`）で2インスタンス管理。**

各 `CaptionSystem`（route_a / route_b）が既存通り独自の `CostMonitor` を持つ。`MultiCaptionSystem` に合算ヘルパー：

```python
@property
def total_estimated_cost_usd(self) -> float:
    cost_a = self.route_a_system._cost_monitor.estimated_cost_usd() \
        if self.route_a_system._cost_monitor else 0.0
    cost_b = self.route_b_system._cost_monitor.estimated_cost_usd() \
        if self.route_b_system._cost_monitor else 0.0
    return cost_a + cost_b
```

`max_session_minutes` は既存設定をそのまま使う。コスト等価で制限したい場合は半分に設定するか、将来 issue で合算ガード機能を追加。

### 既存テスト互換性

`test_cost_monitor.py` は `CostMonitor` 単体テスト。`RATE_USD_PER_MINUTE` の値も変更しないため**既存テスト全て無影響**。

## 5. overlay.html の双方向レイアウト

### 既存構造

- `#subtitle-container`（bottom: 60px の fixed 位置）に `#original-text`（22px）+ `#translated-text`（36px）の2段
- `showSubtitle(original, translated)` 関数が経路不問で単一エリアに表示

### 拡張設計：上下2段レイアウト

```
┌─────────────────────────────────────────┐
│ [系統A] You are absolutely right.       │ ← 経路A 原文（小）
│         あなたの言う通りです。          │ ← 経路A 翻訳（大）
├─────────────────────────────────────────┤
│ [系統B] では始めましょう。              │ ← 経路B 原文（小）
│         Let's get started.              │ ← 経路B 翻訳（小・確認）
└─────────────────────────────────────────┘
```

各経路コンテナは既存 CSS を踏襲し、`bottom` 位置だけずらす。フェードアウトタイマーは経路A・B それぞれ独立変数で管理。

### JS の振り分けロジック

```javascript
ws.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    const route = data.route ?? "a";  // 後方互換: route未定義は "a" 扱い
    if (data.original !== undefined && data.translated !== undefined) {
        showSubtitle(route, data.original, data.translated);
    }
});
```

## 6. TDD RED テストケース一覧

### 新規ファイル

- `tests/test_multi_caption_system.py`
- `tests/test_language_table.py`
- `tests/test_cost_monitor_dual.py`

### MultiCaptionSystem の起動・停止

```python
def test_multi_caption_system_can_be_instantiated():
    """MultiCaptionSystem が RouteConfig 2つを受け取りインスタンス化できること。"""

def test_multi_caption_system_has_two_routes():
    """route_a_system, route_b_system プロパティが CaptionSystem 型を返すこと。"""

def test_multi_caption_system_shutdown_stops_both():
    """shutdown() を呼ぶと両系統の stop_event がセットされること。"""
```

### 経路A・B が独立したセッションを持つこと

```python
def test_route_a_and_b_have_different_translator_instances():
    """route_a と route_b の _realtime_translator が別インスタンスであること。"""

def test_route_a_and_b_have_independent_audio_stats():
    """経路Aの AudioStats 更新が経路Bに影響しないこと。"""
```

### 言語テーブル（`tests/test_language_table.py`）

```python
def test_get_language_display_name_ja():
    """get_language_display_name("ja") が "日本語" を返すこと。"""

def test_get_language_display_name_en():
    """get_language_display_name("en") が "English" を返すこと。"""

def test_get_language_display_name_unknown_returns_code():
    """未定義コードはコード文字列をそのまま返すこと。"""

def test_supported_languages_contains_ja_and_en():
    """SUPPORTED_LANGUAGES に ja と en が含まれること。"""
```

### cost_monitor 2倍化（`tests/test_cost_monitor_dual.py`）

```python
def test_total_estimated_cost_is_sum_of_two_sessions():
    """MultiCaptionSystem.total_estimated_cost_usd が両モニターの合算を返すこと。"""

def test_dual_rate_is_approximately_0_068_per_minute():
    """1分間動作時の合算コストが約 $0.068 であること（±10%）。"""
```

### 出力デバイスが各系統で独立

```python
def test_route_a_output_device_independent_from_route_b():
    """route_a の output_device_index 変更が route_b に影響しないこと。"""
```

### 既存テストへの影響範囲

| テストファイル | 影響 | 理由 |
|---|---|---|
| `test_caption_system_thread_safety.py` | **なし** | `CaptionSystem` 単体テスト、変更なし |
| `test_cost_monitor.py` | **なし** | `RATE_USD_PER_MINUTE` 変更なし |
| `test_realtime_translator.py` | **なし** | `RealtimeTranslator` 変更なし |
| `test_audio_output.py` | **なし** | `AudioOutputStream` 変更なし |
| `test_main_devices.py` | **要確認** | `list_audio_devices` を使用 |
| `test_app_gui.py` | **要確認** | GUI セクション追加 |

## 7. 実装手順（Phase 分け）

### Phase 1: 基盤（影響範囲最小・テスト先行）
1. `constants.py` に `SUPPORTED_LANGUAGES` テーブル作成
2. `test_language_table.py` RED → GREEN

### Phase 2: コア（MultiCaptionSystem）
3. `main.py` に `RouteConfig` dataclass + `MultiCaptionSystem` クラス追加
4. `test_multi_caption_system.py` RED → GREEN
5. `_realtime_broadcast` に `route` 引数を追加、WebSocket payload に `route` フィールド含める

### Phase 3: overlay.html
6. `overlay.html` を上下2段レイアウトに改修。経路A/B 振り分けロジック実装
7. ブラウザ手動確認

### Phase 4: GUI（app.py）
8. 経路A・B セクションの描画（Dear PyGui の `child_window` または `group`）
9. 「翻訳こんにゃくモード」プリセットボタン
10. 独立レベルメーター × 4 の接続

### Phase 5: 統合
11. `config.yaml.example` に `translation_konnyaku` セクション追加
12. `README.md` にセットアップ手順追記
13. 既存テスト全件 GREEN 確認

## 8. リスク・懸念点

### R1: asyncio ループの競合
既存 `CaptionSystem.start()` は内部で `asyncio.new_event_loop()` スレッドを起動する。2インスタンスが独立ループを持つことを確認し、`asyncio.get_event_loop()` のグローバル汚染がないか検証必要。

### R2: PyAudio インスタンスの共有
`CaptionSystem` が内部で `pyaudio.PyAudio()` を生成する場合、2インスタンスが別々に生成すると PA の初期化が2回走る。`pa.terminate()` のタイミングも競合し得る。`MultiCaptionSystem` で `PyAudio` インスタンスを1つ共有する設計が安全かを PRG が実装前に確認。

### R3: OpenAI API レートリミット
同一 API キーで同時に2 WebSocket セッション。OpenAI の同時接続数制限（tier により異なる）に抵触する可能性。tier 確認と超過時のエラーハンドリング（`on_error` コールバック）を実装。

### R4: Windows WASAPI loopback + マイク 同時使用
経路A（loopback）と経路B（マイク）を同時に pyaudiowpatch でオープンする際、デバイス排他制御が問題になり得る。実機での結合テストを Phase 3 以前に実施推奨。

### R5: overlay.html の後方互換
既存片方向モードユーザーが `overlay.html` を更新すると、字幕レイアウトが変わる。`route ?? "a"` フォールバックで動作継続はできるが、事前周知が必要。

## 9. 実装メモ（PRGちゃんへの引き継ぎ）

- `CaptionSystem.__init__` は PyAudio の初期化を内部で持っているかを確認し、2インスタンス生成時の PyAudio 共有要否を判断してから実装（R2 検証）
- `_realtime_broadcast` への `route` 引数追加は既存の `on_result` コールバックシグネチャを変えないことを推奨（後方互換）
- `SubtitleBroadcaster` は `main.py` に定義されているが、`MultiCaptionSystem` で2系統が同一ブロードキャスターを共有することを推奨：overlay.html への WebSocket は1本
- サンプル値・設定値のハードコードは一切禁止。`config.yaml.example` のキー値はプレースホルダーのみ記載
- 各 Phase 完了時に QAちゃんレビューを通すこと
