#!/usr/bin/env python3
"""Quick backtest - trades off speed for simplicity"""

import json
from datetime import datetime, timezone

MAKER_FEE = 0.00015
TAKER_FEE = 0.00045

with open("btc_4yr.json") as f:
    data = json.load(f)
n = len(data)

closes = [d['close'] for d in data]
highs = [d['high'] for d in data]
lows = [d['low'] for d in data]
ts = [d['timestamp'] for d in data]

# Range filter
rng_qty, rng_per = 2.618, 14
alpha = 2 / (rng_per + 1)
rng = [0] * n
ac = [abs(closes[i] - closes[i-1]) for i in range(1, n)]
rng[rng_per-1] = rng_qty * sum(ac[:rng_per]) / rng_per
for i in range(rng_per, n):
    rng[i] = rng[i-1] + alpha * (rng_qty * ac[i-rng_per] - rng[i-1])

filt = [(highs[i] + lows[i]) / 2 for i in range(n)]
for i in range(1, n):
    if highs[i] - rng[i] > filt[i-1]:
        filt[i] = highs[i] - rng[i]
    elif lows[i] + rng[i] < filt[i-1]:
        filt[i] = lows[i] + rng[i]

direction = [0] * n
for i in range(1, n):
    direction[i] = 1 if filt[i] > filt[i-1] else (-1 if filt[i] < filt[i-1] else direction[i-1])

signals = [(i, 'long' if direction[i] == 1 else 'short') for i in range(1, n) if direction[i] != direction[i-1]]

vol = [0] * n
for i in range(14, n):
    vol[i] = sum(abs(closes[i-j] - closes[i-j-1]) / closes[i-j-1] for j in range(1, 15)) / 14

hours = [datetime.fromtimestamp(ts[i]/1000, tz=timezone.utc).hour for i in range(n)]

entries = []
for idx, stype in signals:
    ep = closes[idx]
    mp, ml, hold = 0, 0, 0
    for j in range(idx+1, min(idx+100, n)):
        hold = j - idx
        pnl = (closes[j] - ep) / ep if stype == 'long' else (ep - closes[j]) / ep
        mp = max(mp, pnl)
        ml = min(ml, pnl)
        if direction[j] != direction[j-1]:
            break
    entries.append({'idx': idx, 'type': stype, 'mp': mp, 'ml': ml, 'hold': hold, 'vol': vol[idx], 'hour': hours[idx]})

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

print("\nHr|Tot|Exc|Rate")
for h in range(24):
    t, e = he[h]['t'], he[h]['e']
    if t > 0:
        print(f"{h:02d}|{t:4d}|{e:3d}|{e/t*100:.1f}%")

# Backtest
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
        if (st == 'long' and (pnl >= trail or pnl <= -isl)) or (st == 'short' and (pnl >= trail or pnl <= -isl)):
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
print(f"Trades: {total}, Win Rate: {wins/total*100:.1f}%" if total > 0 else "No trades")

with open('results.json', 'w') as f:
    json.dump({'counts': counts, 'capital': capital, 'return': (capital/10000-1)*100, 'trades': total, 'wins': wins, 'losses': losses}, f)