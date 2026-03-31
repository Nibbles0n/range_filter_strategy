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
DASHBOARD_DATA_FILE = Path("/home/picaso/.openclaw/workspace/range_filter_strategy/dashboard_data.json")
DASHBOARD_HTML_FILE = Path("/home/picaso/.openclaw/workspace/range_filter_strategy/index.html")

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
# === CORE TRADING ===
INITIAL_CAPITAL = 50         # Starting capital in dollars
RISK_PCT = 0.02             # Max % of account risked per trade (2% = $1 on $50)
POLYMARKET_FEE = 0.01        # 1% on winnings
MAX_ENTRY_PRICE = 0.60       # Skip if YES > this (filters expensive entries)
SLIPPAGE_THRESHOLD = 0.03   # Max 3% worse than midpoint we'll accept
MIN_POSITION = 1              # Minimum $ to bother trading
PLACE_LIVE_ORDERS = False   # True = real Polymarket orders (requires funded account)

# === TOKENS — Research-verified momentum signals (2026-03-31) ===
# BTC: INVERSE momentum — 47.8% WR, larger deviations predict LOSSES → DROPPED
# XRP: NOISE — 43% WR, deviation tells us nothing about direction → DROPPED
# DOGE: NOISE — 56% WR but Δ=+0.07, statistical noise → PAUSED
# SOL: STRONG momentum — 54.5% WR, Δ=+3.17, large deviations predict WIN → KEEP
# ETH: WEAK momentum — 52.6% WR, Δ=+0.60 → KEEP
TOKENS = ['eth', 'sol']

# === CONVICTION SIZING — volatility-adjusted deviation ratio ===
# SOL/ETH momentum is STRONGER when deviations are larger
# Scale up position when deviation/threshold is high
CONVICTION_MULTIPLIER_ENABLED = True
CONVICTION_THRESHOLD_2X = 2.5   # deviation/threshold > 2.5x → 1.5x position
CONVICTION_THRESHOLD_3X = 3.5   # deviation/threshold > 3.5x → 2.0x position

# === TELEGRAM ===
TELEGRAM_BOT_TOKEN = ""  # Get from @BotFather
TELEGRAM_CHAT_ID = ""     # Get from @userinfobot

# === TOKEN FILTER ===
TOKEN_FILTER_ENABLED = True
TOKEN_FILTER_MIN_TRADES = 20
TOKEN_FILTER_MIN_WIN_RATE = 0.40

# === DASHBOARD ===
DASHBOARD_ENABLED = True

# === BALANCE MILESTONES ===
BALANCE_MILESTONES = [1.10, 1.25, 1.50, 2.0]  # Multiples of initial capital
DRAWDOWN_ALERT_THRESHOLD = 0.20  # Alert if balance drops 20% from peak

# Computed: max position from risk rule
# NOTE: This is calculated dynamically from current account balance in the loop
# MAX_POSITION = max(INITIAL_CAPITAL * RISK_PCT, MIN_POSITION)

