import sys
import os
import time
from datetime import datetime
import concurrent.futures
import warnings

# Filter warning agar log bersih
warnings.filterwarnings("ignore", category=UserWarning)

# ==========================================
# 1. CEK LIBRARY
# ==========================================
try:
    import ccxt
    import pandas as pd
    import pandas_ta as ta
    import mplfinance as mpf
    import requests
except ImportError as e:
    sys.exit(f"Library Error: {e}. Install dulu: pip install ccxt pandas pandas_ta mplfinance requests")

# ==========================================
# 2. KONFIGURASI
# ==========================================
API_KEY = os.environ.get('BINANCE_API_KEY', 'fZwDMOfBL6rDU9jfUQox64fUAb2RSN48myxMPUGDAINYjmLdqJmUFhVRWLqlsX97')
API_SECRET = os.environ.get('BINANCE_API_SECRET', 'FmZNNbIOWIAddxVoLcNowLNW379E6gxyM85Bvy3QzlRMtK1eMApJp6vJtpGHWdWB')

# Telegram Config (Isi manual jika tidak pakai env var)
# Contoh: '123456789:ABCdefGhIJKlmNoPQRstUvwxyz'
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8361349338:AAHOlx4fKz_bp1MHnVg8CxS9MY_pcejxLes') 
# Contoh: '987654321'
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '6018760579')

# STRATEGI MTF
# Bot akan mencari setup BBMA di 3 TF ini secara berurutan
TF_1 = '4h'    # Big Map
TF_2 = '1h'    # Middle Map
TF_3 = '15m'   # Entry Map

LIMIT = 100             
TOP_COIN_COUNT = 300    
MAX_THREADS = 10        

OUTPUT_FOLDER = 'triple_bbma_results'
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
processed_signals = {} 

# ==========================================
# 3. KONEKSI EXCHANGE
# ==========================================
exchange = ccxt.binance({
    'apiKey': API_KEY, 'secret': API_SECRET,
    'options': {'defaultType': 'future'},
    'enableRateLimit': True, 
})

# ==========================================
# 4. TELEGRAM & CHART
# ==========================================
def send_telegram_alert(symbol, data_4h, data_1h, data_15m, image_path):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    
    icon = "🟢" if data_15m['tipe'] == "BUY" else "🔴"
    
    caption = (
        f"💎 <b>BBMA TRIPLE ALIGNMENT</b>\n"
        f"🪙 <b>{symbol}</b>\n"
        f"──────────────────────\n"
        f"🏗 <b>4H Setup:</b> {data_4h['signal']} {icon}\n"
        f"🛠 <b>1H Setup:</b> {data_1h['signal']} {icon}\n"
        f"🚀 <b>15M Entry:</b> {data_15m['signal']} {icon}\n"
        f"──────────────────────\n"
        f"🏷 <b>Tipe:</b> {data_15m['tipe']} STRONG\n"
        f"💰 <b>Harga:</b> {data_15m['price']}\n"
        f"──────────────────────\n"
        f"<i>Ketiga Timeframe Valid BBMA Setup ✅</i>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as img:
            requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, files={'photo': img}, timeout=20)
    except Exception as e:
        print(f"Gagal kirim TG: {e}")

def generate_chart(df, symbol, signal_info):
    try:
        # Kita gambar chart entry (15m) agar trader bisa lihat posisi masuk
        filename = f"{OUTPUT_FOLDER}/{symbol.replace('/','-')}_{signal_info['tipe']}.png"
        plot_df = df.tail(70).set_index('timestamp')
        
        style = mpf.make_mpf_style(base_mpf_style='nightclouds', rc={'font.size': 8})
        adds = [
            mpf.make_addplot(plot_df['BB_Up'], color='green', width=0.8),
            mpf.make_addplot(plot_df['BB_Mid'], color='orange', width=0.8),
            mpf.make_addplot(plot_df['BB_Low'], color='green', width=0.8),
            mpf.make_addplot(plot_df['MA5_Hi'], color='cyan', width=0.6),
            mpf.make_addplot(plot_df['MA5_Lo'], color='magenta', width=0.6),
        ]
        if 'EMA_50' in plot_df.columns:
            adds.append(mpf.make_addplot(plot_df['EMA_50'], color='yellow', width=1.5))

        mpf.plot(plot_df, type='candle', style=style, addplot=adds, 
                 title=f"{symbol} [15M] - {signal_info['signal']}", 
                 savefig=dict(fname=filename, bbox_inches='tight'), volume=False)
        return filename
    except: return None

