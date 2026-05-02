import pandas as pd
import pandas_ta as ta
import yfinance as yf
import yaml
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os

class FXAnalyzerPro:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)
        self.output_dir = "charts"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def fetch_data(self, symbol):
        c = self.config['trading']
        fetch_period = c['period']
        if c['interval'] in ["1m", "5m"]:
            fetch_period = "1d"
            
        df = yf.download(symbol, period=fetch_period, interval=c['interval'], progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    def analyze_row(self, prev, curr, l_cfg, m_col, s_col, r_col):
        is_gold = (prev[m_col] < prev[s_col]) and (curr[m_col] > curr[s_col])
        is_dead = (prev[m_col] > prev[s_col]) and (curr[m_col] < curr[s_col])
        
        level = 0
        sig_type = "HOLD"
        
        if is_gold:
            level = 1
            sig_type = "BUY"
            if curr[m_col] < 0:
                level = 2
                if 30 <= curr[r_col] <= 50: level = 3
        elif is_dead:
            level = 1
            sig_type = "SELL"
            if curr[m_col] > 0:
                level = 2
                if 50 <= curr[r_col] <= 70: level = 3
                
        return sig_type, level

    def analyze(self, df):
        l_cfg = self.config['logic']
        macd = df.ta.macd(fast=l_cfg['macd']['fast'], slow=l_cfg['macd']['slow'], signal=l_cfg['macd']['signal'])
        rsi = df.ta.rsi(length=l_cfg['rsi']['length'])
        df = pd.concat([df, macd, rsi], axis=1)

        m_col = f"MACD_{l_cfg['macd']['fast']}_{l_cfg['macd']['slow']}_{l_cfg['macd']['signal']}"
        s_col = f"MACDs_{l_cfg['macd']['fast']}_{l_cfg['macd']['slow']}_{l_cfg['macd']['signal']}"
        r_col = f"RSI_{l_cfg['rsi']['length']}"

        if len(df) < 2: return "DATA_SHORTAGE", 0, None, None, None, df

        sig_type, level = self.analyze_row(df.iloc[-2], df.iloc[-1], l_cfg, m_col, s_col, r_col)
        
        mapping = {1: "WATCH (Lv.1)", 2: "STANDARD (Lv.2)", 3: "STRONG (Lv.3)"}
        signal_msg = mapping.get(level, "HOLD")
        if signal_msg != "HOLD": signal_msg = f"{sig_type} {signal_msg}"

        sl_price = None
        if level > 0:
            pct = l_cfg['risk']['stop_loss_pct']
            sl_price = df.iloc[-1]['Close'] * (1 - pct if sig_type == "BUY" else 1 + pct)

        return signal_msg, level, df.index[-1], df.iloc[-1]['Close'], sl_price, df

    def generate_chart(self, df, symbol, signal, current_level, time, price, config):
        l_cfg = config['logic']
        m_col = f"MACD_{l_cfg['macd']['fast']}_{l_cfg['macd']['slow']}_{l_cfg['macd']['signal']}"
        s_col = f"MACDs_{l_cfg['macd']['fast']}_{l_cfg['macd']['slow']}_{l_cfg['macd']['signal']}"
        r_col = f"RSI_{l_cfg['rsi']['length']}"
        
        plot_df = df.tail(100).copy()
        dates = plot_df.index

        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10), sharex=True, gridspec_kw={'height_ratios': [2, 1, 1]})
        fig.patch.set_facecolor('#fdfaf4')
        
        ax1.plot(dates, plot_df['Close'], color='#2c3e50', linewidth=1.5, label='Price', alpha=0.8)
        ax1.set_title(f"FX-Compass Pro: {symbol} ({config['trading']['interval']})", fontsize=16, fontweight='bold')

        for i in range(1, len(plot_df)):
            sig_type, lv = self.analyze_row(plot_df.iloc[i-1], plot_df.iloc[i], l_cfg, m_col, s_col, r_col)
            if lv > 0:
                color_map = {1: {'BUY': '#90ee90', 'SELL': '#ffcccb', 's': 80},
                             2: {'BUY': '#2ecc71', 'SELL': '#e74c3c', 's': 200},
                             3: {'BUY': '#f1c40f', 'SELL': '#9b59b6', 's': 450}}
                m_type = '^' if sig_type == "BUY" else 'v'
                ax1.scatter(plot_df.index[i], plot_df.iloc[i]['Close'], 
                            color=color_map[lv][sig_type], marker=m_type, s=color_map[lv]['s'], 
                            edgecolor='black', linewidth=0.5, zorder=5)

        ax1.legend(loc='upper left'); ax1.grid(alpha=0.3)
        ax2.plot(dates, plot_df[m_col], color='#3498db', label='MACD')
        ax2.plot(dates, plot_df[s_col], color='#e67e22', label='Signal')
        ax2.axhline(0, color='black', linewidth=1); ax2.legend(loc='upper left'); ax2.grid(alpha=0.3)
        ax3.plot(dates, plot_df[r_col], color='#9b59b6', label='RSI')
        ax3.axhline(l_cfg['rsi']['sell_threshold'], color='#e74c3c', linestyle='--')
        ax3.axhline(l_cfg['rsi']['buy_threshold'], color='#2ecc71', linestyle='--')
        ax3.set_ylim(0, 100); ax3.legend(loc='upper left'); ax3.grid(alpha=0.3)

        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        fig.autofmt_xdate()

        file_name = f"chart_{symbol.replace('=X', '')}_{time.strftime('%H%M')}.png"
        file_path = os.path.join(self.output_dir, file_name)
        plt.savefig(file_path, bbox_inches='tight', dpi=150)
        plt.close()
        return file_path

if __name__ == "__main__":
    analyzer = FXAnalyzerPro()
    # configからsymbolsリストを取得
    target_symbols = analyzer.config['trading'].get('symbols', ["USDJPY=X"])
    
    print(f"\n--- FX-Compass Pro Multi-Scanner ---")
    for symbol in target_symbols:
        try:
            print(f"分析中: {symbol}...")
            df_raw = analyzer.fetch_data(symbol)
            sig, lv, time, price, sl, df_final = analyzer.analyze(df_raw)
            print(f"  最新判定: {sig} (Lv.{lv})")
            if time:
                path = analyzer.generate_chart(df_final, symbol, sig, lv, time, price, analyzer.config)
                print(f"  チャート生成完了: {path}")
        except Exception as e:
            print(f"  {symbol} エラー: {e}")