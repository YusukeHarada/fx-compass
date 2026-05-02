import os

import matplotlib
# GitHub Actions等のGUIがない環境でも動作するようにバックエンドをAggに設定
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import yaml
import yfinance as yf


class FXAnalyzerPro:

    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)
        self.output_dir = "charts"
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Data Acquisition 層
    # ------------------------------------------------------------------

    def fetch_data(self, symbol):
        """yfinance から OHLC データを取得する。短期足は period を自動調整する。"""
        c = self.config['trading']
        fetch_period = "1d" if c['interval'] in ["1m", "5m"] else c['period']
        df = yf.download(symbol, period=fetch_period, interval=c['interval'], progress=False)
        # yfinance が MultiIndex を返す場合にフラット化する
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    # ------------------------------------------------------------------
    # Logic Engine 層
    # ------------------------------------------------------------------

    def _calc_indicators(self, df):
        """EMA・MACD・RSI を計算して新しい DataFrame を返す（元の df を変更しない）。"""
        result = df.copy()
        l_cfg = self.config['logic']

        ema_fast = result['Close'].ewm(span=l_cfg['macd']['fast'], adjust=False).mean()
        ema_slow = result['Close'].ewm(span=l_cfg['macd']['slow'], adjust=False).mean()
        result['MACD'] = ema_fast - ema_slow
        result['MACDs'] = result['MACD'].ewm(span=l_cfg['macd']['signal'], adjust=False).mean()

        diff = result['Close'].diff()
        gain = diff.clip(lower=0)
        loss = -diff.clip(upper=0)
        avg_gain = gain.rolling(window=l_cfg['rsi']['length']).mean()
        avg_loss = loss.rolling(window=l_cfg['rsi']['length']).mean()
        # avg_loss=0（単調増加）→ RSI=100、avg_gain=0（単調減少）→ RSI=0
        import numpy as np
        rsi_values = np.where(
            avg_loss == 0,
            100.0,
            np.where(avg_gain == 0, 0.0, 100 - (100 / (1 + avg_gain / avg_loss)))
        )
        result['RSI'] = rsi_values

        return result

    def analyze_row(self, prev, curr, l_cfg, m_col, s_col, r_col):
        """
        2本のローソク足からシグナル種別とレベルを返す。

        Returns
        -------
        sig_type : str   "BUY" / "SELL" / "HOLD"
        level    : int   0 (HOLD) / 1 (WATCH) / 2 (STANDARD) / 3 (STRONG)
        """
        is_gold = (prev[m_col] < prev[s_col]) and (curr[m_col] > curr[s_col])
        is_dead = (prev[m_col] > prev[s_col]) and (curr[m_col] < curr[s_col])

        if is_gold:
            level = 1
            if curr[m_col] < 0:
                level = 3 if 30 <= curr[r_col] <= 50 else 2
            return "BUY", level

        if is_dead:
            level = 1
            if curr[m_col] > 0:
                level = 3 if 50 <= curr[r_col] <= 70 else 2
            return "SELL", level

        return "HOLD", 0

    def _build_signal_message(self, sig_type, level):
        """シグナル種別とレベルから表示用メッセージを組み立てる。"""
        label = {1: "WATCH (Lv.1)", 2: "STANDARD (Lv.2)", 3: "STRONG (Lv.3)"}
        if level == 0:
            return "HOLD"
        return f"{sig_type} {label[level]}"

    def analyze(self, df):
        """
        指標計算 → シグナル判定 → 損切り価格算出を行う。

        Returns
        -------
        signal_msg : str
        level      : int
        time       : Timestamp | None
        price      : float | None
        sl_price   : float | None
        df_out     : DataFrame  指標列付きの DataFrame
        """
        if len(df) < 2:
            return "DATA_SHORTAGE", 0, None, None, None, df

        df_out = self._calc_indicators(df)
        sig_type, level = self.analyze_row(
            df_out.iloc[-2], df_out.iloc[-1],
            self.config['logic'], 'MACD', 'MACDs', 'RSI'
        )

        signal_msg = self._build_signal_message(sig_type, level)

        sl_price = None
        if level > 0:
            pct = self.config['risk']['stop_loss_pct']
            multiplier = (1 - pct) if sig_type == "BUY" else (1 + pct)
            sl_price = df_out.iloc[-1]['Close'] * multiplier

        return signal_msg, level, df_out.index[-1], df_out.iloc[-1]['Close'], sl_price, df_out

    # ------------------------------------------------------------------
    # Presentation 層
    # ------------------------------------------------------------------

    def generate_chart(self, df, symbol, signal, current_level, time, price, config):
        """分析済み DataFrame からシグナルプロット付きチャートを PNG で保存する。"""
        l_cfg = config['logic']
        plot_df = df.tail(100).copy()
        dates = plot_df.index

        fig, (ax1, ax2, ax3) = plt.subplots(
            3, 1, figsize=(14, 10), sharex=True,
            gridspec_kw={'height_ratios': [2, 1, 1]}
        )
        fig.patch.set_facecolor('#fdfaf4')

        # 価格ライン
        ax1.plot(dates, plot_df['Close'], color='#2c3e50', linewidth=1.5, label='Price', alpha=0.8)
        ax1.set_title(
            f"FX-Compass Pro: {symbol} ({config['trading']['interval']})",
            fontsize=16, fontweight='bold'
        )

        # シグナルマーカー（過去 100 本分を再計算してプロット）
        color_map = {
            1: {'BUY': '#90ee90', 'SELL': '#ffcccb', 's': 80},
            2: {'BUY': '#2ecc71', 'SELL': '#e74c3c', 's': 200},
            3: {'BUY': '#f1c40f', 'SELL': '#9b59b6', 's': 450},
        }
        for i in range(1, len(plot_df)):
            sig_type, lv = self.analyze_row(
                plot_df.iloc[i - 1], plot_df.iloc[i],
                l_cfg, 'MACD', 'MACDs', 'RSI'
            )
            if lv > 0:
                marker = '^' if sig_type == "BUY" else 'v'
                ax1.scatter(
                    plot_df.index[i], plot_df.iloc[i]['Close'],
                    color=color_map[lv][sig_type], marker=marker,
                    s=color_map[lv]['s'], edgecolor='black', linewidth=0.5, zorder=5
                )

        ax1.legend(loc='upper left')
        ax1.grid(alpha=0.3)

        # MACD
        ax2.plot(dates, plot_df['MACD'],  color='#3498db', label='MACD')
        ax2.plot(dates, plot_df['MACDs'], color='#e67e22', label='Signal')
        ax2.axhline(0, color='black', linewidth=1)
        ax2.legend(loc='upper left')
        ax2.grid(alpha=0.3)

        # RSI
        ax3.plot(dates, plot_df['RSI'], color='#9b59b6', label='RSI')
        ax3.axhline(l_cfg['rsi']['sell_threshold'], color='#e74c3c', linestyle='--')
        ax3.axhline(l_cfg['rsi']['buy_threshold'],  color='#2ecc71', linestyle='--')
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


if __name__ == "__main__":
    analyzer = FXAnalyzerPro()
    target_symbols = analyzer.config['trading'].get('symbols', ["USDJPY=X"])

    print("\n--- FX-Compass Pro Multi-Scanner ---")
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