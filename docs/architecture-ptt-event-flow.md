# PTT イベントフロー責任グラフ

生成日: 2026-05-22  
目的: 複数イベントソースのアクション漏れ検出 + オーナーシップ整理

---

## 1. 現状アーキテクチャ（As-Is）

```mermaid
flowchart LR
    classDef trigger  fill:#2980b9,color:#fff,stroke:#1a5276
    classDef command  fill:#e67e22,color:#fff,stroke:#a04000
    classDef state    fill:#27ae60,color:#fff,stroke:#145a32
    classDef ui       fill:#8e44ad,color:#fff,stroke:#4a235a
    classDef gap      fill:#e74c3c,color:#fff,stroke:#c0392b,stroke-dasharray:5 5

    subgraph LAYER_A["① 入力ソース層  (app.py — イベントハンドラ)"]
        direction TB
        F8P["⌨️ F8 press\n_on_ptt_press"]:::trigger
        F8R["⌨️ F8 release\n_on_ptt_release"]:::trigger
        BTNP["🖱️ GUI btn ↓\n_on_ptt_btn_pressed"]:::trigger
        BTNP_L["🖱️ GUI btn ↓ ラッチ中\n_on_ptt_btn_pressed\n(ラッチ解除分岐)"]:::trigger
        BTNR["🖱️ GUI btn ↑\n_on_ptt_btn_released"]:::trigger
        LATCHON["☑️ ラッチ ON\n_on_ptt_latch_changed"]:::trigger
        LATCHOFF["☑️ ラッチ OFF\n_on_ptt_latch_changed"]:::trigger
        RBON["🔘 系統B ON\n_on_route_b_enable_change\n(PTT有効時)"]:::trigger
        RBOFF["🔘 系統B OFF\n_on_route_b_enable_change\n(PTT有効時)"]:::trigger
    end

    subgraph LAYER_B["② コマンド層  (MultiCaptionSystem — 操作の実体)"]
        direction TB
        RF["resume_from_idle()"]:::command
        SB["start_route('b')\n※一部スレッド/直接混在"]:::command
        SO["stop_route('b')\n※一部スレッド/直接混在"]:::command
        OG["open_audio_gate()"]:::command
        CG["close_audio_gate()"]:::command
    end

    subgraph LAYER_C["③ 状態層  (CaptionSystem — 状態所有者)"]
        direction TB
        RS["RouteState\nIDLE→STARTING\n→RUNNING→STOPPING"]:::state
        AG["_audio_gate: bool\nFalse=音声ブロック\nTrue=音声通過"]:::state
    end

    subgraph LAYER_D["④ UI更新層  (app.py — 表示同期)"]
        direction TB
        GQ["_gui_queue\ncmd: update_ptt_visual\n(非メインスレッド→安全)"]:::ui
        VF["_update_ptt_visual_feedback()\n(一部直接呼び・一部queue経由)"]:::ui
        BL["_update_billing_lamp()"]:::ui
    end

    %% ──────────────────────────────────────────
    %% F8 press — 完全 (基準実装)
    F8P -->|"✅"| RF
    F8P -->|"✅ thread"| SB
    F8P -->|"✅"| OG
    F8P -->|"✅ queue"| GQ

    %% F8 release — 完全
    F8R -->|"✅"| CG
    F8R -->|"✅ thread"| SO
    F8R -->|"✅ queue"| GQ

    %% GUI btn pressed (通常) — 完全
    BTNP -->|"✅"| RF
    BTNP -->|"✅ thread"| SB
    BTNP -->|"✅"| OG
    BTNP -->|"✅ queue"| GQ

    %% GUI btn pressed (ラッチ解除) — W-5 欠落
    BTNP_L -->|"✅ thread"| SO
    BTNP_L -.->|"❌ W-5 欠落"| CG
    BTNP_L -->|"✅ queue"| GQ

    %% GUI btn released — 完全
    BTNR -->|"✅"| CG
    BTNR -->|"✅ thread"| SO
    BTNR -->|"✅ queue"| GQ

    %% Latch ON — open_audio_gate 欠落
    LATCHON -->|"✅"| RF
    LATCHON -->|"✅ direct"| SB
    LATCHON -.->|"❌ 欠落"| OG
    LATCHON -->|"✅ queue"| GQ

    %% Latch OFF — close_audio_gate 欠落
    LATCHOFF -->|"✅ thread"| SO
    LATCHOFF -.->|"❌ 欠落"| CG
    LATCHOFF -->|"✅ queue"| GQ

    %% 系統B ON (PTT有効) — open_audio_gate 欠落
    RBON -->|"✅"| RF
    RBON -->|"✅ direct"| SB
    RBON -.->|"❌ 欠落"| OG
    RBON -->|"✅ 直接呼び"| VF

    %% 系統B OFF (PTT有効) — close_audio_gate 欠落
    RBOFF -->|"✅ thread"| SO
    RBOFF -.->|"❌ 欠落"| CG
    RBOFF -->|"✅ 直接呼び"| VF

    %% State 更新
    SB --> RS
    SO --> RS
    OG --> AG
    CG --> AG

    %% UI 更新
    RS --> VF
    AG --> VF
    GQ --> VF
    VF --> BL
```

---

## 2. トリガー × アクション 漏れ検出マトリクス