# ── Telegram Alerting ─────────────────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    """Send a message via Telegram bot. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False

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
def calculate_adaptive_position_size(orderbook: dict, side: str, midpoint: float, max_budget: float) -> dict:
    """
    Determine the maximum position size we can take while keeping slippage
    under SLIPPAGE_THRESHOLD, capped at max_budget.
    
    Walks the orderbook level by level, tracking cumulative slippage.
    Stops when filling more would exceed the slippage threshold or our budget.
    
    Returns:
        max_dollar_amount: maximum $ we can safely deploy
        shares_at_threshold: shares we'd have at that amount
        vwap_at_threshold: realistic VWAP at that amount
        pct_worse_at_threshold: slippage at that amount
    """
    if orderbook is None or midpoint is None or midpoint <= 0:
        return {
            "max_dollar_amount": 0,
            "shares_at_threshold": 0,
            "vwap_at_threshold": None,
            "pct_worse_at_threshold": None,
            "liquidity_flags": ["orderbook_fetch_failed"],
        }
    
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    liquidity_flags = []
    
    # Determine worst acceptable price (midpoint * (1 + threshold))
    worst_acceptable = midpoint * (1 + SLIPPAGE_THRESHOLD)
    
    # Calculate spread for quality logging
    if bids and asks:
        spread = float(asks[0]["price"]) - float(bids[0]["price"])
        spread_pct = spread / midpoint * 100
        if spread_pct > 5:
            liquidity_flags.append("wide_spread")
        if spread_pct > 10:
            liquidity_flags.append("very_wide_spread")
    else:
        liquidity_flags.append("no_bbo")
    
    # Get the relevant levels
    if side == "BUY":
        levels = sorted(asks, key=lambda x: float(x["price"]))  # low→high
    else:
        levels = sorted(bids, key=lambda x: float(x["price"]), reverse=True)  # high→low
    
    # Walk the book and find max position at threshold
    max_dollars = 0
    total_cost = 0.0
    shares_filled = 0
    vwap_at_max = midpoint  # start at midpoint
    
    for level in levels:
        price = float(level["price"])
        size = float(level["size"])
        
        # Stop if this level is already worse than our threshold
        if price > worst_acceptable:
            break
        
        # How much can we fill at this level?
        fill_value = price * size  # dollar value of all shares at this level
        fill_shares = size
        
        # Cap at max_budget
        remaining_budget = max_budget - max_dollars
        if remaining_budget <= 0:
            break
        
        actual_fill_value = min(fill_value, remaining_budget)
        actual_fill_shares = actual_fill_value / price
        
        max_dollars += actual_fill_value
        total_cost += actual_fill_value
        shares_filled += actual_fill_shares
    
    if shares_filled > 0:
        vwap_at_max = total_cost / shares_filled
    else:
        vwap_at_max = worst_acceptable  # worst case if nothing filled at threshold
    
    pct_worse = (vwap_at_max - midpoint) / midpoint * 100
    
    # Determine liquidity flags
    if max_dollars < MIN_POSITION:
        liquidity_flags.append("insufficient_liquidity")
    if pct_worse > SLIPPAGE_THRESHOLD * 100:
        liquidity_flags.append("exceeds_slippage_threshold")
    
    return {
        "max_dollar_amount": round(max_dollars, 2),
        "shares_at_threshold": round(shares_filled, 4),
        "vwap_at_threshold": round(vwap_at_max, 4),
        "pct_worse_at_threshold": round(pct_worse, 2),
        "worst_acceptable_price": round(worst_acceptable, 4),
        "midpoint": round(midpoint, 4),
        "liquidity_flags": liquidity_flags if liquidity_flags else ["ok"],
        "spread_pct": round(spread_pct, 2) if 'spread_pct' in dir() else None,
    }


