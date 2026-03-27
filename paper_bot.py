#!/usr/bin/env python3
"""Malcolm's Polymarket Strategy - Paper Trading Version"""
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

# Paper trading
trades = []
pnl = 0.0

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
    global bar_open, bar_high, bar_low, bar_start_time, current_price, atr, trades, pnl
    
    if not all([atr, current_price, bar_open, bar_start_time]):
        return
    
    now_ms = time.time() * 1000
    time_elapsed = (now_ms - bar_start_time) / 1000 / 60
    time_remaining = 5 - time_elapsed
    
    if time_remaining <= 0.1 or time_remaining > 5:
        return
    
    max_rev = atr * (time_remaining / 5)
    upper_wick = bar_high - bar_open
    lower_wick = bar_open - bar_low
    
    if upper_wick > max_rev * THRESHOLD_MULT:
        direction = "LONG"
        entry_price = current_price
        
        trades.append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'direction': direction,
            'entry': entry_price,
            'bar_open': bar_open,
            'wick': upper_wick,
            'max_rev': max_rev,
            'time_remaining': time_remaining,
            'closed': False,
            'result': None
        })
        print(f"\n  📝 PAPER LONG at ${entry_price:.2f} | Wick: ${upper_wick:.2f} | MaxRev: ${max_rev:.2f}", flush=True)
    
    if lower_wick > max_rev * THRESHOLD_MULT:
        direction = "SHORT"
        entry_price = current_price
        
        trades.append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'direction': direction,
            'entry': entry_price,
            'bar_open': bar_open,
            'wick': lower_wick,
            'max_rev': max_rev,
            'time_remaining': time_remaining,
            'closed': False,
            'result': None
        })
        print(f"\n  📝 PAPER SHORT at ${entry_price:.2f} | Wick: ${lower_wick:.2f} | MaxRev: ${max_rev:.2f}", flush=True)

def on_message(ws, message):
    global bar_open, bar_high, bar_low, bar_start_time, current_price, atr, trades, pnl
    
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
    
    # New bar
    if bar_start_time != t:
        if bar_start_time is not None and is_closed:
            direction = "UP" if c > bar_open else "DOWN"
            
            # Close any open trades from previous bar
            for trade in trades:
                if not trade['closed']:
                    if trade['direction'] == 'LONG':
                        outcome = c > trade['entry']
                        pnl_change = (c - trade['entry']) / trade['entry'] * 100
                    else:
                        outcome = c < trade['entry']
                        pnl_change = (trade['entry'] - c) / trade['entry'] * 100
                    
                    trade['closed'] = True
                    trade['exit'] = c
                    trade['outcome'] = outcome
                    trade['pnl_pct'] = pnl_change
                    trade['result'] = 'WIN' if outcome else 'LOSS'
                    
                    if outcome:
                        pnl += pnl_change * 0.95
                    else:
                        pnl += pnl_change
            
            wins = len([t for t in trades if t.get('result') == 'WIN'])
            losses = len([t for t in trades if t.get('result') == 'LOSS'])
            print(f"  BAR CLOSED {datetime.fromtimestamp(bar_start_time/1000).strftime('%H:%M')}: {direction} | Closed trades: W={wins} L={losses} | Running PnL: ${pnl:.2f}", flush=True)
        
        bar_open = o
        bar_high = h
        bar_low = l
        bar_start_time = t
        
        klines = get_klines()
        if klines:
            atr = calc_atr(klines)
    
    bar_high = max(bar_high, h)
    bar_low = min(bar_low, l)
    current_price = c
    
    check_signal()

def on_error(ws, error):
    print(f"WS Error: {error}", flush=True)

def on_close(ws):
    print("WebSocket closed", flush=True)

def on_open(ws):
    ws.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": ["btcusdt@kline_5m"],
        "id": 1
    }))
    print("Subscribed", flush=True)

def main():
    global atr, bar_open, bar_high, bar_low, bar_start_time, current_price, trades, pnl
    
    print("="*60, flush=True)
    print("MALCOLM'S POLYMARKET STRATEGY - PAPER TRADING", flush=True)
    print("="*60, flush=True)
    
    klines = get_klines()
    if klines:
        atr = calc_atr(klines)
        bar_open = klines[-1]['open']
        bar_high = klines[-1]['high']
        bar_low = klines[-1]['low']
        bar_start_time = klines[-1]['timestamp']
        current_price = klines[-1]['close']
        print(f"ATR: ${atr:.2f}", flush=True)
        print(f"Current bar: O={bar_open:.0f}", flush=True)
    print("="*60, flush=True)
    print("\n👀 Paper trading...\n", flush=True)
    
    ws = websocket.WebSocketApp(
        "wss://stream.binance.com:9443/ws",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.on_open = on_open
    
    try:
        ws.run_forever(ping_interval=10)
    except KeyboardInterrupt:
        print("\n\n=== FINAL RESULTS ===", flush=True)
        print(f"Total PnL: ${pnl:.2f}", flush=True)
        wins = len([t for t in trades if t.get('result') == 'WIN'])
        losses = len([t for t in trades if t.get('result') == 'LOSS'])
        print(f"Trades: {wins+losses} (W={wins} L={losses})", flush=True)
        if wins + losses > 0:
            print(f"Win Rate: {wins/(wins+losses)*100:.1f}%", flush=True)
        
        with open('paper_results.json', 'w') as f:
            json.dump({'pnl': pnl, 'trades': [t for t in trades if t.get('result')]}, f, indent=2, default=str)

if __name__ == "__main__":
    main()