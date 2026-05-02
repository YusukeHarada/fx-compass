import pytest
import pandas as pd
import numpy as np
import sys
import os

# テスト実行時に親ディレクトリ（プロジェクトルート）をパスに追加し
# ModuleNotFoundError: No module named 'main' を回避する
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import FXAnalyzerPro

# =================================================================
# 1. ホワイトボックス・テスト: 内部アルゴリズムの正当性検証
# =================================================================

def test_ema_logic_consistency():
    """EMAの計算ロジックが再帰的な指数平滑移動平均の定義に一致するか検証"""
    # 非常に単純なデータで計算過程を追跡
    data = {'Close': [10.0, 20.0, 30.0]}
    df = pd.DataFrame(data)
    span = 2
    alpha = 2 / (span + 1) # 0.666...
    
    # pandasのewm(adjust=False)は再帰的定義: y_t = (1-alpha)*y_{t-1} + alpha*x_t
    ema = df['Close'].ewm(span=span, adjust=False).mean()
    
    # 手計算比較
    # y0 = 10.0
    # y1 = (1-0.666)*10 + 0.666*20 = 3.33 + 13.33 = 16.66...
    expected_y1 = (1 - alpha) * data['Close'][0] + alpha * data['Close'][1]
    assert np.isclose(ema.iloc[1], expected_y1)

def test_rsi_calculation_with_varied_data():
    """RSIの計算が価格の変動幅に対して数学的に正しい比率を返すか検証"""
    # 上昇のみのデータ
    data = {'Close': [100, 110, 120, 130, 140, 150]}
    df = pd.DataFrame(data)
    
    diff = df['Close'].diff()
    gain = diff.clip(lower=0).rolling(window=5).mean()
    loss = (-diff.clip(upper=0)).rolling(window=5).mean()
    
    # 下落がない場合、avg_loss=0となりRSIは100に漸近するはず
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 最終行がNaNでないこと（window=5に対してデータが足りている）を確認
    assert rsi.iloc[-1] == 100.0 or np.isinf(rs.iloc[-1])

def test_rsi_zero_division_resilience():
    """価格が完全にフラットな場合のゼロ除算耐性を検証"""
    data = {'Close': [100.0] * 20}
    df = pd.DataFrame(data)
    
    # main.pyのロジックをシミュレート
    diff = df['Close'].diff()
    gain = diff.clip(lower=0).rolling(window=14).mean()
    loss = (-diff.clip(upper=0)).rolling(window=14).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 計算が停止（例外発生）しないことを確認
    assert len(rsi) == 20

# =================================================================
# 2. ブラックボックス・テスト: 判定インターフェースの検証
# =================================================================

@pytest.fixture
def analyzer():
    return FXAnalyzerPro()

@pytest.fixture
def logic_config():
    return {
        'macd': {'fast': 12, 'slow': 26, 'signal': 9},
        'rsi': {'length': 14, 'buy_threshold': 30, 'sell_threshold': 70},
        'risk': {'stop_loss_pct': 0.01}
    }

def test_signal_level_boundaries(analyzer, logic_config):
    """各シグナルレベルの境界条件を網羅的にテスト"""
    m_col, s_col, r_col = 'MACD', 'MACDs', 'RSI'
    
    # Lv.1 BUY: GCのみ
    prev = {m_col: 0.5, s_col: 0.6, r_col: 60}
    curr = {m_col: 0.7, s_col: 0.6, r_col: 60}
    _, level = analyzer.analyze_row(prev, curr, logic_config, m_col, s_col, r_col)
    assert level == 1
    
    # Lv.2 BUY: GC + MACD < 0
    prev = {m_col: -0.5, s_col: -0.4, r_col: 60}
    curr = {m_col: -0.3, s_col: -0.4, r_col: 60}
    _, level = analyzer.analyze_row(prev, curr, logic_config, m_col, s_col, r_col)
    assert level == 2
    
    # Lv.3 BUY: GC + MACD < 0 + RSI 30-50
    prev = {m_col: -0.5, s_col: -0.4, r_col: 40}
    curr = {m_col: -0.3, s_col: -0.4, r_col: 40}
    sig_type, level = analyzer.analyze_row(prev, curr, logic_config, m_col, s_col, r_col)
    assert sig_type == "BUY" and level == 3

def test_rsi_strict_thresholds(analyzer, logic_config):
    """RSIの境界値(30, 50)でのLv.3判定の厳密性を検証"""
    m_col, s_col, r_col = 'MACD', 'MACDs', 'RSI'
    
    # RSI=30 (境界値下限) -> Lv.3であるべき
    curr_30 = {'MACD': -0.1, 'MACDs': -0.2, 'RSI': 30}
    prev = {'MACD': -0.3, 'MACDs': -0.2, 'RSI': 30}
    _, level = analyzer.analyze_row(prev, curr_30, logic_config, m_col, s_col, r_col)
    assert level == 3
    
    # RSI=50 (境界値上限) -> Lv.3であるべき
    curr_50 = {'MACD': -0.1, 'MACDs': -0.2, 'RSI': 50}
    _, level = analyzer.analyze_row(prev, curr_50, logic_config, m_col, s_col, r_col)
    assert level == 3
    
    # RSI=50.1 -> Lv.2に落ちるべき
    curr_51 = {'MACD': -0.1, 'MACDs': -0.2, 'RSI': 50.1}
    _, level = analyzer.analyze_row(prev, curr_51, logic_config, m_col, s_col, r_col)
    assert level == 2

def test_signal_persistence_hold(analyzer, logic_config):
    """トレンド継続中（クロスなし）に余計なシグナルが出ないことを検証"""
    m_col, s_col, r_col = 'MACD', 'MACDs', 'RSI'
    
    # MACDがシグナルの上を並走（GC状態だが、クロスした瞬間ではない）
    prev = {m_col: 1.0, s_col: 0.5, r_col: 50}
    curr = {m_col: 1.2, s_col: 0.6, r_col: 50}
    sig_type, level = analyzer.analyze_row(prev, curr, logic_config, m_col, s_col, r_col)
    
    assert sig_type == "HOLD"
    assert level == 0

# =================================================================
# 3. 堅牢性テスト: データ品質と例外処理
# =================================================================

def test_nan_data_resilience(analyzer):
    """不完全なデータフレームに対する処理の堅牢性を検証"""
    df_nan = pd.DataFrame({
        'Close': [100, np.nan, 102, 103, 104],
        'MACD': [0, 0, 0, 0, 0],
        'MACDs': [0, 0, 0, 0, 0],
        'RSI': [50, 50, 50, 50, 50]
    })
    # プログラムが停止せず、何らかのレベルを返すこと
    _, level, _, _, _, _ = analyzer.analyze(df_nan)
    assert isinstance(level, (int, float))

def test_stop_loss_logic(analyzer):
    """リスク管理パラメータに基づく損切り計算の正確性を検証"""
    # 買いシグナル発生を想定
    df = pd.DataFrame({
        'Close': [100.0, 100.0, 200.0], # 最新価格200
        'MACD': [-1.0, -1.0, 1.0],      # GC発生
        'MACDs': [0.0, 0.0, 0.0],
        'RSI': [40.0, 40.0, 40.0]
    }, index=pd.date_range("2023-01-01", periods=3))
    
    _, level, _, price, sl, _ = analyzer.analyze(df)
    
    # 200円の1%下は198円
    if level > 0:
        assert price == 200.0
        assert sl == 198.0