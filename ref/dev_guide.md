# Python実践指南書
## FX-Compass Pro を題材にした C/Java 経験者向け Python・テスト設計ガイド

---

> **この指南書の使い方**
> - **チートシート欄**：次のプロジェクトで「あれどう書くんだっけ」と思ったときに開く
> - **解説欄**：じっくり読んで理解を深めたいときに読む
> - コード例は原則として FX-Compass Pro の実コードを使用。汎用例が分かりやすい場合はそちらを優先する

---

# 第1部：Python速習（C/Java経験者向け）

## 1-1. 型・変数

### チートシート

```python
# 型宣言不要。代入時に型が決まる
price = 150.0        # float
symbol = "USDJPY=X"  # str
count = 14           # int
flag = True          # bool（先頭大文字）

# 型ヒント（任意だが推奨）
def fetch_data(self, symbol: str) -> pd.DataFrame:
    ...
```

### 解説

CやJavaと最も違う点は「型宣言が不要」なことです。Pythonは動的型付け言語なので、変数に値を代入するだけで型が決まります。ただし、型ヒント（`symbol: str`）を付けることでIDEの補完が効くようになり、可読性も上がります。実務では型ヒントを付けることを推奨します。

---

## 1-2. クラスと `self`

### チートシート

```python
class FXAnalyzerPro:
    # コンストラクタ（Javaの __init__ に相当）
    def __init__(self, config_path: str = "config.yaml"):
        self.config = yaml.safe_load(open(config_path))
        self.output_dir = "charts"

    # メソッド（第1引数に必ず self を書く）
    def fetch_data(self, symbol: str) -> pd.DataFrame:
        return yf.download(symbol)

# インスタンス生成（new は不要）
analyzer = FXAnalyzerPro()
analyzer.fetch_data("USDJPY=X")
```

### 解説

Javaとの主な違いは2つです。

**`self` を明示する**：Javaの `this` に相当しますが、Pythonではメソッドの第1引数として必ず明示します。呼び出し側は渡しません。

**`new` が不要**：`FXAnalyzerPro()` と書くだけでインスタンスが生成されます。

アクセス修飾子（`public`/`private`）は文法上存在しません。慣習として、内部メソッドには `_` を前置します（例：`_calc_indicators`）。

---

## 1-3. 例外処理

### チートシート

```python
# 基本構文（try/except/finally）
try:
    df = analyzer.fetch_data(symbol)
except Exception as e:
    print(f"  {symbol} エラー: {e}")
finally:
    pass  # 必ず実行（省略可）

# 特定の例外を捕捉
try:
    value = int("abc")
except ValueError as e:
    print(f"変換エラー: {e}")
except TypeError:
    print("型エラー")
```

### 解説

Javaの `try/catch/finally` とほぼ同じです。`catch` が `except` に変わり、複数の例外を列挙できます。FX-Compass Pro では `__main__` ブロックでシンボルごとのエラーを `except Exception` で握り潰し、次のシンボルの処理を継続しています。

---

## 1-4. リスト・辞書・内包表記

### チートシート

```python
# リスト（Javaの ArrayList に相当）
symbols = ["USDJPY=X", "EURJPY=X"]
symbols.append("GBPJPY=X")

# 辞書（Javaの HashMap に相当）
color_map = {
    1: {"BUY": "#90ee90", "SELL": "#ffcccb"},
    2: {"BUY": "#2ecc71", "SELL": "#e74c3c"},
}
color_map[1]["BUY"]  # → "#90ee90"

# リスト内包表記（C/Javaにない構文）
closes = [150.0 + i * 0.01 for i in range(60)]
# 上は以下と同等
closes = []
for i in range(60):
    closes.append(150.0 + i * 0.01)

# 条件付き内包表記
buy_signals = [v for v in signals if v > 0]
```

### 解説

内包表記はPythonで最もよく使う構文の一つです。ループを1行で書けるため、慣れると可読性が上がります。ただし複雑な条件が重なる場合は通常の `for` ループの方が読みやすいことがあります。

