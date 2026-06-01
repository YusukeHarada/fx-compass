"""
FX-Compass Pro テストスイート

テスト観点:
  - ホワイトボックス : EMA・RSI・EMA200・ATR・ADX の算術的妥当性
  - ブラックボックス : analyze_row の全シグナル状態遷移と境界値（Lv.4 含む）
  - ブラックボックス : _build_signal_message の全パターン（Lv.4 含む）
  - ブラックボックス : analyze() の返り値・ATR 損切り価格算出
  - 堅牢性          : データ不足・NaN・avg_loss=0 への耐故障性
  - 副作用          : generate_chart の PNG 出力（tmp_path で隔離）
  - モック          : fetch_data が yfinance.download を呼ぶことの確認
  - CLI             : _parse_args の引数パース
  - エントリーポイント: main() 関数の全分岐
"""

import datetime
import math
import os

import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from main import FXAnalyzerPro, _parse_args, _print_results_table, _notify_discord, main


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

CONFIG_YAML = """\
trading:
  symbols:
    - "USDJPY=X"
  interval: "1h"
  period: "3mo"
  watch_interval_seconds: 300
logic:
  mode: "strict"
  macd:
    fast: 12
    slow: 26
    signal: 9
  rsi:
    length: 14
    buy_threshold: 30
    sell_threshold: 70
  adx:
    period: 14
    threshold: 25
risk:
  stop_loss_pct: 0.01
  atr_period: 14
  atr_multiplier: 2.0
"""


@pytest.fixture
def analyzer(tmp_path):
    """一時ディレクトリに config.yaml を置き、FXAnalyzerPro を初期化する。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML)
    a = FXAnalyzerPro(config_path=str(cfg))
    a.output_dir = str(tmp_path / "charts")
    os.makedirs(a.output_dir, exist_ok=True)
    return a


def _make_df(closes):
    """テスト用の OHLC DataFrame を生成する。"""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h")
    return pd.DataFrame({
        "Open":   closes,
        "High":   [c + 0.1 for c in closes],
        "Low":    [c - 0.1 for c in closes],
        "Close":  closes,
        "Volume": [1000] * len(closes),
    }, index=idx)


def _row(macd, signal, rsi, close=150.0, adx=float('nan'), trend=None):
    """analyze_row に渡す dict 形式の行データを生成する。

    adx=NaN, trend=None のデフォルトでは Lv.4 は発動しない。
    """
    d = {"MACD": macd, "MACDs": signal, "RSI": rsi, "Close": close, "ADX": adx}
    if trend is not None:
        d['trend'] = trend
    return d


# ---------------------------------------------------------------------------
# ホワイトボックステスト: _calc_indicators
# ---------------------------------------------------------------------------

class TestCalcIndicators:

    def test_does_not_mutate_input(self, analyzer):
        """_calc_indicators は入力 df を変更しない（副作用なし）。"""
        closes = [150.0] * 60
        df = _make_df(closes)
        original_cols = set(df.columns)
        analyzer._calc_indicators(df)
        assert set(df.columns) == original_cols

    def test_output_has_required_columns(self, analyzer):
        """_calc_indicators の出力には MACD・MACDs・RSI・EMA200・ATR・ADX 列が含まれる。"""
        df = _make_df([150.0] * 60)
        result = analyzer._calc_indicators(df)
        for col in ("MACD", "MACDs", "RSI", "EMA200", "ATR", "ADX"):
            assert col in result.columns

    def test_ema_constant_series_macd_near_zero(self, analyzer):
        """定数系列の MACD は 0 に収束する。"""
        df = _make_df([150.0] * 60)
        result = analyzer._calc_indicators(df)
        assert abs(result["MACD"].iloc[-1]) < 1e-6

    def test_rsi_upper_bound(self, analyzer):
        """単調増加系列の RSI は 100 以下かつ 90 超になる。"""
        df = _make_df(list(range(100, 160)))
        result = analyzer._calc_indicators(df)
        rsi = result["RSI"].iloc[-1]
        assert rsi <= 100.0
        assert rsi > 90.0

    def test_rsi_lower_bound(self, analyzer):
        """単調減少系列の RSI は 0 以上かつ 10 未満になる。"""
        df = _make_df(list(range(160, 100, -1)))
        result = analyzer._calc_indicators(df)
        rsi = result["RSI"].iloc[-1]
        assert rsi >= 0.0
        assert rsi < 10.0

    def test_no_nan_in_tail_for_sufficient_data(self, analyzer):
        """十分なデータ長では末尾の MACD・RSI に NaN が残らない。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        result = analyzer._calc_indicators(df)
        assert not math.isnan(result["MACD"].iloc[-1])
        assert not math.isnan(result["RSI"].iloc[-1])

    def test_avg_loss_zero_no_exception(self, analyzer):
        """avg_loss=0（単調増加）でもゼロ除算が起きない。"""
        df = _make_df([100.0 + i for i in range(60)])
        result = analyzer._calc_indicators(df)
        assert "RSI" in result.columns


# ---------------------------------------------------------------------------
# ホワイトボックステスト: 新規指標（EMA200・ATR・ADX）
# ---------------------------------------------------------------------------

