#!/usr/bin/env python3
"""
Fast Range Filter Strategy - Optimized for speed
"""

import json
from datetime import datetime, timezone
from itertools import product

# HYPERLIQUID FEES (Tier 0)
MAKER_FEE = 0.00015
TAKER_FEE = 0.00045

def load_data():
    with open("/home/picaso/.openclaw/workspace/range_filter_strategy/btc_4yr.json") as f:
        data = json.load(f)
    return data

def calc_range_filter(highs, lows, closes, rng_qty=2.618, rng_period=14, smooth_per=27):
    n = len(closes)
    if n < rng_period:
        return None, None, None
    
    # Average Change
    ac = [abs(closes[i] - closes[i-1]) for i in range(1, n)]
    
    # EMA of AC
    alpha = 2 / (rng_period + 1)
    ac_ema = [ac[0]]
    for c in ac[1:]:
        ac_ema.append((c - ac_ema[-1]) * alpha + ac_ema[-1])
    
    # Range
    rng = [0] * (rng_period - 1) + [rng_qty * ac_ema[i - rng_period] for i in range(rng_period - 1, n - 1)]
    
    # Smooth
    if len(rng) > smooth_per:
        alpha_s = 2 / (smooth_per + 1)
        for i in range(smooth_per, n):
            rng[i] = (rng[i] - rng[i-1]) * alpha_s + rng[i-1]
    
    # Filter
    filt = [(highs[i] + lows[i]) / 2 for i in range(n)]
    for i in range(1, n):
        h_r = highs[i] - rng[i]
        l_r = lows[i] + rng[i]
        if h_r > filt[i-1]:
            filt[i] = h_r
        elif l_r < filt[i-1]:
            filt[i] = l_r
    
    # Bands
    hi = [filt[i] + rng[i] for i in range(n)]
    lo = [filt[i] - rng[i] for i in range(n)]
    
    return hi, lo, filt