---

## 1-5. f-string（文字列フォーマット）

### チートシート

```python
symbol = "USDJPY=X"
price = 150.123
level = 3

# f-string（Python 3.6+）
print(f"【{symbol}】価格: {price:.3f}  Lv.{level}")
# → 【USDJPY=X】価格: 150.123  Lv.3

# 書式指定
f"{price:.3f}"   # 小数点3桁
f"{price:>10.2f}" # 右寄せ10文字
```

### 解説

Javaの `String.format()` に相当します。`{}` の中に変数名と書式指定子を書きます。`:.3f` は「小数点以下3桁の浮動小数点」を意味します。

---

## 1-6. `__main__` ブロックと モジュール

### チートシート

```python
# main.py の末尾
if __name__ == "__main__":
    analyzer = FXAnalyzerPro()
    for symbol in analyzer.config['trading']['symbols']:
        df = analyzer.fetch_data(symbol)
        sig, lv, time, price, sl, df_out = analyzer.analyze(df)
```

### 解説

`if __name__ == "__main__":` は「このファイルが直接実行されたときだけ動くコード」を書く場所です。`import main` されたときは実行されません。Javaの `public static void main(String[] args)` に相当しますが、ファイル内のどこにでも書けます。

テストでこのブロックを到達させるには工夫が必要で（今回はASTで切り出す方法を使いました）、将来的には `def main():` に切り出して `if __name__ == "__main__": main()` とするのが推奨パターンです。

---

## 1-7. よく使うライブラリ早見表

| ライブラリ | 用途 | 相当するJavaのもの |
|-----------|------|-----------------|
| `pandas` | 表形式データ処理 | 独自（Apache Commons CSVより高機能） |
| `numpy` | 数値演算・配列処理 | 独自 |
| `yfinance` | 株価・為替データ取得 | 独自 |
| `matplotlib` | グラフ描画 | JFreeChart |
| `yaml` | YAML設定ファイル読み込み | SnakeYAML |
| `pytest` | テストフレームワーク | JUnit |
| `unittest.mock` | モック | Mockito |

---

# 第2部：Pythonプロジェクトの構成

## 2-1. ディレクトリ構成

### チートシート

```
fx-compass/
├── main.py              # メインロジック
├── config.yaml          # 設定ファイル
├── requirements.txt     # 依存ライブラリ一覧
├── tests/
│   ├── conftest.py      # pytest共通設定（sys.path設定）
│   └── test_logic.py    # テストコード
├── charts/              # 生成チャート出力先（.gitignore推奨）
└── .github/
    └── workflows/
        └── test.yml     # GitHub Actions CI設定
```

### 解説

Javaのような厳格なパッケージ構造はありません。シンプルなプロジェクトではルートに `main.py` を置き、テストは `tests/` に分けるのが一般的です。

---

## 2-2. requirements.txt

### チートシート

```
# requirements.txt
yfinance
pandas
PyYAML
matplotlib
pytest
pytest-cov
```

```bash
# インストール
pip install -r requirements.txt
```

### 解説

Javaの `pom.xml`（Maven）や `build.gradle`（Gradle）に相当する依存管理ファイルです。バージョンを固定したい場合は `pandas==2.0.3` のように指定します。ただし個人開発ではバージョン固定しない方が更新が楽です。

---

## 2-3. config.yaml の読み込み

### チートシート

```python
import yaml

with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

# 辞書としてアクセス
symbol = config['trading']['symbols'][0]  # "USDJPY=X"
fast   = config['logic']['macd']['fast']  # 12
pct    = config['risk']['stop_loss_pct']  # 0.01
```

```yaml
# config.yaml
trading:
  symbols:
    - "USDJPY=X"
logic:
  macd:
    fast: 12
risk:
  stop_loss_pct: 0.01
```

### 解説