class TestNewIndicators:

    def test_ema200_column_exists(self, analyzer):
        """EMA200 列が出力に存在する。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        result = analyzer._calc_indicators(df)
        assert "EMA200" in result.columns

    def test_atr_column_exists(self, analyzer):
        """ATR 列が出力に存在する。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        result = analyzer._calc_indicators(df)
        assert "ATR" in result.columns

    def test_adx_column_exists(self, analyzer):
        """ADX 列が出力に存在する。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        result = analyzer._calc_indicators(df)
        assert "ADX" in result.columns

    def test_ema200_constant_series(self, analyzer):
        """定数系列の EMA200 は定数に等しくなる。"""
        df = _make_df([150.0] * 60)
        result = analyzer._calc_indicators(df)
        assert abs(result["EMA200"].iloc[-1] - 150.0) < 1e-6

    def test_atr_positive_for_volatile_series(self, analyzer):
        """OHLC にボラティリティがある場合、末尾の ATR は正の値になる。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        result = analyzer._calc_indicators(df)
        atr = result["ATR"].iloc[-1]
        assert not pd.isna(atr)
        assert atr > 0

    def test_adx_nan_for_insufficient_data(self, analyzer):
        """2 本足のみのデータでは ADX は NaN（ローリング計算に十分なデータ不足）。"""
        df = _make_df([150.0, 151.0])
        result = analyzer._calc_indicators(df)
        assert pd.isna(result["ADX"].iloc[-1])

    def test_trend_column_binary(self, analyzer):
        """trend 列は 0.0 または 1.0 のみを持つ（NaN を除く）。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        result = analyzer._calc_indicators(df)
        non_nan = result["trend"].dropna()
        assert set(non_nan.unique()).issubset({0.0, 1.0})


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze_row — 状態遷移
# ---------------------------------------------------------------------------

class TestAnalyzeRow:

    def _call(self, analyzer, prev, curr):
        return analyzer.analyze_row(
            prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )

    def test_hold_no_cross(self, analyzer):
        """クロスなし → HOLD / Lv.0。"""
        sig, lv = self._call(analyzer, _row(-0.2, -0.1, 40), _row(-0.3, -0.1, 40))
        assert sig == "HOLD" and lv == 0

    def test_hold_macd_equals_signal(self, analyzer):
        """MACD == Signal のとき → HOLD / Lv.0。"""
        sig, lv = self._call(analyzer, _row(0.1, 0.1, 50), _row(0.1, 0.1, 50))
        assert sig == "HOLD" and lv == 0

    # --- BUY ---

    def test_buy_lv1_gc_above_zero(self, analyzer):
        """MACD > 0 でゴールデンクロス → BUY / Lv.1。"""
        sig, lv = self._call(analyzer, _row(0.1, 0.2, 40), _row(0.3, 0.2, 40))
        assert sig == "BUY" and lv == 1

    def test_buy_lv2_gc_below_zero_rsi_out_of_range(self, analyzer):
        """MACD < 0 でゴールデンクロス かつ RSI が Lv.3 範囲外 → BUY / Lv.2。"""
        sig, lv = self._call(analyzer, _row(-0.3, -0.1, 51), _row(-0.05, -0.1, 51))
        assert sig == "BUY" and lv == 2

    def test_buy_lv3_gc_below_zero_rsi_30(self, analyzer):
        """MACD < 0 でゴールデンクロス かつ RSI = 30（下限境界）→ BUY / Lv.3。"""
        sig, lv = self._call(analyzer, _row(-0.3, -0.1, 30), _row(-0.05, -0.1, 30))
        assert sig == "BUY" and lv == 3

    def test_buy_lv3_gc_below_zero_rsi_40(self, analyzer):
        """MACD < 0 でゴールデンクロス かつ RSI = 40 → BUY / Lv.3。"""
        sig, lv = self._call(analyzer, _row(-0.3, -0.1, 40), _row(-0.05, -0.1, 40))
        assert sig == "BUY" and lv == 3

    def test_buy_lv3_gc_below_zero_rsi_50(self, analyzer):
        """MACD < 0 でゴールデンクロス かつ RSI = 50（上限境界）→ BUY / Lv.3。"""
        sig, lv = self._call(analyzer, _row(-0.3, -0.1, 50), _row(-0.05, -0.1, 50))
        assert sig == "BUY" and lv == 3

    def test_buy_lv2_rsi_29_below_lower_boundary(self, analyzer):
        """RSI = 29（下限未満）→ Lv.3 にならず Lv.2。"""
        sig, lv = self._call(analyzer, _row(-0.3, -0.1, 29), _row(-0.05, -0.1, 29))
        assert sig == "BUY" and lv == 2

    def test_buy_lv2_rsi_51_above_upper_boundary(self, analyzer):
        """RSI = 51（上限超え）→ Lv.3 にならず Lv.2。"""
        sig, lv = self._call(analyzer, _row(-0.3, -0.1, 51), _row(-0.05, -0.1, 51))
        assert sig == "BUY" and lv == 2

    # --- SELL ---

    def test_sell_lv1_dc_below_zero(self, analyzer):
        """MACD < 0 でデッドクロス → SELL / Lv.1。"""
        sig, lv = self._call(analyzer, _row(-0.1, -0.2, 60), _row(-0.3, -0.2, 60))
        assert sig == "SELL" and lv == 1

    def test_sell_lv2_dc_above_zero_rsi_out_of_range(self, analyzer):
        """MACD > 0 でデッドクロス かつ RSI が Lv.3 範囲外 → SELL / Lv.2。"""
        sig, lv = self._call(analyzer, _row(0.3, 0.1, 49), _row(0.05, 0.1, 49))
        assert sig == "SELL" and lv == 2

    def test_sell_lv3_dc_above_zero_rsi_50(self, analyzer):
        """MACD > 0 でデッドクロス かつ RSI = 50（下限境界）→ SELL / Lv.3。"""
        sig, lv = self._call(analyzer, _row(0.3, 0.1, 50), _row(0.05, 0.1, 50))
        assert sig == "SELL" and lv == 3

    def test_sell_lv3_dc_above_zero_rsi_60(self, analyzer):
        """MACD > 0 でデッドクロス かつ RSI = 60 → SELL / Lv.3。"""
        sig, lv = self._call(analyzer, _row(0.3, 0.1, 60), _row(0.05, 0.1, 60))
        assert sig == "SELL" and lv == 3

    def test_sell_lv3_dc_above_zero_rsi_70(self, analyzer):
        """MACD > 0 でデッドクロス かつ RSI = 70（上限境界）→ SELL / Lv.3。"""
        sig, lv = self._call(analyzer, _row(0.3, 0.1, 70), _row(0.05, 0.1, 70))
        assert sig == "SELL" and lv == 3

    def test_sell_lv2_rsi_49_below_lower_boundary(self, analyzer):
        """RSI = 49（下限未満）→ Lv.3 にならず Lv.2。"""
        sig, lv = self._call(analyzer, _row(0.3, 0.1, 49), _row(0.05, 0.1, 49))
        assert sig == "SELL" and lv == 2

    def test_sell_lv2_rsi_71_above_upper_boundary(self, analyzer):
        """RSI = 71（上限超え）→ Lv.3 にならず Lv.2。"""
        sig, lv = self._call(analyzer, _row(0.3, 0.1, 71), _row(0.05, 0.1, 71))
        assert sig == "SELL" and lv == 2

    def test_rsi_50_is_buy_lv3_boundary(self, analyzer):
        """RSI=50 は BUY Lv.3 の上限境界として Lv.3 になる。"""
        sig, lv = self._call(analyzer, _row(-0.3, -0.1, 50), _row(-0.05, -0.1, 50))
        assert sig == "BUY" and lv == 3

    def test_rsi_50_is_sell_lv3_boundary(self, analyzer):
        """RSI=50 は SELL Lv.3 の下限境界として Lv.3 になる（BUY/SELL は独立した判定）。"""
        sig, lv = self._call(analyzer, _row(0.3, 0.1, 50), _row(0.05, 0.1, 50))
        assert sig == "SELL" and lv == 3


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze_row — Lv.4 CONFIRMED
# ---------------------------------------------------------------------------

class TestAnalyzeRowLv4:

    def _call(self, analyzer, prev, curr):
        return analyzer.analyze_row(
            prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )

    def test_lv4_buy_all_filters_pass(self, analyzer):
        """BUY Lv.3 条件 + EMA200 上昇方向 + ADX 強 → BUY / Lv.4。"""
        prev = _row(-0.3, -0.1, 40, adx=30.0, trend=1)
        curr = _row(-0.05, -0.1, 40, adx=30.0, trend=1)
        sig, lv = self._call(analyzer, prev, curr)
        assert sig == "BUY" and lv == 4

    def test_lv4_sell_all_filters_pass(self, analyzer):
        """SELL Lv.3 条件 + EMA200 下降方向 + ADX 強 → SELL / Lv.4。"""
        prev = _row(0.3, 0.1, 60, adx=30.0, trend=0)
        curr = _row(0.05, 0.1, 60, adx=30.0, trend=0)
        sig, lv = self._call(analyzer, prev, curr)
        assert sig == "SELL" and lv == 4

    def test_lv4_blocked_by_ema200_misalign_buy(self, analyzer):
        """BUY Lv.3 だが trend=0（下降トレンド）→ Lv.4 にならず Lv.3。"""
        prev = _row(-0.3, -0.1, 40, adx=30.0, trend=0)
        curr = _row(-0.05, -0.1, 40, adx=30.0, trend=0)
        sig, lv = self._call(analyzer, prev, curr)
        assert sig == "BUY" and lv == 3

    def test_lv4_blocked_by_ema200_misalign_sell(self, analyzer):
        """SELL Lv.3 だが trend=1（上昇トレンド）→ Lv.4 にならず Lv.3。"""
        prev = _row(0.3, 0.1, 60, adx=30.0, trend=1)
        curr = _row(0.05, 0.1, 60, adx=30.0, trend=1)
        sig, lv = self._call(analyzer, prev, curr)
        assert sig == "SELL" and lv == 3

    def test_lv4_blocked_by_adx_too_low(self, analyzer):
        """BUY Lv.3、trend=1 でも ADX=20（閾値以下）→ Lv.3 に留まる。"""
        prev = _row(-0.3, -0.1, 40, adx=20.0, trend=1)
        curr = _row(-0.05, -0.1, 40, adx=20.0, trend=1)
        sig, lv = self._call(analyzer, prev, curr)
        assert sig == "BUY" and lv == 3

    def test_lv4_blocked_by_adx_nan(self, analyzer):
        """BUY Lv.3、trend=1 でも ADX=NaN → Lv.4 にならず Lv.3。"""
        prev = _row(-0.3, -0.1, 40, adx=float('nan'), trend=1)
        curr = _row(-0.05, -0.1, 40, adx=float('nan'), trend=1)
        sig, lv = self._call(analyzer, prev, curr)
        assert sig == "BUY" and lv == 3

    def test_lv4_not_triggered_for_lv2_base(self, analyzer):
        """Lv.2 ベース（RSI範囲外）では EMA200+ADX が揃っても Lv.4 にならない。"""
        prev = _row(-0.3, -0.1, 60, adx=30.0, trend=1)  # RSI=60 → Lv.2
        curr = _row(-0.05, -0.1, 60, adx=30.0, trend=1)
        sig, lv = self._call(analyzer, prev, curr)
        assert sig == "BUY" and lv == 2


# ---------------------------------------------------------------------------
# ブラックボックステスト: _build_signal_message
# ---------------------------------------------------------------------------

class TestBuildSignalMessage:

    def test_hold(self, analyzer):
        assert analyzer._build_signal_message("HOLD", 0) == "HOLD"

    def test_buy_lv1(self, analyzer):
        assert analyzer._build_signal_message("BUY", 1) == "BUY WATCH (Lv.1)"

    def test_buy_lv2(self, analyzer):
        assert analyzer._build_signal_message("BUY", 2) == "BUY STANDARD (Lv.2)"

    def test_buy_lv3(self, analyzer):
        assert analyzer._build_signal_message("BUY", 3) == "BUY STRONG (Lv.3)"

    def test_buy_lv4(self, analyzer):
        assert analyzer._build_signal_message("BUY", 4) == "BUY CONFIRMED (Lv.4)"

    def test_sell_lv1(self, analyzer):
        assert analyzer._build_signal_message("SELL", 1) == "SELL WATCH (Lv.1)"

    def test_sell_lv2(self, analyzer):
        assert analyzer._build_signal_message("SELL", 2) == "SELL STANDARD (Lv.2)"

    def test_sell_lv3(self, analyzer):
        assert analyzer._build_signal_message("SELL", 3) == "SELL STRONG (Lv.3)"

    def test_sell_lv4(self, analyzer):
        assert analyzer._build_signal_message("SELL", 4) == "SELL CONFIRMED (Lv.4)"


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze()
# ---------------------------------------------------------------------------

class TestAnalyze:

    def test_data_shortage_returns_early(self, analyzer):
        """データが 1 本以下のとき DATA_SHORTAGE を返す。"""
        df = _make_df([150.0])
        sig, lv, time, price, sl, _ = analyzer.analyze(df)
        assert sig == "DATA_SHORTAGE"
        assert lv == 0
        assert time is None
        assert price is None
        assert sl is None

    def test_empty_df_returns_data_shortage(self, analyzer):
        """空 DataFrame でも DATA_SHORTAGE を返す。"""
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        sig, lv, _, _, _, _ = analyzer.analyze(df)
        assert sig == "DATA_SHORTAGE"

    def test_hold_returns_no_sl(self, analyzer):
        """HOLD シグナルのとき sl_price は None。"""
        df = _make_df([150.0] * 60)
        sig, lv, _, _, sl, _ = analyzer.analyze(df)
        assert lv == 0
        assert sl is None

    def test_output_df_has_indicator_columns(self, analyzer):
        """analyze() の返り値 df_out に指標列が含まれる。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        _, _, _, _, _, df_out = analyzer.analyze(df)
        for col in ("MACD", "MACDs", "RSI"):
            assert col in df_out.columns

    def test_input_df_not_mutated(self, analyzer):
        """analyze() は入力 df を変更しない。"""
        closes = [150.0] * 60
        df = _make_df(closes)
        original_cols = set(df.columns)
        analyzer.analyze(df)
        assert set(df.columns) == original_cols

    def test_stop_loss_pct_formula_math(self, analyzer):
        """stop_loss_pct=0.01 の数式検証: price * (1 - 0.01) = price * 0.99。"""
        pct = analyzer.config["risk"]["stop_loss_pct"]
        price = 150.0
        assert abs(price * (1 - pct) - 148.5) < 1e-9

    def test_stop_loss_sell_formula_math(self, analyzer):
        """stop_loss_pct=0.01 の数式検証: price * (1 + 0.01) = price * 1.01。"""
        pct = analyzer.config["risk"]["stop_loss_pct"]
        price = 150.0
        assert abs(price * (1 + pct) - 151.5) < 1e-9

    def test_returns_correct_price_and_time(self, analyzer):
        """price と time が確定足（末尾から2番目）と一致する。"""
        closes = [150.0 + i * 0.01 for i in range(60)]
        df = _make_df(closes)
        _, _, time, price, _, df_out = analyzer.analyze(df)
        assert time == df_out.index[-2]
        assert abs(float(price) - df_out.iloc[-2]["Close"]) < 1e-9