def get_direction(filt):
    direction = [0] * len(filt)
    for i in range(1, len(filt)):
        if filt[i] > filt[i-1]:
            direction[i] = 1
        elif filt[i] < filt[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
    return direction

def get_volatility(closes, idx, window=14):
    if idx < window:
        return 0.0
    total = 0
    for i in range(idx - window, idx):
        total += abs(closes[i] - closes[i-1]) / closes[i-1]
    return total / window

def get_hour(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour

def main():
    print("Loading data...")
    data = load_data()
    n = len(data)
    print(f"Processing {n} candles...")
    
    highs = [d['high'] for d in data]
    lows = [d['low'] for d in data]
    closes = [d['close'] for d in data]
    timestamps = [d['timestamp'] for d in data]
    
    print("Calculating Range Filter...")
    hi_band, lo_band, filt = calc_range_filter(highs, lows, closes)
    direction = get_direction(filt)
    
    print("Finding signals and analyzing entries...")
    # Find all direction changes
    signals = []
    for i in range(1, n):
        if direction[i] == 1 and direction[i-1] == -1:
            signals.append((i, 'long'))
        elif direction[i] == -1 and direction[i-1] == 1:
            signals.append((i, 'short'))
    
    print(f"Found {len(signals)} signals")
    
    # Analyze entries efficiently
    entries = []
    for sig_idx, sig_type in signals:
        entry_price = closes[sig_idx]
        
        # Find max profit in next 100 candles or until opposite signal
        max_profit = 0
        max_loss = 0
        exit_idx = min(sig_idx + 100, n)
        holding = 0
        
        for j in range(sig_idx + 1, min(sig_idx + 100, n)):
            holding = j - sig_idx
            if sig_type == 'long':
                pnl = (closes[j] - entry_price) / entry_price
            else:
                pnl = (entry_price - closes[j]) / entry_price
            
            max_profit = max(max_profit, pnl)
            max_loss = min(max_loss, pnl)
            
            # Stop at opposite signal
            if j > 0 and direction[j] != direction[j-1]:
                exit_idx = j
                break
        
        entries.append({
            'idx': sig_idx,
            'type': sig_type,
            'price': entry_price,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'holding': holding,
            'volatility': get_volatility(closes, sig_idx),
            'band_width': (hi_band[sig_idx] - lo_band[sig_idx]) / closes[sig_idx] if closes[sig_idx] > 0 else 0,
            'hour': get_hour(timestamps[sig_idx])
        })
    
    # Classify
    for e in entries:
        if e['max_profit'] >= 0.02 and e['holding'] >= 5:
            e['class'] = 'excellent'
        elif e['max_profit'] >= 0.01:
            e['class'] = 'good'
        elif e['max_profit'] >= 0.005:
            e['class'] = 'neutral'
        else:
            e['class'] = 'poor'
    
    # Count
    counts = {'excellent': 0, 'good': 0, 'neutral': 0, 'poor': 0}
    for e in entries:
        counts[e['class']] += 1
    
    print(f"\n=== ENTRY CLASSIFICATION ===")
    print(f"Total: {len(entries)}")
    for c in ['excellent', 'good', 'neutral', 'poor']:
        pct = counts[c] / len(entries) * 100
        print(f"{c:9}: {counts[c]:5} ({pct:.1f}%)")
    
    # Hour analysis
    print(f"\n=== HOURLY ANALYSIS ===")
    hour_stats = {}
    for e in entries:
        h = e['hour']
        if h not in hour_stats:
            hour_stats[h] = {'exc': 0, 'total': 0}
        hour_stats[h]['total'] += 1
        if e['class'] == 'excellent':
            hour_stats[h]['exc'] += 1
    
    print("Hr | Total | Exc | Rate")
    for h in sorted(hour_stats.keys()):
        t = hour_stats[h]['total']
        e = hour_stats[h]['exc']
        print(f"{h:02d} | {t:5d} | {e:3d} | {e/t*100:.1f}%")
    
    # Quick backtest with trailing stop
    print(f"\n=== QUICK BACKTEST (no filters) ===")
    
    capital = 10000
    position_pct = 0.01
    trail = 0.0225
    isl = 0.015
    
    wins = 0
    losses = 0
    
    for e in entries:
        idx = e['idx']
        stype = e['type']
        entry_px = closes[idx]
        pos_size = capital * position_pct
        
        # Fee on entry
        fee = pos_size * MAKER_FEE
        
        # Find exit with trailing stop
        exited = False
        trail_active = False
        trail_px = 0
        
        for j in range(idx + 1, min(idx + 288, n)):
            cur_px = closes[j]
            
            if stype == 'long':
                pnl = (cur_px - entry_px) / entry_px
                
                if not trail_active and pnl >= isl:
                    trail_active = True
                    trail_px = cur_px * (1 - trail)
                
                if trail_active:
                    new_trail = cur_px * (1 - trail)
                    if new_trail > trail_px:
                        trail_px = new_trail
                    if cur_px <= trail_px:
                        # Exit long
                        exit_px = cur_px
                        pnl = (exit_px - entry_px) / entry_px
                        net = pos_size * pnl - pos_size * (MAKER_FEE + TAKER_FEE)
                        capital += net
                        if net > 0: wins += 1
                        else: losses += 1
                        exited = True
                        break
                
                if pnl <= -isl:
                    exit_px = cur_px
                    pnl = (exit_px - entry_px) / entry_px
                    net = pos_size * pnl - pos_size * (MAKER_FEE + TAKER_FEE)
                    capital += net
                    if net > 0: wins += 1
                    else: losses += 1
                    exited = True
                    break
            else:  # short
                pnl = (entry_px - cur_px) / entry_px
                
                if not trail_active and pnl >= isl:
                    trail_active = True
                    trail_px = cur_px * (1 + trail)
                
                if trail_active:
                    new_trail = cur_px * (1 + trail)
                    if new_trail < trail_px:
                        trail_px = new_trail
                    if cur_px >= trail_px:
                        exit_px = cur_px
                        pnl = (entry_px - exit_px) / entry_px
                        net = pos_size * pnl - pos_size * (MAKER_FEE + TAKER_FEE)
                        capital += net
                        if net > 0: wins += 1
                        else: losses += 1
                        exited = True
                        break
                
                if pnl <= -isl:
                    exit_px = cur_px
                    pnl = (entry_px - exit_px) / entry_px
                    net = pos_size * pnl - pos_size * (MAKER_FEE + TAKER_FEE)
                    capital += net
                    if net > 0: wins += 1
                    else: losses += 1
                    exited = True
                    break
            
            # Exit on opposite signal
            if direction[j] != direction[j-1]:
                exit_px = cur_px
                if stype == 'long':
                    pnl = (exit_px - entry_px) / entry_px
                else:
                    pnl = (entry_px - exit_px) / entry_px
                net = pos_size * pnl - pos_size * (MAKER_FEE + TAKER_FEE)
                capital += net
                if net > 0: wins += 1
                else: losses += 1
                exited = True
                break
        
        if not exited:
            # Timeout - close at last price
            exit_px = closes[min(idx + 287, n - 1)]
            if stype == 'long':
                pnl = (exit_px - entry_px) / entry_px
            else:
                pnl = (entry_px - exit_px) / entry_px
            net = pos_size * pnl - pos_size * (MAKER_FEE + TAKER_FEE)
            capital += net
            if net > 0: wins += 1
            else: losses += 1
    
    total = wins + losses
    print(f"Final Capital: ${capital:,.2f}")
    print(f"Return: {(capital/10000-1)*100:.2f}%")
    print(f"Trades: {total}")
    print(f"Wins: {wins} ({wins/total*100:.1f}%)" if total > 0 else "No trades")
    print(f"Losses: {losses}")
    
    # Save results
    results = {
        'classification': counts,
        'hourly': {h: {'exc': hour_stats[h]['exc'], 'total': hour_stats[h]['total']} for h in hour_stats},
        'backtest': {
            'final_capital': capital,
            'return': (capital/10000-1)*100,
            'trades': total,
            'wins': wins,
            'losses': losses
        }
    }
    
    with open('/home/picaso/.openclaw/workspace/range_filter_strategy/quick_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print("\nResults saved!")

if __name__ == "__main__":
    main()