設定をコードから分離することで、コードを変更せずにパラメータを変更できます。FX-Compass Pro では MACD/RSI のパラメータをすべて `config.yaml` に集約しており、チューニングが容易です。

**よくある罠**：`config['logic']['risk']` のように階層を間違えると `KeyError` になります。`config.yaml` の構造を見て正しいキーパスを確認する習慣をつけましょう。今回の開発でも実際にこのミスが発生しました。

---

## 2-4. GitHub Actions による CI

### チートシート

```yaml
# .github/workflows/test.yml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.10"
      - run: pip install -r requirements.txt
      - run: pytest tests/ --cov=main --cov-report=term-missing
```

### 解説

push のたびに自動でテストが走ります。Javaの Jenkins や Maven の CI 設定に相当します。GitHub Actions は無料枠が広く、個人プロジェクトでは実質無料で使えます。

---

# 第3部：pytest によるテスト実装

## 3-1. テストの基本構造

### チートシート

```python
# tests/test_logic.py
import pytest
from main import FXAnalyzerPro

class TestAnalyzeRow:

    def test_hold_no_cross(self, analyzer):
        """クロスなし → HOLD / Lv.0。"""
        sig, lv = analyzer.analyze_row(...)
        assert sig == "HOLD"
        assert lv == 0
```

### 解説

JUnitとの主な違いは以下の通りです。

| JUnit | pytest |
|-------|--------|
| `@Test` アノテーション | `test_` で始まるメソッド名 |
| `assertEquals(a, b)` | `assert a == b` |
| `@BeforeEach` | `@pytest.fixture` |
| `assertThrows` | `pytest.raises()` |

クラスに `Test` プレフィックスを付けると pytest が自動収集します。メソッド名も `test_` で始めます。

---

## 3-2. フィクスチャ（fixture）

### チートシート

```python
# 共通の初期化処理をフィクスチャとして定義
@pytest.fixture
def analyzer(tmp_path):
    # tmp_path は pytest が提供する一時ディレクトリ
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML)
    a = FXAnalyzerPro(config_path=str(cfg))
    a.output_dir = str(tmp_path / "charts")
    os.makedirs(a.output_dir, exist_ok=True)
    return a

# テストメソッドの引数にフィクスチャ名を書くと自動注入される
def test_something(self, analyzer):
    sig, lv = analyzer.analyze_row(...)
```

### 解説

JUnitの `@BeforeEach` に相当しますが、より柔軟です。フィクスチャは引数として受け取るため、テストごとに異なるフィクスチャを組み合わせられます。`tmp_path` は pytest 組み込みのフィクスチャで、テストごとにユニークな一時ディレクトリを提供します。ファイル出力のテストで重宝します。

---

## 3-3. モック（Mock）

### チートシート

```python
from unittest.mock import patch, MagicMock

# 外部APIをモックに差し替える
def test_calls_yf_download(self, analyzer):
    mock_df = _make_df([150.0] * 30)
    with patch("main.yf.download", return_value=mock_df) as mock_dl:
        result = analyzer.fetch_data("USDJPY=X")
        mock_dl.assert_called_once()  # 呼ばれたことを確認
        assert not result.empty

# 例外を発生させるモック
with patch("main.yf.download", side_effect=RuntimeError("network error")):
    with pytest.raises(Exception):
        analyzer.fetch_data("USDJPY=X")

# クラスのメソッドをモック
with patch.object(analyzer, "analyze_row", return_value=("BUY", 3)):
    sig, lv, time, price, sl, _ = analyzer.analyze(df)
    assert lv == 3
```

### 解説

モックの主な用途は「外部依存を排除してテストを安定させること」です。`yfinance` はネットワークに依存するため、テストで実際に呼ぶと不安定になります。`patch` で差し替えることで、ネットワークなしでテスト可能になります。

`patch("main.yf.download")` の文字列は「テスト対象モジュールからの参照パス」を指定します。`yfinance` 本体ではなく、`main.py` が `yf` としてインポートした場所を指定するのがポイントです。

