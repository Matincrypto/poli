import requests
import json
import time
import logging
import asyncio
import sqlite3
from telegram import Bot
from telegram.error import TelegramError
from telegram.constants import ParseMode
from datetime import datetime

# --- Telegram Bot Configuration ---
TELEGRAM_BOT_TOKEN = "7435237309:AAEAXXkce1VU8Wk-NqxX1v6VKnSMaydbErs" 
TELEGRAM_GROUP_CHAT_ID = -1002684336789 
TELEGRAM_MESSAGE_THREAD_ID = 2

# --- API and Comparison Configuration ---
WALLEX_BASE_URL = "https://api.wallex.ir/v1/"
PRICE_DIFFERENCE_ALERT_THRESHOLD = 1.0 
MIN_GLOBAL_VOLUME_USD_FOR_COMPARISON = 50000000 
MAX_ALLOWED_PERCENTAGE_DIFFERENCE = 100.0

# --- Logging Configuration ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Functions ---
def setup_database():
    try:
        conn = sqlite3.connect('signals.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                asset TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                entry_or_sell_price REAL NOT NULL,
                target_price REAL NOT NULL,
                percentage_difference REAL NOT NULL,
                grade TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("Database 'signals.db' is set up.")
    except Exception as e:
        logger.error(f"Database setup failed: {e}")

def save_signal_to_db(timestamp, asset, signal_type, entry_or_sell_price, target_price, percentage_diff):
    try:
        conn = sqlite3.connect('signals.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO signals (timestamp, asset, signal_type, entry_or_sell_price, target_price, percentage_difference)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (timestamp, asset, signal_type, entry_or_sell_price, target_price, percentage_diff))
        conn.commit()
        conn.close()
        logger.info(f"Signal for {asset} saved to database.")
    except Exception as e:
        logger.error(f"Failed to save signal for {asset} to database: {e}")

# --- Helper and API Functions ---
def safe_float_conversion(value):
    if value is None or value == '' or value == '-': return None
    try: return float(value)
    except ValueError: return None

def get_wallex_markets_usdt_only():
    try:
        r = requests.get(WALLEX_BASE_URL + "markets")
        r.raise_for_status()
        data = r.json().get("result", {}).get("symbols", {})
        return {s: d for s, d in data.items() if d.get('quoteAsset') == 'USDT'}
    except Exception as e:
        logger.error(f"Error fetching Wallex markets: {e}")
        return {}

def get_global_currency_stats():
    try:
        r = requests.get(WALLEX_BASE_URL + "currencies/stats")
        r.raise_for_status() 
        return r.json().get("result", [])
    except Exception as e:
        logger.error(f"Error fetching global currency stats: {e}")
        return []

def get_wallex_mid_price_from_order_book(wallex_symbol):
    try:
        r = requests.get(WALLEX_BASE_URL + "depth", params={"symbol": wallex_symbol})
        r.raise_for_status()
        data = r.json().get("result", {})
        asks, bids = data.get("ask"), data.get("bid")
        if asks and bids and asks[0] and bids[0]:
            low_ask = safe_float_conversion(asks[0].get("price"))
            high_bid = safe_float_conversion(bids[0].get("price"))
            if low_ask and high_bid: return (low_ask + high_bid) / 2
        return None
    except Exception as e:
        logger.warning(f"Could not fetch order book for {wallex_symbol}: {e}")
        return None

def get_global_price_and_volume(crypto_key, global_stats):
    for currency in global_stats:
        if currency.get('key') == crypto_key:
            return safe_float_conversion(currency.get('price')), safe_float_conversion(currency.get('volume_24h'))
    return None, None

async def send_telegram_message(message_text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_CHAT_ID: return
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_GROUP_CHAT_ID,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN,
            message_thread_id=TELEGRAM_MESSAGE_THREAD_ID,
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")

# --- Main Analysis Logic ---
async def analyze_prices():
    logger.info(f"--- Starting Analysis Cycle ---")
    all_currencies_stats = get_global_currency_stats() 
    wallex_usdt_markets = get_wallex_markets_usdt_only()      
    if not all_currencies_stats: return
    
    for currency in all_currencies_stats:
        crypto_key = currency.get('key')
        if not crypto_key: continue

        global_price, global_volume = get_global_price_and_volume(crypto_key, all_currencies_stats) 
        if not global_volume or global_volume < MIN_GLOBAL_VOLUME_USD_FOR_COMPARISON: continue

        wallex_usdt_symbol = f"{crypto_key}USDT"
        if wallex_usdt_symbol in wallex_usdt_markets:
            wallex_mid_price = get_wallex_mid_price_from_order_book(wallex_usdt_symbol)
            
            if wallex_mid_price and global_price:
                percentage_difference = ((wallex_mid_price - global_price) / global_price) * 100
                
                if abs(percentage_difference) >= PRICE_DIFFERENCE_ALERT_THRESHOLD and abs(percentage_difference) <= MAX_ALLOWED_PERCENTAGE_DIFFERENCE:
                    
                    timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    if percentage_difference < 0: # Buy Signal
                        signal_type, signal_type_fa = 'BUY', "BUY"
                        profit_percent = ((global_price - wallex_mid_price) / wallex_mid_price) * 100
                        action_text = f"خرید {crypto_key.upper()} در والکس"
                        price_label = "قیمت ورود"
                    else: # Sell Signal
                        signal_type, signal_type_fa = 'SELL', "SELL"
                        profit_percent = percentage_difference
                        action_text = f"فروش {crypto_key.upper()} در والکس"
                        price_label = "قیمت فروش"
                    
                    save_signal_to_db(timestamp_str, crypto_key.upper(), signal_type, wallex_mid_price, global_price, percentage_difference)
                    
                    # --- CLEANER MESSAGE FORMAT ---
                    title = f"*{crypto_key.upper()}-USDT : {signal_type_fa}*"
                    price_line = f"{price_label}: `${wallex_mid_price:,.4f}`"
                    target_line = f"قیمت تارگت: `${global_price:,.4f}`"
                    profit_line = f"سود: *{profit_percent:.2f}%*"
                    wallex_trade_link = f"https://wallex.ir/app/trade/{crypto_key.upper()}USDT"
                    action_link = f"[{action_text}]({wallex_trade_link})"
                    
                    alert_message = (
                        f"{title}\n\n"
                        f"{price_line}\n"
                        f"{target_line}\n"
                        f"{profit_line}\n\n"
                        f"{action_link}\n"
                        f"_{timestamp_str}_"
                    )
                    
                    await send_telegram_message(alert_message)
        
        await asyncio.sleep(0.1)

# --- Main Execution Block ---
async def main():
    try:
        while True:
            await analyze_prices() 
            wait_time = 300
            logger.info(f"--- Cycle complete. Waiting {wait_time} seconds. ---")
            await asyncio.sleep(wait_time) 
    except KeyboardInterrupt:
        logger.info("Script stopped by user.")
    except Exception as e:
        logger.critical(f"Critical error in main loop: {e}", exc_info=True)

if __name__ == "__main__":
    setup_database()
    asyncio.run(main())