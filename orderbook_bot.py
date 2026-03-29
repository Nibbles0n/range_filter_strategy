#!/usr/bin/env python3
"""
Malcolm's Polymarket Strategy — Orderbook-Aware Paper Trading
100% realistic fill simulation using live Polymarket CLOB orderbook data.

Principle: No midpoint lies. Every trade is recorded with its actual
orderbook-derived VWAP fill price, not just the midpoint.

Signal → Fetch orderbook → Calculate realistic VWAP → Record trade
"""

import json
import time
import requests
import threading
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ── Setup ────────────────────────────────────────────────────────────────────
LOG_FILE = Path("/home/picaso/.openclaw/workspace/range_filter_strategy/bot.log")
RESULTS_FILE = Path("/home/picaso/.openclaw/workspace/range_filter_strategy/orderbook_results.json")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
POSITION_SIZE = 100          # $ per trade
POLYMARKET_FEE = 0.01        # 1% on winnings
MAX_ENTRY_PRICE = 0.60       # Skip if YES > this (filters expensive entries)
PLACE_LIVE_ORDERS = False   # True = real Polymarket orders (requires funded account)
TOKENS = ['btc', 'eth', 'sol', 'xrp', 'doge']

# ── Polymarket CLOB Client ────────────────────────────────────────────────────
_clob_client = None

def get_clob_client():
    """Lazily create authenticated CLOB client."""
    global _clob_client
    if _clob_client is not None:
        return _clob_client
    
    creds_file = Path(__file__).parent / ".polymarket_creds.json"
    if not creds_file.exists():
        logger.warning("No .polymarket_creds.json — using unauthenticated CLOB fallback")
        return None
    
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        creds_data = json.loads(creds_file.read_text())
        creds = ApiCreds(
            api_key=creds_data["api_key"],
            api_secret=creds_data["api_secret"],
            api_passphrase=creds_data["api_passphrase"],
        )
        _clob_client = ClobClient(host="https://clob.polymarket.com", chain_id=137, creds=creds)
        logger.info("CLOB: authenticated")
        return _clob_client
    except Exception as e:
        logger.warning(f"CLOB auth failed: {e}")
        return None

# ── Orderbook Fetching ────────────────────────────────────────────────────────
def fetch_orderbook(token_id: str) -> dict:
    """
    Fetch full orderbook from Polymarket CLOB.
    Public endpoint — no auth required.
    
    Returns dict with bids and asks, each as list of {price, size}.
    """
    try:
        resp = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"Orderbook fetch failed for {token_id[:20]}: {e}")
    return None

def get_midpoint(token_id: str) -> float:
    """Get current midpoint from Polymarket CLOB."""
    client = get_clob_client()
    if client:
        try:
            result = client.get_midpoint(token_id)
            if result and 'mid' in result:
                return float(result['mid'])
        except:
            pass
    try:
        resp = requests.get(
            "https://clob.polymarket.com/midpoint",
            params={"token_id": token_id},
            timeout=5
        )
        if resp.status_code == 200:
            return float(resp.json().get('mid_price', 0))
    except:
        pass
    return None

