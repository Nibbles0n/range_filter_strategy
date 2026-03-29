# Polymarket Orderbook Testing Protocol
**Date:** 2026-03-29  
**Principle:** 100% realistic fill simulation. No midpoint lies.

---

## The Problem with Midpoint-Only Tracking

When we record a trade at midpoint price, we're lying to ourselves about execution quality.

**Example:**
```
Polymarket YES token for BTC-updown-5m market:
- Best Ask: $0.52 (size: 200 shares)
- Best Bid: $0.48 (size: 200 shares)
- Midpoint: $0.50

We want to BUY $100 at ~$0.50:
- Shares needed: $100 / $0.50 = 200 shares

Orderbook sweep for 200 shares:
- 200 shares @ $0.52 = $104 total → VWAP = $0.52

Realistic fill: $0.52 (4% worse than midpoint)
Midpoint backtest would record: $0.50
→ 4% error per trade = catastrophic backtest inflation
```

This is why past backtest results are meaningless. We were filling at midpoint which never actually happens.

---

## Realistic Fill Calculation

**Step 1 — Fetch orderbook at signal time:**
```python
def get_orderbook(token_id: str) -> dict:
    """Fetch full orderbook from Polymarket CLOB. Public endpoint."""
    resp = requests.get(
        "https://clob.polymarket.com/book",
        params={"token_id": token_id},
        timeout=10
    )
    return resp.json()
```

**Step 2 — Calculate realistic fill for our order size:**
```python
def calculate_realistic_fill(orderbook: dict, side: str, dollar_amount: float) -> dict:
    """
    Walk the orderbook to calculate realistic fill price.
    
    For BUY: walk asks (lowest first)
    For SELL: walk bids (highest first)
    
    Returns dict with:
      - vwap: volume-weighted average price paid
      - total_cost: actual dollars spent
      - shares_filled: how many shares we got
      - pct_worse_than_midpoint: slippage quality
      - fully_filled: bool — did we fill the full order?
    """
    if side == "BUY":
        levels = orderbook.get("asks", [])  # sorted low→high
    else:
        levels = orderbook.get("bids", [])  # sorted high→low
    
    remaining_dollars = dollar_amount
    total_cost = 0.0
    shares_filled = 0
    
    for level in levels:
        price = float(level["price"])
        size = float(level["size"])
        size_cost = size * price  # cost to buy all at this level
        
        if remaining_dollars <= 0:
            break
        
        # How much of our order can be filled at this level?
        fill_value = min(remaining_dollars, size_cost)
        fill_shares = fill_value / price
        
        total_cost += fill_value
        shares_filled += fill_shares
        remaining_dollars -= fill_value
    
    if shares_filled == 0:
        return {
            "vwap": 0,
            "total_cost": 0,
            "shares_filled": 0,
            "pct_worse_than_midpoint": 0,
            "fully_filled": False,
        }
    
    vwap = total_cost / shares_filled
    
    # Calculate midpoint for comparison
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    if bids and asks:
        mid = (float(bids[0]["price"]) + float(asks[0]["price"])) / 2
        pct_worse = (vwap - mid) / mid * 100
    else:
        mid = vwap
        pct_worse = 0
    
    return {
        "vwap": round(vwap, 4),
        "total_cost": round(total_cost, 2),
        "shares_filled": round(shares_filled, 4),
        "pct_worse_than_midpoint": round(pct_worse, 2),
        "fully_filled": remaining_dollars <= 0.01,  # within $0.01 = fully filled
        "midpoint": round(mid, 4) if mid else None,
    }
```

