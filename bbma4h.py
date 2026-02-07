import sys
import os
import time
from datetime import datetime
import concurrent.futures  # Untuk Multithreading (Kecepatan Tinggi)

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
# 2. KONFIGURASI PENGGUNA
# ==========================================
API_KEY = os.environ.get('BINANCE_API_KEY', 'fZwDMOfBL6rDU9jfUQox64fUAb2RSN48myxMPUGDAINYjmLdqJmUFhVRWLqlsX97')
API_SECRET = os.environ.get('BINANCE_API_SECRET', 'FmZNNbIOWIAddxVoLcNowLNW379E6gxyM85Bvy3QzlRMtK1eMApJp6vJtpGHWdWB')

# Telegram Config (Isi manual jika tidak pakai env var)
# Contoh: '123456789:ABCdefGhIJKlmNoPQRstUvwxyz'
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8361349338:AAHOlx4fKz_bp1MHnVg8CxS9MY_pcejxLes') 
# Contoh: '987654321'
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '-1003618941801')

# Setting Bot
TIMEFRAME = '4h'       # Timeframe Eksekusi
LIMIT = 200             # Data history
TOP_COIN_COUNT = 300    # TARGET: 300 KOIN
MAX_THREADS = 10        # 10 Worker agar scan 300 koin lebih cepat

OUTPUT_FOLDER = 'smart_bbma_results'
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Memori agar tidak spam sinyal yang sama
processed_candles = {} 

# ==========================================
# 3. KONEKSI EXCHANGE
# ==========================================
exchange = ccxt.binance({
    'apiKey': API_KEY, 'secret': API_SECRET,
    'options': {'defaultType': 'future'},
    'enableRateLimit': True, # WAJIB TRUE agar ccxt otomatis menahan rate limit
})

# ==========================================
# 4. FUNGSI TELEGRAM
# ==========================================
def send_telegram_alert(symbol, signal_data, image_path):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    
    caption = (
        f"🧠 <b>SMART BBMA ANALYSIS (Top 300)</b>\n"
        f"💎 <b>Coin:</b> {symbol}\n"
        f"⏱ <b>Timeframe:</b> {TIMEFRAME}\n"
        f"--------------------------------\n"
        f"📢 <b>SIGNAL: {signal_data['tipe']}</b>\n"
        f"🎯 <b>Setup:</b> {signal_data['signal']}\n"
        f"💰 <b>Harga:</b> {signal_data['price']}\n"
        f"--------------------------------\n"
        f"📝 <b>PENJELASAN ANALISA:</b>\n"
        f"{signal_data['explanation']}\n"
        f"--------------------------------\n"
        f"<i>Valid Close Candle ✅</i>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as img:
            requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, files={'photo': img}, timeout=20)
    except Exception as e:
        print(f"Gagal kirim TG: {e}")

# ==========================================
# 5. DATA FETCHING & INDICATORS
# ==========================================
def get_top_symbols(limit=300):
    try:
        print(f"Mengambil daftar {limit} koin teratas...")
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        # Filter hanya USDT
        valid_tickers = [t for t in tickers.values() if '/USDT' in t['symbol'] and 'USDC' not in t['symbol'] and 'UP/' not in t['symbol'] and 'DOWN/' not in t['symbol']]
        # Sortir berdasarkan Volume Transaksi (Quote Volume)
        sorted_tickers = sorted(valid_tickers, key=lambda x: x['quoteVolume'] if x['quoteVolume'] else 0, reverse=True)
        return [t['symbol'] for t in sorted_tickers[:limit]]
    except Exception as e: 
        print(f"Gagal ambil simbol: {e}")
        return []

def fetch_ohlcv(symbol):
    try:
        # Mengambil data candle
        bars = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except: return None

def add_indicators(df):
    # Bollinger Bands (20, 2)
    bb = df.ta.bbands(length=20, std=2)
    if bb is not None:
        df['BB_Up'] = bb.iloc[:, 2]; df['BB_Mid'] = bb.iloc[:, 1]; df['BB_Low'] = bb.iloc[:, 0]
    
    # Moving Averages & EMA
    df['MA5_Hi'] = df['high'].rolling(5).mean()
    df['MA5_Lo'] = df['low'].rolling(5).mean()
    df['EMA_50'] = df.ta.ema(length=50)
    return df

def generate_chart(df, symbol, signal_info):
    try:
        filename = f"{OUTPUT_FOLDER}/{symbol.replace('/','-')}_{signal_info['tipe']}.png"
        plot_df = df.tail(60).set_index('timestamp')
        
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
                 title=f"{symbol} - {signal_info['signal']}", 
                 savefig=dict(fname=filename, bbox_inches='tight'), volume=False)
        return filename
    except: return None