| モック操作 | 説明 |
|-----------|------|
| `return_value=x` | 呼ばれたときに `x` を返す |
| `side_effect=Exception()` | 呼ばれたときに例外を投げる |
| `assert_called_once()` | 1回だけ呼ばれたことを確認 |
| `assert_called_once_with(x)` | 引数 `x` で1回呼ばれたことを確認 |

---

## 3-4. `pytest.raises` による例外テスト

### チートシート

```python
# 例外が発生することを確認
def test_fetch_raises_on_error(self, analyzer):
    with patch("main.yf.download", side_effect=RuntimeError("network error")):
        with pytest.raises(Exception):
            analyzer.fetch_data("USDJPY=X")

# 例外メッセージも確認したい場合
with pytest.raises(ValueError, match="invalid"):
    int("abc_invalid")
```

### 解説

`try/except` でフラグを立てる書き方（`caught = True`）は、例外が出ない場合にフラグ代入行が「到達不能コード」になり、カバレッジ計測で未到達として検出されます。`pytest.raises()` を使うと、この問題が起きません。今回の開発でも実際にこの落とし穴にはまりました。

---

## 3-5. `tmp_path` によるファイル出力テスト

### チートシート

```python
def test_chart_file_created(self, analyzer, tmp_path):
    # output_dir を一時ディレクトリに差し替え
    analyzer.output_dir = str(tmp_path / "charts")
    os.makedirs(analyzer.output_dir, exist_ok=True)

    path = analyzer.generate_chart(df_out, "USDJPY=X", ...)

    # ファイルが存在することを確認
    assert os.path.exists(path)
    assert path.endswith(".png")
    assert os.path.dirname(path) == analyzer.output_dir
```

### 解説

ファイル出力のテストで `tmp_path` を使わずに固定パスに書き出すと、テストが副作用を持ち（ファイルが残る）、CI環境で競合することがあります。`tmp_path` はテスト終了後に自動削除されるため、クリーンな状態が保たれます。

---

# 第4部：テスト設計・テスト観点の整理

## 4-1. テスト種別の使い分け

### チートシート

| 種別 | 観点 | FX-Compass Proでの適用例 |
|------|------|------------------------|
| ホワイトボックス | 内部ロジックの算術的妥当性 | EMAの収束・RSIの上下限・ゼロ除算耐性 |
| ブラックボックス | 入出力の正確性・状態遷移 | `analyze_row` の全シグナルパターン |
| 堅牢性 | 異常入力への耐性 | NaN含有・データ不足・定数系列 |
| 副作用 | ファイル出力などの外部影響 | `generate_chart` のPNG出力確認 |
| モック | 外部依存の排除 | `yfinance` のネットワーク呼び出し |
| 統合 | コンポーネント間の連携 | fetch → analyze → chart のパイプライン |

### 解説

すべてをブラックボックステストにすると、内部ロジックのバグを捕捉しにくくなります。逆にすべてをホワイトボックスにすると、リファクタリング時にテストが壊れやすくなります。今回は `_calc_indicators` のような計算ロジックはホワイトボックス、`analyze_row` のような判定ロジックはブラックボックスで設計しました。

---

## 4-2. 境界値分析

### チートシート

```
BUY Lv.3 の RSI 条件：30 ≦ RSI ≦ 50

テストすべき境界値：
  29  → Lv.2（下限未満）
  30  → Lv.3（下限境界）★
  40  → Lv.3（中間値）
  50  → Lv.3（上限境界）★
  51  → Lv.2（上限超え）
```

```python
def test_buy_lv3_rsi_boundary_lower(self, analyzer):
    """RSI = 30（下限境界）→ Lv.3 になる。"""
    sig, lv = self._call(analyzer, _row(-0.3, -0.1, 30), _row(-0.05, -0.1, 30))
    assert sig == "BUY" and lv == 3

def test_buy_lv2_rsi_29_below_lower_boundary(self, analyzer):
    """RSI = 29（下限未満）→ Lv.3 にならず Lv.2。"""
    sig, lv = self._call(analyzer, _row(-0.3, -0.1, 29), _row(-0.05, -0.1, 29))
    assert sig == "BUY" and lv == 2
```

