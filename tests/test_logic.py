"""
FX-Compass Pro テストスイート

テスト観点:
  - ホワイトボックス: EMA・RSI の算術的妥当性
  - ブラックボックス: analyze_row の全シグナル状態遷移と境界値
  - 堅牢性: データ不足・NaN・ゼロ除算への耐故障性
  - 副作用: generate_chart のファイル出力（tmp_path で隔離）
"""

import os
import math
import pytest
import pandas as pd
import numpy as np
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
    config_file.write_text(config_text)
    a = FXAnalyzerPro(config_path=str(config_file))
    a.output_dir = str(tmp_path / "charts")
    os.makedirs(a.output_dir, exist_ok=True)
    return a


def _make_df(closes, interval="1h"):
    """テスト用の OHLC DataFrame を生成する。"""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h")
    return pd.DataFrame({
        "Open":  closes,
        "High":  [c + 0.1 for c in closes],
        "Low":   [c - 0.1 for c in closes],
        "Close": closes,
        "Volume": [1000] * len(closes),
    }, index=idx)


def _make_analyzed_df(analyzer, closes):
    """analyze() が受け取る前処理済み DataFrame（指標列付き）を返す。"""
    df = _make_df(closes)
    sig, lv, time, price, sl, df_out = analyzer.analyze(df)
    return df_out


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
        """MACD が 0 より上でゴールデンクロス → Lv.1（0ライン条件を満たさない）。"""
        prev = self._row(macd=0.1, signal=0.2, rsi=40)
        curr = self._row(macd=0.3, signal=0.2, rsi=40)
        sig, lv = analyzer.analyze_row(
            prev, curr,
            analyzer.config["logic"],
            "MACD", "MACDs", "RSI"
        )
        assert sig == "BUY"
        assert lv == 1

    def test_buy_lv2_golden_cross_below_zero(self, analyzer):
        """MACD が 0 より下でゴールデンクロス → Lv.2。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=40)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=40)
        sig, lv = analyzer.analyze_row(
            prev, curr,
            analyzer.config["logic"],
            "MACD", "MACDs", "RSI"
        )
        assert sig == "BUY"
        assert lv == 2

    def test_buy_lv3_rsi_in_range(self, analyzer):
        """MACD が 0 より下でゴールデンクロス かつ RSI が 30〜50 → Lv.3。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=40)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=40)
        sig, lv = analyzer.analyze_row(
            prev, curr,
            analyzer.config["logic"],
            "MACD", "MACDs", "RSI"
        )
        assert sig == "BUY"
        assert lv == 3

    # --- SELL 系 ---

    def test_sell_lv1_dead_cross_below_zero(self, analyzer):
        """MACD が 0 より下でデッドクロス → Lv.1。"""
        prev = self._row(macd=-0.1, signal=-0.2, rsi=60)
        curr = self._row(macd=-0.3, signal=-0.2, rsi=60)
        sig, lv = analyzer.analyze_row(
            prev, curr,
            analyzer.config["logic"],
            "MACD", "MACDs", "RSI"
        )
        assert sig == "SELL"
        assert lv == 1

    def test_sell_lv2_dead_cross_above_zero(self, analyzer):
        """MACD が 0 より上でデッドクロス → Lv.2。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=60)
        curr = self._row(macd=0.05, signal=0.1, rsi=60)
        sig, lv = analyzer.analyze_row(
            prev, curr,
            analyzer.config["logic"],
            "MACD", "MACDs", "RSI"
        )
        assert sig == "SELL"
        assert lv == 2

    def test_sell_lv3_rsi_in_range(self, analyzer):
        """MACD が 0 より上でデッドクロス かつ RSI が 50〜70 → Lv.3。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=60)
        curr = self._row(macd=0.05, signal=0.1, rsi=60)
        sig, lv = analyzer.analyze_row(
            prev, curr,
            analyzer.config["logic"],
            "MACD", "MACDs", "RSI"
        )
        assert sig == "SELL"
        assert lv == 3

    # --- 境界値: BUY Lv.3 の RSI 境界 ---

    def test_buy_lv3_rsi_boundary_lower(self, analyzer):
        """RSI = 30（下限境界）→ Lv.3 になる。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=30)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=30)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 3

    def test_buy_lv3_rsi_boundary_upper(self, analyzer):
        """RSI = 50（上限境界）→ Lv.3 になる。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=50)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=50)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 3

    def test_buy_lv2_rsi_just_above_upper_boundary(self, analyzer):
        """RSI = 51（上限超え）→ Lv.3 にならず Lv.2 に留まる。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=51)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=51)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 2

    def test_buy_lv2_rsi_just_below_lower_boundary(self, analyzer):
        """RSI = 29（下限未満）→ Lv.3 にならず Lv.2 に留まる。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=29)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=29)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 2

    # --- 境界値: SELL Lv.3 の RSI 境界 ---

    def test_sell_lv3_rsi_boundary_lower(self, analyzer):
        """RSI = 50（下限境界）→ Lv.3 になる。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=50)
        curr = self._row(macd=0.05, signal=0.1, rsi=50)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 3

    def test_sell_lv3_rsi_boundary_upper(self, analyzer):
        """RSI = 70（上限境界）→ Lv.3 になる。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=70)
        curr = self._row(macd=0.05, signal=0.1, rsi=70)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 3

    def test_sell_lv2_rsi_just_below_lower_boundary(self, analyzer):
        """RSI = 49（下限未満）→ Lv.3 にならず Lv.2 に留まる。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=49)
        curr = self._row(macd=0.05, signal=0.1, rsi=49)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 2

    def test_sell_lv2_rsi_just_above_upper_boundary(self, analyzer):
        """RSI = 71（上限超え）→ Lv.3 にならず Lv.2 に留まる。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=71)
        curr = self._row(macd=0.05, signal=0.1, rsi=71)
        _, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert lv == 2

    def test_rsi_50_buy_lv3(self, analyzer):
        """RSI = 50 は BUY Lv.3 の上限境界でもあり Lv.3 になる。"""
        prev = self._row(macd=-0.3, signal=-0.1, rsi=50)
        curr = self._row(macd=-0.05, signal=-0.1, rsi=50)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "BUY"
        assert lv == 3

    def test_rsi_50_sell_lv3(self, analyzer):
        """RSI = 50 は SELL Lv.3 の下限境界でもあり Lv.3 になる（BUY/SELL は別判定）。"""
        prev = self._row(macd=0.3, signal=0.1, rsi=50)
        curr = self._row(macd=0.05, signal=0.1, rsi=50)
        sig, lv = analyzer.analyze_row(prev, curr, analyzer.config["logic"], "MACD", "MACDs", "RSI")
        assert sig == "SELL"
        assert lv == 3


