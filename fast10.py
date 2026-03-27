#!/usr/bin/env python3
"""Optimized - O(n) range calculation"""
import json
from datetime import datetime, timezone

MAKER_FEE = 0.00015
TAKER_FEE = 0.00045

with open("btc_4yr.json") as f:
    data = json.load(f)
data = data[-30000:]
n = len(data)
print(f"{n} candles")

closes = [d['close'] for d in data]
highs = [d['high'] for d in data]
lows = [d['low'] for d in data]
ts = [d['timestamp'] for d in data]

# O(n) range calculation
rng_qty, rng_per = 2.618, 14
alpha = 2 / (rng_per + 1)
rng = [0] * n

# First value: simple average of first rng_per changes
ac = [abs(closes[i] - closes[i-1]) for i in range(1, n+1)]
rng_sum = sum(ac[:rng_per])
rng[rng_per-1] = rng_qty * rng_sum / rng_per

# Sliding window EMA - O(n)
for i in range(rng_per, n):
    rng_sum = rng_sum - ac[i-rng_per] + ac[i]
    rng[i] = rng[i-1] + alpha * (rng_qty * ac[i] / rng_per - rng[i-1])

# Range filter type 1
filt = [(highs[i] + lows[i]) / 2 for i in range(n)]
for i in range(1, n):
    h_r = highs[i] - rng[i]
    l_r = lows[i] + rng[i]
    if h_r > filt[i-1]:
        filt[i] = h_r
    elif l_r < filt[i-1]:
        filt[i] = l_r

direction = [0] * n
for i in range(1, n):
    direction[i] = 1 if filt[i] > filt[i-1] else (-1 if filt[i] < filt[i-1] else direction[i-1])

signals = [(i, 'long' if direction[i] == 1 else 'short') for i in range(1, n) if direction[i] != direction[i-1]]
print(f"Signals: {len(signals)}")

entries = []
for idx, stype in signals:
    ep = closes[idx]
    mp, hold = 0, 0
    for j in range(idx+1, min(idx+100, n)):
        hold = j - idx
        mp = max(mp, (closes[j] - ep) / ep if stype == 'long' else (ep - closes[j]) / ep)
        if direction[j] != direction[j-1]:
            break
    entries.append({'type': stype, 'mp': mp, 'hold': hold, 'hour': datetime.fromtimestamp(ts[idx]/1000, tz=timezone.utc).hour})

counts = {'excellent': 0, 'good': 0, 'neutral': 0, 'poor': 0}
for e in entries:
    e['class'] = 'excellent' if e['mp'] >= 0.02 and e['hold'] >= 5 else ('good' if e['mp'] >= 0.01 else ('neutral' if e['mp'] >= 0.005 else 'poor'))
    counts[e['class']] += 1
print("ENTRIES:", counts)

he = {h: {'e': 0, 't': 0} for h in range(24)}
for e in entries:
    he[e['hour']]['t'] += 1
    if e['class'] == 'excellent':
        he[e['hour']]['e'] += 1

print("\nHr|Total|Exc|Rate")
for h in range(24):
    t, e = he[h]['t'], he[h]['e']
    if t > 0:
        print(f"{h:02d}|{t:5d}|{e:3d}|{e/t*100:.1f}%")

capital = 10000
trail, isl = 0.02, 0.015
wins, losses = 0, 0

for e in entries:
    ep = closes[e['idx']]
    pos = capital * 0.01
    st = e['type']
    for j in range(e['idx']+1, min(e['idx']+288, n)):
        cur = closes[j]
        pnl = (cur - ep) / ep if st == 'long' else (ep - cur) / ep
        if pnl >= trail or pnl <= -isl:
            net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
            capital += net
            if net > 0: wins += 1
            else: losses += 1
            break
    else:
        cur = closes[min(e['idx']+287, n-1)]
        pnl = (cur - ep) / ep if st == 'long' else (ep - cur) / ep
        net = pos * pnl - pos * (MAKER_FEE + TAKER_FEE)
        capital += net
        if net > 0: wins += 1
        else: losses += 1

total = wins + losses
print(f"\nCapital: ${capital:,.2f}")
print(f"Return: {(capital/10000-1)*100:.2f}%")
print(f"Trades: {total}, WR: {wins/total*100:.1f}%" if total > 0 else "No trades")