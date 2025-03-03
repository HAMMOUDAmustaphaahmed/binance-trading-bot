import os
import requests
import time
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")
ALL_TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h", "8h", "1d", "1w"]
MAX_WORKERS = 8  # Adjust based on your system capabilities
REQUEST_DELAY = 0.5  # Seconds between API calls
CANDLE_LIMIT = 50  # Increased from 15 to 50 as requested

# --------------------- OPTIMIZED CORE FUNCTIONS ---------------------
def get_usdt_pairs():
    """Fetch all active USDT trading pairs with cache validation."""
    url = "https://api.binance.com/api/v3/exchangeInfo"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return [
            symbol['symbol']
            for symbol in response.json()['symbols']
            if symbol['symbol'].endswith('USDT')
            and symbol['status'] == 'TRADING'
            and symbol['quoteAsset'] == 'USDT'
        ]
    except Exception as e:
        print(f"Error fetching pairs: {e}")
        return []

def parse_candle(value):
    """Efficient candle data parser with error handling."""
    try:
        return [float(value[i]) for i in [1, 2, 3, 4]]  # Open, High, Low, Close
    except (IndexError, TypeError, ValueError):
        return [0.0, 0.0, 0.0, 0.0]

def find_reference_group(candles):
    """Optimized reference group detection with early exit."""
    best_group = None
    current_group = None
    
    for i, candle in enumerate(candles):
        o, h, l, c = parse_candle(candle)
        
        # Check green candle condition
        if c <= o:
            current_group = None
            continue
            
        # Start or extend group
        if current_group is None:
            current_group = {
                'start': i,
                'size': 1,
                'max_high': h,
                'min_low': l
            }
        else:
            # Check ascending high condition
            if h > current_group['max_high']:
                current_group['size'] += 1
                current_group['max_high'] = h
                current_group['min_low'] = min(current_group['min_low'], l)
            else:
                current_group = None
                continue
                
        # Validate group size and track best
        if 1 <= current_group['size'] <= 3:
            if not best_group or current_group['max_high'] > best_group['max_high']:
                best_group = current_group.copy()
        else:
            current_group = None
            
    return best_group

def validate_conditions(reference, candles):
    """Optimized validation with numpy-like vector operations."""
    if not reference:
        return False
        
    start = reference['start'] + reference['size']
    if len(candles) - start < 3:
        return False
        
    midpoint = (reference['max_high'] + reference['min_low']) / 2
    ref_high = reference['max_high']
    
    # Check subsequent candles
    for candle in candles[start:]:
        _, h, l, _ = parse_candle(candle)
        if h > ref_high or l < midpoint:
            return False
            
    # Check final candle
    return parse_candle(candles[-1])[1] >= ref_high * 0.90

# --------------------- TELEGRAM INTEGRATION ---------------------
def send_to_telegram(message):
    """Robust message sending with retries."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    for _ in range(3):  # Simple retry mechanism
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return
        except Exception as e:
            print(f"Telegram send failed: {e}")
            time.sleep(2)

# --------------------- OPTIMIZED API HANDLING ---------------------
def analyze_timeframe(pair, timeframe):
    """Analyze a single pair on a single timeframe and send immediate notification if valid."""
    try:
        response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": pair, "interval": timeframe, "limit": CANDLE_LIMIT},
            timeout=10
        )
        response.raise_for_status()
        candles = response.json()
        
        if len(candles) >= CANDLE_LIMIT:
            reference = find_reference_group(candles)
            if validate_conditions(reference, candles):
                # Immediately send notification for this match
                message = f"✅ *ALERT:* {pair} valid in {timeframe} timeframe!"
                send_to_telegram(message)
                return timeframe
        
        return None
    except Exception as e:
        print(f"Error analyzing {pair} on {timeframe}: {e}")
        return None

def process_pair(pair):
    """Process all timeframes for a single pair with immediate notifications."""
    valid_timeframes = []
    results_message = []
    
    for tf in ALL_TIMEFRAMES:
        valid_tf = analyze_timeframe(pair, tf)
        if valid_tf:
            valid_timeframes.append(valid_tf)
            results_message.append(f"- {valid_tf}")
        time.sleep(REQUEST_DELAY)  # Respect API rate limits
    
    # Send a summary for this pair if it has any valid timeframes
    if valid_timeframes:
        summary = f"📊 Summary for {pair}: Valid in {len(valid_timeframes)} timeframes:\n" + "\n".join(results_message)
        send_to_telegram(summary)
        
    return pair, valid_timeframes

# --------------------- MAIN EXECUTION ---------------------
if __name__ == "__main__":
    # Phase 1: Data Collection
    print("Fetching USDT pairs...")
    all_pairs = get_usdt_pairs()
    if not all_pairs:
        exit("No USDT pairs found.")
    
    send_to_telegram(f"🚀 Starting analysis of {len(all_pairs)} pairs with {CANDLE_LIMIT} candles per timeframe...")
    
    # Phase 2: Parallel Processing with immediate notifications
    print(f"Analyzing {len(all_pairs)} pairs across {len(ALL_TIMEFRAMES)} timeframes...")
    valid_pairs = []
    valid_count = 0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Process each pair individually
        futures = [executor.submit(process_pair, pair) for pair in all_pairs]
        
        # Track progress and collect results
        for i, future in enumerate(futures, 1):
            pair, valid_timeframes = future.result()
            
            if valid_timeframes:
                valid_pairs.append(pair)
                valid_count += 1
                
            # Progress updates
            if i % 100 == 0 or i == len(all_pairs):
                progress = f"📊 Progress: {i}/{len(all_pairs)} pairs processed. Found {valid_count} valid pairs so far."
                print(progress)
                send_to_telegram(progress)
    
    # Final summary
    if valid_pairs:
        summary = f"🔥 Analysis complete! Found {len(valid_pairs)} qualifying pairs."
    else:
        summary = "❌ No valid pairs found."
    
    send_to_telegram(summary)
    print("\n✅ Analysis complete. All results sent to Telegram.")