### 解説

境界値分析は「条件の境目で正しく動くか」を確認する手法です。`<=` と `<` の書き間違いは境界値でしか検出できません。

テストデータを作るときに「なんとなく中間値」を使うと、境界値のバグを見逃します。今回の開発でも、最初のテストで RSI=40（Lv.3範囲内）を使ってしまい Lv.2 のテストが通らないという問題が発生しました。条件を表に整理してから値を選ぶ習慣が重要です。

**境界値の選び方**：
- 境界値そのもの（30, 50）
- 境界の1つ外側（29, 51）
- 中間値（40）の最低1つ

---

## 4-3. 状態遷移テスト

### チートシート

```
analyze_row の状態遷移表：

MACD位置  | クロス  | RSI範囲      | → 状態
---------|--------|-------------|-------
ゼロ以上  | GC     | any         | BUY Lv.1
ゼロ未満  | GC     | 範囲外       | BUY Lv.2
ゼロ未満  | GC     | 30≦RSI≦50   | BUY Lv.3
ゼロ以下  | DC     | any         | SELL Lv.1
ゼロ超    | DC     | 範囲外       | SELL Lv.2
ゼロ超    | DC     | 50≦RSI≦70   | SELL Lv.3
-        | なし   | any         | HOLD

各セルに最低1つのテストケースを対応させる
```

### 解説

状態遷移テストは「すべての状態と遷移条件に対してテストが存在するか」を確認します。状態遷移表を先に作ると、テストの抜け漏れが一目でわかります。

今回は7つの状態に対してそれぞれテストメソッドを作成しました。テスト名はできるだけ「条件 → 期待結果」の形で書くと、失敗したときに何が問題かがすぐわかります。

---

## 4-4. 副作用のないテスト設計

### チートシート

```python
# ❌ 悪い例：入力データが変わるかもしれない関数
def analyze(self, df):
    df['MACD'] = ...  # df を直接書き換える（副作用あり）
    return signal

# ✅ 良い例：入力を変更しない（純粋関数）
def _calc_indicators(self, df):
    result = df.copy()  # コピーを作って操作する
    result['MACD'] = ...
    return result

# テストで副作用がないことを確認
def test_does_not_mutate_input(self, analyzer):
    df = _make_df([150.0] * 60)
    original_cols = set(df.columns)
    analyzer._calc_indicators(df)
    assert set(df.columns) == original_cols  # 列が増えていない
```

### 解説

入力データを書き換える関数は「副作用がある」と言います。副作用がある関数はテストが難しく、バグの原因にもなります。今回のリファクタリングで `_calc_indicators` を `df.copy()` を使う純粋関数に変更しました。テストでこの性質を明示的に確認することで、将来の変更でうっかり副作用を導入してしまうことを防げます。

---

## 4-5. カバレッジ100%の詰め方

### チートシート

```bash
# カバレッジ計測
pytest tests/ --cov=main --cov-report=term-missing

# 出力例
Name      Stmts   Miss  Cover   Missing
---------------------------------------
main.py     117      5    96%   124-126, 201-215
```

```
未到達行の種類と対策：

種類                    | 対策
------------------------|----------------------------------
条件分岐の片側          | モックで条件を制御して到達させる
例外処理の except 節    | pytest.raises() で例外を発生させる
ファイル出力の分岐      | tmp_path でファイル出力テストを追加
__main__ ブロック       | AST で切り出して exec する
到達不能コード          | コード自体を削除する
```

### 解説

カバレッジ100%は「バグがない」ことを保証しません。「すべての行が少なくとも1回実行されたこと」を意味します。それでも目指す価値はあります。未到達行を見つけると「なぜそこに到達できないのか」を考えるきっかけになり、設計の問題や冗長なコードを発見できます。