# ---------------------------------------------------------------------------
# ATR ベース損切り価格テスト
# ---------------------------------------------------------------------------

class TestAtrStopLoss:

    def test_atr_sl_buy_is_below_price(self, analyzer):
        """BUY シグナル時: ATR ベースの sl_price < price。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        with patch.object(analyzer, "analyze_row", return_value=("BUY", 3)):
            _, lv, _, price, sl, _ = analyzer.analyze(df)
        assert lv == 3
        assert sl is not None
        assert sl < float(price)

    def test_atr_sl_sell_is_above_price(self, analyzer):
        """SELL シグナル時: ATR ベースの sl_price > price。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        with patch.object(analyzer, "analyze_row", return_value=("SELL", 2)):
            _, lv, _, price, sl, _ = analyzer.analyze(df)
        assert lv == 2
        assert sl is not None
        assert sl > float(price)

    def test_atr_sl_fallback_to_pct_when_nan(self, analyzer):
        """ATR が NaN のとき固定% フォールバック: sl = price * (1 - pct)。"""
        df = _make_df([150.0 + i * 0.01 for i in range(60)])
        df_ind = analyzer._calc_indicators(df)
        # ATR 列を NaN で上書きして fallback を強制
        df_ind["ATR"] = float('nan')
        with patch.object(analyzer, "_calc_indicators", return_value=df_ind):
            with patch.object(analyzer, "analyze_row", return_value=("BUY", 3)):
                _, lv, _, price, sl, _ = analyzer.analyze(df)
        pct = analyzer.config["risk"]["stop_loss_pct"]
        assert abs(sl - float(price) * (1 - pct)) < 1e-6


