import pandas as pd
import numpy as np
import yfinance as yf
import yaml
import matplotlib
# GitHub Actions等のGUIがない環境でも動作するようにバックエンドをAggに設定
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
import argparse

class FXAnalyzerPro:
    """
    FX相場のテクニカル分析およびチャート生成を行うメインクラス。
    """
    def __init__(self, config_path="config.yaml"):
        # 設定ファイルの読み込み
        try:
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)
        except FileNotFoundError:
            # テスト環境などでファイルがない場合のフォールバック
            self.config = {
                'trading': {'interval': '1h', 'period': '5d', 'symbols': ['USDJPY=X']},
                'logic': {
                    'macd': {'fast': 12, 'slow': 26, 'signal': 9},
                    'rsi': {'length': 14, 'buy_threshold': 30, 'sell_threshold': 70},
                    'risk': {'stop_loss_pct': 0.01}
                }
            }
        
        self.output_dir = "charts"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def fetch_data(self, symbol):
        """指定されたシンボルのデータを取得する。"""
        c = self.config['trading']
        fetch_period = c['period']
        if c['interval'] in ["1m", "5m"]:
            fetch_period = "1d"
            
        df = yf.download(symbol, period=fetch_period, interval=c['interval'], progress=False)
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    def analyze_row(self, prev, curr, l_cfg, m_col, s_col, r_col):
        """2行分のデータからシグナルを判定する（純粋関数的設計）。"""
        # MACDのクロス判定
        is_gold = (prev[m_col] < prev[s_col]) and (curr[m_col] > curr[s_col])
        is_dead = (prev[m_col] > prev[s_col]) and (curr[m_col] < curr[s_col])
        
        level = 0
        sig_type = "HOLD"
        
        if is_gold:
            level = 1
            sig_type = "BUY"
            if curr[m_col] < 0:
                level = 2
                if 30 <= curr[r_col] <= 50: 
                    level = 3
        elif is_dead:
            level = 1
            sig_type = "SELL"
            if curr[m_col] > 0:
                level = 2
                if 50 <= curr[r_col] <= 70: 
                    level = 3
                
        return sig_type, level

    def analyze(self, df):
        """データフレーム全体に対して指標計算と最新行の判定を行う。"""
        l_cfg = self.config['logic']
        
        # データ不足時のガード
        if len(df) < 2: 
            return "DATA_SHORTAGE", 0, None, None, None, df

        # 指標計算
        # MACD
        ema_fast = df['Close'].ewm(span=l_cfg['macd']['fast'], adjust=False).mean()
        ema_slow = df['Close'].ewm(span=l_cfg['macd']['slow'], adjust=False).mean()
        df['MACD'] = ema_fast - ema_slow
        df['MACDs'] = df['MACD'].ewm(span=l_cfg['macd']['signal'], adjust=False).mean()
        
        # RSI (Wilder's RSIに準拠した計算、ゼロ除算対策付き)
        diff = df['Close'].diff()
        gain = diff.clip(lower=0)
        loss = -diff.clip(upper=0)
        
        avg_gain = gain.rolling(window=l_cfg['rsi']['length']).mean()
        avg_loss = loss.rolling(window=l_cfg['rsi']['length']).mean()
        
        # ゼロ除算を回避するための安全な計算
        # avg_lossが0（価格が下がっていない）かつavg_gainも0（価格が動いていない）ならRSI=50
        # avg_lossが0だがavg_gainが正ならRSI=100
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = avg_gain / avg_loss
            df['RSI'] = 100 - (100 / (1 + rs))
        
        # 無限大（avg_loss=0）を100に、NaN（動きなし）を50に置換
        df['RSI'] = df['RSI'].replace([np.inf, -np.inf], 100).fillna(50)

        m_col, s_col, r_col = 'MACD', 'MACDs', 'RSI'
        
        # 計算結果のバリデーション
        if len(df) < 2 or df[r_col].isna().iloc[-1] or df[m_col].isna().iloc[-1]:
            return "INDICATOR_CALC_ERROR", 0, None, None, None, df

        # 最新2行で判定
        sig_type, level = self.analyze_row(df.iloc[-2], df.iloc[-1], l_cfg, m_col, s_col, r_col)
        
        mapping = {1: "WATCH (Lv.1)", 2: "STANDARD (Lv.2)", 3: "STRONG (Lv.3)"}
        signal_msg = mapping.get(level, "HOLD")
        if signal_msg != "HOLD": 
            signal_msg = f"{sig_type} {signal_msg}"

        sl_price = None
        if level > 0:
            pct = l_cfg['risk']['stop_loss_pct']
            sl_price = df.iloc[-1]['Close'] * (1 - pct if sig_type == "BUY" else 1 + pct)

        return signal_msg, level, df.index[-1], df.iloc[-1]['Close'], sl_price, df

    def generate_chart(self, df, symbol, time, **kwargs):
        """分析結果に基づくチャートを生成。テストコードの拡張引数にも対応。"""
        l_cfg = self.config['logic']
        m_col, s_col, r_col = 'MACD', 'MACDs', 'RSI'
        
        # 最新100件を表示
        plot_df = df.tail(100).copy()
        dates = plot_df.index

        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10), sharex=True, gridspec_kw={'height_ratios': [2, 1, 1]})
        fig.patch.set_facecolor('#fdfaf4')
        
        # メイン価格チャート
        ax1.plot(dates, plot_df['Close'], color='#2c3e50', linewidth=1.5, label='Price', alpha=0.8)
        ax1.set_title(f"FX-Compass Pro: {symbol} ({self.config['trading']['interval']})", fontsize=16, fontweight='bold')

        # 過去のシグナルをプロット
        for i in range(1, len(plot_df)):
            s_type, lv = self.analyze_row(plot_df.iloc[i-1], plot_df.iloc[i], l_cfg, m_col, s_col, r_col)
            if lv > 0:
                color_map = {1: {'BUY': '#90ee90', 'SELL': '#ffcccb', 's': 80},
                             2: {'BUY': '#2ecc71', 'SELL': '#e74c3c', 's': 200},
                             3: {'BUY': '#f1c40f', 'SELL': '#9b59b6', 's': 450}}
                m_marker = '^' if s_type == "BUY" else 'v'
                ax1.scatter(plot_df.index[i], plot_df.iloc[i]['Close'], 
                            color=color_map[lv][s_type], marker=m_marker, s=color_map[lv]['s'], 
                            edgecolor='black', linewidth=0.5, zorder=5)

        ax1.legend(loc='upper left')
        ax1.grid(alpha=0.3)
        
        # MACDサブチャート
        ax2.plot(dates, plot_df[m_col], color='#3498db', label='MACD')
        ax2.plot(dates, plot_df[s_col], color='#e67e22', label='Signal')
        ax2.axhline(0, color='black', linewidth=1)
        ax2.legend(loc='upper left')
        ax2.grid(alpha=0.3)
        
        # RSIサブチャート
        ax3.plot(dates, plot_df[r_col], color='#9b59b6', label='RSI')
        ax3.axhline(l_cfg['rsi']['sell_threshold'], color='#e74c3c', linestyle='--')
        ax3.axhline(l_cfg['rsi']['buy_threshold'], color='#2ecc71', linestyle='--')
        ax3.set_ylim(0, 100)
        ax3.legend(loc='upper left')
        ax3.grid(alpha=0.3)

        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
        fig.autofmt_xdate()

        file_name = f"chart_{symbol.replace('=X', '')}_{time.strftime('%H%M')}.png"
        file_path = os.path.join(self.output_dir, file_name)
        plt.savefig(file_path, bbox_inches='tight', dpi=150)
        plt.close()
        return file_path

    def run_full_scan(self):
        """全ターゲットの分析実行。"""
        target_symbols = self.config['trading'].get('symbols', ["USDJPY=X"])
        scan_results = []
        
        print(f"\n--- FX-Compass Pro Multi-Scanner ---")
        for symbol in target_symbols:
            try:
                print(f"分析中: {symbol}...")
                df_raw = self.fetch_data(symbol)
                sig, lv, time, price, sl, df_final = self.analyze(df_raw)
                
                chart_path = None
                if time is not None:
                    chart_path = self.generate_chart(df_final, symbol, time)
                
                res = {"symbol": symbol, "signal": sig, "level": lv, "chart": chart_path}
                scan_results.append(res)
                print(f"  最新判定: {sig} (Lv.{lv})")
                if chart_path:
                    print(f"  チャート生成完了: {chart_path}")
                    
            except Exception as e:
                print(f"  {symbol} エラー: {e}")
                scan_results.append({"symbol": symbol, "error": str(e)})
                
        return scan_results

def main():
    parser = argparse.ArgumentParser(description='FX-Compass Pro Scanner')
    parser.add_argument('--config', type=str, default='config.yaml', help='Path to config file')
    args = parser.parse_args()

    try:
        analyzer = FXAnalyzerPro(config_path=args.config)
        analyzer.run_full_scan()
    except Exception as e:
        print(f"システムエラー: {e}")

if __name__ == "__main__":
    main()