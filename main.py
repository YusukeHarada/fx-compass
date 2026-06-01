import argparse
import json
import os
import sys
import time
import urllib.request

import matplotlib
# GitHub Actions等のGUIがない環境でも動作するようにバックエンドをAggに設定
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import yaml
import yfinance as yf
from rich.console import Console
from rich.table import Table


# ------------------------------------------------------------------
# CLI Entry Point
# ------------------------------------------------------------------

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="FX-Compass Pro signal scanner")
    parser.add_argument(
        "--symbols", nargs="+", metavar="SYM",
        help="スキャン対象シンボル（例: USDJPY=X EURJPY=X）"
    )
    parser.add_argument(
        "--interval", choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="時間足（config.yaml を上書き）"
    )
    parser.add_argument(
        "--min-level", type=int, choices=[1, 2, 3, 4], default=1,
        metavar="LEVEL",
        help="このレベル以上のシグナルのみ表示（1=WATCH, 2=STANDARD, 3=STRONG, 4=CONFIRMED）"
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="定期的にスキャンを繰り返す（間隔は config.yaml の watch_interval_seconds）"
    )
    return parser.parse_args(argv)


def _print_results_table(results: list, title: str = "FX-Compass Pro — Signal Summary") -> None:
    """スキャン結果を rich テーブルで表示する。"""
    console = Console()
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Symbol",  style="cyan",  width=12)
    table.add_column("Signal",  width=24)
    table.add_column("Lv.",     justify="center", width=4)
    table.add_column("Price",   justify="right",  width=10)
    table.add_column("S/L",     justify="right",  width=10)
    table.add_column("Time",    width=15)

    level_styles = {0: "dim", 1: "white", 2: "yellow", 3: "bold green", 4: "bold magenta"}

    for r in results:
        lv = r["level"]
        style = level_styles.get(lv, "white")
        sl_str    = f"{r['sl_price']:.3f}" if r["sl_price"] is not None else "—"
        price_str = f"{r['price']:.3f}"    if r["price"]    is not None else "—"
        time_str  = r["time"].strftime("%m/%d %H:%M") if r["time"] is not None else "—"
        lv_str    = str(lv) if lv > 0 else "—"
        table.add_row(r["symbol"], r["signal"], lv_str, price_str, sl_str, time_str, style=style)

    console.print(table)


def _notify_discord(message: str, webhook_url: str, chart_paths: list | None = None) -> None:
    """Discord Webhook でメッセージを送信する。chart_paths があれば画像も添付する。"""
    try:
        if chart_paths:
            boundary = "FXCompassBoundary"
            crlf = b"\r\n"
            body = b""
            body += f"--{boundary}".encode() + crlf
            body += b'Content-Disposition: form-data; name="payload_json"' + crlf
            body += b'Content-Type: application/json' + crlf
            body += crlf
            body += json.dumps({"content": message}).encode() + crlf
            for i, path in enumerate(chart_paths):
                fname = os.path.basename(path)
                with open(path, "rb") as f:
                    file_data = f.read()
                body += f"--{boundary}".encode() + crlf
                body += f'Content-Disposition: form-data; name="files[{i}]"; filename="{fname}"'.encode() + crlf
                body += b'Content-Type: image/png' + crlf
                body += crlf
                body += file_data + crlf
            body += f"--{boundary}--".encode() + crlf
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        else:
            body = json.dumps({"content": message}).encode()
            headers = {"Content-Type": "application/json"}

        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as _:
            pass
    except Exception as e:
        print(f"[WARN] Discord 通知失敗: {e}", file=sys.stderr)