# ==========================================
# 6. LOGIKA SMART ANALYSIS
# ==========================================
def analyze_market_structure(df):
    if df.empty or 'BB_Up' not in df.columns: return None
    c = df.iloc[-2] # Close Candle
    
    signal_data = None
    
    # Trend Filter
    trend_status = "SIDEWAYS"
    if c['close'] > c.get('EMA_50', 0): trend_status = "BULLISH"
    elif c['close'] < c.get('EMA_50', 9999999): trend_status = "BEARISH"

    # --- 1. EXTREME ---
    if c['MA5_Hi'] > c['BB_Up']:
        signal_data = {
            "signal": "EXTREME", "tipe": "SELL",
            "explanation": "MA5 High keluar Top BB. Indikasi pasar Overbought (Jenuh Beli). Potensi koreksi."
        }
    elif c['MA5_Lo'] < c['BB_Low']:
        signal_data = {
            "signal": "EXTREME", "tipe": "BUY",
            "explanation": "MA5 Low keluar Low BB. Indikasi pasar Oversold (Jenuh Jual). Potensi pantulan."
        }

    # --- 2. CSM (MOMENTUM) ---
    elif c['close'] > c['BB_Up']:
        signal_data = {
            "signal": "CSM (MOMENTUM)", "tipe": "BUY",
            "explanation": "Candle Close Tebal di atas Top BB. Kekuatan Buyer dominan. Indikasi lanjut naik."
        }
    elif c['close'] < c['BB_Low']:
        signal_data = {
            "signal": "CSM (MOMENTUM)", "tipe": "SELL",
            "explanation": "Candle Close Tebal di bawah Low BB. Kekuatan Seller dominan. Indikasi lanjut turun."
        }

    # --- 3. RE-ENTRY (TREND FOLLOWER) ---
    else:
        if trend_status == "BULLISH" and c['close'] > c['BB_Mid']:
            if c['low'] <= c['MA5_Lo']: 
                signal_data = {
                    "signal": "RE-ENTRY", "tipe": "BUY",
                    "explanation": "Trend BULLISH (Diatas EMA50). Harga koreksi menyentuh MA5 Low. Titik masuk aman."
                }
        
        elif trend_status == "BEARISH" and c['close'] < c['BB_Mid']:
            if c['high'] >= c['MA5_Hi']: 
                signal_data = {
                    "signal": "RE-ENTRY", "tipe": "SELL",
                    "explanation": "Trend BEARISH (Dibawah EMA50). Harga naik koreksi menyentuh MA5 High. Titik masuk aman."
                }

    if signal_data:
        signal_data['price'] = c['close']
        signal_data['time'] = c['timestamp']
        return signal_data
    
    return None

# ==========================================
# 7. WORKER THREAD
# ==========================================
def worker_scan(symbol):
    try:
        # Fetch Data
        df = fetch_ohlcv(symbol)
        if df is None or len(df) < 55: return None
        
        # Analisa
        df = add_indicators(df)
        result = analyze_market_structure(df)
        
        if result:
            result['symbol'] = symbol
            result['df'] = df
            return result
    except:
        pass
    return None

# ==========================================
# 8. MAIN ENGINE
# ==========================================
def main():
    print(f"=== SMART BBMA ANALYST (300 COINS) ===")
    print(f"Mode: {MAX_THREADS} Threads | TF: {TIMEFRAME}")
    print(f"Target: Top {TOP_COIN_COUNT} Liquid Coins")
    
    global processed_candles

    while True:
        try:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Mengambil daftar koin...")
            symbols = get_top_symbols(TOP_COIN_COUNT)
            
            if not symbols:
                print("Gagal mengambil simbol. Ulangi...")
                time.sleep(5)
                continue

            print(f"🚀 Memulai Scan {len(symbols)} Koin...")
            
            alerts_queue = []
            start_t = time.time()
            
            # --- PARALLEL EXECUTION ---
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
                futures = {executor.submit(worker_scan, sym): sym for sym in symbols}
                
                # Progress Bar Sederhana
                completed = 0
                for future in concurrent.futures.as_completed(futures):
                    res = future.result()
                    if res:
                        alerts_queue.append(res)
                    completed += 1
                    sys.stdout.write(f"\rProgress: {completed}/{len(symbols)} koin diproses...")
                    sys.stdout.flush()
            
            duration = time.time() - start_t
            print(f"\n✅ Selesai dalam {duration:.2f} detik. Sinyal: {len(alerts_queue)}")

            # --- KIRIM HASIL ---
            for alert in alerts_queue:
                sym = alert['symbol']
                
                if processed_candles.get(sym) != alert['time']:
                    processed_candles[sym] = alert['time']
                    
                    print(f"🔥 KIRIM TELEGRAM: {sym} [{alert['tipe']}]")
                    img = generate_chart(alert['df'], sym, alert)
                    if img: send_telegram_alert(sym, alert, img)
            
            # Jeda agar tidak terkena Rate Limit Binance karena request 300 koin cukup berat
            print("⏳ Menunggu siklus berikutnya (30 detik)...")
            time.sleep(30)

        except KeyboardInterrupt:
            print("\nBerhenti.")
            break
        except Exception as e:
            print(f"Error Main Loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()


