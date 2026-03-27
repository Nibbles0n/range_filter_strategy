#!/usr/bin/env python3
"""Collect tick data from Binance and test Malcolm's Polymarket strategy"""
import json
import time
import requests
from collections import defaultdict

BINANCE_API = "https://api.binance.com/api/v3"

def get_recent_agg_trades(symbol="BTCUSDT", minutes=120):
    """Get recent agg trades (tick-level)"""
    trades = []
    end_time = int(time.time() * 1000)
    start_time = end_time - minutes * 60 * 1000
    
    try:
        resp = requests.get(f"{BINANCE_API}/aggTrades", params={
            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time,
            "limit": 1000
        }, timeout=10)
        data = resp.json()
        
        for trade in data:
            trades.append({
                'timestamp': trade['T'],
                'price': float(trade['p']),
                'quantity': float(trade['q']),
            })
        
        return trades
    except Exception as e:
        print(f"Error getting trades: {e}")
        return []

def get_klines(symbol="BTCUSDT", interval="5m", limit=500):
    """Get klines for ATR"""
    try:
        resp = requests.get(f"{BINANCE_API}/klines", params={
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }, timeout=10)
        data = resp.json()
        
        klines = []
        for k in data:
            klines.append({
                'timestamp': k[0],
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
            })
        return klines
    except Exception as e:
        print(f"Error getting klines: {e}")
        return []

def calculate_atr(klines, period=14):
    """Calculate ATR from klines"""
    if len(klines) < period + 1:
        return None
    
    trs = []
    for i in range(1, len(klines)):
        hl = klines[i]['high'] - klines[i]['low']
        hc = abs(klines[i]['high'] - klines[i-1]['close'])
        lc = abs(klines[i]['low'] - klines[i-1]['close'])
        trs.append(max(hl, hc, lc))
    
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr

def test_strategy_on_historical_bars():
    """Test Malcolm's strategy on completed 5min bars using tick data"""
    print("=== Malcolm's Polymarket Strategy Backtest ===\n")
    
    # Get klines for ATR calculation
    klines = get_klines(limit=500)
    if not klines:
        print("Failed to get klines")
        return
    
    atr = calculate_atr(klines)
    print(f"14-bar ATR: ${atr:.2f} ({atr/klines[-1]['close']*100:.3f}%)\n")
    
    # Group trades by 5min bar
    trades = get_recent_agg_trades(minutes=240)  # Last 4 hours
    print(f"Got {len(trades)} tick trades\n")
    
    # Group by 5min bar
    bars = defaultdict(list)
    for trade in trades:
        # Floor timestamp to 5min interval
        bar_ts = (trade['timestamp'] // (5 * 60 * 1000)) * (5 * 60 * 1000)
        bars[bar_ts].append(trade)
    
    # For each completed bar, check if strategy signal was hit
    results = []
    for bar_ts in sorted(bars.keys())[:-1]:  # Skip current bar
        bar_trades = sorted(bars[bar_ts], key=lambda x: x['timestamp'])
        if len(bar_trades) < 2:
            continue
        
        bar_open = bar_trades[0]['price']
        bar_close = bar_trades[-1]['price']
        bar_high = max(t['price'] for t in bar_trades)
        bar_low = min(t['price'] for t in bar_trades)
        
        # Find the bar's ATR (previous bar)
        bar_idx = None
        for i, k in enumerate(klines):
            if k['timestamp'] == bar_ts:
                bar_idx = i
                break
        
        if bar_idx is None or bar_idx < 14:
            continue
        
        prev_atr = 0
        trs = []
        for j in range(bar_idx-13, bar_idx+1):
            if j < len(klines) and j > 0:
                hl = klines[j]['high'] - klines[j]['low']
                hc = abs(klines[j]['high'] - klines[j-1]['close'])
                lc = abs(klines[j]['low'] - klines[j-1]['close'])
                trs.append(max(hl, hc, lc))
        
        if len(trs) < 14:
            continue
        
        bar_atr = sum(trs[-14:]) / 14
        
        # Now check each trade to see if signal was hit
        # Signal: price moved > threshold * max_possible_reversal at some point
        direction = "UP" if bar_close > bar_open else "DOWN"
        outcome = "WIN" if (bar_close > bar_open and bar_close > bar_open) else "LOSS"
        
        # Simplified: check if bar was "strong" (moved > 1.5x ATR)
        body = abs(bar_close - bar_open)
        
        # Per Malcolm's theory: if move > 1.5x ATR at any point, enter
        # With time decay: at minute T of 5min bar, max remaining move = ATR * (5-T)/5
        
        strong_signal = False
        for t in bar_trades:
            time_in_bar = (t['timestamp'] - bar_ts) / 1000 / 60  # minutes
            time_remaining = 5 - time_in_bar
            
            if time_remaining <= 0:
                continue
            
            # How far has price moved from bar open?
            move_from_open = abs(t['price'] - bar_open)
            
            # Max possible reversal from this point
            max_rev = bar_atr * (time_remaining / 5)
            
            # Signal: if move > 1.5x max possible reversal
            if move_from_open > max_rev * 1.5:
                strong_signal = True
                break
        
        results.append({
            'time': time.strftime('%H:%M', time.localtime(bar_ts/1000)),
            'open': bar_open,
            'close': bar_close,
            'high': bar_high,
            'low': bar_low,
            'atr': bar_atr,
            'body': body,
            'direction': direction,
            'strong': strong_signal,
            'outcome': "WIN" if (direction == "UP" and bar_close > bar_open) or (direction == "DOWN" and bar_close < bar_open) else "LOSS"
        })
    
    # Analyze results
    if not results:
        print("Not enough data")
        return
    
    strong_bars = [r for r in results if r['strong']]
    weak_bars = [r for r in results if not r['strong']]
    
    print(f"Total bars: {len(results)}")
    print(f"Bars with STRONG signal: {len(strong_bars)}")
    print(f"Bars with weak/no signal: {len(weak_bars)}\n")
    
    if strong_bars:
        wins = sum(1 for r in strong_bars if r['outcome'] == 'WIN')
        print(f"=== STRONG SIGNAL BARS ===")
        print(f"Win Rate: {wins}/{len(strong_bars)} = {wins/len(strong_bars)*100:.1f}%\n")
        
        # Breakdown by direction
        up_bars = [r for r in strong_bars if r['direction'] == 'UP']
        down_bars = [r for r in strong_bars if r['direction'] == 'DOWN']
        
        if up_bars:
            up_wins = sum(1 for r in up_bars if r['outcome'] == 'WIN')
            print(f"UP bars: {len(up_bars)}, Win {up_wins} ({up_wins/len(up_bars)*100:.1f}%)")
        if down_bars:
            down_wins = sum(1 for r in down_bars if r['outcome'] == 'WIN')
            print(f"DOWN bars: {len(down_bars)}, Win {down_wins} ({down_wins/len(down_bars)*100:.1f}%)")
    
    print("\n--- Sample strong bars ---")
    for r in strong_bars[-10:]:
        print(f"{r['time']} | {r['direction']:4} | Open: {r['open']:.0f} Close: {r['close']:.0f} | Body: {r['body']:.0f} | ATR: {r['atr']:.0f} | {r['outcome']}")

if __name__ == "__main__":
    test_strategy_on_historical_bars()