# ==========================================
# 5. DATA ENGINE
# ==========================================
def get_top_symbols(limit=300):
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        valid_tickers = [t for t in tickers.values() if '/USDT' in t['symbol'] and 'USDC' not in t['symbol'] and 'UP/' not in t['symbol'] and 'DOWN/' not in t['symbol']]
        sorted_tickers = sorted(valid_tickers, key=lambda x: x['quoteVolume'] if x['quoteVolume'] else 0, reverse=True)
        return [t['symbol'] for t in sorted_tickers[:limit]]
    except: return []

def fetch_ohlcv(symbol, timeframe):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except: return None

def add_indicators(df):
    bb = df.ta.bbands(length=20, std=2)
    if bb is not None:
        df['BB_Up'] = bb.iloc[:, 2]; df['BB_Mid'] = bb.iloc[:, 1]; df['BB_Low'] = bb.iloc[:, 0]
    df['MA5_Hi'] = df['high'].rolling(5).mean()
    df['MA5_Lo'] = df['low'].rolling(5).mean()
    df['EMA_50'] = df.ta.ema(length=50)
    return df

# ==========================================
# 6. LOGIKA INTI (GENERIC BBMA ANALYZER)
# ==========================================
# Fungsi ini bisa dipakai untuk 4H, 1H, maupun 15M
def detect_bbma_setup(df):
    if df is None or len(df) < 55: return None
    c = df.iloc[-2] # Candle Close
    prev = df.iloc[-3]

    # Cek EMA Trend (Opsional, tapi bagus untuk filter)
    ema_val = c.get('EMA_50', 0)
    is_above_ema = c['close'] > ema_val
    
    signal_data = None
    tipe = "NONE"

    # --- SETUP BUY ---
    # Syarat utama: Tidak boleh candle bear momentum kuat
    
    # 1. EXTREME BUY
    if c['MA5_Lo'] < c['BB_Low']:
        signal_data = {"signal": "EXTREME", "tipe": "BUY"}
    # 2. TP WAJIB BUY
    elif c['low'] <= c['BB_Mid'] and c['close'] >= c['BB_Mid'] and c['high'] < c['BB_Up'] and is_above_ema:
        signal_data = {"signal": "TP WAJIB", "tipe": "BUY"}
    # 3. MHV BUY
    elif c['low'] <= c['BB_Low'] * 1.002 and c['close'] > c['BB_Low'] and c['MA5_Lo'] > c['BB_Low']:
        signal_data = {"signal": "MHV", "tipe": "BUY"}
    # 4. CSA BUY
    elif prev['close'] < c['BB_Mid'] and c['close'] > c['BB_Mid']:
        signal_data = {"signal": "CSA", "tipe": "BUY"}
    # 5. RE-ENTRY BUY (Wajib diatas EMA50 agar valid)
    elif c['close'] > c['BB_Mid'] and c['low'] <= c['MA5_Lo'] and is_above_ema:
        signal_data = {"signal": "RE-ENTRY", "tipe": "BUY"}
    # 6. MOMENTUM BUY
    elif c['close'] > c['BB_Up']:
        signal_data = {"signal": "CSM (MOMENTUM)", "tipe": "BUY"}

    # --- SETUP SELL ---
    
    # 1. EXTREME SELL
    if c['MA5_Hi'] > c['BB_Up']:
        signal_data = {"signal": "EXTREME", "tipe": "SELL"}
    # 2. TP WAJIB SELL
    elif c['high'] >= c['BB_Mid'] and c['close'] <= c['BB_Mid'] and c['low'] > c['BB_Low'] and not is_above_ema:
        signal_data = {"signal": "TP WAJIB", "tipe": "SELL"}
    # 3. MHV SELL
    elif c['high'] >= c['BB_Up'] * 0.998 and c['close'] < c['BB_Up'] and c['MA5_Hi'] < c['BB_Up']:
        signal_data = {"signal": "MHV", "tipe": "SELL"}
    # 4. CSA SELL
    elif prev['close'] > c['BB_Mid'] and c['close'] < c['BB_Mid']:
        signal_data = {"signal": "CSA", "tipe": "SELL"}
    # 5. RE-ENTRY SELL (Wajib dibawah EMA50 agar valid)
    elif c['close'] < c['BB_Mid'] and c['high'] >= c['MA5_Hi'] and not is_above_ema:
        signal_data = {"signal": "RE-ENTRY", "tipe": "SELL"}
    # 6. MOMENTUM SELL
    elif c['close'] < c['BB_Low']:
        signal_data = {"signal": "CSM (MOMENTUM)", "tipe": "SELL"}

    if signal_data:
        signal_data['price'] = c['close']
        signal_data['time'] = c['timestamp']
        return signal_data
    
    return None