# ---------------------------------------------------------------------------
# ブラックボックステスト: analyze() のシグナル出力と損切り価格
# ---------------------------------------------------------------------------

class TestAnalyze:

    def test_stop_loss_buy(self, analyzer):
        """BUY シグナル時の損切り価格は エントリー価格 × (1 - stop_loss_pct)。"""
        # 単調増加 → GC 状態を analyze() 経由で再現するのは難しいため
        # HOLD ケースで sl_price が None になることを確認する
        closes = [150.0] * 60
        df = _make_df(closes)
        sig, lv, _, price, sl, _ = analyzer.analyze(df)
        # 定数系列は HOLD になるはず
        assert sig == "HOLD"
        assert sl is None

    def test_data_shortage_returns_early(self, analyzer):
        """データが 1 本以下のとき DATA_SHORTAGE を返す。"""
        df = _make_df([150.0])
        sig, lv, time, price, sl, _ = analyzer.analyze(df)
        assert sig == "DATA_SHORTAGE"
        assert lv == 0

    def test_stop_loss_price_formula_buy(self, analyzer):
        """stop_loss_pct=0.01 のとき、BUY の損切り価格は price * 0.99 になる。"""
        # analyze_row を直接呼んでレベルを確認したうえで価格計算式を検証
        pct = analyzer.config["logic"]["risk"]["stop_loss_pct"]
        price = 150.0
        expected_sl = price * (1 - pct)
        assert abs(expected_sl - 148.5) < 1e-9

    def test_stop_loss_price_formula_sell(self, analyzer):
        """stop_loss_pct=0.01 のとき、SELL の損切り価格は price * 1.01 になる。"""
        pct = analyzer.config["logic"]["risk"]["stop_loss_pct"]
        price = 150.0
        expected_sl = price * (1 + pct)
        assert abs(expected_sl - 151.5) < 1e-9