**Step 3 — Record trade with full transparency:**
```python
trade = {
    "timestamp": signal_time.isoformat(),
    "token": token,
    "signal": signal,  # "LONG" or "SHORT"
    "midpoint_at_signal": midpoint,
    "realistic_fill_vwap": fill_result["vwap"],
    "orderbook_spread": round(asks[0]["price"] - bids[0]["price"], 4),
    "pct_worse_than_midpoint": fill_result["pct_worse_than_midpoint"],
    "shares_filled": fill_result["shares_filled"],
    "dollar_amount": dollar_amount,
    "fully_filled": fill_result["fully_filled"],
    "outcome": None,  # filled in when market resolves
    "pnl": None,  # calculated from realistic fill, not midpoint
    "liquidity_flags": [],  # ["thin_market", "wide_spread", "partial_fill", etc.]
}
```

---

## Trade Recording Schema

Every trade recorded must include:

| Field | Required | Description |
|-------|----------|-------------|
| `timestamp` | ✅ | When signal fired |
| `token` | ✅ | BTC, ETH, SOL, etc. |
| `signal` | ✅ | LONG or SHORT |
| `midpoint_at_signal` | ✅ | Polymarket midpoint at signal |
| `realistic_fill_vwap` | ✅ | Actual VWAP from orderbook sweep |
| `pct_worse_than_midpoint` | ✅ | Slippage quality % |
| `orderbook_spread` | ✅ | Bid-ask spread at signal |
| `shares_filled` | ✅ | How many shares we got |
| `dollar_amount` | ✅ | How much we tried to bet |
| `fully_filled` | ✅ | Did we fill the whole order? |
| `outcome` | ✅ (when resolved) | WIN or LOSE |
| `pnl` | ✅ (when resolved) | Realized P&L from realistic fill |
| `exit_price` | ✅ (when resolved) | Price when market resolved |
| `liquidity_flags` | ✅ | Any issues (thin, wide spread, partial) |
| `binance_bar_deviation` | ✅ | Actual BTC deviation that triggered signal |
| `atr_threshold` | ✅ | ATR threshold at signal time |

---

## Liquidity Quality Flags

If any of these are true, note it in `liquidity_flags`:

- `thin_market`: total visible liquidity < $500 at relevant price level
- `wide_spread`: bid-ask spread > 5% of price
- `partial_fill`: could only fill < 80% of desired bet size
- `vwap_poor`: fill was > 3% worse than midpoint
- `no_liquidity`: no liquidity at any price level

---

## Signal-to-Trade Flow

```
1. Binance 5m candle closes → check deviation vs open
2. Deviation > ATR threshold → SIGNAL
3. Fetch Polymarket orderbook for YES token
4. Calculate realistic fill for our bet size ($50-100)
5. Record trade with midpoint AND realistic fill
6. Wait for bar close to resolve outcome
7. Record WIN/LOSE with P&L calculated from realistic fill
8. Log everything with full transparency
```

---

## What We Stopped Doing

❌ **Stopped:** Using midpoint price as entry in backtests  
✅ **Now:** Always using orderbook VWAP

❌ **Stopped:** Trusting historical backtest results as meaningful  
✅ **Now:** Only forward paper trading with realistic fills

❌ **Stopped:** Large historical datasets as primary validation  
✅ **Now:** Live trading with proper execution simulation

❌ **Stopped:** Hiding that midpoint ≠ real fill  
✅ **Now:** Explicitly tracking and displaying the difference

---

## Validation Rules

1. Every signal MUST have an orderbook fetch attempt
2. If orderbook fetch fails → record trade with `realistic_fill_vwap = None` and flag `orderbook_fetch_failed = True`
3. Never record a trade with midpoint-only and call it realistic
4. Review `pct_worse_than_midpoint` distribution weekly — if median > 3%, our bet size is too large for this market
5. Forward testing: need 200+ live signals before drawing any conclusions

---

## Success Metrics (Forward Only)

We only care about:
- **Realistic win rate** — calculated from realistic fill costs
- **Realistic P&L** — based on actual VWAP entries
- **Slippage quality** — median % worse than midpoint
- **Liquidity flag rate** — % of trades with any quality issue

We DO NOT report:
- Midpoint-based backtest returns
- Historical results from before this protocol
- Any metric that uses midpoint as entry price