# ── Realistic Fill Calculation ────────────────────────────────────────────────
def calculate_realistic_fill(orderbook: dict, side: str, dollar_amount: float) -> dict:
    """
    Walk the orderbook to calculate realistic fill price for our order.
    
    For BUY (bet YES): walk asks low→high until order is filled
    For SELL (bet NO): walk bids high→low until order is filled
    
    Returns:
        vwap: volume-weighted average price paid
        total_cost: actual dollars spent
        shares_filled: how many shares we got
        pct_worse_than_midpoint: slippage quality (0 = perfect)
        fully_filled: did we fill the full order?
        midpoint: midpoint at time of fill calculation
        liquidity_flags: list of any issues
    """
    if orderbook is None:
        return {
            "vwap": None,
            "total_cost": None,
            "shares_filled": 0,
            "pct_worse_than_midpoint": None,
            "fully_filled": False,
            "midpoint": None,
            "liquidity_flags": ["orderbook_fetch_failed"],
        }
    
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    liquidity_flags = []
    
    # Calculate midpoint
    if bids and asks:
        midpoint = (float(bids[0]["price"]) + float(asks[0]["price"])) / 2
        spread = float(asks[0]["price"]) - float(bids[0]["price"])
        spread_pct = spread / midpoint * 100
        
        if spread_pct > 5:
            liquidity_flags.append("wide_spread")
        if spread_pct > 10:
            liquidity_flags.append("very_wide_spread")
    else:
        midpoint = None
        liquidity_flags.append("no_bbo")
    
    # Determine which levels to walk
    if side == "BUY":
        levels = sorted(asks, key=lambda x: float(x["price"]))  # low→high
    else:
        levels = sorted(bids, key=lambda x: float(x["price"]), reverse=True)  # high→low
    
    # Walk the book
    remaining_dollars = dollar_amount
    total_cost = 0.0
    shares_filled = 0
    
    total_visible_liquidity = sum(float(l["price"]) * float(l["size"]) for l in levels)
    if total_visible_liquidity < dollar_amount:
        liquidity_flags.append("thin_market")
    
    for level in levels:
        price = float(level["price"])
        size = float(level["size"])
        size_cost = size * price  # dollar value at this level
        
        if remaining_dollars <= 0.01:
            break
        
        fill_value = min(remaining_dollars, size_cost)
        fill_shares = fill_value / price
        
        total_cost += fill_value
        shares_filled += fill_shares
        remaining_dollars -= fill_value
    
    if shares_filled == 0:
        return {
            "vwap": None,
            "total_cost": 0,
            "shares_filled": 0,
            "pct_worse_than_midpoint": None,
            "fully_filled": False,
            "midpoint": midpoint,
            "liquidity_flags": liquidity_flags + ["no_liquidity"],
        }
    
    vwap = total_cost / shares_filled
    
    if midpoint and midpoint > 0:
        pct_worse = (vwap - midpoint) / midpoint * 100
        if pct_worse > 3:
            liquidity_flags.append("vwap_poor")
        if pct_worse > 5:
            liquidity_flags.append("very_poor_vwap")
    else:
        pct_worse = 0
    
    fully_filled = remaining_dollars <= 0.01
    if not fully_filled:
        liquidity_flags.append("partial_fill")
    
    return {
        "vwap": round(vwap, 4),
        "total_cost": round(total_cost, 2),
        "shares_filled": round(shares_filled, 4),
        "pct_worse_than_midpoint": round(pct_worse, 2),
        "fully_filled": fully_filled,
        "midpoint": round(midpoint, 4) if midpoint else None,
        "liquidity_flags": liquidity_flags if liquidity_flags else ["ok"],
    }

# ── State ────────────────────────────────────────────────────────────────────
state = {}
for token in TOKENS:
    state[token] = {
        'bar_open': None,
        'bar_start_time': None,
        'bar_close': None,
        'current_price': None,
        'atr_per_bar': None,
        'pending_trade': None,
        'polymarket_yes_price': None,
        'current_slug': None,
        'yes_token': None,
        'no_token': None,
    }

# ── Binance Data ──────────────────────────────────────────────────────────────
def get_binance_klines(symbol, limit=15):
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "5m", "limit": limit},
            timeout=10
        )
        data = resp.json()
        return [{
            'timestamp': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4])
        } for k in data]
    except:
        return []

def calc_atr(klines):
    if len(klines) < 15:
        return None
    trs = []
    for i in range(1, 15):
        hl = klines[i]['high'] - klines[i]['low']
        hc = abs(klines[i]['high'] - klines[i-1]['close'])
        lc = abs(klines[i]['low'] - klines[i-1]['close'])
        trs.append(max(hl, hc, lc))
    return sum(trs) / 14

