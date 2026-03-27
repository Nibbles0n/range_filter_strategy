#!/usr/bin/env python3
"""Malcolm's Polymarket Strategy - Simple Live Bot"""
import json
import time
import requests
from datetime import datetime
import websocket

# Config
SYMBOL = "btcusdt"
INTERVAL = "5m"
THRESHOLD_MULT = 2.0
PAPER_MODE = True

# State
bar_open = None
bar_high = None  
bar_low = None
bar_start_time = None
current_price = None
atr = None

def get_klines():
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 20},
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
        print(f"Error: {e}")
        return []

def calc_atr(klines):
    if len(klines) < 15:
        return None
    trs = []
    for i in range(1, len(klines)):
        hl = klines[i]['high'] - klines[i]['low']
        hc = abs(klines[i]['high'] - klines[i-1]['close'])
        lc = abs(klines[i]['low'] - klines[i-1]['close'])
        trs.append(max(hl, hc, lc))
    return sum(trs[-14:]) / 14

def check_signal():
    global bar_open, bar_high, bar_low, bar_start_time, current_price, atr
    
    if not all([atr, current_price, bar_open, bar_start_time]):
        return
    
    now_ms = time.time() * 1000
    time_elapsed = (now_ms - bar_start_time) / 1000 / 60
    time_remaining = 5 - time_elapsed
    
    if time_remaining <= 0.1:
        return
    
    max_rev = atr * (time_remaining / 5)
    upper_wick = bar_high - bar_open
    lower_wick = bar_open - bar_low
    
    if upper_wick > max_rev * THRESHOLD_MULT:
        print(f"\n🚀 LONG | Time: {time_remaining:.1f}min | Wick: ${upper_wick:.2f} | MaxRev: ${max_rev:.2f} | Price: ${current_price:.2f}")
        if not PAPER_MODE:
            print("   ⚠️ LIVE ORDER")
    
    if lower_wick > max_rev * THRESHOLD_MULT:
        print(f"\n🚩 SHORT | Time: {time_remaining:.1f}min | Wick: ${lower_wick:.2f} | MaxRev: ${max_rev:.2f} | Price: ${current_price:.2f}")
        if not PAPER_MODE:
            print("   ⚠️ LIVE ORDER")

# WebSocket message handler
def on_message(ws, message):
    global bar_open, bar_high, bar_low, bar_start_time, current_price
    
    data = json.loads(message)
    if 'k' not in data:
        return
    
    k = data['k']
    t = k['t']
    o = float(k['o'])
    h = float(k['h'])
    l = float(k['l'])
    c = float(k['c'])
    is_closed = k['x']
    
    global atr
    
    if bar_start_time != t:
        if bar_start_time is not None and is_closed:
            direction = "UP" if c > bar_open else "DOWN"
            print(f"  BAR CLOSED: O:{bar_open:.0f} C:{c:.0f} {direction}")
        bar_open = o
        bar_high = h
        bar_low = l
        bar_start_time = t
        
        # Update ATR
        klines = get_klines()
        if klines:
            atr = calc_atr(klines)
    
    bar_high = max(bar_high, h)
    bar_low = min(bar_low, l)
    current_price = c
    
    check_signal()

def on_error(ws, error):
    print(f"WS Error: {error}")

def on_close(ws):
    print("WebSocket closed")

def on_open(ws):
    ws.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": ["btcusdt@kline_5m"],
        "id": 1
    }))
    print("Subscribed to klines")

def main():
    global atr, bar_open, bar_high, bar_low, bar_start_time, current_price
    
    print("="*60)
    print("MALCOLM'S POLYMARKET STRATEGY")
    print("="*60)
    
    # Init ATR
    klines = get_klines()
    if klines:
        atr = calc_atr(klines)
        bar_open = klines[-1]['open']
        bar_high = klines[-1]['high']
        bar_low = klines[-1]['low']
        bar_start_time = klines[-1]['timestamp']
        current_price = klines[-1]['close']
        print(f"ATR: ${atr:.2f}")
        print(f"Current bar: O:{bar_open} H:{bar_high} L:{bar_low}")
        print(f"Started: {datetime.fromtimestamp(bar_start_time/1000).strftime('%H:%M:%S')}")
    print("="*60)
    
    ws = websocket.WebSocketApp(
        "wss://stream.binance.com:9443/ws",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.on_open = on_open
    
    print("\n👀 Watching for signals...\n")
    ws.run_forever(ping_interval=10)

if __name__ == "__main__":
    main()