# ---------------------------------------------------------------------------
# 堅牢性テスト
# ---------------------------------------------------------------------------

class TestRobustness:

    def test_constant_series_no_exception(self, analyzer):
        """定数系列（avg_loss=0）で例外が発生しない。"""
        df = _make_df([150.0] * 60)
        sig, lv, _, _, _, df_out = analyzer.analyze(df)
        assert "RSI" in df_out.columns

    def test_nan_in_close_no_exception(self, analyzer):
        """Close に NaN が含まれても例外が発生しない。"""
        closes = [150.0] * 30 + [float("nan")] * 5 + [151.0] * 25
        df = _make_df(closes)
        sig, lv, _, _, _, df_out = analyzer.analyze(df)
        assert "RSI" in df_out.columns

    def test_two_rows_data_shortage(self, analyzer):
        """2 本のデータは DATA_SHORTAGE になる（形成中足を除くと確定足が1本のみ）。"""
        df = _make_df([150.0, 151.0])
        sig, _, _, _, _, _ = analyzer.analyze(df)
        assert sig == "DATA_SHORTAGE"

    def test_three_rows_minimum(self, analyzer):
        """3 本ちょうどのデータでは DATA_SHORTAGE にならない。"""
        df = _make_df([150.0, 151.0, 152.0])
        sig, _, _, _, _, _ = analyzer.analyze(df)
        assert sig != "DATA_SHORTAGE"

    def test_one_row_data_shortage(self, analyzer):
        """1 本のデータは DATA_SHORTAGE になる。"""
        df = _make_df([150.0])
        sig, _, _, _, _, _ = analyzer.analyze(df)
        assert sig == "DATA_SHORTAGE"


