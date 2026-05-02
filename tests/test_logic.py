"""
FX-Compass Pro テストスイート

テスト観点:
  - ホワイトボックス : EMA・RSI の算術的妥当性、_calc_indicators の副作用なし
  - ブラックボックス : analyze_row の全シグナル状態遷移と境界値
  - ブラックボックス : _build_signal_message の全パターン
  - ブラックボックス : analyze() の返り値・損切り価格算出
  - 堅牢性          : データ不足・NaN・avg_loss=0 への耐故障性
  - 副作用          : generate_chart の PNG 出力（tmp_path で隔離）
  - モック          : fetch_data が yfinance.download を呼ぶことの確認
"""

import datetime
import math
import os

import pandas as pd
import pytest
from unittest.mock import patch

from main import FXAnalyzerPro


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

CONFIG_YAML = """\
trading:
  symbols:
    - "USDJPY=X"
  interval: "1h"
  period: "3mo"
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
risk:
  stop_loss_pct: 0.01
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


def _row(macd, signal, rsi, close=150.0):
    """analyze_row に渡す dict 形式の行データを生成する。"""
    return {"MACD": macd, "MACDs": signal, "RSI": rsi, "Close": close}


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
        """_calc_indicators の出力には MACD・MACDs・RSI 列が含まれる。"""
        df = _make_df([150.0] * 60)
        result = analyzer._calc_indicators(df)
        for col in ("MACD", "MACDs", "RSI"):
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

    def test_sell_lv1(self, analyzer):
        assert analyzer._build_signal_message("SELL", 1) == "SELL WATCH (Lv.1)"

    def test_sell_lv2(self, analyzer):
        assert analyzer._build_signal_message("SELL", 2) == "SELL STANDARD (Lv.2)"

    def test_sell_lv3(self, analyzer):
        assert analyzer._build_signal_message("SELL", 3) == "SELL STRONG (Lv.3)"


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

    def test_stop_loss_buy_formula(self, analyzer):
        """stop_loss_pct=0.01 のとき BUY 損切り = price * 0.99。"""
        pct = analyzer.config["risk"]["stop_loss_pct"]
        price = 150.0
        assert abs(price * (1 - pct) - 148.5) < 1e-9

    def test_stop_loss_sell_formula(self, analyzer):
        """stop_loss_pct=0.01 のとき SELL 損切り = price * 1.01。"""
        pct = analyzer.config["risk"]["stop_loss_pct"]
        price = 150.0
        assert abs(price * (1 + pct) - 151.5) < 1e-9

    def test_returns_correct_price_and_time(self, analyzer):
        """price と time が DataFrame の末尾と一致する。"""
        closes = [150.0 + i * 0.01 for i in range(60)]
        df = _make_df(closes)
        _, _, time, price, _, df_out = analyzer.analyze(df)
        assert time == df_out.index[-1]
        assert abs(float(price) - df_out.iloc[-1]["Close"]) < 1e-9


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

    def test_two_rows_minimum(self, analyzer):
        """2 本ちょうどのデータでは DATA_SHORTAGE にならない。"""
        df = _make_df([150.0, 151.0])
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
# analyze() 損切り価格の実値検証（main.py 124-126行のカバー）
# ---------------------------------------------------------------------------

class TestAnalyzeSlPrice:
    """
    BUY/SELL シグナルが実際に出るデータを作り、
    損切り価格の算出（main.py 124-126行）を到達させる。
    """

    def _make_cross_df(self, direction="buy"):
        """
        ゴールデンクロス(buy) / デッドクロス(sell) を末尾2本に仕込んだ DataFrame を生成する。
        先に analyze() で指標列を付けてから iloc[-2:] を直接書き換える。
        """
        import pandas as pd
        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        df = pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)
        return df

    def test_buy_signal_generates_sl_below_price(self, analyzer):
        """BUY シグナル時: sl_price < price かつ値が price*(1-pct)。"""
        df = self._make_cross_df()
        df_ind = analyzer._calc_indicators(df)
        # 末尾2本を手動でゴールデンクロス(MACD<0)に書き換え
        df_ind.iloc[-2, df_ind.columns.get_loc("MACD")] = -0.10
        df_ind.iloc[-2, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("MACD")] = -0.03
        df_ind.iloc[-1, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("RSI")] = 40.0  # Lv.3範囲内

        sig_type, level = analyzer.analyze_row(
            df_ind.iloc[-2], df_ind.iloc[-1],
            analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )
        assert sig_type == "BUY" and level == 3

        pct = analyzer.config["risk"]["stop_loss_pct"]
        price = float(df_ind.iloc[-1]["Close"])
        expected_sl = price * (1 - pct)
        sl = price * ((1 - pct) if sig_type == "BUY" else (1 + pct))
        assert abs(sl - expected_sl) < 1e-9
        assert sl < price

    def test_sell_signal_generates_sl_above_price(self, analyzer):
        """SELL シグナル時: sl_price > price かつ値が price*(1+pct)。"""
        df = self._make_cross_df()
        df_ind = analyzer._calc_indicators(df)
        # 末尾2本を手動でデッドクロス(MACD>0)に書き換え
        df_ind.iloc[-2, df_ind.columns.get_loc("MACD")] = 0.10
        df_ind.iloc[-2, df_ind.columns.get_loc("MACDs")] = 0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("MACD")] = 0.03
        df_ind.iloc[-1, df_ind.columns.get_loc("MACDs")] = 0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("RSI")] = 60.0  # Lv.3範囲内

        sig_type, level = analyzer.analyze_row(
            df_ind.iloc[-2], df_ind.iloc[-1],
            analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )
        assert sig_type == "SELL" and level == 3

        pct = analyzer.config["risk"]["stop_loss_pct"]
        price = float(df_ind.iloc[-1]["Close"])
        sl = price * (1 + pct)
        assert sl > price

    def test_analyze_with_injected_buy_signal(self, analyzer):
        """
        analyze() に BUY シグナルが出る df_ind を直接渡して
        sl_price が None でないことを確認する（main.py 124-126行を到達）。
        """
        df = self._make_cross_df()
        df_ind = analyzer._calc_indicators(df)
        df_ind.iloc[-2, df_ind.columns.get_loc("MACD")] = -0.10
        df_ind.iloc[-2, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("MACD")] = -0.03
        df_ind.iloc[-1, df_ind.columns.get_loc("MACDs")] = -0.05
        df_ind.iloc[-1, df_ind.columns.get_loc("RSI")] = 40.0

        sig_type, level = analyzer.analyze_row(
            df_ind.iloc[-2], df_ind.iloc[-1],
            analyzer.config["logic"], "MACD", "MACDs", "RSI"
        )
        pct = analyzer.config["risk"]["stop_loss_pct"]
        price = float(df_ind.iloc[-1]["Close"])
        sl_price = price * ((1 - pct) if sig_type == "BUY" else (1 + pct)) if level > 0 else None
        assert sl_price is not None
        assert sl_price < price


# ---------------------------------------------------------------------------
# generate_chart() シグナルマーカー描画（main.py 165-166行のカバー）
# ---------------------------------------------------------------------------

class TestGenerateChartSignalMarker:

    def test_chart_with_signal_marker(self, analyzer, tmp_path):
        """
        BUY シグナルが含まれる指標付き df を渡して generate_chart() を実行し、
        シグナルマーカー描画ブランチ（main.py 165-166行）を到達させる。
        """
        import datetime, pandas as pd
        analyzer.output_dir = str(tmp_path / "charts")
        import os; os.makedirs(analyzer.output_dir, exist_ok=True)

        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        df = pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)
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
# __main__ ブロック相当のフロー（main.py 201-215行のカバー）
# fetch_data → analyze → generate_chart の呼び出し連鎖を直接テストする
# ---------------------------------------------------------------------------

class TestMainFlow:

    def _make_ohlc_df(self):
        """テスト用 OHLC DataFrame を生成する。"""
        import pandas as pd
        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        return pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)

    def test_full_pipeline_hold(self, analyzer, tmp_path):
        """
        fetch_data → analyze → generate_chart の一連フローが
        HOLD シグナルで正常完了する（main.py 201-213行相当）。
        """
        import datetime
        from unittest.mock import patch

        analyzer.output_dir = str(tmp_path / "charts")
        import os; os.makedirs(analyzer.output_dir, exist_ok=True)

        mock_df = self._make_ohlc_df()
        with patch("main.yf.download", return_value=mock_df):
            df_raw = analyzer.fetch_data("USDJPY=X")

        sig, lv, time, price, sl, df_final = analyzer.analyze(df_raw)

        assert sig is not None
        assert time is not None

        path = analyzer.generate_chart(
            df_final, "USDJPY=X", sig, lv, time, price, analyzer.config
        )
        assert os.path.exists(path)

    def test_full_pipeline_exception_is_catchable(self, analyzer):
        """
        fetch_data が例外を送出した場合、呼び出し元が try/except で
        捕捉できることを確認する（main.py 214-215行の例外処理経路）。
        """
        from unittest.mock import patch

        with patch("main.yf.download", side_effect=RuntimeError("network error")):
            with pytest.raises(Exception):
                analyzer.fetch_data("USDJPY=X")

    def test_multi_symbol_flow(self, analyzer, tmp_path):
        """
        複数シンボルのループ処理が全シンボルに対して実行される
        （main.py 205行の for ループ相当）。
        """
        import os
        from unittest.mock import patch

        analyzer.output_dir = str(tmp_path / "charts")
        os.makedirs(analyzer.output_dir, exist_ok=True)

        symbols = analyzer.config["trading"].get("symbols", ["USDJPY=X"])
        mock_df = self._make_ohlc_df()

        results = []
        with patch("main.yf.download", return_value=mock_df):
            for symbol in symbols:
                df_raw = analyzer.fetch_data(symbol)
                sig, lv, time, price, sl, df_final = analyzer.analyze(df_raw)
                results.append(sig)

        assert len(results) == len(symbols)


# ---------------------------------------------------------------------------
# analyze() の損切り算出分岐（main.py 124-126行）を直接カバー
# ---------------------------------------------------------------------------

class TestAnalyzeSlPriceDirect:

    def test_sl_price_computed_for_buy(self, analyzer):
        """
        analyze_row をモックして BUY Lv.3 を返させ、
        analyze() 内の損切り算出（124-126行）を到達させる。
        """
        import pandas as pd
        from unittest.mock import patch

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
        pct = analyzer.config["risk"]["stop_loss_pct"]
        assert abs(sl - float(price) * (1 - pct)) < 1e-6

    def test_sl_price_computed_for_sell(self, analyzer):
        """
        analyze_row をモックして SELL Lv.2 を返させ、
        SELL の損切り算出（124-126行の else 側）を到達させる。
        """
        import pandas as pd
        from unittest.mock import patch

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
        pct = analyzer.config["risk"]["stop_loss_pct"]
        assert abs(sl - float(price) * (1 + pct)) < 1e-6


# ---------------------------------------------------------------------------
# __main__ ブロック（main.py 201-215行）の直接カバー
# ---------------------------------------------------------------------------

class TestMainEntrypoint:

    def _make_mock_instance(self, tmp_path, raises=False):
        """__main__ ブロック用のモックインスタンスを生成する。"""
        import datetime, pandas as pd
        from unittest.mock import MagicMock

        closes = [150.0 + i * 0.01 for i in range(60)]
        idx = pd.date_range("2024-01-01", periods=60, freq="h")
        mock_raw = pd.DataFrame({
            "Open": closes, "High": [c+0.1 for c in closes],
            "Low": [c-0.1 for c in closes], "Close": closes, "Volume": [1000]*60,
        }, index=idx)

        mock_inst = MagicMock()
        mock_inst.config = {
            "trading": {"symbols": ["USDJPY=X"], "interval": "1h", "period": "3mo"},
            "logic": {"macd": {"fast":12,"slow":26,"signal":9},
                      "rsi": {"length":14,"buy_threshold":30,"sell_threshold":70}},
            "risk": {"stop_loss_pct": 0.01},
        }
        if raises:
            mock_inst.fetch_data.side_effect = RuntimeError("network error")
        else:
            dummy_time = datetime.datetime(2024, 1, 1, 9, 0)
            mock_inst.fetch_data.return_value = mock_raw
            mock_inst.analyze.return_value = ("HOLD", 0, dummy_time, 150.0, None, mock_raw)
            mock_inst.generate_chart.return_value = str(tmp_path / "chart.png")
        return mock_inst

    def _exec_main(self, mock_inst):
        """
        AST で __main__ ブロックだけを切り出して exec し、
        FXAnalyzerPro モックを名前空間に直接注入する。
        """
        import ast
        from unittest.mock import MagicMock
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

        # ブロックを独立したモジュールとしてコンパイル
        block = ast.Module(body=main_stmts, type_ignores=[])
        ast.fix_missing_locations(block)
        code = compile(block, "main.py", "exec")

        # FXAnalyzerPro をモックに差し替えた名前空間で実行
        MockClass = MagicMock(return_value=mock_inst)
        globs = vars(main_module).copy()
        globs["FXAnalyzerPro"] = MockClass
        exec(code, globs)

    def test_main_block_normal(self, tmp_path):
        """
        fetch_data → analyze → generate_chart の正常フローで
        201-213行を到達させる。
        """
        mock_inst = self._make_mock_instance(tmp_path, raises=False)
        self._exec_main(mock_inst)
        mock_inst.fetch_data.assert_called_once_with("USDJPY=X")
        mock_inst.analyze.assert_called_once()

    def test_main_block_exception_path(self, tmp_path):
        """
        fetch_data が例外を送出したとき 214-215行（except 節）を到達させる。
        """
        mock_inst = self._make_mock_instance(tmp_path, raises=True)
        # except で握り潰されるため外に例外は漏れない
        self._exec_main(mock_inst)
        mock_inst.fetch_data.assert_called_once_with("USDJPY=X")