**詰める順番**：

1. `--cov-report=term-missing` で未到達行を特定する
2. 未到達行の種類を判断する（分岐・例外・副作用・__main__）
3. 種類に応じた対策を取る
4. 「到達不能コード」はテストで到達させるより削除を検討する

**今回のプロジェクトで特に苦労した箇所**：

`__main__` ブロックは `runpy` でも `exec` 全体でも到達できず、最終的にASTで `if __name__ == "__main__":` の中身だけを切り出して実行する方法を使いました。次のプロジェクトからは最初から `def main():` に切り出しておくと、`main()` を直接呼ぶだけでテストできます。

```python
# 推奨パターン
def main():
    analyzer = FXAnalyzerPro()
    for symbol in analyzer.config['trading']['symbols']:
        ...

if __name__ == "__main__":
    main()

# テスト側
def test_main():
    with patch("main.FXAnalyzerPro") as MockClass:
        ...
        from main import main
        main()  # 直接呼べる
```

---

## 4-6. テスト設計のチェックリスト

次のプロジェクトでテストを書くときに確認するリストです。

```
□ 状態遷移表を書いたか
□ 各状態に対応するテストケースがあるか
□ 境界値（下限・境界・上限・境界+1）がテストされているか
□ 正常系だけでなく異常系（NaN・空・最小値）のテストがあるか
□ 外部依存（ネットワーク・ファイル）はモックで排除されているか
□ ファイル出力テストは tmp_path で隔離されているか
□ 副作用なし（入力不変）のテストがあるか
□ pytest.raises を使っているか（try/except + flag ではなく）
□ テスト名が「条件 → 期待結果」の形になっているか
□ カバレッジ計測で未到達行がないか確認したか
```

---

# 第5部：今回の学びと次のプロジェクトへの教訓

## 5-1. やって良かったこと

**設定をコードから分離した**：`config.yaml` にパラメータを集約したことで、コードを触らずにMACD/RSIのパラメータをチューニングできます。次のプロジェクトでも設定はファイルに外出しする癖をつけましょう。

**指標計算を純粋関数に分離した**：`_calc_indicators` を `df.copy()` を使う副作用なし関数にしたことで、テストが書きやすくなり、入力データの意図しない変更というバグも防げました。

**テスト名を詳細に書いた**：`test_buy_lv3_gc_below_zero_rsi_30` のように条件と期待値をテスト名に書いたことで、CI が失敗したときに原因が一目でわかりました。

## 5-2. 次のプロジェクトで最初からやること

**`def main():` に切り出す**：`if __name__ == "__main__":` の中身を関数にしておくと、`__main__` ブロックのテストが圧倒的に楽になります。

**状態遷移表を先に書く**：テストコードを書く前に判定ロジックの状態遷移表を書くと、テストの抜け漏れがなくなります。今回は後から表を整理しましたが、最初から作っておくとテスト設計がスムーズです。

**型ヒントを付ける**：`def analyze(self, df: pd.DataFrame) -> tuple:` のように型ヒントを付けると、IDEの補完が効き、コードレビューの可読性も上がります。

## 5-3. 失敗から学んだこと

**動いているコードを触るときはテストを先に書く**：`_calc_indicators` のリファクタリング時に `avg_loss=0` のケースを考慮せず RSI が NaN になるバグを混入させました。既存の挙動をテストで押さえてからリファクタリングすれば防げた失敗です。

**`config.yaml` の構造をコードで確認する**：`config['logic']['risk']` と `config['risk']` の違いは YAML を見れば一目瞭然ですが、思い込みで書いてしまうと `KeyError` になります。テストでコンフィグの参照を確認する項目を1つ入れておくと早期発見できます。

**境界値のテストデータは条件表から選ぶ**：「なんとなく中間値」を使うと境界値のバグを見逃します。今回は RSI=40（Lv.3範囲内）を Lv.2のテストデータとして使ってしまい、テストが誤って通るという問題が起きました。

---