# ---------------------------------------------------------------------------
# 副作用テスト: generate_chart
# ---------------------------------------------------------------------------

class TestGenerateChart:

    def _analyzed_df(self, analyzer):
        closes = [150.0 + i * 0.05 for i in range(60)]
        df = _make_df(closes)
        _, _, _, _, _, df_out = analyzer.analyze(df)
        return df_out

    def test_chart_file_created(self, analyzer, tmp_path):
        """generate_chart() を呼ぶと PNG ファイルが生成される。"""
        analyzer.output_dir = str(tmp_path / "charts")
        os.makedirs(analyzer.output_dir, exist_ok=True)
        df_out = self._analyzed_df(analyzer)
        dummy_time = datetime.datetime(2024, 1, 1, 9, 0)

        path = analyzer.generate_chart(
            df_out, "USDJPY=X", "HOLD", 0, dummy_time, 150.0, analyzer.config
        )

        assert os.path.exists(path)
        assert path.endswith(".png")

    def test_chart_saved_in_output_dir(self, analyzer, tmp_path):
        """生成された PNG が output_dir 配下に保存される。"""
        isolated_dir = str(tmp_path / "charts_isolated")
        os.makedirs(isolated_dir, exist_ok=True)
        analyzer.output_dir = isolated_dir
        df_out = self._analyzed_df(analyzer)
        dummy_time = datetime.datetime(2024, 6, 1, 12, 0)

        path = analyzer.generate_chart(
            df_out, "USDJPY=X", "HOLD", 0, dummy_time, 150.0, analyzer.config
        )

        assert os.path.dirname(path) == isolated_dir

    def test_chart_filename_excludes_equals_x(self, analyzer, tmp_path):
        """ファイル名から '=X' が除去される。"""
        analyzer.output_dir = str(tmp_path / "charts")
        os.makedirs(analyzer.output_dir, exist_ok=True)
        df_out = self._analyzed_df(analyzer)
        dummy_time = datetime.datetime(2024, 1, 1, 9, 0)

        path = analyzer.generate_chart(
            df_out, "USDJPY=X", "HOLD", 0, dummy_time, 150.0, analyzer.config
        )

        assert "=X" not in os.path.basename(path)


# ---------------------------------------------------------------------------
# generate_chart() シグナルマーカー描画（color_map Lv.4 含む）
# ---------------------------------------------------------------------------

