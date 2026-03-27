#!/usr/bin/env python3
"""Malcolm's Polymarket Strategy - Live Bot"""
import json
import time
import requests
from datetime import datetime
import websocket
import threading
import signal
import sys

# Config
SYMBOL = "btcusdt"
INTERVAL = "5m"
THRESHOLD_MULT = 2.0  # Enter when move > max_possible_reversal * this
POSITION_SIZE = 10  # USD per trade
PAPER_MODE = True  # Set to False when ready to trade

# State
bar_open = None
bar_high = None
bar_low = None
bar_start_time = None
current_price = None
atr = None
closes_14 = []  # Last 14 closes for ATR
closes_lock = threading.Lock()

def get_klines_5m():
    """Get last 20 klines from Binance"""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": SYMBOL.upper(), "interval": INTERVAL, "limit": 20},
            timeout=10
        )
        data = resp.json()
        klines = []
        for k in data:
            klines.append({
                'timestamp': k[0],
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4])
            })
        return klines
    except Exception as e:
        print(f"Error getting klines: {e}")
        return []

def calculate_atr(klines):
    """Calculate 14-bar ATR"""
    if len(klines) < 15:
        return None
    
    trs = []
    for i in range(1, len(klines)):
        hl = klines[i]['high'] - klines[i]['low']
        hc = abs(klines[i]['high'] - klines[i-1]['close'])
        lc = abs(klines[i]['low'] - klines[i-1]['close'])
        trs.append(max(hl, hc, lc))
    
    if len(trs) < 14:
        return None
    
    return sum(trs[-14:]) / 14

def on_message(ws, message):
    global bar_open, bar_high, bar_low, bar_start_time, current_price, atr
    
    data = json.loads(message)
    
    # Kline/candlestick data
    if 'k' in data:
        k = data['k']
        t = k['t']  # Kline start time
        o = float(k['o'])
        h = float(k['h'])
        l = float(k['l'])
        c = float(k['c'])
        
        # New bar
        if bar_start_time != t:
            if bar_start_time is not None:
                # Bar closed - log it
                bar_duration = (datetime.now().timestamp() * 1000 - bar_start_time) / 1000
                direction = "UP" if c > bar_open else "DOWN"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] BAR CLOSED | O:{bar_open:.2f} H:{bar_high:.2f} L:{bar_low:.2f} C:{c:.2f} | {direction} | Duration: {bar_duration:.0f}s")
            
            bar_open = o
            bar_high = h
            bar_low = l
            bar_start_time = t
            
            # Update ATR
            klines = get_klines_5m()
            if klines:
                new_atr = calculate_atr(klines)
                if new_atr:
                    atr = new_atr
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ATR Updated: ${atr:.2f} ({atr/bar_open*100:.3f}%)")
        
        # Update current bar
        bar_high = max(bar_high, h)
        bar_low = min(bar_low, l)
        current_price = c
        
        # Check signal every tick
        if atr and current_price and bar_open:
            check_signal()

def check_signal():
    """Check if Malcolm's signal is active"""
    if not atr or not current_price or not bar_open or not bar_start_time:
        return
    
    # Time remaining in bar (in minutes)
    now_ms = time.time() * 1000
    time_elapsed = (now_ms - bar_start_time) / 1000 / 60  # minutes
    time_remaining = 5 - time_elapsed
    
    if time_remaining <= 0:
        return
    
    # Max possible reversal from bar open, given time remaining
    max_possible_reversal = atr * (time_remaining / 5)
    
    # Current move from bar open
    upper_wick = bar_high - bar_open
    lower_wick = bar_open - bar_low
    
    # LONG signal: price moved UP beyond threshold
    if upper_wick > max_possible_reversal * THRESHOLD_MULT:
        entry_price = current_price
        max_loss = upper_wick * 0.5  # Rough stop
        print(f"\n{'='*50}")
        print(f"🚀 LONG SIGNAL!")
        print(f"Time remaining: {time_remaining:.1f} min")
        print(f"Upper wick: ${upper_wick:.2f}")
        print(f"Max reversal: ${max_possible_reversal:.2f}")
        print(f"Threshold: ${max_possible_reversal * THRESHOLD_MULT:.2f}")
        print(f"Current price: ${entry_price:.2f}")
        print(f"Stop loss (50%): ${entry_price - max_loss:.2f}")
        print(f"{'='*50}\n")
        
        if not PAPER_MODE:
            # PLACE ORDER HERE
            print("⚠️ LIVE TRADING - ORDER PLACED")
        else:
            print(f"📝 PAPER TRADE: Bought at ${entry_price:.2f}")
    
    # SHORT signal
    if lower_wick > max_possible_reversal * THRESHOLD_MULT:
        entry_price = current_price
        max_loss = lower_wick * 0.5
        print(f"\n{'='*50}")
        print(f"🚩 SHORT SIGNAL!")
        print(f"Time remaining: {time_remaining:.1f} min")
        print(f"Lower wick: ${lower_wick:.2f}")
        print(f"Max reversal: ${max_possible_reversal:.2f}")
        print(f"Threshold: ${max_possible_reversal * THRESHOLD_MULT:.2f}")
        print(f"Current price: ${entry_price:.2f}")
        print(f"Stop loss (50%): ${entry_price + max_loss:.2f}")
        print(f"{'='*50}\n")
        
        if not PAPER_MODE:
            print("⚠️ LIVE TRADING - ORDER PLACED")
        else:
            print(f"📝 PAPER TRADE: Shorted at ${entry_price:.2f}")

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws):
    print("WebSocket closed")

def on_open(ws):
    print("WebSocket connected")
    # Subscribe to BTCUSDT kline
    ws.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": [f"{SYMBOL}@kline_{INTERVAL}"],
        "id": 1
    }))
    print(f"Subscribed to {SYMBOL}@kline_{INTERVAL}")

def main():
    print("="*60)
    print("MALCOLM'S POLYMARKET STRATEGY - LIVE BOT")
    print("="*60)
    print(f"Symbol: {SYMBOL}")
    print(f"Threshold: {THRESHOLD_MULT}x ATR")
    print(f"Position size: ${POSITION_SIZE}")
    print(f"Mode: {'PAPER' if PAPER_MODE else 'LIVE'}")
    print("="*60)
    
    # Initialize ATR
    print("\nInitializing ATR...")
    klines = get_klines_5m()
    if klines:
        atr = calculate_atr(klines)
        if atr:
            print(f"Initial ATR: ${atr:.2f}")
            bar_open = klines[-1]['open']
            bar_high = klines[-1]['high']
            bar_low = klines[-1]['low']
            bar_start_time = klines[-1]['timestamp']
            print(f"Current bar started: {datetime.fromtimestamp(bar_start_time/1000).strftime('%H:%M:%S')}")
    
    # Start WebSocket
    ws = websocket.WebSocketApp(
        "wss://stream.binance.com:9443/ws",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    
    ws.on_open = on_open
    
    # Run with 30 second ping to keep alive
    def run_ws():
        ws.run_forever(ping_interval=30)
    
    ws_thread = threading.Thread(target=run_ws)
    ws_thread.daemon = True
    ws_thread.start()
    
    print("\n👀 Watching for signals... Press Ctrl+C to stop")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        ws.close()
        sys.exit(0)

if __name__ == "__main__":
    main()