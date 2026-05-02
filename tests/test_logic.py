"""
FX-Compass Pro テストスイート (拡張版)

テスト観点:
  - ホワイトボックス: EMA・RSI の算術的妥当性、MultiIndex カラム解消
  - ブラックボックス: analyze_row の全シグナル状態遷移と境界値、損切り計算
  - 堅牢性: データ不足・NaN・ゼロ除算・yfinanceエラーへの耐故障性
  - 副作用: generate_chart のファイル出力、run_full_scan の統合フロー
"""

import os
import math
import pytest
import pandas as pd
import numpy as np
import datetime
from unittest.mock import patch, MagicMock
from main import FXAnalyzerPro

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture
def analyzer(tmp_path):
    """一時ディレクトリに config.yaml を置き、FXAnalyzerPro を初期化する。"""
    config_text = """
trading:
  symbols:
    - "USDJPY=X"
    - "EURJPY=X"
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
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_text, encoding="utf-8")
    a = FXAnalyzerPro(config_path=str(config_file))
    # 出力先も一時ディレクトリに隔離
    a.output_dir = str(tmp_path / "charts")
    os.makedirs(a.output_dir, exist_ok=True)
    return a


def _make_df(closes, interval="1h", multi_index=False):
    """テスト用の OHLC DataFrame を生成する。"""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h")
    df = pd.DataFrame({
        "Open":  closes,
        "High":  [c + 0.1 for c in closes],
        "Low":   [c - 0.1 for c in closes],
        "Close": closes,
        "Volume": [1000] * len(closes),
    }, index=idx)
    
    if multi_index:
        # yfinance の最新仕様を模した MultiIndex (Price, Symbol)
        df.columns = pd.MultiIndex.from_product([df.columns, ["USDJPY=X"]])
    return df


# ---------------------------------------------------------------------------
# ホワイトボックステスト: 指標計算の算術的妥当性
# ---------------------------------------------------------------------------

class TestIndicatorCalculation:

    def test_ema_converges_to_constant_series(self, analyzer):
        """定数系列の EMA は定数に収束する。"""
        closes = [150.0] * 50
        df = _make_df(closes)
        _, _, _, _, _, df_out = analyzer.analyze(df)
        assert abs(df_out["MACD"].iloc[-1]) < 1e-6

    def test_rsi_upper_bound(self, analyzer):
        """単調増加系列の RSI は 100 に近づく（上限超えなし）。"""
        closes = list(range(100, 160))
        df = _make_df(closes)
        _, _, _, _, _, df_out = analyzer.analyze(df)
        rsi_last = df_out["RSI"].iloc[-1]
        assert rsi_last <= 100.0
        assert rsi_last > 90.0

    def test_rsi_lower_bound(self, analyzer):
        """単調減少系列の RSI は 0 に近づく（下限割れなし）。"""
        closes = list(range(160, 100, -1))
        df = _make_df(closes)
        _, _, _, _, _, df_out = analyzer.analyze(df)
        rsi_last = df_out["RSI"].iloc[-1]
        assert rsi_last >= 0.0
        assert rsi_last < 10.0

    def test_no_nan_in_output_columns(self, analyzer):
        """十分なデータ長では MACD・RSI の末尾に NaN が残らない。"""
        closes = [150.0 + i * 0.01 for i in range(60)]
        df = _make_df(closes)
        _, _, _, _, _, df_out = analyzer.analyze(df)
        assert not math.isnan(df_out["MACD"].iloc[-1])
        assert not math.isnan(df_out["RSI"].iloc[-1])

    def test_multiindex_column_flattening(self, analyzer):
        """yfinance特有の MultiIndex カラムが正しくフラット化されるか検証。"""
        df_multi = _make_df([150.0] * 50, multi_index=True)
        # fetch_data 内のロジックが正常にカラムを処理できるか
        with patch("main.yf.download", return_value=df_multi):
            df_fetched = analyzer.fetch_data("USDJPY=X")
            # columns が MultiIndex ではなく Index であること、'Close' が存在することを確認
            assert not isinstance(df_fetched.columns, pd.MultiIndex)
            assert "Close" in df_fetched.columns


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze_row の状態遷移
# ---------------------------------------------------------------------------

class TestAnalyzeRow:

    def _row(self, macd, signal, rsi):
        return {"MACD": macd, "MACDs": signal, "RSI": rsi, "Close": 150.0}

    def test_hold_when_no_cross(self, analyzer):
        prev = self._row(macd=-0.1, signal=-0.05, rsi=40)
        curr = self._row(macd=-0.2, signal=-0.05, rsi=40)
        sig, lv = analyzer.analyze_row(
            prev, curr,
            analyzer.config["logic"],
            "MACD", "MACDs", "RSI"
        )
        assert sig == "HOLD"
        assert lv == 0

    # --- BUY 系 ---
    def test_buy_lv1_golden_cross_above_zero(self, analyzer):
        """MACD が 0 より上でゴールデンクロス → Lv.1。"""
        prev = self._row(macd=0.1, signal=0.2, rsi=40)
        curr = self._row(macd=0.3, signal=0.2, rsi=40)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "BUY" and lv == 1

    def test_buy_lv2_golden_cross_below_zero(self, analyzer):
        """MACD が 0 より下でゴールデンクロス かつ RSI が Lv.3範囲外 → Lv.2。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=55)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=55)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "BUY" and lv == 2

    def test_buy_lv3_rsi_in_range(self, analyzer):
        """MACD が 0 より下でゴールデンクロス かつ RSI が 30〜50 → Lv.3。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=40)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=40)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "BUY" and lv == 3

    # --- SELL 系 ---
    def test_sell_lv1_dead_cross_below_zero(self, analyzer):
        """MACD が 0 より下でデッドクロス → Lv.1。"""
        prev = self._row(macd=-0.1, signal=-0.2, rsi=60)
        curr = self._row(macd=-0.3, signal=-0.2, rsi=60)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "SELL" and lv == 1

    def test_sell_lv2_dead_cross_above_zero(self, analyzer):
        """MACD が 0 より上でデッドクロス かつ RSI が Lv.3範囲外 → Lv.2。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=45)
        curr = self._row(macd=0.05, signal=0.1, rsi=45)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "SELL" and lv == 2

    def test_sell_lv3_rsi_in_range(self, analyzer):
        """MACD が 0 より上でデッドクロス かつ RSI が 50〜70 → Lv.3。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=60)
        curr = self._row(macd=0.05, signal=0.1, rsi=60)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "SELL" and lv == 3

    # --- 境界値検証 ---
    @pytest.mark.parametrize("rsi_val, expected_lv", [
        (29.9, 2), (30.0, 3), (40.0, 3), (50.0, 3), (50.1, 2)
    ])
    def test_buy_lv3_rsi_boundaries(self, analyzer, rsi_val, expected_lv):
        """BUY Lv.3 における RSI の境界値を網羅。"""
        prev = self._row(macd=-0.2, signal=-0.1, rsi=rsi_val)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=rsi_val)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == expected_lv

    @pytest.mark.parametrize("rsi_val, expected_lv", [
        (49.9, 2), (50.0, 3), (60.0, 3), (70.0, 3), (70.1, 2)
    ])
    def test_sell_lv3_rsi_boundaries(self, analyzer, rsi_val, expected_lv):
        """SELL Lv.3 における RSI の境界値を網羅。"""
        prev = self._row(macd=0.2, signal=0.1, rsi=rsi_val)
        curr = self._row(macd=0.05, signal=0.1, rsi=rsi_val)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == expected_lv


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze() の出力
# ---------------------------------------------------------------------------

class TestAnalyze:

    def test_data_shortage_returns_early(self, analyzer):
        """データが 1 本以下のとき DATA_SHORTAGE を返す。"""
        df = _make_df([150.0])
        sig, lv, _, _, _, _ = analyzer.analyze(df)
        assert sig == "DATA_SHORTAGE"

    def test_stop_loss_calculation_logic(self, analyzer):
        """損切り価格の計算式が BUY/SELL で正しい方向を向いているか。"""
        # BUY シグナルをエミュレートするデータ
        closes = [100.0] * 40 + [101.0, 102.0, 103.0, 104.0, 105.0]
        df = _make_df(closes)
        # analyze_row をパッチして強制的に BUY を返す
        with patch.object(FXAnalyzerPro, 'analyze_row', return_value=("BUY", 3)):
            sig, lv, time, price, sl, _ = analyzer.analyze(df)
            assert sig == "BUY"
            assert sl < price  # 損切りは下にあるはず
            assert np.isclose(sl, price * (1 - analyzer.config["risk"]["stop_loss_pct"]))

        # SELL シグナルをエミュレート
        with patch.object(FXAnalyzerPro, 'analyze_row', return_value=("SELL", 3)):
            sig, lv, time, price, sl, _ = analyzer.analyze(df)
            assert sig == "SELL"
            assert sl > price  # 損切りは上にあるはず
            assert np.isclose(sl, price * (1 + analyzer.config["risk"]["stop_loss_pct"]))


# ---------------------------------------------------------------------------
# 堅牢性・異常系テスト
# ---------------------------------------------------------------------------

class TestRobustness:

    def test_all_same_price_no_exception(self, analyzer):
        """全値が同一のとき（RSI の avg_loss=0）ゼロ除算等が発生しない。"""
        closes = [150.0] * 60
        df = _make_df(closes)
        sig, lv, _, _, _, _ = analyzer.analyze(df)
        assert sig in ["HOLD", "DATA_SHORTAGE"]

    def test_nan_in_close_no_exception(self, analyzer):
        """Close に NaN が含まれていても計算が続行できる。"""
        closes = [150.0] * 30 + [float("nan")] * 5 + [151.0] * 25
        df = _make_df(closes)
        sig, _, _, _, _, _ = analyzer.analyze(df)
        assert sig != "INDICATOR_CALC_ERROR"

    def test_yfinance_download_error_handling(self, analyzer):
        """yfinance が例外を投げた場合、空の DataFrame を返して続行するか。"""
        with patch("main.yf.download", side_effect=Exception("API Error")):
            result = analyzer.fetch_data("USDJPY=X")
            assert result.empty


# ---------------------------------------------------------------------------
# 副作用・統合テスト
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_generate_chart_file_output(self, analyzer, tmp_path):
        """チャートファイルが指定のディレクトリに PNG として保存される。"""
        closes = [150.0 + i * 0.05 for i in range(60)]
        df = _make_df(closes)
        _, _, _, _, _, df_analyzed = analyzer.analyze(df)

        path = analyzer.generate_chart(
            df_analyzed, "USDJPY=X", "HOLD", 0, 
            datetime.datetime.now(), 150.0, analyzer.config
        )

        assert os.path.exists(path)
        assert path.endswith(".png")
        assert os.path.dirname(path) == analyzer.output_dir

    def test_run_full_scan_loop(self, analyzer):
        """全通貨スキャンの流れをモックで検証。"""
        mock_df = _make_df([150.0] * 50)
        
        with patch.object(analyzer, 'fetch_data', return_value=mock_df) as mock_fetch, \
             patch.object(analyzer, 'generate_chart', return_value="dummy.png") as mock_chart:
            
            results = analyzer.run_full_scan()
            
            # config で定義した 2 通貨分呼ばれているか
            assert mock_fetch.call_count == 2
            assert len(results) == 2
            # 各結果に必須項目が含まれているか
            assert all(k in results[0] for k in ["symbol", "signal", "level", "chart_path"])

    def test_fetch_data_interval_adjustment(self, analyzer):
        """1m, 5m 足の時に period が '1d' に自動調整されるか。"""
        analyzer.config["trading"]["interval"] = "1m"
        with patch("main.yf.download", return_value=_make_df([150]*10)) as mock_dl:
            analyzer.fetch_data("USDJPY=X")
            _, kwargs = mock_dl.call_args
            assert kwargs["period"] == "1d"


# ---------------------------------------------------------------------------
# エッジケース: 設定のフォールバック
# ---------------------------------------------------------------------------

def test_config_load_fallback(tmp_path):
    """設定ファイルが欠落している場合、デフォルト値で初期化されるか。"""
    with patch("builtins.open", side_effect=FileNotFoundError):
        analyzer = FXAnalyzerPro("non_existent.yaml")
        assert "trading" in analyzer.config
        assert "symbols" in analyzer.config["trading"]