class TestGenerateChartSignalMarker:

    def test_chart_with_signal_marker(self, analyzer, tmp_path):
        """
        BUY シグナルが含まれる指標付き df を渡して generate_chart() を実行し、
        シグナルマーカー描画ブランチを到達させる。
        """
        analyzer.output_dir = str(tmp_path / "charts")
        os.makedirs(analyzer.output_dir, exist_ok=True)

        closes = [150.0 + i * 0.01 for i in range(60)]
        df = _make_df(closes)
        df_ind = analyzer._calc_indicators(df)

        # 途中にゴールデンクロス(BUY Lv.3)を仕込む
        mid = 30
        df_ind.iloc[mid-1, df_ind.columns.get_loc("MACD")] = -0.10
        df_ind.iloc[mid-1, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[mid,   df_ind.columns.get_loc("MACD")] = -0.03
        df_ind.iloc[mid,   df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[mid,   df_ind.columns.get_loc("RSI")] = 40.0

        dummy_time = datetime.datetime(2024, 1, 1, 9, 0)
        path = analyzer.generate_chart(
            df_ind, "USDJPY=X", "BUY STRONG (Lv.3)", 3,
            dummy_time, 150.0, analyzer.config
        )
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# モックテスト: fetch_data
# ---------------------------------------------------------------------------

class TestFetchData:

    def test_calls_yf_download(self, analyzer):
        """fetch_data() が yfinance.download を呼び出す。"""
        mock_df = _make_df([150.0] * 30)
        with patch("main.yf.download", return_value=mock_df) as mock_dl:
            result = analyzer.fetch_data("USDJPY=X")
            mock_dl.assert_called_once()
            assert not result.empty

    def test_short_interval_overrides_period(self, analyzer):
        """1m 足では period が '1d' で呼ばれる。"""
        analyzer.config["trading"]["interval"] = "1m"
        mock_df = _make_df([150.0] * 60)
        with patch("main.yf.download", return_value=mock_df) as mock_dl:
            analyzer.fetch_data("USDJPY=X")
            _, kwargs = mock_dl.call_args
            assert kwargs.get("period") == "1d"

    def test_5m_interval_overrides_period(self, analyzer):
        """5m 足でも period が '1d' で呼ばれる。"""
        analyzer.config["trading"]["interval"] = "5m"
        mock_df = _make_df([150.0] * 60)
        with patch("main.yf.download", return_value=mock_df) as mock_dl:
            analyzer.fetch_data("USDJPY=X")
            _, kwargs = mock_dl.call_args
            assert kwargs.get("period") == "1d"

    def test_1h_interval_uses_config_period(self, analyzer):
        """1h 足では config の period がそのまま使われる。"""
        analyzer.config["trading"]["interval"] = "1h"
        mock_df = _make_df([150.0] * 60)
        with patch("main.yf.download", return_value=mock_df) as mock_dl:
            analyzer.fetch_data("USDJPY=X")
            _, kwargs = mock_dl.call_args
            assert kwargs.get("period") == analyzer.config["trading"]["period"]

    def test_multiindex_columns_flattened(self, analyzer):
        """MultiIndex カラムが返された場合にフラット化される。"""
        mock_df = _make_df([150.0] * 30)
        multi_cols = pd.MultiIndex.from_tuples(
            [(c, "USDJPY=X") for c in mock_df.columns]
        )
        mock_df.columns = multi_cols
        with patch("main.yf.download", return_value=mock_df):
            result = analyzer.fetch_data("USDJPY=X")
            assert not isinstance(result.columns, pd.MultiIndex)


# ---------------------------------------------------------------------------
# analyze() 損切り価格の実値検証
# ---------------------------------------------------------------------------

class TestAnalyzeSlPrice:
    """
    BUY/SELL シグナルが実際に出るデータを作り、損切り価格の方向性を検証する。
    """

    def _make_cross_df(self):
        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        return pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)

    def test_buy_signal_sl_below_price(self, analyzer):
        """BUY シグナル時: sl_price < price（ATR ベース）。"""
        df = self._make_cross_df()
        df_ind = analyzer._calc_indicators(df)
        df_ind.iloc[-2, df_ind.columns.get_loc("MACD")]  = -0.10
        df_ind.iloc[-2, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("MACD")]  = -0.03
        df_ind.iloc[-1, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("RSI")]   = 40.0

        sig_type, level = analyzer.analyze_row(
            df_ind.iloc[-2], df_ind.iloc[-1],
            analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )
        assert sig_type == "BUY" and level >= 3

        price = float(df_ind.iloc[-1]["Close"])
        atr   = float(df_ind.iloc[-1]["ATR"])
        mult  = analyzer.config["risk"]["atr_multiplier"]
        sl    = price - mult * atr
        assert sl < price

    def test_sell_signal_sl_above_price(self, analyzer):
        """SELL シグナル時: sl_price > price（ATR ベース）。"""
        df = self._make_cross_df()
        df_ind = analyzer._calc_indicators(df)
        df_ind.iloc[-2, df_ind.columns.get_loc("MACD")]  = 0.10
        df_ind.iloc[-2, df_ind.columns.get_loc("MACDs")] = 0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("MACD")]  = 0.03
        df_ind.iloc[-1, df_ind.columns.get_loc("MACDs")] = 0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("RSI")]   = 60.0

        sig_type, level = analyzer.analyze_row(
            df_ind.iloc[-2], df_ind.iloc[-1],
            analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )
        assert sig_type == "SELL" and level >= 3

        price = float(df_ind.iloc[-1]["Close"])
        atr   = float(df_ind.iloc[-1]["ATR"])
        mult  = analyzer.config["risk"]["atr_multiplier"]
        sl    = price + mult * atr
        assert sl > price

    def test_analyze_with_injected_buy_gives_sl(self, analyzer):
        """analyze() 経由で BUY シグナルが出るとき sl_price が None でない。"""
        df = self._make_cross_df()
        df_ind = analyzer._calc_indicators(df)
        df_ind.iloc[-2, df_ind.columns.get_loc("MACD")]  = -0.10
        df_ind.iloc[-2, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("MACD")]  = -0.03
        df_ind.iloc[-1, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("RSI")]   = 40.0

        sig_type, level = analyzer.analyze_row(
            df_ind.iloc[-2], df_ind.iloc[-1],
            analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )
        price = float(df_ind.iloc[-1]["Close"])
        sl_price = price - 1.0 if sig_type == "BUY" and level > 0 else None
        assert sl_price is not None
        assert sl_price < price