✅ 実装あり　❌ 欠落（バグ）　－ 不要（意味的に無関係）　⚠️ 実装方式が不統一

| イベントトリガー | resume_from_idle | start_route('b') | open_audio_gate | stop_route('b') | close_audio_gate | update_ptt_visual |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| F8 press（**基準実装**） | ✅ | ✅ thread | ✅ | - | - | ✅ queue |
| F8 release | - | - | - | ✅ thread | ✅ | ✅ queue |
| GUI btn ↓（通常） | ✅ | ✅ thread | ✅ | - | - | ✅ queue |
| GUI btn ↓（**ラッチ解除**） | - | - | - | ✅ thread | **❌ W-5** | ✅ queue |
| GUI btn ↑ | - | - | - | ✅ thread | ✅ | ✅ queue |
| ラッチ ON | ✅ | ✅ ⚠️direct | **❌** | - | - | ✅ queue |
| ラッチ OFF | - | - | - | ✅ thread | **❌** | ✅ queue |
| 系統B ON（PTT有効） | ✅ | ✅ ⚠️direct | **❌** | - | - | ✅ ⚠️直接 |
| 系統B OFF（PTT有効） | - | - | - | ✅ thread | **❌** | ✅ ⚠️直接 |
| 系統B ON（PTT無効） | - | ✅ ⚠️direct | - | - | - | ✅ ⚠️直接 |
| 系統B OFF（PTT無効） | - | - | - | ✅ ⚠️direct | - | ✅ ⚠️直接 |

**漏れ合計: ❌ 5箇所、⚠️（実装方式不統一）9箇所**

---

## 3. オーナーシップ分析

### 現状の問題

```
【問題の根本】各ハンドラが「何を呼ぶか」を個別に判断している
             → 漏れは「人的ミス」ではなく「構造的必然」

app.py:
  _on_ptt_press()       → resume + start(thread) + open_gate + queue  ← 完全
  _on_ptt_btn_pressed() → resume + start(thread) + open_gate + queue  ← 完全
  _on_ptt_latch_changed(ON) → resume + start(direct)          + queue  ← open_gate 忘れ
  _on_route_b_enable(ON)    → resume + start(direct)          + direct ← open_gate 忘れ
```

### 状態オーナーシップ

| 状態変数 | 所有クラス | 操作権限 | 問題 |
|:---|:---|:---|:---|
| `RouteState` | `CaptionSystem` | `start()` / `stop()` のみ | ✅ 適切 |
| `_audio_gate` | `CaptionSystem` | `open/close_audio_gate()` | ⚠️ 呼出漏れあり |
| `_konnyaku_running` | app.py global | `_on_konnyaku_start_stop_click()` | ⚠️ global変数 |
| `_ptt_enabled` | app.py global | `_on_ptt_enabled_change()` のみのはずが `route_b_enable` も変更 | ❌ 責任分散 |

---

## 4. 改善提案（To-Be）— コマンド層の導入

```mermaid
flowchart LR
    classDef trigger  fill:#2980b9,color:#fff,stroke:#1a5276
    classDef command  fill:#e67e22,color:#fff,stroke:#a04000
    classDef state    fill:#27ae60,color:#fff,stroke:#145a32
    classDef ui       fill:#8e44ad,color:#fff,stroke:#4a235a

    subgraph LAYER_A["① 入力ソース層（薄くなる）"]
        F8P["⌨️ F8 press"]:::trigger
        BTNP["🖱️ GUI btn ↓"]:::trigger
        LATCHON["☑️ ラッチ ON"]:::trigger
        RBON["🔘 系統B ON"]:::trigger
        F8R["⌨️ F8 release"]:::trigger
        BTNR["🖱️ GUI btn ↑"]:::trigger
        LATCHOFF["☑️ ラッチ OFF"]:::trigger
        RBOFF["🔘 系統B OFF"]:::trigger
    end

    subgraph LAYER_CMD["② ドメインコマンド層（新設・責任の集約）"]
        CMD_START["cmd_ptt_start_b()\n= resume_from_idle\n+ start_route('b') thread\n+ open_audio_gate\n+ queue(update_ptt_visual)"]:::command
        CMD_STOP["cmd_ptt_stop_b()\n= stop_route('b') thread\n+ close_audio_gate\n+ queue(update_ptt_visual)"]:::command
    end

    subgraph LAYER_C["③ 状態層（変更なし）"]
        RS["RouteState"]:::state
        AG["_audio_gate"]:::state
    end

    subgraph LAYER_D["④ UI更新層（変更なし）"]
        GQ["_gui_queue\n→ _update_ptt_visual_feedback"]:::ui
    end

    F8P --> CMD_START
    BTNP --> CMD_START
    LATCHON --> CMD_START
    RBON --> CMD_START

    F8R --> CMD_STOP
    BTNR --> CMD_STOP
    LATCHOFF --> CMD_STOP
    RBOFF --> CMD_STOP

    CMD_START --> RS
    CMD_START --> AG
    CMD_START --> GQ

    CMD_STOP --> RS
    CMD_STOP --> AG
    CMD_STOP --> GQ
```

### 改善効果

- ❌ 5箇所の漏れ → 0（コマンド関数内で完全セットを保証）
- ⚠️ 9箇所の実装方式不統一 → 0（1箇所に集約）
- 新トリガー追加時: コマンド関数を呼ぶだけ（漏れ不可能な構造）
