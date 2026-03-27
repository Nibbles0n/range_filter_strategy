#!/usr/bin/env python3
"""Ultra minimal - Pi friendly"""
import json

MAKER_FEE = 0.00015
TAKER_FEE = 0.00045

print("Loading data...")
with open("btc_50k.json") as f:
    data = json.load(f)
n = len(data)
print(f"Data: {n} candles")

closes = [d['close'] for d in data]
highs = [d['high'] for d in data]
lows = [d['low'] for d in data]
del data

# Range filter
print("Range filter...")
rng_qty, rng_per = 2.618, 14
alpha = 2 / (rng_per + 1)
rng = [0.0] * n
ac = [abs(closes[i] - closes[i-1]) for i in range(1, n)]
rng[rng_per-1] = rng_qty * sum(ac[:rng_per]) / rng_per
for i in range(rng_per, n):
    rng[i] = rng[i-1] + alpha * (rng_qty * ac[i-rng_per] - rng[i-1])

filt = [(highs[i] + lows[i]) / 2.0 for i in range(n)]
for i in range(1, n):
    h_r = highs[i] - rng[i]
    l_r = lows[i] + rng[i]
    if h_r > filt[i-1]: filt[i] = h_r
    elif l_r < filt[i-1]: filt[i] = l_r

direction = [0] * n
for i in range(1, n):
    direction[i] = 1 if filt[i] > filt[i-1] else (-1 if filt[i] < filt[i-1] else direction[i-1])

signals = [(i, 'long' if direction[i] == 1 else 'short') for i in range(1, n) if direction[i] != direction[i-1]]
print(f"Signals: {len(signals)}")

del rng, filt, highs, lows, ac
print("Memory freed")

# Analyze
entries = []
for idx, stype in signals:
    ep = closes[idx]
    mp, hold = 0.0, 0
    for j in range(idx+1, min(idx+100, n)):
        hold = j - idx
        mp = max(mp, (closes[j] - ep) / ep if stype == 'long' else (ep - closes[j]) / ep)
        if direction[j] != direction[j-1]: break
    entries.append({'idx': idx, 'type': stype, 'mp': mp, 'hold': hold})

print(f"Entries: {len(entries)}")

# Backtest
print("Backtest...")
capital = 10000.0
trail, isl = 0.0225, 0.015
wins, losses = 0, 0

for i, e in enumerate(entries):
    idx = e['idx']
    stype = e['type']
    ep = closes[idx]
    pos = capital * 0.01
    trail_active = False
    trail_px = 0.0
    
    for j in range(idx+1, min(idx+288, n)):
        cur = closes[j]
        if stype == 'long':
            pnl = (cur - ep) / ep
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
            elif pnl <= -isl:
                net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
                capital += net
                if net > 0: wins += 1
                else: losses += 1
                break
        else:
            pnl = (ep - cur) / ep
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
            elif pnl <= -isl:
                net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
                capital += net
                if net > 0: wins += 1
                else: losses += 1
                break
        if direction[j] != direction[j-1]:
            net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
            capital += net
            if net > 0: wins += 1
            else: losses += 1
            break
    else:
        cur = closes[min(idx+287, n-1)]
        pnl = (cur - ep) / ep if stype == 'long' else (ep - cur) / ep
        net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
        capital += net
        if net > 0: wins += 1
        else: losses += 1
    
    if i % 2000 == 0:
        print(f"Progress: {i}/{len(entries)}...")

total = wins + losses
print(f"\n=== RESULTS ===")
print(f"Capital: ${capital:,.2f}")
print(f"Return: {(capital/10000-1)*100:.2f}%")
print(f"Trades: {total}")
print(f"Win Rate: {wins/total*100:.1f}%" if total > 0 else "No trades")

with open('results.json', 'w') as f:
    json.dump({'capital': capital, 'return': (capital/10000-1)*100, 'trades': total, 'wins': wins, 'losses': losses}, f)