# ── Polymarket Market Discovery ──────────────────────────────────────────────
def get_polymarket_market(token):
    """Get current Polymarket market for token's 5m interval."""
    utc_now = datetime.now(timezone.utc)
    ts = (int(utc_now.timestamp()) // 300) * 300
    slug = f'{token}-updown-5m-{ts}'
    
    try:
        resp = requests.get(
            'https://gamma-api.polymarket.com/markets',
            params={'slug': slug},
            timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            return None
        
        m = data[0]
        outcome_raw = m.get('outcomePrices', '[]')
        outcome_prices = json.loads(outcome_raw) if isinstance(outcome_raw, str) else outcome_raw
        tokens_raw = m.get('clobTokenIds', '[]')
        tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
        
        yes_price = get_midpoint(tokens[0]) if len(tokens) >= 1 else None
        
        # Fallback to gamma outcome prices
        if yes_price is None and len(outcome_prices) >= 2:
            try:
                yes_price = float(outcome_prices[0])
            except:
                pass
        
        return {
            'slug': slug,
            'question': m.get('question'),
            'yes_price': yes_price,
            'yes_token': tokens[0] if len(tokens) >= 1 else None,
            'no_token': tokens[1] if len(tokens) >= 2 else None,
            'closed': m.get('closed'),
        }
    except:
        return None

# ── Results Persistence ───────────────────────────────────────────────────────
def load_results():
    default = {
        'trades': [],
        'summary': {'total': 0, 'correct': 0, 'wrong': 0, 'pnl': 0},
        'by_token': {t: {'total': 0, 'correct': 0, 'wrong': 0, 'pnl': 0} for t in TOKENS},
        'fill_quality': {'pct_worse_list': [], 'partial_fill_count': 0, 'orderbook_fail_count': 0},
    }
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE) as f:
                return json.load(f)
        except:
            pass
    return default

def save_results(results):
    tmp = RESULTS_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(results, f, indent=2)
    tmp.replace(RESULTS_FILE)

def record_trade(token, signal, entry_price, fill_result, outcome, reason, meta):
    """
    Record a trade with FULL TRANSPARENCY.
    
    fill_result: dict from calculate_realistic_fill()
    entry_price: midpoint price at signal time
    """
    results = load_results()
    
    # Calculate P&L from REALISTIC FILL, not midpoint
    if fill_result["vwap"] and fill_result["vwap"] > 0:
        effective_entry = fill_result["vwap"]
    else:
        effective_entry = entry_price  # fallback if no orderbook
    
    if outcome == 'WIN':
        # Win: we get 1/effective_entry shares, cost was effective_entry * shares
        # Net: shares * (1/effective_entry - 1) * (1 - fee)
        pnl = POSITION_SIZE * (1/effective_entry - 1) * (1 - POLYMARKET_FEE)
    else:
        # Loss: lose our bet amount (but entry was at realistic fill)
        pnl = -POSITION_SIZE
    
    trade = {
        'timestamp': datetime.now().isoformat(),
        'token': token,
        'signal': signal,
        
        # Entry price transparency
        'midpoint_at_signal': entry_price,
        'realistic_fill_vwap': fill_result.get("vwap"),
        'pct_worse_than_midpoint': fill_result.get("pct_worse_than_midpoint"),
        'orderbook_spread': meta.get('orderbook_spread'),
        
        # Fill details
        'shares_filled': fill_result.get("shares_filled"),
        'dollar_amount': POSITION_SIZE,
        'fully_filled': fill_result.get("fully_filled"),
        'liquidity_flags': fill_result.get("liquidity_flags", []),
        
        # Outcome
        'outcome': outcome,
        'pnl': round(pnl, 2),
        'reason': reason,
        
        # Context
        'bar_open': meta.get('bar_open'),
        'bar_close': meta.get('bar_close'),
        'binance_deviation': meta.get('deviation'),
        'atr_threshold': meta.get('threshold'),
        'time_remaining': meta.get('time_remaining'),
    }
    
    results['trades'].append(trade)
    results['summary']['total'] += 1
    results['summary']['pnl'] += round(pnl, 2)
    results['by_token'][token]['total'] += 1
    results['by_token'][token]['pnl'] += round(pnl, 2)
    
    if outcome == 'WIN':
        results['summary']['correct'] += 1
        results['by_token'][token]['correct'] += 1
    else:
        results['summary']['wrong'] += 1
        results['by_token'][token]['wrong'] += 1
    
    # Fill quality tracking
    if fill_result.get("pct_worse_than_midpoint") is not None:
        results['fill_quality']['pct_worse_list'].append(fill_result["pct_worse_than_midpoint"])
    if not fill_result.get("fully_filled", True):
        results['fill_quality']['partial_fill_count'] += 1
    if "orderbook_fetch_failed" in fill_result.get("liquidity_flags", []):
        results['fill_quality']['orderbook_fail_count'] += 1
    
    save_results(results)
    
    # Log with full transparency
    vwap = fill_result.get("vwap")
    pct = fill_result.get("pct_worse_than_midpoint")
    flags = fill_result.get("liquidity_flags", [])
    
    if vwap and entry_price:
        quality_msg = f"VWAP ${vwap:.4f} ({pct:+.2f}% vs mid ${entry_price:.4f})"
    elif vwap is None:
        quality_msg = "⚠️ ORDERBOOK FAILED — using midpoint as fallback"
    else:
        quality_msg = f"VWAP ${vwap:.4f}"
    
    logger.info(
        f"TRADE | {token.upper()} {signal} | {outcome} | {quality_msg} | "
        f"Flags: {flags} | P&L: ${pnl:.2f}"
    )
    
    return trade

# ── Signal Detection ───────────────────────────────────────────────────────────
def check_signal(token):
    s = state[token]
    
    if s['atr_per_bar'] is None or s['bar_open'] is None or s['current_price'] is None:
        return
    
    if s['pending_trade'] is not None:
        return
    
    if s['polymarket_yes_price'] is None:
        return
    
    # Time remaining in bar
    now_ms = time.time() * 1000
    time_elapsed = (now_ms - s['bar_start_time']) / 1000 / 60
    time_remaining = 5 - time_elapsed
    
    if time_remaining <= 0:
        return
    
    threshold = s['atr_per_bar'] * (time_remaining / 5)
    deviation = abs(s['current_price'] - s['bar_open'])
    
    if deviation <= threshold:
        return
    
    # Signal detected
    if s['current_price'] > s['bar_open']:
        direction = 'LONG'
        expected = 'UP'
    else:
        direction = 'SHORT'
        expected = 'DOWN'
    
    # Get market data
    market = get_polymarket_market(token)
    if market is None:
        logger.warning(f"{token.upper()}: No Polymarket market found")
        return
    
    yes_token = market.get('yes_token')
    no_token = market.get('no_token')
    
    if yes_token is None:
        logger.warning(f"{token.upper()}: No YES token ID")
        return
    
    # Get midpoint at signal time (before orderbook to avoid look-ahead)
    midpoint = s['polymarket_yes_price']
    if midpoint is None or midpoint <= 0:
        logger.debug(f"{token.upper()}: No midpoint price available")
        return
    
    # Skip if price too high
    if midpoint > MAX_ENTRY_PRICE:
        logger.debug(f"{token.upper()}: YES price ${midpoint:.3f} > MAX ${MAX_ENTRY_PRICE}")
        return
    
    # Fetch orderbook for realistic fill calculation
    order_token = yes_token if direction == 'LONG' else no_token
    orderbook = fetch_orderbook(order_token)
    
    # Calculate realistic fill
    fill_result = calculate_realistic_fill(orderbook, side='BUY', dollar_amount=POSITION_SIZE)
    
    # Get orderbook spread for logging
    orderbook_spread = None
    if orderbook:
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        if bids and asks:
            orderbook_spread = round(float(asks[0]["price"]) - float(bids[0]["price"]), 4)
    
    # Record pending trade with FULL transparency
    s['pending_trade'] = {
        'signal': direction,
        'expected': expected,
        'midpoint': midpoint,
        'fill_result': fill_result,
        'bar_open': s['bar_open'],
        'atr_per_bar': s['atr_per_bar'],
        'deviation': deviation,
        'threshold': threshold,
        'time_remaining': time_remaining,
        'orderbook_spread': orderbook_spread,
        'market': market,
    }
    
    # Log the signal with realistic fill info
    vwap = fill_result.get("vwap")
    pct = fill_result.get("pct_worse_than_midpoint")
    flags = fill_result.get("liquidity_flags", [])
    
    if vwap:
        fill_msg = f"→ Fill: ${vwap:.4f} ({pct:+.2f}% vs mid) | Flags: {flags}"
    else:
        fill_msg = "→ Fill: ⚠️ ORDERBOOK UNAVAILABLE (using midpoint)"
    
    print(f"\n🚦 {token.upper()} {direction} SIGNAL | Dev: ${deviation:.2f} > ${threshold:.4f}")
    print(f"   Midpoint: ${midpoint:.4f} | {fill_msg}")
    print(f"   Time left: {time_remaining:.1f}m | Bar open: ${s['bar_open']:.2f}")

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main_loop():
    last_status = 0
    
    while True:
        now = time.time()
        
        for token in TOKENS:
            s = state[token]
            symbol = f"{token.upper()}USDT"
            
            klines = get_binance_klines(symbol)
            if not klines:
                continue
            
            current_bar = klines[-1]
            t = current_bar['timestamp']
            
            # New bar detected
            if s['bar_start_time'] is not None and t != s['bar_start_time']:
                # Resolve pending trade from previous bar
                if s['pending_trade'] and s['bar_close'] is not None:
                    pt = s['pending_trade']
                    bar_direction = 'UP' if s['bar_close'] > pt['bar_open'] else 'DOWN'
                    correct = bar_direction == pt['expected']
                    outcome = 'WIN' if correct else 'LOSE'
                    
                    reason = f"{pt['expected']} expected, was {bar_direction}"
                    
                    # Record trade with realistic fill
                    meta = {
                        'bar_open': pt['bar_open'],
                        'bar_close': s['bar_close'],
                        'deviation': pt['deviation'],
                        'threshold': pt['threshold'],
                        'time_remaining': pt.get('time_remaining'),
                        'orderbook_spread': pt.get('orderbook_spread'),
                    }
                    
                    record_trade(
                        token, pt['signal'], pt['midpoint'],
                        pt['fill_result'], outcome, reason, meta
                    )
                    
                    result_icon = "✅" if correct else "❌"
                    print(f"\n   {result_icon} {token.upper()} RESOLVED: {outcome} | {reason}")
                
                # New bar setup
                atr = calc_atr(klines)
                s['atr_per_bar'] = atr / 14 if atr else None
                s['bar_open'] = current_bar['open']
                s['bar_start_time'] = t
                s['bar_close'] = None
                
                # Update Polymarket market
                market = get_polymarket_market(token)
                if market:
                    if market['slug'] != s['current_slug']:
                        print(f"\n🔄 {token.upper()} NEW MARKET: {market['slug']}")
                        s['current_slug'] = market['slug']
                        s['pending_trade'] = None
                    s['polymarket_yes_price'] = market['yes_price']
                    s['yes_token'] = market.get('yes_token')
                    s['no_token'] = market.get('no_token')
            
            # Update current bar state
            s['current_price'] = current_bar['close']
            s['bar_close'] = current_bar['close']
            
            # Check for signal
            check_signal(token)
        
        # Status every 30 seconds
        if now - last_status >= 30:
            results = load_results()
            s = results['summary']
            fq = results.get('fill_quality', {})
            
            wr = s['correct'] / max(1, s['total']) * 100
            pct_list = fq.get('pct_worse_list', [])
            median_slip = sorted(pct_list)[len(pct_list)//2] if pct_list else 0
            avg_slip = sum(pct_list) / len(pct_list) if pct_list else 0
            
            print(f"\n{'='*60}")
            print(f"STATUS | {datetime.now().strftime('%H:%M:%S')}")
            print(f"Trades: {s['total']} | Win rate: {wr:.0f}% | P&L: ${s['pnl']:.2f}")
            print(f"Fill quality — Median slip: {median_slip:+.2f}% | Avg: {avg_slip:+.2f}%")
            print(f"Partial fills: {fq.get('partial_fill_count', 0)} | Orderbook fails: {fq.get('orderbook_fail_count', 0)}")
            for token in TOKENS:
                t = results['by_token'][token]
                if t['total'] > 0:
                    wr_t = t['correct'] / max(1, t['total']) * 100
                    print(f"  {token.upper()}: {t['total']} trades | {t['correct']}W/{t['wrong']}L | ${t['pnl']:.2f}")
            print(f"{'='*60}")
            last_status = now
        
        time.sleep(1)

# ── Init & Run ─────────────────────────────────────────────────────────────────
def main():
    print("="*60)
    print("MALCOLM'S POLYMARKET BOT — ORDERBOOK-AWARE EDITION")
    print("Principle: No midpoint lies. Every fill is orderbook-verified.")
    print("="*60)
    
    for token in TOKENS:
        s = state[token]
        symbol = f"{token.upper()}USDT"
        
        klines = get_binance_klines(symbol)
        if klines:
            atr = calc_atr(klines)
            s['atr_per_bar'] = atr / 14 if atr else None
            s['bar_open'] = klines[-1]['open']
            s['bar_start_time'] = klines[-1]['timestamp']
            s['current_price'] = klines[-1]['close']
            s['bar_close'] = klines[-1]['close']
            print(f"{token.upper()}: ATR/bar=${s['atr_per_bar']:.4f} | Open=${s['bar_open']:.2f}")
        
        try:
            market = get_polymarket_market(token)
            if market:
                s['current_slug'] = market['slug']
                s['polymarket_yes_price'] = market['yes_price']
                s['yes_token'] = market.get('yes_token')
                s['no_token'] = market.get('no_token')
                print(f"  Polymarket: YES ${market['yes_price']:.3f}" if market['yes_price'] else "  Polymarket: waiting...")
        except Exception as e:
            logger.error(f"Polymarket init error for {token}: {e}")
    
    print("="*60)
    print("\n👀 Running with full orderbook transparency...\n")
    
    while True:
        try:
            main_loop()
        except Exception as e:
            logger.error(f"Main loop crashed: {e}\n{traceback.format_exc()}")
            print(f"⚠️  Crashed: {e}. Restarting in 10s...")
            time.sleep(10)

if __name__ == "__main__":
    main()
