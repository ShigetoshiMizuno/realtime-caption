---
commission_id: billing-lamp-3color
parent_id: (top-level / prg-designer)
mode: impl
enforce: strict
max_depth: 0
due_at: 2026-05-28T23:59:00+09:00
---

# 発注書: B-4 — 課金ランプ 3 色化実装 (SPEC SEM-B5 / §20.3)

## 目的

SPEC SEM-B5 / §20.3 の確定仕様に従い、A/B 個別課金ランプを
`audio_gate` 状態を反映した 3 色（赤/黄/緑）に切り替える。

現行実装では `_get_route_billing_active(bool)` で赤/緑の 2 色しか対応していない。
`audio_gate=False` かつ `STARTING/RUNNING` の「接続中待機」状態が黄色で表現できていない。

## スコープ

- **操作可能なファイル:** `app.py` のみ
- スコープ外のファイルは一切変更しないこと

## 仕様

### 1. 色定数の追加（app.py 2547 行付近）

以下の 3 定数を新規追加すること。既存の `_BILLING_ROUTE_LAMP_COLOR_ON/OFF` は
後方互換のため **削除しない**（既存テスト test_billing_lamp.py が参照しているため）。

```python
_BILLING_ROUTE_LAMP_COLOR_RED    = (220, 0, 0, 255)    # 赤: 送信中（audio_gate=True）
_BILLING_ROUTE_LAMP_COLOR_YELLOW = (255, 200, 0, 255)  # 黄: 接続中待機（STARTING/RUNNING + gate=False）
_BILLING_ROUTE_LAMP_COLOR_GREEN  = (0, 200, 0, 255)    # 緑: 停止（IDLE 等）
```

**配置場所:** 既存 `_BILLING_ROUTE_LAMP_COLOR_ON/OFF` の直後（約 2549 行）。

### 2. `_get_route_billing_color(route_id: str) -> str` 関数の新規追加

**責務:** audio_gate 状態と RouteState から 3 色文字列を返す純関数。

**シグネチャ:**
```python
def _get_route_billing_color(route_id: str) -> str:
    """系統の課金ランプ色を "red" / "yellow" / "green" で返す（audio_gate ベース）。

    | 条件                                              | 戻り値   | 色 |
    |---------------------------------------------------|---------|-----|
    | audio_gate_open == True                           | "red"   | 赤 |
    | state in (STARTING, RUNNING) + gate=False         | "yellow"| 黄 |
    | それ以外（IDLE / ERROR / STOPPING / route=None 等）| "green" | 緑 |

    Parameters
    ----------
    route_id : str
        "a" または "b"

    Returns
    -------
    str
        "red" / "yellow" / "green"
    """
```

**ロジック（疑似コード）:**
```
system = _konnyaku_system
if system is None:
    return "green"

route = system.route_a_system if route_id == "a" else system.route_b_system
if route is None:
    return "green"

# audio_gate が開いていれば送信中（赤）— state 問わず
if getattr(route, "audio_gate_open", False):
    return "red"

# 接続中だがゲート閉（黄）
if route.state in (RouteState.STARTING, RouteState.RUNNING):
    return "yellow"

# それ以外（IDLE/ERROR 等）は停止（緑）
return "green"
```

**配置場所:** 既存 `_get_route_billing_active` の直後（約 2533 行）。

### 3. `_update_billing_lamp` の A/B 個別ランプ更新を 3 色対応に変更

**変更前（app.py 2564-2578 付近）:**
```python
    # 系統A 個別ランプ
    if dpg.does_item_exist(TAG_BILLING_LAMP_A):
        a_on = _get_route_billing_active("a")
        dpg.configure_item(
            TAG_BILLING_LAMP_A,
            color=_BILLING_ROUTE_LAMP_COLOR_ON if a_on else _BILLING_ROUTE_LAMP_COLOR_OFF,
        )

    # 系統B 個別ランプ
    if dpg.does_item_exist(TAG_BILLING_LAMP_B):
        b_on = _get_route_billing_active("b")
        dpg.configure_item(
            TAG_BILLING_LAMP_B,
            color=_BILLING_ROUTE_LAMP_COLOR_ON if b_on else _BILLING_ROUTE_LAMP_COLOR_OFF,
        )
```

**変更後:**
```python
    # 系統A 個別ランプ（3 色: audio_gate ベース）
    if dpg.does_item_exist(TAG_BILLING_LAMP_A):
        _BILLING_ROUTE_3COLOR = {
            "red":    _BILLING_ROUTE_LAMP_COLOR_RED,
            "yellow": _BILLING_ROUTE_LAMP_COLOR_YELLOW,
            "green":  _BILLING_ROUTE_LAMP_COLOR_GREEN,
        }
        a_color = _BILLING_ROUTE_3COLOR[_get_route_billing_color("a")]
        dpg.configure_item(TAG_BILLING_LAMP_A, color=a_color)

    # 系統B 個別ランプ（3 色: audio_gate ベース）
    if dpg.does_item_exist(TAG_BILLING_LAMP_B):
        _BILLING_ROUTE_3COLOR = {
            "red":    _BILLING_ROUTE_LAMP_COLOR_RED,
            "yellow": _BILLING_ROUTE_LAMP_COLOR_YELLOW,
            "green":  _BILLING_ROUTE_LAMP_COLOR_GREEN,
        }
        b_color = _BILLING_ROUTE_3COLOR[_get_route_billing_color("b")]
        dpg.configure_item(TAG_BILLING_LAMP_B, color=b_color)
```

