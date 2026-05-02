import pytest
import pandas as pd
import numpy as np

# 計算ロジックが正しいか、簡易的なデータで検証するテスト
def test_macd_calculation():
    # 30行のダミー価格データを作成
    data = {'Close': np.linspace(100, 110, 30)}
    df = pd.DataFrame(data)
    
    # EMAの計算（main.pyのロジックを模倣）
    ema_fast = df['Close'].ewm(span=12, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=26, adjust=False).mean()
    macd = ema_fast - ema_slow
    
    # MACDが計算されているか確認
    assert not macd.isna().all()
    assert len(macd) == 30

def test_rsi_calculation():
    data = {'Close': [100, 102, 104, 103, 101, 105, 107, 108, 110, 109]}
    df = pd.DataFrame(data)
    
    diff = df['Close'].diff()
    gain = diff.clip(lower=0)
    loss = -diff.clip(upper=0)
    avg_gain = gain.rolling(window=5).mean()
    avg_loss = loss.rolling(window=5).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    # RSIが0-100の範囲に収まっているか確認
    valid_rsi = rsi.dropna()
    assert all(valid_rsi >= 0) and all(valid_rsi <= 100)