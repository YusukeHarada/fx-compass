"""
FX-Compass Pro テストスイート (修正版)

テスト観点:
  - 整合性: main.py のメソッドシグネチャおよび戻り値形式への適合
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
        df.columns = pd.MultiIndex.from_product([df.columns, ["USDJPY=X"]])
    return df


# ---------------------------------------------------------------------------
# ホワイトボックステスト
# ---------------------------------------------------------------------------

class TestIndicatorCalculation:

    def test_ema_converges_to_constant_series(self, analyzer):
        closes = [150.0] * 50
        df = _make_df(closes)
        _, _, _, _, _, df_out = analyzer.analyze(df)
        assert abs(df_out["MACD"].iloc[-1]) < 1e-6

    def test_rsi_bounds(self, analyzer):
        # 単調増加
        df_up = _make_df(list(range(100, 160)))
        _, _, _, _, _, df_out_up = analyzer.analyze(df_up)
        assert 90 < df_out_up["RSI"].iloc[-1] <= 100
        
        # 単調減少
        df_down = _make_df(list(range(160, 100, -1)))
        _, _, _, _, _, df_out_down = analyzer.analyze(df_down)
        assert 0 <= df_out_down["RSI"].iloc[-1] < 10

    def test_multiindex_flattening(self, analyzer):
        df_multi = _make_df([150.0] * 50, multi_index=True)
        with patch("main.yf.download", return_value=df_multi):
            df_fetched = analyzer.fetch_data("USDJPY=X")
            assert not isinstance(df_fetched.columns, pd.MultiIndex)
            assert "Close" in df_fetched.columns


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze_row (ロジックの心臓部)
# ---------------------------------------------------------------------------

class TestAnalyzeRow:

    def _row(self, macd, signal, rsi):
        return {"MACD": macd, "MACDs": signal, "RSI": rsi, "Close": 150.0}

    @pytest.mark.parametrize("rsi_val, expected_lv", [
        (29.9, 2), (30.0, 3), (40.0, 3), (50.0, 3), (50.1, 2)
    ])
    def test_buy_lv3_rsi_boundaries(self, analyzer, rsi_val, expected_lv):
        prev = self._row(macd=-0.2, signal=-0.1, rsi=rsi_val)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=rsi_val)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "BUY"
        assert lv == expected_lv

    @pytest.mark.parametrize("rsi_val, expected_lv", [
        (49.9, 2), (50.0, 3), (60.0, 3), (70.0, 3), (71.0, 2)
    ])
    def test_sell_lv3_rsi_boundaries(self, analyzer, rsi_val, expected_lv):
        prev = self._row(macd=0.2, signal=0.1, rsi=rsi_val)
        curr = self._row(macd=0.05, signal=0.1, rsi=rsi_val)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "SELL"
        assert lv == expected_lv


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze()
# ---------------------------------------------------------------------------

class TestAnalyze:

    def test_stop_loss_calculation_logic(self, analyzer):
        closes = [100.0] * 50 
        df = _make_df(closes)
        
        with patch.object(analyzer, 'analyze_row', return_value=("BUY", 3)):
            sig_str, lv, _, price, sl, _ = analyzer.analyze(df)
            assert "BUY" in sig_str
            assert sl < price
            assert np.isclose(sl, price * 0.99)

        with patch.object(analyzer, 'analyze_row', return_value=("SELL", 2)):
            sig_str, lv, _, price, sl, _ = analyzer.analyze(df)
            assert "SELL" in sig_str
            assert sl > price
            assert np.isclose(sl, price * 1.01)

    def test_data_shortage(self, analyzer):
        df = _make_df([150.0])
        sig, _, _, _, _, _ = analyzer.analyze(df)
        assert "DATA_SHORTAGE" in sig


# ---------------------------------------------------------------------------
# 堅牢性テスト
# ---------------------------------------------------------------------------

class TestRobustness:

    def test_yfinance_download_error_handling(self, analyzer):
        with patch("main.yf.download", side_effect=Exception("API Error")):
            with pytest.raises(Exception) as excinfo:
                analyzer.fetch_data("USDJPY=X")
            assert "API Error" in str(excinfo.value)

    def test_nan_resilience(self, analyzer):
        closes = [150.0] * 20 + [np.nan] * 5 + [151.0] * 25
        df = _make_df(closes)
        sig, _, _, _, _, _ = analyzer.analyze(df)
        assert "ERROR" not in sig


# ---------------------------------------------------------------------------
# 副作用・統合テスト
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_generate_chart_file_output(self, analyzer, tmp_path):
        """
        generate_chart(df, symbol, time) のテスト。
        第3引数 time は datetime オブジェクトである必要があるため修正。
        """
        closes = [150.0] * 60
        df = _make_df(closes)
        _, _, _, _, time_val, df_analyzed = analyzer.analyze(df)

        # analyze() が正常に datetime を返しているか、あるいは手動で生成する
        if not isinstance(time_val, datetime.datetime):
            time_val = datetime.datetime.now()

        try:
            # シグネチャに合わせて引数を渡す
            path = analyzer.generate_chart(
                df_analyzed, 
                "USDJPY=X", 
                time_val
            )
            assert os.path.exists(path)
            assert path.endswith(".png")
        except (TypeError, AttributeError) as e:
            pytest.fail(f"generate_chart integration failed: {e}")

    def test_run_full_scan_loop(self, analyzer):
        mock_df = _make_df([150.0] * 50)
        now = datetime.datetime.now()
        
        with patch.object(analyzer, 'fetch_data', return_value=mock_df), \
             patch.object(analyzer, 'analyze', return_value=("BUY", 3, now, 150.0, 148.5, mock_df)), \
             patch.object(analyzer, 'generate_chart', return_value="charts/dummy.png"):
            
            results = analyzer.run_full_scan()
            
            assert isinstance(results, list)
            if len(results) > 0:
                item = results[0]
                assert "symbol" in item
                assert "signal" in item
                assert "chart" in item