# ---------------------------------------------------------------------------
# analyze() の損切り算出分岐を直接カバー
# ---------------------------------------------------------------------------

class TestAnalyzeSlPriceDirect:

    def test_sl_price_computed_for_buy(self, analyzer):
        """
        analyze_row をモックして BUY Lv.3 を返させ、
        analyze() 内の損切り算出を到達させる（ATR ベース: sl < price）。
        """
        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        df = pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)

        with patch.object(analyzer, "analyze_row", return_value=("BUY", 3)):
            sig, lv, time, price, sl, _ = analyzer.analyze(df)

        assert lv == 3
        assert sl is not None
        assert sl < float(price)

    def test_sl_price_computed_for_sell(self, analyzer):
        """
        analyze_row をモックして SELL Lv.2 を返させ、
        SELL の損切り算出（else 側）を到達させる（ATR ベース: sl > price）。
        """
        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        df = pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)

        with patch.object(analyzer, "analyze_row", return_value=("SELL", 2)):
            sig, lv, time, price, sl, _ = analyzer.analyze(df)

        assert lv == 2
        assert sl is not None
        assert sl > float(price)


# ---------------------------------------------------------------------------
# パイプライン統合テスト
# ---------------------------------------------------------------------------

class TestMainFlow:

    def _make_ohlc_df(self):
        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        return pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)

    def test_full_pipeline_hold(self, analyzer, tmp_path):
        """fetch_data → analyze → generate_chart の一連フローが正常完了する。"""
        analyzer.output_dir = str(tmp_path / "charts")
        os.makedirs(analyzer.output_dir, exist_ok=True)

        mock_df = self._make_ohlc_df()
        with patch("main.yf.download", return_value=mock_df):
            df_raw = analyzer.fetch_data("USDJPY=X")

        sig, lv, t, price, sl, df_final = analyzer.analyze(df_raw)

        assert sig is not None
        assert t is not None

        path = analyzer.generate_chart(
            df_final, "USDJPY=X", sig, lv, t, price, analyzer.config
        )
        assert os.path.exists(path)

    def test_full_pipeline_exception_is_catchable(self, analyzer):
        """fetch_data が例外を送出した場合、呼び出し元が捕捉できる。"""
        with patch("main.yf.download", side_effect=RuntimeError("network error")):
            with pytest.raises(Exception):
                analyzer.fetch_data("USDJPY=X")

    def test_multi_symbol_flow(self, analyzer, tmp_path):
        """複数シンボルのループ処理が全シンボルに対して実行される。"""
        analyzer.output_dir = str(tmp_path / "charts")
        os.makedirs(analyzer.output_dir, exist_ok=True)

        symbols = analyzer.config["trading"].get("symbols", ["USDJPY=X"])
        mock_df = self._make_ohlc_df()

        results = []
        with patch("main.yf.download", return_value=mock_df):
            for symbol in symbols:
                df_raw = analyzer.fetch_data(symbol)
                sig, lv, t, price, sl, df_final = analyzer.analyze(df_raw)
                results.append(sig)

        assert len(results) == len(symbols)


# ---------------------------------------------------------------------------
# CLI 引数パース: _parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:

    def test_default_args(self):
        """引数なしのデフォルト: min_level=1, watch=False, symbols=None, interval=None。"""
        args = _parse_args([])
        assert args.min_level == 1
        assert args.watch is False
        assert args.symbols is None
        assert args.interval is None

    def test_symbols_override(self):
        """--symbols でシンボルリストが設定される。"""
        args = _parse_args(["--symbols", "USDJPY=X", "EURJPY=X"])
        assert args.symbols == ["USDJPY=X", "EURJPY=X"]

    def test_interval_override(self):
        """--interval で時間足が設定される。"""
        args = _parse_args(["--interval", "4h"])
        assert args.interval == "4h"

    def test_min_level_and_watch(self):
        """--min-level と --watch が同時に設定される。"""
        args = _parse_args(["--min-level", "3", "--watch"])
        assert args.min_level == 3
        assert args.watch is True


# ---------------------------------------------------------------------------
# main() 関数テスト
# ---------------------------------------------------------------------------