*FX-Compass Pro 開発を通じて整理 — 次のツール開発のための自分への手紙*

---

# 補足1：pandas 基本操作（C/Java経験者向け）

## B1-1. DataFrame と Series の概念

### チートシート

```python
import pandas as pd

# DataFrame：2次元の表（Excelのシートに相当）
df = pd.DataFrame({
    "Open":  [150.0, 150.1, 150.2],
    "Close": [150.1, 150.2, 150.3],
}, index=pd.date_range("2024-01-01", periods=3, freq="h"))

#           Open  Close
# 2024-01-01 00:00  150.0  150.1
# 2024-01-01 01:00  150.1  150.2
# 2024-01-01 02:00  150.2  150.3

# Series：1次元の列（DataFrame の1列）
close_series = df["Close"]  # → Series
last_value   = df["Close"].iloc[-1]   # 末尾の値（float）
second_row   = df.iloc[1]             # 2行目（Series）

# 列の追加
df["MACD"] = df["Close"] - df["Open"]
```

### 解説

`DataFrame` は「インデックス付きの2次元表」です。Javaの `List<Map<String, Object>>` に近いですが、列ごとに型が統一されており、数値演算が高速です。

`Series` は DataFrame の1列を取り出したものです。リストのように見えますが、インデックスを持っており、インデックスを基準に演算します。

| 操作 | コード | 説明 |
|------|--------|------|
| 列を取得 | `df["Close"]` | Series を返す |
| 末尾N行 | `df.tail(100)` | 末尾100行の DataFrame |
| 位置で行取得 | `df.iloc[-1]` | 末尾行（Series） |
| 位置で値取得 | `df.iloc[-1]["Close"]` | 末尾の Close 値 |
| 列を追加 | `df["新列"] = 値` | 既存列の演算結果を追加 |
| コピー | `df.copy()` | 独立したコピーを作成 |

---

## B1-2. FX-Compass Pro で使った主要メソッド

### チートシート

```python
# ewm：指数移動平均（EMA）の計算
ema_fast = df["Close"].ewm(span=12, adjust=False).mean()

# rolling：単純移動平均・移動集計
avg_gain = gain.rolling(window=14).mean()

# diff：前の行との差分（前日比・前時間比）
diff = df["Close"].diff()
# → 1行目は NaN、2行目以降は「現在値 - 前の値」

# clip：値を範囲で切り取る
gain = diff.clip(lower=0)   # 0未満を0にする（マイナスの変化を除外）
loss = -diff.clip(upper=0)  # 0超を0にして符号反転（プラスの変化を除外）

# replace：特定の値を置換
avg_loss.replace(0, float("nan"))  # 0をNaNに置換（ゼロ除算対策）

# MultiIndex の確認とフラット化
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
```

### 解説

**`ewm(span=N).mean()`**：指数移動平均を計算します。直近のデータほど重みが大きくなります。MACDの計算に使います。`adjust=False` は初期値を単純平均ではなく最初の値にする設定です。

**`rolling(window=N).mean()`**：直近N個の単純平均を計算します。先頭のN-1個は NaN になります。RSIの計算に使います。

**`diff()`**：前の行との差分を計算します。為替では「1時間前からの変化」を求めるのに使います。

**`clip(lower=0)`**：0未満の値を0に揃えます。RSIの計算でプラスの変化量とマイナスの変化量を分離するために使っています。

**MultiIndex**：`yfinance` が複数シンボルを返すとき、列名が `("Close", "USDJPY=X")` のような階層構造（MultiIndex）になることがあります。`get_level_values(0)` で第1階層だけ取り出してフラット化します。

---

## B1-3. numpy との併用

### チートシート

```python
import numpy as np

# np.where：条件によって値を切り替える（三項演算子の配列版）
rsi_values = np.where(
    avg_loss == 0,    # 条件
    100.0,            # True のとき
    np.where(
        avg_gain == 0,
        0.0,
        100 - (100 / (1 + avg_gain / avg_loss))
    )
)
df["RSI"] = rsi_values
```

