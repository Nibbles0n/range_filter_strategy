#!/usr/bin/env python3
"""Ultra-fast version - no loops in loops"""

import json
from datetime import datetime, timezone

MAKER_FEE = 0.00015
TAKER_FEE = 0.00045

def main():
    print("Loading data...")
    with open("/home/picaso/.openclaw/workspace/range_filter_strategy/btc_4yr.json") as f:
        data = json.load(f)
    n = len(data)
    print(f"Processing {n} candles...")
    
    closes = [d['close'] for d in data]
    highs = [d['high'] for d in data]
    lows = [d['low'] for d in data]
    timestamps = [d['timestamp'] for d in data]
    
    print("Calculating Range Filter (Type 1)...")
    rng_qty, rng_per, sm_per = 2.618, 14, 27
    
    # AC (simplified - just abs change)
    changes = [0] + [abs(closes[i] - closes[i-1]) for i in range(1, n)]
    
    # Range EMA
    rng = [0] * n
    alpha = 2 / (rng_per + 1)
    rng_sum = sum(changes[:rng_per])
    rng[rng_per-1] = rng_qty * rng_sum / rng_per
    for i in range(rng_per, n):
        rng[i] = (rng[i-1] + alpha * (rng_qty * changes[i] - rng[i-1]))
    
    # Smooth
    alpha_s = 2 / (sm_per + 1)
    for i in range(sm_per, n):
        rng[i] = rng[i-1] + alpha_s * (rng[i] - rng[i-1])
    
    # Filter
    filt = [(highs[i] + lows[i]) / 2 for i in range(n)]
    for i in range(1, n):
        h_r = highs[i] - rng[i]
        l_r = lows[i] + rng[i]
        if h_r > filt[i-1]:
            filt[i] = h_r
        elif l_r < filt[i-1]:
            filt[i] = l_r
    
    # Direction
    direction = [0] * n
    for i in range(1, n):
        if filt[i] > filt[i-1]:
            direction[i] = 1
        elif filt[i] < filt[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
    
    # Signals
    signals = []
    for i in range(1, n):
        if direction[i] != direction[i-1]:
            signals.append((i, 'long' if direction[i] == 1 else 'short'))
    
    print(f"Found {len(signals)} signals")
    
    # Volatility (simple rolling window)
    print("Calculating volatility...")
    vol = [0] * n
    for i in range(14, n):
        total = sum(abs(closes[i-j] - closes[i-j-1]) / closes[i-j-1] for j in range(1, 15))
        vol[i] = total / 14
    
    # Band width
    bw = [(highs[i] - lows[i]) / closes[i] if closes[i] > 0 else 0 for i in range(n)]
    
    # Hours
    hours = [datetime.fromtimestamp(timestamps[i]/1000, tz=timezone.utc).hour for i in range(n)]
    
    print("Analyzing entries...")
    entries = []
    for sig_idx, stype in signals:
        entry_px = closes[sig_idx]
        
        # Quick scan for max profit
        max_profit = 0
        max_loss = 0
        holding = 0
        exit_j = min(sig_idx + 100, n)
        
        for j in range(sig_idx + 1, min(sig_idx + 100, n)):
            holding = j - sig_idx
            if stype == 'long':
                pnl = (closes[j] - entry_px) / entry_px
            else:
                pnl = (entry_px - closes[j]) / entry_px
            
            max_profit = max(max_profit, pnl)
            max_loss = min(max_loss, pnl)
            
            if j > 0 and direction[j] != direction[j-1]:
                exit_j = j
                break
        
        # Classify
        if max_profit >= 0.02 and holding >= 5:
            cls = 'excellent'
        elif max_profit >= 0.01:
            cls = 'good'
        elif max_profit >= 0.005:
            cls = 'neutral'
        else:
            cls = 'poor'
        
        entries.append({
            'idx': sig_idx,
            'type': stype,
            'max_profit': max_profit,
            'holding': holding,
            'vol': vol[sig_idx],
            'bw': bw[sig_idx],
            'hour': hours[sig_idx],
            'class': cls
        })
    
    # Count
    counts = {'excellent': 0, 'good': 0, 'neutral': 0, 'poor': 0}
    for e in entries:
        counts[e['class']] += 1
    
    print(f"\n=== ENTRIES ===")
    print(f"Total: {len(entries)}")
    for c in ['excellent', 'good', 'neutral', 'poor']:
        pct = counts[c] / len(entries) * 100
        print(f"{c:9}: {counts[c]:5} ({pct:.1f}%)")
    
    # Hourly
    print(f"\n=== HOURLY ===")
    hour_exc = {h: 0 for h in range(24)}
    hour_tot = {h: 0 for h in range(24)}
    for e in entries:
        h = e['hour']
        hour_tot[h] += 1
        if e['class'] == 'excellent':
            hour_exc[h] += 1
    
    print("Hr | Total | Exc | Rate")
    for h in range(24):
        t = hour_tot[h]
        e = hour_exc[h]
        rate = e/t*100 if t > 0 else 0
        if t > 0:
            print(f"{h:02d} | {t:5d} | {e:3d} | {rate:.1f}%")
    
    # Backtest with trailing stop
    print(f"\n=== BACKTEST (Trail=2.25%, ISL=1.5%) ===")
    capital = 10000
    pos_pct = 0.01
    trail = 0.0225
    isl = 0.015
    
    wins, losses = 0, 0
    
    for e in entries:
        idx = e['idx']
        stype = e['type']
        entry_px = closes[idx]
        pos = capital * pos_pct
        
        trail_active = False
        trail_px = 0
        
        for j in range(idx + 1, min(idx + 288, n)):
            cur = closes[j]
            
            if stype == 'long':
                pnl = (cur - entry_px) / entry_px
                
                if not trail_active and pnl >= isl:
                    trail_active = True
                    trail_px = cur * (1 - trail)
                
                if trail_active:
                    trail_px = max(trail_px, cur * (1 - trail))
                    if cur <= trail_px:
                        net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
                        capital += net
                        if net > 0: wins += 1
                        else: losses += 1
                        break
                
                if pnl <= -isl:
                    net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
                    capital += net
                    if net > 0: wins += 1
                    else: losses += 1
                    break
            else:
                pnl = (entry_px - cur) / entry_px
                
                if not trail_active and pnl >= isl:
                    trail_active = True
                    trail_px = cur * (1 + trail)
                
                if trail_active:
                    trail_px = min(trail_px, cur * (1 + trail))
                    if cur >= trail_px:
                        net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
                        capital += net
                        if net > 0: wins += 1
                        else: losses += 1
                        break
                
                if pnl <= -isl:
                    net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
                    capital += net
                    if net > 0: wins += 1
                    else: losses += 1
                    break
            
            if direction[j] != direction[j-1]:
                if stype == 'long':
                    pnl = (cur - entry_px) / entry_px
                else:
                    pnl = (entry_px - cur) / entry_px
                net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
                capital += net
                if net > 0: wins += 1
                else: losses += 1
                break
        else:
            # Timeout
            cur = closes[min(idx + 287, n - 1)]
            if stype == 'long':
                pnl = (cur - entry_px) / entry_px
            else:
                pnl = (entry_px - cur) / entry_px
            net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
            capital += net
            if net > 0: wins += 1
            else: losses += 1
    
    total = wins + losses
    print(f"Capital: ${capital:,.2f}")
    print(f"Return: {(capital/10000-1)*100:.2f}%")
    print(f"Trades: {total}")
    print(f"Win Rate: {wins/total*100:.1f}%" if total > 0 else "No trades")
    
    # Save
    with open('/home/picaso/.openclaw/workspace/range_filter_strategy/results.json', 'w') as f:
        json.dump({
            'entries': counts,
            'hourly': {h: {'exc': hour_exc[h], 'total': hour_tot[h]} for h in range(24)},
            'backtest': {'capital': capital, 'return': (capital/10000-1)*100, 'trades': total, 'wins': wins, 'losses': losses}
        }, f, indent=2)
    print("\nSaved!")

if __name__ == "__main__":
    main()