# ==========================================
# 7. WORKER BERTINGKAT (TRIPLE CHECK)
# ==========================================
def worker_scan(symbol):
    try:
        # --- 1. SCAN 4 JAM (BIG MAP) ---
        df_4h = fetch_ohlcv(symbol, TF_1)
        df_4h = add_indicators(df_4h)
        res_4h = detect_bbma_setup(df_4h)
        
        # Jika 4H tidak ada setup BBMA apapun, SKIP (Hemat Waktu)
        if not res_4h: return None

        # --- 2. SCAN 1 JAM (MIDDLE MAP) ---
        df_1h = fetch_ohlcv(symbol, TF_2)
        df_1h = add_indicators(df_1h)
        res_1h = detect_bbma_setup(df_1h)

        # Jika 1H tidak ada setup, atau arahnya berlawanan dengan 4H, SKIP
        # Contoh: 4H Re-Entry BUY, tapi 1H Momentum SELL -> SKIP (Riskan)
        if not res_1h or res_1h['tipe'] != res_4h['tipe']: return None

        # --- 3. SCAN 15 MENIT (ENTRY MAP) ---
        df_15m = fetch_ohlcv(symbol, TF_3)
        df_15m = add_indicators(df_15m)
        res_15m = detect_bbma_setup(df_15m)

        # Jika 15M tidak ada setup, atau arahnya berlawanan, SKIP
        if not res_15m or res_15m['tipe'] != res_1h['tipe']: return None

        # --- JACKPOT: KETIGA TIMEFRAME VALID DAN SEARAH ---
        return {
            'symbol': symbol,
            '4h': res_4h,
            '1h': res_1h,
            '15m': res_15m,
            'df_chart': df_15m # Kita pakai data 15m untuk gambar
        }

    except: pass
    return None

# ==========================================
# 8. MAIN LOOP
# ==========================================
def main():
    print(f"=== BBMA TRIPLE THREAT ({TF_1} + {TF_2} + {TF_3}) ===")
    print(f"Logic: Bot mencari Setup BBMA (Extreme/CSA/Re-entry) yang VALID di 3 Timeframe sekaligus.")
    print(f"Target: {TOP_COIN_COUNT} Koin | Threads: {MAX_THREADS}")
    
    global processed_signals

    while True:
        try:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Memulai Triple Scan...")
            symbols = get_top_symbols(TOP_COIN_COUNT)
            alerts_queue = []
            
            completed = 0
            start_t = time.time()
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
                futures = {executor.submit(worker_scan, sym): sym for sym in symbols}
                
                for future in concurrent.futures.as_completed(futures):
                    res = future.result()
                    if res: alerts_queue.append(res)
                    completed += 1
                    if completed % 10 == 0:
                        sys.stdout.write(f"\rScanning: {completed}/{len(symbols)}...")
                        sys.stdout.flush()
            
            duration = time.time() - start_t
            print(f"\n✅ Selesai dalam {duration:.2f} detik. Sinyal Valid: {len(alerts_queue)}")

            for alert in alerts_queue:
                sym = alert['symbol']
                # Gunakan timestamp candle 15m sebagai ID unik
                if processed_signals.get(sym) != alert['15m']['time']:
                    processed_signals[sym] = alert['15m']['time']
                    
                    print(f"🔥 MATCH: {sym} | 4H:{alert['4h']['signal']} | 1H:{alert['1h']['signal']} | 15M:{alert['15m']['signal']}")
                    
                    img = generate_chart(alert['df_chart'], sym, alert['15m'])
                    if img: send_telegram_alert(sym, alert['4h'], alert['1h'], alert['15m'], img)
            
            print("⏳ Menunggu 45 detik...")
            time.sleep(45)

        except KeyboardInterrupt: break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()


