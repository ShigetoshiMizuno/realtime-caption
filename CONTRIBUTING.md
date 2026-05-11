# コントリビューションガイドライン

## テスト fixture における API キーの取り扱い

**テスト fixture に実 API キーを絶対に書かないこと。**

過去に DeepL の実 API キーがテストコードにハードコードされ、public リポジトリに push された事案が発生しました（Issue #11、PR #10）。
再発防止のため、以下のルールを必ず守ること。

---

## フェイク値命名規約

テスト fixture・ドキュメント例には、`.gitleaks.toml` allowlist に登録された
以下のパターンのみを使用すること。これらは gitleaks スキャンで素通りする。

| 用途 | 推奨パターン | 例 |
|---|---|---|
| OpenAI 風 | `sk-test-fake-...` または `sk-xxxxxxxxxxxxxxxxxxxx` | `"sk-test-fake-0000000000000000"` |
| DeepL 風 | `FAKE-TEST-KEY-...:fx` または `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx` | `"FAKE-TEST-KEY-0000-0000-aaaaaaaaaaaa:fx"` |
| プレースホルダー | `your-api-key-here` | `"your-api-key-here"` |

**禁止例:**
- 実在しそうな UUID（`72bc1168-...:fx` のように `hex-hex-...:fx` パターン）
- ランダム生成した sk-XXXX 形式
- 任意の `-fake-` / `-test-` インフィックス（allowlist 対象外なので検出される）

新しいフェイク値パターンが必要なら、まず `.gitleaks.toml` の allowlist に追加してから使用すること。

---

## credential ファイルの取り扱い

- `config.yaml` は `.gitignore` に含まれており、絶対にコミットしないこと
- `config.yaml.example` のみをリポジトリで管理する。example には必ずプレースホルダー値のみ記載すること
- `.env` ファイルは `.gitignore` に含めること
- API キーは環境変数または `config.yaml`（gitignore 済み）で管理すること

---

## gitleaks によるシークレット検出

このリポジトリでは [gitleaks](https://github.com/gitleaks/gitleaks) を使ってシークレットの混入を自動検出しています。

### 仕組み

- **ローカル**: `pre-commit` フックにより、コミット前に自動スキャン
- **CI**: GitHub Actions により、PR・push 時にフルスキャン（`.github/workflows/gitleaks.yml`）

allowlist（誤検知除外）の設定は `.gitleaks.toml` で管理しています。

---

## ローカルでの動作確認手順

### 1. pre-commit のインストール

```bash
pip install pre-commit
```

### 2. フックの有効化

リポジトリルートで以下を実行：

```bash
pre-commit install
```

これ以降、`git commit` のたびに gitleaks が自動実行されます。

### 3. 全ファイルをスキャン

```bash
pre-commit run --all-files
```

### 4. 特定ファイルのスキャン

```bash
pre-commit run --files path/to/file.py
```

---

## 過去事案

- Issue #11: DeepL 実 API キーのテストハードコード問題
- PR #10: 当該コミットの対応（キー無効化・履歴書き換え）

---

## 問い合わせ

不明点は Issue または PR のコメントでご連絡ください。