def calculate_realistic_fill(orderbook: dict, side: str, dollar_amount: float, midpoint: float = None) -> dict:
    """
    Walk the orderbook to calculate realistic fill price for a FIXED dollar amount.
    
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
        mid_calc = (float(bids[0]["price"]) + float(asks[0]["price"])) / 2
        spread = float(asks[0]["price"]) - float(bids[0]["price"])
        spread_pct = spread / mid_calc * 100
        
        if spread_pct > 5:
            liquidity_flags.append("wide_spread")
        if spread_pct > 10:
            liquidity_flags.append("very_wide_spread")
        
        if midpoint is None:
            midpoint = mid_calc
    else:
        midpoint = midpoint or None
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
# Load initial balance from results file to persist across restarts
_initial_data = None
def _load_initial_state():
    global _initial_data
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE) as f:
                _initial_data = json.load(f)
        except:
            _initial_data = None

_load_initial_state()
account_balance = (_initial_data.get('account_balance', INITIAL_CAPITAL) 
                   if _initial_data else INITIAL_CAPITAL)

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

# ── Balance Milestone Tracking ────────────────────────────────────────────────
_balance_milestones_achieved = set()
_peak_balance = account_balance
_drawdown_alerted = False

def check_balance_milestones():
    """Check for balance milestones and send alerts."""
    global _balance_milestones_achieved, _peak_balance, account_balance
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    # Update peak
    if account_balance > _peak_balance:
        _peak_balance = account_balance
    
    # Check milestones (multiples of initial capital)
    for milestone in BALANCE_MILESTONES:
        target = INITIAL_CAPITAL * milestone
        if account_balance >= target and milestone not in _balance_milestones_achieved:
            _balance_milestones_achieved.add(milestone)
            pct = int((account_balance / INITIAL_CAPITAL - 1) * 100)
            msg = f"🎉 <b>Balance Milestone!</b>\n\n💰 ${account_balance:.2f}\n(+{pct}% from start)\n\nKeep it up! 🚀"
            send_telegram(msg)
    
    # Drawdown alert
    global _drawdown_alerted
    if _peak_balance > 0:
        drawdown = (_peak_balance - account_balance) / _peak_balance
        if drawdown >= DRAWDOWN_ALERT_THRESHOLD and not _drawdown_alerted:
            _drawdown_alerted = True
            pct = int(drawdown * 100)
            msg = f"⚠️ <b>Drawdown Alert</b>\n\n💰 Current: ${account_balance:.2f}\n📈 Peak: ${_peak_balance:.2f}\n📉 Drawdown: {pct}%"
            send_telegram(msg)
        elif drawdown < DRAWDOWN_ALERT_THRESHOLD * 0.5:
            # Reset if recovered past half the threshold
            _drawdown_alerted = False

# ── Token Performance Filter ──────────────────────────────────────────────────
_paused_tokens = {}  # token -> {paused_at, recent_wr, recent_trades}

def get_token_recent_wr(token: str) -> tuple:
    """Get win rate over last TOKEN_FILTER_MIN_TRADES for a token."""
    if not RESULTS_FILE.exists():
        return None, []
    
    try:
        with open(RESULTS_FILE) as f:
            results = json.load(f)
    except:
        return None, []
    
    # Get last N trades for this token
    token_trades = [t for t in results.get('trades', []) if t.get('token') == token]
    recent = token_trades[-TOKEN_FILTER_MIN_TRADES:] if len(token_trades) >= TOKEN_FILTER_MIN_TRADES else token_trades
    
    if len(recent) < TOKEN_FILTER_MIN_TRADES:
        return None, recent
    
    wins = sum(1 for t in recent if t.get('outcome') == 'WIN')
    wr = wins / len(recent)
    return wr, recent

def is_token_paused(token: str) -> bool:
    """Check if a token is currently paused due to poor performance."""
    if not TOKEN_FILTER_ENABLED:
        return False
    return token in _paused_tokens

def check_and_update_token_filter():
    """Update paused tokens list based on recent performance."""
    if not TOKEN_FILTER_ENABLED:
        return
    
    for token in TOKENS:
        wr, recent = get_token_recent_wr(token)
        
        if wr is not None and len(recent) >= TOKEN_FILTER_MIN_TRADES:
            if wr < TOKEN_FILTER_MIN_WIN_RATE:
                if token not in _paused_tokens:
                    _paused_tokens[token] = {
                        'paused_at': datetime.now().isoformat(),
                        'recent_wr': wr,
                        'recent_trades': len(recent)
                    }
                    msg = (f"⚠️ <b>Token Paused</b>\n\n"
                           f"{token.upper()}\n"
                           f"WR: {wr*100:.0f}% over last {len(recent)} trades\n"
                           f"Min WR: {TOKEN_FILTER_MIN_WIN_RATE*100:.0f}%\n\n"
                           f"Will resume when WR recovers.")
                    send_telegram(msg)
            else:
                # Recovered — remove from paused
                if token in _paused_tokens:
                    del _paused_tokens[token]

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
        'account_balance': INITIAL_CAPITAL,
    }
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE) as f:
                data = json.load(f)
                return data
        except:
            pass
    return default

def save_results(results):
    tmp = RESULTS_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(results, f, indent=2)
    tmp.replace(RESULTS_FILE)

# ── Dashboard Generation ──────────────────────────────────────────────────────
def generate_dashboard_html():
    """Generate the web dashboard HTML file."""
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Malcolm's Polymarket Bot</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #e6edf3; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #58a6ff; margin-bottom: 20px; font-size: 1.5rem; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin-bottom: 16px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .stat { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
        .stat-label { color: #8b949e; font-size: 0.75rem; text-transform: uppercase; }
        .stat-value { font-size: 1.5rem; font-weight: bold; margin-top: 4px; }
        .positive { color: #3fb950; }
        .negative { color: #f85149; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 10px; border-bottom: 1px solid #30363d; }
        th { color: #8b949e; font-size: 0.75rem; text-transform: uppercase; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
        .badge-win { background: #3fb95033; color: #3fb950; }
        .badge-lose { background: #f8514933; color: #f85149; }
        .badge-paused { background: #f0883e33; color: #f0883e; }
        .refresh { color: #8b949e; font-size: 0.75rem; margin-top: 10px; text-align: center; }
        .paused-banner { background: #f0883e33; border: 1px solid #f0883e; border-radius: 6px; padding: 12px; margin-bottom: 16px; }
        .paused-banner strong { color: #f0883e; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Malcolm's Polymarket Bot</h1>
        
        <div id="paused-banner" class="paused-banner" style="display:none;">
            <strong>⚠️ Paused Tokens:</strong> <span id="paused-list"></span>
        </div>
        
        <div class="stats">
            <div class="stat">
                <div class="stat-label">Balance</div>
                <div class="stat-value" id="balance">—</div>
            </div>
            <div class="stat">
                <div class="stat-label">Total P&L</div>
                <div class="stat-value" id="pnl">—</div>
            </div>
            <div class="stat">
                <div class="stat-label">Win Rate</div>
                <div class="stat-value" id="wr">—</div>
            </div>
            <div class="stat">
                <div class="stat-label">Total Trades</div>
                <div class="stat-value" id="total">—</div>
            </div>
            <div class="stat">
                <div class="stat-label">Avg Slippage</div>
                <div class="stat-value" id="slip">—</div>
            </div>
            <div class="stat">
                <div class="stat-label">Peak Balance</div>
                <div class="stat-value" id="peak">—</div>
            </div>
        </div>

        <div class="card">
            <h2 style="margin-bottom:12px;font-size:1rem;color:#8b949e;">BY TOKEN</h2>
            <table>
                <thead>
                    <tr>
                        <th>Token</th>
                        <th>Trades</th>
                        <th>W / L</th>
                        <th>Win Rate</th>
                        <th>P&L</th>
                        <th>Avg Pos</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="token-table"></tbody>
            </table>
        </div>

        <div class="card">
            <h2 style="margin-bottom:12px;font-size:1rem;color:#8b949e;">LAST 10 TRADES</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Token</th>
                        <th>Signal</th>
                        <th>Outcome</th>
                        <th>Entry</th>
                        <th>P&L</th>
                    </tr>
                </thead>
                <tbody id="trades-table"></tbody>
            </table>
        </div>

        <div class="refresh">Auto-refresh every 30s • Last updated: <span id="last-update">—</span></div>
    </div>

    <script>
        async function loadDashboard() {
            try {
                const resp = await fetch('dashboard_data.json');
                const data = await resp.json();
                
                // Update stats
                document.getElementById('balance').textContent = '$' + data.account_balance.toFixed(2);
                document.getElementById('pnl').textContent = (data.pnl >= 0 ? '+' : '') + '$' + data.pnl.toFixed(2);
                document.getElementById('pnl').className = 'stat-value ' + (data.pnl >= 0 ? 'positive' : 'negative');
                document.getElementById('wr').textContent = data.win_rate.toFixed(1) + '%';
                document.getElementById('total').textContent = data.total_trades;
                document.getElementById('slip').textContent = (data.avg_slippage >= 0 ? '+' : '') + data.avg_slippage.toFixed(2) + '%';
                document.getElementById('peak').textContent = '$' + data.peak_balance.toFixed(2);
                document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
                
                // Paused tokens
                const paused = data.paused_tokens || [];
                if (paused.length > 0) {
                    document.getElementById('paused-banner').style.display = 'block';
                    document.getElementById('paused-list').textContent = paused.map(t => t.toUpperCase()).join(', ');
                } else {
                    document.getElementById('paused-banner').style.display = 'none';
                }
                
                // Token table
                const tokenTable = document.getElementById('token-table');
                tokenTable.innerHTML = '';
                for (const [token, t] of Object.entries(data.by_token)) {
                    const wr = t.total > 0 ? (t.correct / t.total * 100).toFixed(0) + '%' : '—';
                    const avgPos = t.avg_position ? '$' + t.avg_position.toFixed(2) : '—';
                    const isPaused = paused.includes(token);
                    const pnlClass = t.pnl >= 0 ? 'positive' : 'negative';
                    const status = isPaused ? '<span class="badge badge-paused">PAUSED</span>' : '';
                    tokenTable.innerHTML += `<tr>
                        <td><strong>${token.toUpperCase()}</strong></td>
                        <td>${t.total || 0}</td>
                        <td>${t.correct || 0} / ${t.wrong || 0}</td>
                        <td>${wr}</td>
                        <td class="${pnlClass}">${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(2)}</td>
                        <td>${avgPos}</td>
                        <td>${status}</td>
                    </tr>`;
                }
                
                // Trades table (last 10)
                const tradesTable = document.getElementById('trades-table');
                tradesTable.innerHTML = '';
                for (const tr of (data.recent_trades || []).slice(-10)) {
                    const outcomeClass = tr.outcome === 'WIN' ? 'badge-win' : 'badge-lose';
                    const pnlClass = tr.pnl >= 0 ? 'positive' : 'negative';
                    const time = new Date(tr.timestamp).toLocaleTimeString();
                    const entry = tr.realistic_fill_vwap ? '$' + tr.realistic_fill_vwap.toFixed(4) : '$' + tr.midpoint_at_signal.toFixed(4);
                    tradesTable.innerHTML += `<tr>
                        <td>${time}</td>
                        <td>${tr.token.toUpperCase()}</td>
                        <td>${tr.signal}</td>
                        <td><span class="badge ${outcomeClass}">${tr.outcome}</span></td>
                        <td>${entry}</td>
                        <td class="${pnlClass}">${tr.pnl >= 0 ? '+' : ''}$${tr.pnl.toFixed(2)}</td>
                    </tr>`;
                }
            } catch (e) {
                console.error('Dashboard load failed:', e);
            }
        }
        
        loadDashboard();
        setInterval(loadDashboard, 30000);
    </script>
</body>
</html>'''
    
    with open(DASHBOARD_HTML_FILE, 'w') as f:
        f.write(html)

def update_dashboard_data():
    """Write current data to dashboard_data.json for the HTML to read."""
    if not DASHBOARD_ENABLED:
        return
    
    results = load_results()
    
    # Calculate per-token avg position
    by_token = {}
    for token in TOKENS:
        t = results['by_token'].get(token, {'total': 0, 'correct': 0, 'wrong': 0, 'pnl': 0})
        token_trades = [tr for tr in results['trades'] if tr.get('token') == token]
        positions = [tr.get('dollar_amount', 0) for tr in token_trades]
        avg_pos = sum(positions) / len(positions) if positions else 0
        by_token[token] = {
            **t,
            'avg_position': avg_pos
        }
    
    # Fill quality stats
    fq = results.get('fill_quality', {})
    pct_list = fq.get('pct_worse_list', [])
    avg_slip = sum(pct_list) / len(pct_list) if pct_list else 0
    
    # Summary stats
    s = results['summary']
    total = s['total']
    win_rate = (s['correct'] / max(1, total) * 100)
    
    dashboard_data = {
        'account_balance': account_balance,
        'pnl': s['pnl'],
        'win_rate': win_rate,
        'total_trades': total,
        'avg_slippage': avg_slip,
        'peak_balance': _peak_balance,
        'by_token': by_token,
        'recent_trades': results['trades'][-10:],
        'paused_tokens': list(_paused_tokens.keys()),
        'timestamp': datetime.now().isoformat()
    }
    
    tmp = DASHBOARD_DATA_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(dashboard_data, f, indent=2)
    tmp.replace(DASHBOARD_DATA_FILE)

# ── Trade Recording ────────────────────────────────────────────────────────────
def record_trade(token, signal, entry_price, fill_result, outcome, reason, meta):
    """
    Record a trade with FULL TRANSPARENCY.
    
    fill_result: dict from calculate_realistic_fill()
    entry_price: midpoint price at signal time
    meta: dict with additional context including adaptive_position
    """
    global account_balance, _peak_balance
    results = load_results()
    
    # Use adaptive position size from signal time
    actual_position = meta.get('adaptive_position', meta.get('dollar_amount', MIN_POSITION))
    
    # Calculate P&L from REALISTIC FILL, not midpoint
    if fill_result["vwap"] and fill_result["vwap"] > 0:
        effective_entry = fill_result["vwap"]
    else:
        effective_entry = entry_price  # fallback if no orderbook
    
    if outcome == 'WIN':
        # Win: we get 1/effective_entry shares for our dollar amount
        # Net: actual_position * (1/effective_entry - 1) * (1 - fee)
        pnl = actual_position * (1/effective_entry - 1) * (1 - POLYMARKET_FEE)
    else:
        # Loss: lose our actual bet amount
        pnl = -actual_position
    
    # Update account balance
    account_balance += pnl
    
    # Update peak balance
    if account_balance > _peak_balance:
        _peak_balance = account_balance
    
    trade = {
        'timestamp': datetime.now().isoformat(),
        'token': token,
        'signal': signal,
        
        # Entry price transparency
        'midpoint_at_signal': entry_price,
        'realistic_fill_vwap': fill_result.get("vwap"),
        'pct_worse_than_midpoint': fill_result.get("pct_worse_than_midpoint"),
        'orderbook_spread': meta.get('orderbook_spread'),
        
        # Fill details — adaptive sizing
        'shares_filled': fill_result.get("shares_filled"),
        'dollar_amount': actual_position,
        'max_slippage_threshold_pct': SLIPPAGE_THRESHOLD * 100,
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
    
    # Persist account balance
    results['account_balance'] = round(account_balance, 2)
    
    save_results(results)
    
    # Update token filter
    check_and_update_token_filter()
    
    # Update dashboard
    update_dashboard_data()
    
    # Check balance milestones
    check_balance_milestones()
    
    # Send Telegram alerts
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        # Trade resolved alert
        if outcome == 'WIN':
            msg = (f"✅ <b>WIN</b>\n"
                   f"{token.upper()} {signal}\n"
                   f"P&L: +${pnl:.2f}\n"
                   f"Balance: ${account_balance:.2f}")
        else:
            msg = (f"❌ <b>LOSE</b>\n"
                   f"{token.upper()} {signal}\n"
                   f"P&L: ${pnl:.2f}\n"
                   f"Balance: ${account_balance:.2f}")
        send_telegram(msg)
    
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
    
    # Check if token is paused
    if is_token_paused(token):
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
    
    # Calculate max position from current account balance × risk %
    max_from_risk = account_balance * RISK_PCT
    effective_max_position = max(max_from_risk, MIN_POSITION)
    
    # Conviction multiplier: SOL/ETH momentum is stronger at high deviations
    # Volatility-adjusted deviation ratio tells us how strong the signal is
    conviction_multiplier = 1.0
    if CONVICTION_MULTIPLIER_ENABLED:
        deviation_ratio = deviation / threshold if threshold > 0 else 1.0
        if deviation_ratio >= CONVICTION_THRESHOLD_3X:
            conviction_multiplier = 2.0
        elif deviation_ratio >= CONVICTION_THRESHOLD_2X:
            conviction_multiplier = 1.5
        # Also boost slightly for SOL since it has the strongest momentum signal
        if token == 'sol' and deviation_ratio >= 2.0:
            conviction_multiplier = max(conviction_multiplier, 1.5)
    
    # Apply conviction multiplier to max position
    effective_max_position = min(effective_max_position * conviction_multiplier, max_from_risk * 2.0)
    
    # First: calculate adaptive position size based on liquidity (capped by risk)
    adaptive = calculate_adaptive_position_size(orderbook, side='BUY', midpoint=midpoint, max_budget=effective_max_position)
    # Position is min of what the orderbook allows AND what our risk rule allows
    actual_position = min(adaptive["max_dollar_amount"], effective_max_position)
    
    # Skip if not enough liquidity at our slippage threshold
    if actual_position < MIN_POSITION:
        logger.debug(f"{token.upper()}: Insufficient liquidity — ${actual_position:.2f} < ${MIN_POSITION} min")
        return
    
    # Calculate realistic fill for our adaptive position size
    fill_result = calculate_realistic_fill(orderbook, side='BUY', dollar_amount=actual_position, midpoint=midpoint)
    
    # Get orderbook spread for logging
    orderbook_spread = None
    if orderbook:
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        if bids and asks:
            orderbook_spread = round(float(asks[0]["price"]) - float(bids[0]["price"]), 4)
    
    # Record pending trade with FULL transparency
    deviation_ratio = deviation / threshold if threshold > 0 else 1.0
    s['pending_trade'] = {
        'signal': direction,
        'expected': expected,
        'midpoint': midpoint,
        'fill_result': fill_result,
        'adaptive_position': actual_position,
        'conviction_multiplier': conviction_multiplier,
        'deviation_ratio': deviation_ratio,
        'max_acceptable_slippage': SLIPPAGE_THRESHOLD * 100,
        'bar_open': s['bar_open'],
        'atr_per_bar': s['atr_per_bar'],
        'deviation': deviation,
        'threshold': threshold,
        'time_remaining': time_remaining,
        'orderbook_spread': orderbook_spread,
        'market': market,
    }
    
    # Log the signal with adaptive sizing info
    vwap = fill_result.get("vwap")
    pct = fill_result.get("pct_worse_than_midpoint")
    flags = fill_result.get("liquidity_flags", [])
    
    conv_str = f" | {conviction_multiplier:.1f}x conviction" if conviction_multiplier > 1.0 else ""
    if vwap:
        fill_msg = f"→ Pos: ${actual_position:.2f}{conv_str} | VWAP: ${vwap:.4f} ({pct:+.2f}% vs mid) | {flags}"
    else:
        fill_msg = f"→ Pos: ${actual_position:.2f}{conv_str} | ⚠️ ORDERBOOK UNAVAILABLE"
    
    print(f"\n🚦 {token.upper()} {direction} SIGNAL | Dev: ${deviation:.2f} > ${threshold:.4f} ({deviation_ratio:.1f}x ratio)")
    print(f"   Midpoint: ${midpoint:.4f} | {fill_msg}")
    print(f"   Time left: {time_remaining:.1f}m | Bar open: ${s['bar_open']:.2f}")
    
    # Send Telegram signal alert
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        msg = (f"🚦 <b>Signal Detected</b>\n"
               f"{token.upper()} {direction}\n"
               f"Entry: ${midpoint:.4f}\n"
               f"Position: ${actual_position:.2f}\n"
               f"Time left: {time_remaining:.1f}m")
        send_telegram(msg)

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main_loop():
    last_status = 0
    last_filter_check = 0
    
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
                    
                    # Record trade with realistic fill + adaptive sizing
                    meta = {
                        'bar_open': pt['bar_open'],
                        'bar_close': s['bar_close'],
                        'deviation': pt['deviation'],
                        'threshold': pt['threshold'],
                        'time_remaining': pt.get('time_remaining'),
                        'orderbook_spread': pt.get('orderbook_spread'),
                        'adaptive_position': pt.get('adaptive_position', MIN_POSITION),
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
        
        # Periodic filter check (every 5 minutes)
        if now - last_filter_check >= 300:
            check_and_update_token_filter()
            update_dashboard_data()
            last_filter_check = now
        
        # Status every 30 seconds
        if now - last_status >= 30:
            results = load_results()
            s = results['summary']
            fq = results.get('fill_quality', {})
            
            wr = s['correct'] / max(1, s['total']) * 100
            pct_list = fq.get('pct_worse_list', [])
            median_slip = sorted(pct_list)[len(pct_list)//2] if pct_list else 0
            avg_slip = sum(pct_list) / len(pct_list) if pct_list else 0
            
            # Calculate avg position size from trades
            positions = [tr['dollar_amount'] for tr in results['trades'] if 'dollar_amount' in tr]
            avg_pos = sum(positions) / max(1, len(positions))
            max_risk_pos = account_balance * RISK_PCT
            
            print(f"\n{'='*60}")
            print(f"STATUS | {datetime.now().strftime('%H:%M:%S')}")
            print(f"Balance: ${account_balance:.2f} | Risk: {RISK_PCT*100:.0f}% = max ${max_risk_pos:.2f}/trade")
            print(f"Trades: {s['total']} | Win rate: {wr:.0f}% | P&L: ${s['pnl']:.2f}")
            print(f"Avg position: ${avg_pos:.2f} | Slip: {median_slip:+.2f}% median | {avg_slip:+.2f}% avg")
            print(f"Skipped (thin): {fq.get('orderbook_fail_count', 0)} | Partial fills: {fq.get('partial_fill_count', 0)}")
            
            # Show paused tokens
            if _paused_tokens:
                paused_list = list(_paused_tokens.keys())
                print(f"⏸️  PAUSED TOKENS: {', '.join(paused_list)}")
            
            for token in TOKENS:
                t = results['by_token'][token]
                if t['total'] > 0:
                    wr_t = t['correct'] / max(1, t['total']) * 100
                    tok_positions = [tr['dollar_amount'] for tr in results['trades'] if tr.get('token') == token and 'dollar_amount' in tr]
                    avg_tok_pos = sum(tok_positions) / max(1, len(tok_positions))
                    is_paused = " [PAUSED]" if token in _paused_tokens else ""
                    print(f"  {token.upper()}{is_paused}: {t['total']} trades | avg ${avg_tok_pos:.2f}/trade | {t['correct']}W/{t['wrong']}L | ${t['pnl']:.2f}")
            print(f"{'='*60}")
            last_status = now
        
        time.sleep(1)

# ── Init & Run ─────────────────────────────────────────────────────────────────
def main():
    print("="*60)
    print("MALCOLM'S POLYMARKET BOT — ORDERBOOK-AWARE EDITION")
    print("Principle: No midpoint lies. Every fill is orderbook-verified.")
    print(f"Risk: {RISK_PCT*100:.0f}% per trade = max ${INITIAL_CAPITAL*RISK_PCT:.2f}/trade on ${INITIAL_CAPITAL}")
    print(f"Slippage threshold: {SLIPPAGE_THRESHOLD*100:.0f}% | Min position: ${MIN_POSITION}")
    print(f"Token filter: {'ON' if TOKEN_FILTER_ENABLED else 'OFF'} (WR<{TOKEN_FILTER_MIN_WIN_RATE*100:.0f}% after {TOKEN_FILTER_MIN_TRADES} trades → pause)")
    print(f"Dashboard: {'ON' if DASHBOARD_ENABLED else 'OFF'}")
    print(f"Telegram: {'ON' if TELEGRAM_BOT_TOKEN else 'OFF'}")
    print("="*60)
    
    # Generate dashboard HTML
    if DASHBOARD_ENABLED:
        generate_dashboard_html()
        print("📊 Dashboard generated: index.html")
    
    # Load persisted state
    results = load_results()
    global account_balance, _peak_balance
    account_balance = results.get('account_balance', INITIAL_CAPITAL)
    _peak_balance = account_balance
    print(f"💰 Loaded balance: ${account_balance:.2f}")
    
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
    
    # Initial dashboard update
    if DASHBOARD_ENABLED:
        update_dashboard_data()
    
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