# ------------------------------------------------------------------
# FX Analyzer
# ------------------------------------------------------------------

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
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    # ------------------------------------------------------------------
    # Logic Engine 層
    # ------------------------------------------------------------------

    def _calc_indicators(self, df):
        """EMA・MACD・RSI・EMA200・ATR・ADX を計算して新しい DataFrame を返す。"""
        result = df.copy()
        l_cfg = self.config['logic']
        r_cfg = self.config.get('risk', {})

        # --- MACD ---
        ema_fast = result['Close'].ewm(span=l_cfg['macd']['fast'], adjust=False).mean()
        ema_slow = result['Close'].ewm(span=l_cfg['macd']['slow'], adjust=False).mean()
        result['MACD']  = ema_fast - ema_slow
        result['MACDs'] = result['MACD'].ewm(span=l_cfg['macd']['signal'], adjust=False).mean()

        # --- RSI ---
        diff     = result['Close'].diff()
        gain     = diff.clip(lower=0)
        loss     = -diff.clip(upper=0)
        avg_gain = gain.rolling(window=l_cfg['rsi']['length']).mean()
        avg_loss = loss.rolling(window=l_cfg['rsi']['length']).mean()
        # avg_loss=0（単調増加）→ RSI=100、avg_gain=0（単調減少）→ RSI=0
        result['RSI'] = np.where(
            avg_loss == 0,
            100.0,
            np.where(avg_gain == 0, 0.0, 100 - (100 / (1 + avg_gain / avg_loss)))
        )

        # --- EMA200 トレンドフィルター ---
        result['EMA200'] = result['Close'].ewm(span=200, adjust=False).mean()
        result['trend']  = np.where(
            result['EMA200'].isna(), np.nan,
            (result['Close'] > result['EMA200']).astype(float)
        )

        # --- ATR（True Range の移動平均）---
        atr_period = r_cfg.get('atr_period', 14)
        high_low   = result['High'] - result['Low']
        high_close = (result['High'] - result['Close'].shift(1)).abs()
        low_close  = (result['Low']  - result['Close'].shift(1)).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        result['ATR'] = true_range.rolling(window=atr_period).mean()

        # --- ADX（方向性指数）---
        adx_period = l_cfg.get('adx', {}).get('period', 14)
        up_move    = result['High'].diff()
        down_move  = -result['Low'].diff()
        plus_dm    = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm   = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        sma_tr     = true_range.rolling(adx_period).mean().replace(0, np.nan)
        plus_di    = 100 * plus_dm.rolling(adx_period).mean() / sma_tr
        minus_di   = 100 * minus_dm.rolling(adx_period).mean() / sma_tr
        di_sum     = (plus_di + minus_di).replace(0, np.nan)
        dx         = 100 * (plus_di - minus_di).abs() / di_sum
        result['ADX'] = dx.rolling(adx_period).mean()

        return result

    def analyze_row(self, prev, curr, l_cfg, m_col, s_col, r_col):
        """
        2本のローソク足からシグナル種別とレベルを返す。

        Returns
        -------
        sig_type : str   "BUY" / "SELL" / "HOLD"
        level    : int   0 (HOLD) / 1 (WATCH) / 2 (STANDARD) / 3 (STRONG) / 4 (CONFIRMED)
        """
        is_gold = (prev[m_col] < prev[s_col]) and (curr[m_col] > curr[s_col])
        is_dead = (prev[m_col] > prev[s_col]) and (curr[m_col] < curr[s_col])

        if is_gold:
            level = 1
            if curr[m_col] < 0:
                level = 3 if 30 <= curr[r_col] <= 50 else 2
            sig_type = "BUY"
        elif is_dead:
            level = 1
            if curr[m_col] > 0:
                level = 3 if 50 <= curr[r_col] <= 70 else 2
            sig_type = "SELL"
        else:
            return "HOLD", 0

        # Lv.3 のとき EMA200 方向 + ADX 強度が揃えば Lv.4 に昇格
        if level == 3:
            adx_threshold = l_cfg.get('adx', {}).get('threshold', 25)
            adx_val   = curr.get('ADX', float('nan'))
            trend_val = curr.get('trend', float('nan'))
            adx_ok  = (not pd.isna(adx_val))   and (adx_val > adx_threshold)
            ema_ok  = (not pd.isna(trend_val))  and (
                (sig_type == "BUY"  and trend_val == 1) or
                (sig_type == "SELL" and trend_val == 0)
            )
            if adx_ok and ema_ok:
                return sig_type, 4

        return sig_type, level

    def _build_signal_message(self, sig_type, level):
        """シグナル種別とレベルから表示用メッセージを組み立てる。"""
        label = {
            1: "WATCH (Lv.1)",
            2: "STANDARD (Lv.2)",
            3: "STRONG (Lv.3)",
            4: "CONFIRMED (Lv.4)",
        }
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
        if len(df) < 3:
            return "DATA_SHORTAGE", 0, None, None, None, df

        df_out   = self._calc_indicators(df)
        # iloc[-1] は yfinance が返す形成中の未確定足。
        # シグナル判定は確定済みの直近2本（iloc[-3], iloc[-2]）のみ使用する。
        sig_type, level = self.analyze_row(
            df_out.iloc[-3], df_out.iloc[-2],
            self.config['logic'], 'MACD', 'MACDs', 'RSI'
        )

        signal_msg = self._build_signal_message(sig_type, level)

        sl_price = None
        if level > 0:
            last  = df_out.iloc[-2]  # 確定足の終値を基準にする
            r_cfg = self.config.get('risk', {})
            atr   = last.get('ATR', float('nan'))
            if not pd.isna(atr) and atr > 0:
                mult     = r_cfg.get('atr_multiplier', 2.0)
                sl_price = last['Close'] - mult * atr if sig_type == "BUY" else last['Close'] + mult * atr
            else:
                pct      = r_cfg.get('stop_loss_pct', 0.01)
                sl_price = last['Close'] * ((1 - pct) if sig_type == "BUY" else (1 + pct))

        return signal_msg, level, df_out.index[-2], df_out.iloc[-2]['Close'], sl_price, df_out

    # ------------------------------------------------------------------
    # Presentation 層
    # ------------------------------------------------------------------

    def generate_chart(self, df, symbol, signal, current_level, time, price, config):
        """分析済み DataFrame からシグナルプロット付きチャートを PNG で保存する。"""
        l_cfg   = config['logic']
        plot_df = df.tail(100).copy()
        dates   = plot_df.index

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
            4: {'BUY': '#ff6600', 'SELL': '#0066ff', 's': 700},
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


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main(argv=None):
    args    = _parse_args(argv)
    analyzer = FXAnalyzerPro()

    # CLI オプションで config をメモリ上書き
    if args.symbols:
        analyzer.config['trading']['symbols'] = args.symbols
    if args.interval:
        analyzer.config['trading']['interval'] = args.interval

    target_symbols = analyzer.config['trading'].get('symbols', ["USDJPY=X"])
    interval_sec   = analyzer.config['trading'].get('watch_interval_seconds', 300)
    min_level      = args.min_level

    first_run = True
    while True:
        results = []
        if not first_run:
            Console().print(f"\n[dim]--- リフレッシュ中 ({interval_sec}秒後に次回スキャン) ---[/dim]")

        for symbol in target_symbols:
            try:
                df_raw = analyzer.fetch_data(symbol)
                sig, lv, t, price, sl, df_final = analyzer.analyze(df_raw)
                chart_path = None
                if t and lv >= min_level:
                    chart_path = analyzer.generate_chart(df_final, symbol, sig, lv, t, price, analyzer.config)
                if lv >= min_level or lv == 0:
                    results.append({
                        "symbol": symbol, "signal": sig, "level": lv,
                        "price": float(price) if price is not None else None,
                        "sl_price": float(sl) if sl is not None else None,
                        "time": t,
                        "chart_path": chart_path,
                    })
            except Exception as e:
                results.append({
                    "symbol": symbol, "signal": f"ERROR: {e}", "level": 0,
                    "price": None, "sl_price": None, "time": None,
                    "chart_path": None,
                })

        _print_results_table(results)

        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if webhook_url:
            notify_targets = [r for r in results if r["level"] >= min_level and r["level"] > 0]
            if notify_targets:
                lines = ["**[FX-Compass]**"]
                for r in notify_targets:
                    sl_str = f"{r['sl_price']:.3f}" if r["sl_price"] else "—"
                    lines.append(f"{r['symbol']}: {r['signal']} @ {r['price']:.3f} SL:{sl_str}")
                chart_paths = [r["chart_path"] for r in notify_targets if r.get("chart_path")]
                _notify_discord("\n".join(lines), webhook_url, chart_paths or None)

        first_run = False

        if not args.watch:
            break
        time.sleep(interval_sec)


if __name__ == "__main__":
    main()
