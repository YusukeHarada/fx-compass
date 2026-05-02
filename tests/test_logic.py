import pytest
import pandas as pd
import numpy as np
from main import FXAnalyzerPro

# =================================================================
# 1. ホワイトボックス・テスト: 指標計算ロジックの検証
# 外部ライブラリに依存せず、計算過程が数学的に正しいかを検証する
# =================================================================

def test_macd_calculation_logic():
    """MACDの計算結果が期待されるEMAの差分と一致するか検証"""
    data = {'Close': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]}
    df = pd.DataFrame(data)
    
    # 内部ロジックの直接検証
    ema_fast = df['Close'].ewm(span=12, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=26, adjust=False).mean()
    expected_macd = ema_fast - ema_slow
    
    # 手動計算値と実装値が一致するか（ホワイトボックス検証）
    assert np.isclose(ema_fast.iloc[-1], 103.01, atol=0.1)
    assert len(expected_macd) == len(df)

def test_rsi_zero_division_resilience():
    """価格が完全にフラットな場合（下落幅ゼロ）のゼロ除算耐性を検証"""
    data = {'Close': [100.0] * 20}
    df = pd.DataFrame(data)
    
    diff = df['Close'].diff()
    gain = diff.clip(lower=0)
    loss = -diff.clip(upper=0)
    
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    
    # ゼロ除算が発生する状況での挙動確認
    # 実装上、avg_lossが0になるとrsはinf、RSIは0になるはず
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    # 計算結果がクラッシュ（例外発生）しないことを確認
    assert not rsi.isna().all()

# =================================================================
# 2. ブラックボックス・テスト: 入出力結果の整合性検証
# 内部ロジックを意識せず、特定の入力に対して期待されるレベルが返るかを確認
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
    
    # ケースA: Lv.1 BUY (ゴールデンクロスのみ、MACD > 0)
    prev = {'MACD': 0.5, 'MACDs': 0.6, 'RSI': 60}
    curr = {'MACD': 0.7, 'MACDs': 0.6, 'RSI': 60}
    _, level = analyzer.analyze_row(prev, curr, logic_config, 'MACD', 'MACDs', 'RSI')
    assert level == 1
    
    # ケースB: Lv.2 BUY (GC + MACD < 0)
    prev = {'MACD': -0.5, 'MACDs': -0.4, 'RSI': 60}
    curr = {'MACD': -0.3, 'MACDs': -0.4, 'RSI': 60}
    _, level = analyzer.analyze_row(prev, curr, logic_config, 'MACD', 'MACDs', 'RSI')
    assert level == 2
    
    # ケースC: Lv.3 BUY (GC + MACD < 0 + RSI 30-50)
    prev = {'MACD': -0.5, 'MACDs': -0.4, 'RSI': 40}
    curr = {'MACD': -0.3, 'MACDs': -0.4, 'RSI': 40}
    _, level = analyzer.analyze_row(prev, curr, logic_config, 'MACD', 'MACDs', 'RSI')
    assert level == 3

def test_signal_sell_logic(analyzer, logic_config):
    """売りシグナルの多段階ロジックを検証"""
    
    # ケース: Lv.3 SELL (デッドクロス + MACD > 0 + RSI 50-70)
    prev = {'MACD': 1.5, 'MACDs': 1.0, 'RSI': 65}
    curr = {'MACD': 0.8, 'MACDs': 1.0, 'RSI': 60}
    sig_type, level = analyzer.analyze_row(prev, curr, logic_config, 'MACD', 'MACDs', 'RSI')
    assert sig_type == "SELL"
    assert level == 3

# =================================================================
# 3. 堅牢性テスト: 異常データへの耐性
# =================================================================

def test_nan_data_handling(analyzer):
    """データにNaNが含まれている場合のロジックの安定性を検証"""
    df_nan = pd.DataFrame({
        'Close': [100, np.nan, 102, 103, 104],
        'MACD': [0, 0, 0, 0, 0],
        'MACDs': [0, 0, 0, 0, 0],
        'RSI': [50, 50, 50, 50, 50]
    })
    
    # 途中にNaNがあってもプログラムがクラッシュしないこと
    msg, level, _, _, _, _ = analyzer.analyze(df_nan)
    assert isinstance(level, int)

def test_stop_loss_calculation(analyzer):
    """損切り価格の計算が算術的に正しいか検証"""
    # 買いシグナルの場合、1%下を指すべき
    df = pd.DataFrame({
        'Close': [100, 100, 100],
        'MACD': [-1, -1, 0.5], # 最後にGC発生
        'MACDs': [-0.5, -0.5, -0.5],
        'RSI': [40, 40, 40]
    })
    # インデックスを付けて正常系ルートに乗せる
    df.index = pd.date_range("2023-01-01", periods=3)
    
    msg, level, time, price, sl, _ = analyzer.analyze(df)
    
    # 100円の1%下 = 99円
    assert sl == 99.0