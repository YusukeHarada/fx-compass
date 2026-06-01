# FX-Compass Pro (為替羅針盤 Pro)

[![CI Status](https://github.com/YusukeHarada/fx-compass/actions/workflows/test.yml/badge.svg)](https://github.com/YusukeHarada/fx-compass/actions/workflows/test.yml)

FX-Compass Proは、為替相場のテクニカル分析を自動化し、統計的な優位性に基づいた意思決定を支援するマルチシンボル・スキャニングツールです。

---

## I. ユーザーガイド (User Guide)

### 1. 概要

MACD・RSI・EMA200・ATR・ADX の 5 指標を組み合わせ、相場の転換点を **4 段階のシグナル強度**で判定します。複数の通貨ペアを同時にスキャンし、カラー表示のサマリーテーブルと PNG チャートで結果を提示します。

### 2. クイックスタート

#### 必要環境

* Python 3.10 以上
* インターネット接続（為替データの取得用）

#### セットアップ

```bash
# 本番依存のみインストール
pip install .

# 開発・テスト用依存も含めてインストール
pip install -e ".[dev]"
```

#### 実行方法

```bash
# 基本: config.yaml の設定でスキャン
python main.py

# シンボルと時間足を直接指定
python main.py --symbols USDJPY=X EURJPY=X --interval 1h

# Lv.2 以上のシグナルのみ表示
python main.py --min-level 2

# 定期監視モード（config の watch_interval_seconds ごとに自動更新）
python main.py --watch
```

実行すると、ターミナルに以下のようなカラーテーブルが表示されます。

```
                        FX-Compass Pro — Signal Summary
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Symbol     ┃ Signal                 ┃ Lv ┃   Price ┃      S/L ┃ Time         ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ MXNJPY=X   │ BUY CONFIRMED (Lv.4)   │  4 │   9.163 │    9.145 │ 06/01 00:00  │  ← bold magenta
│ GBPUSD=X   │ SELL STRONG (Lv.3)     │  3 │   1.345 │    1.347 │ 06/01 00:00  │  ← bold green
│ USDJPY=X   │ HOLD                   │  — │ 159.448 │        — │ 06/01 00:00  │  ← dim
└────────────┴────────────────────────┴────┴─────────┴──────────┴──────────────┘
```

チャートは `charts/` ディレクトリに PNG として保存されます（シグナルあり時のみ）。

#### テストの実行

```bash
pytest tests/ --cov=main --cov-report=term-missing
```

### 3. CLI オプション一覧

| オプション | 説明 | 例 |
|-----------|------|-----|
| `--symbols SYM [SYM ...]` | スキャン対象シンボルを指定（config.yaml を上書き） | `--symbols USDJPY=X EURJPY=X` |
| `--interval {1m,5m,15m,1h,4h,1d}` | 時間足を指定（config.yaml を上書き） | `--interval 4h` |
| `--min-level {1,2,3,4}` | このレベル以上のシグナルのみ表示（デフォルト: 1） | `--min-level 3` |
| `--watch` | 定期的にスキャンを繰り返す | `--watch` |

### 4. シグナル判定の読み方

| レベル | 表示 | 色 | 昇格条件 |
|-------|------|----|---------|
| **Lv.1 WATCH** | 監視開始フェーズ | 白 | MACD クロス発生 |
| **Lv.2 STANDARD** | 反転の可能性あり | 黄 | MACD クロス + MACD がゼロ境界の適切な側 |
| **Lv.3 STRONG** | 複数指標が一致 | 緑 | Lv.2 + RSI が回復・天井ゾーン（30-50 / 50-70） |
| **Lv.4 CONFIRMED** | 最高確度シグナル | 紫 | Lv.3 + EMA200 方向一致 + ADX > 25（トレンド相場） |

### 5. config.yaml パラメータ仕様

| キー | 説明 | デフォルト |
|-----|------|-----------|
| `trading.symbols` | 監視する通貨ペアのリスト（yfinance 形式） | — |
| `trading.interval` | 時間足 (`1m` `5m` `15m` `1h` `4h` `1d`) | `1h` |
| `trading.period` | データ取得期間（`1m`/`5m` 足は自動で `1d` に調整） | `3mo` |
| `trading.watch_interval_seconds` | `--watch` モードのリフレッシュ間隔（秒） | `300` |
| `logic.macd.fast` | MACD 短期 EMA 期間 | `12` |
| `logic.macd.slow` | MACD 長期 EMA 期間 | `26` |
| `logic.macd.signal` | MACD シグナル期間 | `9` |
| `logic.rsi.length` | RSI 計算期間 | `14` |
| `logic.rsi.buy_threshold` | RSI 買い閾値（Lv.3 下限） | `30` |
| `logic.rsi.sell_threshold` | RSI 売り閾値（Lv.3 上限） | `70` |
| `logic.adx.period` | ADX 計算期間 | `14` |
| `logic.adx.threshold` | Lv.4 昇格に必要な ADX 最小値 | `25` |
| `risk.stop_loss_pct` | ATR 未取得時の固定損切り幅（小数） | `0.01`（1%） |
| `risk.atr_period` | ATR 計算期間 | `14` |
| `risk.atr_multiplier` | 損切り幅の ATR 倍率（`価格 ± ATR × multiplier`） | `2.0` |

### 6. 設計上の制約・既知の限界

* **利確ロジックは持たない** — Stop Loss のみ定義。利確タイミングは利用者が判断する。
* **直近 2 本のみで判定** — クロス検出は最新ローソク足 2 本のみ参照する。
* **シグナル連続出力の制御なし** — 連続 BUY シグナル時のナンピン禁止ルールは持たない。
* **外部 API 依存** — `yfinance` の障害時はデータ取得が失敗する。エラーはシンボル単位で記録しスキャンを継続する。
* **短期足の精度** — `1m`/`5m` 足はノイズが多くダマシが増える。`1h` 以上を推奨。
* **EMA200 の信頼性** — データ期間が 200 本未満の場合、EMA200 は統計的に不安定。Lv.4 判定は NaN ガードで自動的にスキップされる。
* **投資助言ではない** — 本ツールの出力は意思決定の補助情報であり、売買を推奨するものではない。

---

## II. 技術仕様書 (Technical Specifications)

### 1. 要求定義 (Requirements Definition)

#### 1.1 機能的要求

* **多変量テクニカル分析**: MACD・RSI・EMA200・ATR・ADX を統合し、4 段階のシグナル強度で判定すること。
* **マルチシンボル・スキャニング**: 複数シンボルに対するバッチ処理を行い、一貫した分析結果を提供すること。
* **CLI インターフェース**: `--symbols` / `--interval` / `--min-level` / `--watch` によりコマンドライン操作を可能にすること。
* **リッチターミナル表示**: レベル別に色分けされたテーブルで全シンボルのサマリーを一画面で提示すること。
* **データ・ビジュアライゼーション**: 分析結果を PNG チャートとして出力し、判定根拠をグラフィカルに提示すること。
* **動的リスク管理**: エントリー価格のボラティリティ（ATR）に基づく Stop Loss を算出すること。

#### 1.2 非機能的要求

* **可搬性**: 標準的な科学計算ライブラリのみで指標計算を実装し、CI/CD 環境との互換性を確保する。
* **保守性**: アルゴリズムを内部カプセル化し、外部ライブラリの仕様変更による影響を最小限に抑える。
* **副作用の排除**: `_calc_indicators` は入力 DataFrame を変更しない純粋関数として実装する。

### 2. システム設計 (System Design)

#### 2.1 アーキテクチャ設計

```
CLI Entry Point (_parse_args / main)
        │
        ├─ Data Acquisition 層  ── fetch_data() ── yfinance
        │
        ├─ Logic Engine 層      ── _calc_indicators()  [MACD / RSI / EMA200 / ATR / ADX]
        │                          analyze_row()        [Lv.0〜4 状態遷移]
        │                          analyze()            [ATR ベース SL 算出]
        │
        └─ Presentation 層      ── _print_results_table()  [rich カラーテーブル]
                                   generate_chart()         [matplotlib PNG]
```

#### 2.2 判定アルゴリズムの状態遷移

| MACD 位置 | クロス種別 | RSI 範囲 | EMA200 方向 | ADX | シグナル | レベル |
|-----------|-----------|---------|------------|-----|---------|-------|
| ≥ 0 | ゴールデンクロス | any | — | — | BUY | Lv.1 WATCH |
| < 0 | ゴールデンクロス | 範囲外 | — | — | BUY | Lv.2 STANDARD |
| < 0 | ゴールデンクロス | 30 ≦ RSI ≦ 50 | — | — | BUY | Lv.3 STRONG |
| < 0 | ゴールデンクロス | 30 ≦ RSI ≦ 50 | 上昇（価格 > EMA200） | > 25 | BUY | **Lv.4 CONFIRMED** |
| ≤ 0 | デッドクロス | any | — | — | SELL | Lv.1 WATCH |
| > 0 | デッドクロス | 範囲外 | — | — | SELL | Lv.2 STANDARD |
| > 0 | デッドクロス | 50 ≦ RSI ≦ 70 | — | — | SELL | Lv.3 STRONG |
| > 0 | デッドクロス | 50 ≦ RSI ≦ 70 | 下降（価格 < EMA200） | > 25 | SELL | **Lv.4 CONFIRMED** |
| — | クロスなし | any | — | — | HOLD | Lv.0 |

#### 2.3 損切り価格（Stop Loss）算出

```
ATR が有効な場合:
  BUY  SL = Close - ATR × atr_multiplier
  SELL SL = Close + ATR × atr_multiplier

ATR が NaN（データ不足）の場合（フォールバック）:
  BUY  SL = Close × (1 - stop_loss_pct)
  SELL SL = Close × (1 + stop_loss_pct)
```

### 3. 品質保証とトレーサビリティ (Quality & Traceability)

#### 3.1 テスト構成

| テストクラス | 観点 | 主な検証内容 |
|-------------|------|------------|
| `TestCalcIndicators` | ホワイトボックス | EMA 収束・RSI 上下限・副作用なし・ゼロ除算耐性 |
| `TestNewIndicators` | ホワイトボックス | EMA200・ATR・ADX 列の存在と値の妥当性 |
| `TestAnalyzeRow` | ブラックボックス | 全シグナル状態遷移・RSI 境界値（Lv.0〜3） |
| `TestAnalyzeRowLv4` | ブラックボックス | Lv.4 昇格条件・EMA200/ADX ブロック条件 |
| `TestBuildSignalMessage` | ブラックボックス | 全 9 パターン（HOLD / BUY・SELL Lv.1〜4） |
| `TestAnalyze` | ブラックボックス | 返り値・入力不変性・データ不足 |
| `TestAtrStopLoss` | ブラックボックス | ATR ベース SL の方向性・フォールバック |
| `TestAnalyzeSlPriceDirect` | ブラックボックス | モックによる BUY/SELL 損切り価格の検証 |
| `TestRobustness` | 堅牢性 | NaN 含有・定数系列・データ本数の境界（1本/2本） |
| `TestGenerateChart` | 副作用 | PNG 出力の存在・出力先・ファイル名の正確性 |
| `TestGenerateChartSignalMarker` | 副作用 | シグナルマーカー描画ブランチの到達確認 |
| `TestFetchData` | モック | yfinance 呼び出し・period 自動調整・MultiIndex 処理 |
| `TestAnalyzeSlPrice` | 統合 | 実値データでの BUY/SELL シグナル・SL 方向性 |
| `TestMainFlow` | 統合 | fetch_data → analyze → generate_chart パイプライン |
| `TestParseArgs` | CLI | `--symbols` / `--interval` / `--min-level` / `--watch` |
| `TestMainFunction` | CLI | `main()` の全分岐（通常・シンボル上書き・例外・watch） |
| `TestMainEntrypoint` | エントリー | `__main__` ブロックが `main()` を呼び出すことの確認 |

#### 3.2 テストカバレッジ

`pytest-cov` による動的解析で `main.py` の C0（命令網羅）100% を維持しています。

* **現状**: `main.py` 100% 達成（**89 テストケース**）

```bash
pytest tests/ --cov=main --cov-report=term-missing
```

---

**FX-Compass Pro: Engineering-Driven Trade Analysis.**