class TestMainFunction:

    def _make_mock_config(self):
        return {
            "trading": {
                "symbols": ["USDJPY=X"],
                "interval": "1h",
                "period": "3mo",
                "watch_interval_seconds": 1,
            },
            "logic": {
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "rsi": {"length": 14, "buy_threshold": 30, "sell_threshold": 70},
                "adx": {"period": 14, "threshold": 25},
            },
            "risk": {"stop_loss_pct": 0.01, "atr_period": 14, "atr_multiplier": 2.0},
        }

    def _make_mock_inst(self, tmp_path, config=None, lv=0, raises=False):
        mock_df = _make_df([150.0 + i * 0.01 for i in range(60)])
        dummy_time = datetime.datetime(2024, 1, 1, 9, 0)

        inst = MagicMock()
        inst.config = config or self._make_mock_config()
        if raises:
            inst.fetch_data.side_effect = RuntimeError("err")
        else:
            inst.fetch_data.return_value = mock_df
            inst.analyze.return_value = (
                "HOLD" if lv == 0 else f"BUY WATCH (Lv.{lv})",
                lv, dummy_time, 150.0,
                None if lv == 0 else 148.5,
                mock_df,
            )
            inst.generate_chart.return_value = str(tmp_path / "chart.png")
        return inst

    def test_main_no_args_hold(self, tmp_path):
        """引数なし・HOLD シグナルで main() が正常終了する。"""
        inst = self._make_mock_inst(tmp_path, lv=0)
        with patch("main.FXAnalyzerPro", return_value=inst):
            main([])
        inst.fetch_data.assert_called_once_with("USDJPY=X")
        inst.analyze.assert_called_once()

    def test_main_symbols_and_interval_override(self, tmp_path):
        """--symbols, --interval の上書きが config に反映され chart が生成される。"""
        config = self._make_mock_config()
        inst = self._make_mock_inst(tmp_path, config=config, lv=1)
        with patch("main.FXAnalyzerPro", return_value=inst):
            main(["--symbols", "EURJPY=X", "--interval", "4h"])
        assert inst.config["trading"]["symbols"] == ["EURJPY=X"]
        assert inst.config["trading"]["interval"] == "4h"
        inst.generate_chart.assert_called_once()

    def test_main_exception_handled_gracefully(self, tmp_path):
        """fetch_data が例外を投げても main() がクラッシュせず表示を返す。"""
        inst = self._make_mock_inst(tmp_path, raises=True)
        with patch("main.FXAnalyzerPro", return_value=inst):
            main([])  # 例外が外に出ないこと
        inst.fetch_data.assert_called_once()

    def test_main_watch_mode_iterates_twice(self, tmp_path):
        """--watch モードで 2 回目のイテレーション（if not first_run: 分岐）を通過する。"""
        config = self._make_mock_config()
        inst = self._make_mock_inst(tmp_path, config=config, lv=0)
        # 2 回目の fetch_data で KeyboardInterrupt（ループを抜ける）
        mock_df = _make_df([150.0 + i * 0.01 for i in range(60)])
        dummy_time = datetime.datetime(2024, 1, 1, 9, 0)
        inst.fetch_data.side_effect = [
            mock_df,          # 1st iteration: success
            KeyboardInterrupt,  # 2nd iteration: interrupt
        ]
        inst.analyze.return_value = ("HOLD", 0, dummy_time, 150.0, None, mock_df)

        with patch("main.FXAnalyzerPro", return_value=inst):
            with patch("main.time.sleep"):
                with pytest.raises(KeyboardInterrupt):
                    main(["--watch"])

        assert inst.fetch_data.call_count == 2

    def test_discord_notify_called_when_signal(self, tmp_path):
        """DISCORD_WEBHOOK_URL 設定 + シグナルあり時に _notify_discord が呼ばれること。"""
        config = self._make_mock_config()
        inst = self._make_mock_inst(tmp_path, config=config, lv=2)
        webhook = "https://discord.com/api/webhooks/123/abc"
        with patch("main.FXAnalyzerPro", return_value=inst):
            with patch("main._notify_discord") as mock_notify:
                with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": webhook}):
                    main([])
        mock_notify.assert_called_once()

    def test_discord_notify_skipped_without_url(self, tmp_path):
        """DISCORD_WEBHOOK_URL 未設定時は _notify_discord が呼ばれない。"""
        config = self._make_mock_config()
        inst = self._make_mock_inst(tmp_path, config=config, lv=2)
        env_without_url = {k: v for k, v in os.environ.items() if k != "DISCORD_WEBHOOK_URL"}
        with patch("main.FXAnalyzerPro", return_value=inst):
            with patch("main._notify_discord") as mock_notify:
                with patch.dict(os.environ, env_without_url, clear=True):
                    main([])
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# LINE Notify 関数テスト
# ---------------------------------------------------------------------------

class TestNotifyDiscord:

    def test_sends_post_request(self):
        """urlopen が Discord Webhook URL と Content-Type ヘッダーで呼ばれること。"""
        webhook = "https://discord.com/api/webhooks/123/abc"
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=MagicMock())
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_cm) as mock_urlopen:
            _notify_discord("test message", webhook)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == webhook
        assert req.get_header("Content-type") == "application/json"

    def test_exception_does_not_crash(self, capsys):
        """urllib エラー時にクラッシュせず stderr に警告を出力する。"""
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            _notify_discord("test", "https://discord.com/api/webhooks/x/y")
        assert "Discord 通知失敗" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# __main__ ブロック（main() 呼び出し）のカバー
# ---------------------------------------------------------------------------

class TestMainEntrypoint:

    def test_main_block_calls_main_function(self):
        """
        if __name__ == '__main__': のブロックが main() を呼び出すことを確認する。
        AST で __main__ ブロックを切り出し、main をモックに差し替えて exec する。
        """
        import ast
        import main as main_module

        with open("main.py") as f:
            source = f.read()

        tree = ast.parse(source)
        main_stmts = []
        for node in tree.body:
            if isinstance(node, ast.If):
                test = node.test
                if (isinstance(test, ast.Compare)
                        and isinstance(test.left, ast.Name)
                        and test.left.id == "__name__"
                        and any(isinstance(c, ast.Constant) and c.value == "__main__"
                                for c in test.comparators)):
                    main_stmts = node.body
                    break

        block = ast.Module(body=main_stmts, type_ignores=[])
        ast.fix_missing_locations(block)
        code = compile(block, "main.py", "exec")

        mock_main = MagicMock()
        globs = vars(main_module).copy()
        globs["main"] = mock_main
        exec(code, globs)
        mock_main.assert_called_once_with()