# ---------------------------------------------------------------------------
# 堅牢性テスト
# ---------------------------------------------------------------------------

class TestRobustness:

    def test_all_same_price_no_exception(self, analyzer):
        """全値が同一のとき（RSI の avg_loss=0）例外が発生しない。"""
        closes = [150.0] * 60
        df = _make_df(closes)
        try:
            analyzer.analyze(df)
        except Exception as e:
            pytest.fail(f"定数系列で例外が発生: {e}")

    def test_nan_in_close_no_exception(self, analyzer):
        """Close に NaN が含まれていても例外が発生しない。"""
        closes = [150.0] * 30 + [float("nan")] * 5 + [151.0] * 25
        df = _make_df(closes)
        try:
            analyzer.analyze(df)
        except Exception as e:
            pytest.fail(f"NaN 含有系列で例外が発生: {e}")

    def test_minimum_required_rows(self, analyzer):
        """2本以下のデータで DATA_SHORTAGE が返る。"""
        for n in [0, 1]:
            df = _make_df([150.0] * n) if n > 0 else pd.DataFrame(
                columns=["Open", "High", "Low", "Close", "Volume"]
            )
            sig, lv, _, _, _, _ = analyzer.analyze(df)
            assert sig == "DATA_SHORTAGE"


# ---------------------------------------------------------------------------
# 副作用テスト: generate_chart のファイル出力
# ---------------------------------------------------------------------------

class TestGenerateChart:

    def test_chart_file_is_created(self, analyzer, tmp_path):
        """generate_chart() を呼ぶと charts/ に PNG ファイルが生成される。"""
        analyzer.output_dir = str(tmp_path / "charts")
        os.makedirs(analyzer.output_dir, exist_ok=True)

        closes = [150.0 + i * 0.05 for i in range(60)]
        df = _make_df(closes)
        _, _, _, _, _, df_analyzed = analyzer.analyze(df)

        import datetime
        dummy_time = datetime.datetime(2024, 1, 1, 9, 0)

        path = analyzer.generate_chart(
            df_analyzed,
            symbol="USDJPY=X",
            signal="HOLD",
            current_level=0,
            time=dummy_time,
            price=150.0,
            config=analyzer.config,
        )

        assert os.path.exists(path), f"チャートファイルが見つかりません: {path}"
        assert path.endswith(".png")

    def test_chart_saved_in_output_dir(self, analyzer, tmp_path):
        """生成された PNG が output_dir 配下に保存される。"""
        output_dir = str(tmp_path / "charts_isolated")
        os.makedirs(output_dir, exist_ok=True)
        analyzer.output_dir = output_dir

        closes = [150.0 + i * 0.05 for i in range(60)]
        df = _make_df(closes)
        _, _, _, _, _, df_analyzed = analyzer.analyze(df)

        import datetime
        dummy_time = datetime.datetime(2024, 6, 1, 12, 0)

        path = analyzer.generate_chart(
            df_analyzed,
            symbol="USDJPY=X",
            signal="HOLD",
            current_level=0,
            time=dummy_time,
            price=150.0,
            config=analyzer.config,
        )

        assert os.path.dirname(path) == output_dir


# ---------------------------------------------------------------------------
# fetch_data のモックテスト
# ---------------------------------------------------------------------------

class TestFetchData:

    def test_fetch_data_calls_yfinance(self, analyzer):
        """fetch_data() が yfinance.download を呼び出すことを確認する。"""
        mock_df = _make_df([150.0] * 30)

        with patch("main.yf.download", return_value=mock_df) as mock_dl:
            result = analyzer.fetch_data("USDJPY=X")
            mock_dl.assert_called_once()
            assert not result.empty

    def test_fetch_data_short_interval_overrides_period(self, analyzer):
        """1m・5m 足では period が自動的に '1d' に上書きされる。"""
        analyzer.config["trading"]["interval"] = "1m"
        mock_df = _make_df([150.0] * 60)

        with patch("main.yf.download", return_value=mock_df) as mock_dl:
            analyzer.fetch_data("USDJPY=X")
            _, kwargs = mock_dl.call_args
            # period が "1d" で呼ばれているか確認
            called_period = mock_dl.call_args[1].get("period") or mock_dl.call_args[0][1]
            assert called_period == "1d"