**注意:** `_BILLING_ROUTE_3COLOR` は関数スコープ内の dict で構わない。
または関数の外にモジュール定数として定義してもよい（どちらでも可）。

## 受け入れ条件

以下コマンドで **29 / 29 PASS** になること:

```bash
python -m pytest tests/test_billing_lamp_3color.py -v
```

かつ、既存テストを壊さないこと:

```bash
python -m pytest tests/test_billing_lamp.py -v
```

（24 / 24 PASS を維持）

## テストケース一覧（全件 RED 確認済み）

テストファイル: `tests/test_billing_lamp_3color.py`（コミット済み）

### TestGetRouteBillingColor（17 件）

| # | テスト名 | 確認内容 |
|---|----------|----------|
| 1 | `test_system_none_returns_green` | system=None → "green" |
| 2 | `test_system_none_route_b_returns_green` | system=None、route_id="b" → "green" |
| 3 | `test_route_none_returns_green` | route_a=None → "green" |
| 4 | `test_route_b_none_returns_green` | route_b=None → "green" |
| 5 | `test_red_when_running_and_gate_open_route_a` | A: RUNNING+gate=True → "red" |
| 6 | `test_red_when_starting_and_gate_open_route_a` | A: STARTING+gate=True → "red" |
| 7 | `test_red_when_running_and_gate_open_route_b` | B: RUNNING+gate=True → "red" |
| 8 | `test_red_when_starting_and_gate_open_route_b` | B: STARTING+gate=True → "red" |
| 9 | `test_red_when_idle_but_gate_open_route_a` | A: IDLE+gate=True → "red"（gate 優先） |
| 10 | `test_yellow_when_running_and_gate_closed_route_a` | A: RUNNING+gate=False → "yellow" |
| 11 | `test_yellow_when_starting_and_gate_closed_route_a` | A: STARTING+gate=False → "yellow" |
| 12 | `test_yellow_when_running_and_gate_closed_route_b` | B: RUNNING+gate=False → "yellow" |
| 13 | `test_yellow_when_starting_and_gate_closed_route_b` | B: STARTING+gate=False → "yellow" |
| 14 | `test_green_when_idle_route_a` | A: IDLE+gate=False → "green" |
| 15 | `test_green_when_idle_route_b` | B: IDLE+gate=False → "green" |
| 16 | `test_green_when_error_route_a` | A: ERROR+gate=False → "green" |
| 17 | `test_green_when_stopping_route_b` | B: STOPPING+gate=False → "green" |

### TestBillingRouteLampColorConstants（3 件）

| # | テスト名 | 確認内容 |
|---|----------|----------|
| 18 | `test_color_red_exists_and_is_correct` | _BILLING_ROUTE_LAMP_COLOR_RED == (220, 0, 0, 255) |
| 19 | `test_color_yellow_exists_and_is_correct` | _BILLING_ROUTE_LAMP_COLOR_YELLOW == (255, 200, 0, 255) |
| 20 | `test_color_green_exists_and_is_correct` | _BILLING_ROUTE_LAMP_COLOR_GREEN == (0, 200, 0, 255) |

### TestUpdateBillingLamp3Color（9 件）

| # | テスト名 | 確認内容 |
|---|----------|----------|
| 21 | `test_route_a_gate_open_lamp_a_is_red` | A gate=True → LAMP_A 赤 |
| 22 | `test_route_b_gate_open_lamp_b_is_red` | B gate=True → LAMP_B 赤 |
| 23 | `test_route_a_running_gate_closed_lamp_a_is_yellow` | A RUNNING+gate=False → LAMP_A 黄 |
| 24 | `test_route_b_starting_gate_closed_lamp_b_is_yellow` | B STARTING+gate=False → LAMP_B 黄 |
| 25 | `test_route_a_idle_lamp_a_is_green` | A IDLE → LAMP_A 緑 |
| 26 | `test_route_b_idle_lamp_b_is_green` | B IDLE → LAMP_B 緑 |
| 27 | `test_both_gates_open_both_lamps_are_red` | A/B 両 gate=True → A/B 両 赤 |
| 28 | `test_a_gate_open_b_running_gate_closed` | A 赤 + B 黄 の混在 |
| 29 | `test_system_none_both_lamps_are_green` | system=None → A/B 両 緑 |

## 配線チェック（必須）

実装後に以下を確認すること:

```bash
# _get_route_billing_color が _update_billing_lamp から呼ばれていること
grep -n "_get_route_billing_color" app.py
```

`_update_billing_lamp` 内の呼び出し行が含まれていること（def 定義行以外にも使用行があること）。

## 完了報告

`commissions/billing-lamp-3color/report.md` に以下を記載:
- テスト PASS 件数（新規 29 件 + 既存 24 件）
- 変更した行数（app.py の diff 統計）
- 配線チェック結果
- codex 利用: あり/なし