### 解説

`np.where(条件, Trueの値, Falseの値)` はC言語の三項演算子 `条件 ? a : b` に相当しますが、配列全体に一括適用できます。

今回のRSI計算では `avg_loss == 0`（単調増加の場合）に `RSI = 100` を代入するために使いました。`pandas` の `replace` だけでは NaN が残るケースがあり、`np.where` で明示的に条件分岐する方が確実です。

---

# 補足2：conftest.py の役割と活用

## B2-1. conftest.py とは

### チートシート

```
tests/
├── conftest.py       ← テストファイル間で共有する設定・フィクスチャ
├── test_logic.py
└── test_other.py     ← conftest.py のフィクスチャをそのまま使える
```

```python
# tests/conftest.py

import sys
import os

# sys.path にリポジトリルートを追加（main.py を import 可能にする）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# フィクスチャをここに書くと全テストファイルで使える
import pytest
from main import FXAnalyzerPro

@pytest.fixture
def analyzer(tmp_path):
    """全テストファイルで共有するアナライザーフィクスチャ。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML)
    return FXAnalyzerPro(config_path=str(cfg))
```

### 解説

`conftest.py` は pytest が自動で読み込む特別なファイルです。主な用途は2つです。

**1. `sys.path` の設定**：`tests/` サブディレクトリからルートの `main.py` を `import` するには、Pythonの検索パスにルートを追加する必要があります。今回の開発で `ModuleNotFoundError: No module named 'main'` が出たのはこれが原因でした。

**2. フィクスチャの共有**：テストファイルが増えてきたとき、共通のフィクスチャ（アナライザーの初期化など）を `conftest.py` に移すと、各テストファイルに重複して書かずに済みます。

---

## B2-2. プロジェクトが大きくなったときの conftest.py 活用

### チートシート

```
fx-compass/
├── conftest.py           ← プロジェクト全体に適用
└── tests/
    ├── conftest.py       ← tests/ 配下に適用（sys.path設定・共通フィクスチャ）
    ├── unit/
    │   ├── conftest.py   ← unit/ 配下にのみ適用
    │   └── test_logic.py
    └── integration/
        ├── conftest.py   ← integration/ 配下にのみ適用
        └── test_pipeline.py
```

```python
# tests/conftest.py に共通フィクスチャをまとめる例
CONFIG_YAML = """\
trading:
  symbols:
    - "USDJPY=X"
  interval: "1h"
  period: "3mo"
logic:
  macd:
    fast: 12
    slow: 26
    signal: 9
  rsi:
    length: 14
    buy_threshold: 30
    sell_threshold: 70
risk:
  stop_loss_pct: 0.01
"""

@pytest.fixture
def analyzer(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML)
    a = FXAnalyzerPro(config_path=str(cfg))
    a.output_dir = str(tmp_path / "charts")
    os.makedirs(a.output_dir, exist_ok=True)
    return a

@pytest.fixture
def sample_df():
    """テスト用の標準OHLCデータを提供する。"""
    closes = [150.0 + i * 0.01 for i in range(60)]
    idx = pd.date_range("2024-01-01", periods=60, freq="h")
    return pd.DataFrame({
        "Open": closes, "High": [c+0.1 for c in closes],
        "Low": [c-0.1 for c in closes], "Close": closes,
        "Volume": [1000]*60,
    }, index=idx)
```

### 解説

`conftest.py` は階層ごとに効果範囲が変わります。`tests/conftest.py` は `tests/` 配下すべてのテストファイルに適用されます。サブディレクトリに `conftest.py` を置くとそのディレクトリ以下にのみ適用されます。

今回の FX-Compass Pro では `_make_df` というヘルパー関数をテストファイル内に定義しましたが、テストファイルが増えたら `conftest.py` の `sample_df` フィクスチャに移行するのが自然です。

---

*補足追記：pandas基本操作 / conftest.py の役割*