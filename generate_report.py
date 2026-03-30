#!/usr/bin/env python3
"""
Malcolm's Polymarket Bot — Performance Report Generator
Run standalone: python3 generate_report.py

Outputs to stdout AND saves to reports/YYYY-MM-DD.md
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from statistics import mean, median, stdev

# ── Config ────────────────────────────────────────────────────────────────────
RESULTS_FILE = Path(__file__).parent / "orderbook_results.json"
REPORTS_DIR  = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

INITIAL_CAPITAL = 50.0
POLYMARKET_FEE  = 0.01   # 1% on winnings

# Min trades before we trust a token's WR
MIN_TRADES_FOR_TRUST = 10

# Confidence level for WR CI
CI_LEVEL = 0.95


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_results():
    if not RESULTS_FILE.exists():
        return None
    with open(RESULTS_FILE) as f:
        return json.load(f)


def normal_ci(wins: int, total: int, level: float = 0.95) -> tuple:
    """Wilson score interval for win rate with confidence interval."""
    if total == 0:
        return 0.0, (0.0, 1.0)
    p = wins / total
    z = 1.96 if level == 0.95 else 1.645  # z-score
    denom = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denom
    half_width = (z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))) / denom
    lo = max(0.0, centre - half_width)
    hi = min(1.0, centre + half_width)
    return p, (lo, hi)


def binom_pvalue(wins: int, total: int, p0: float = 0.5) -> float:
    """Two-sided binomial test: probability of seeing >= wins given true p=p0."""
    # Use normal approximation for large samples
    n = total
    k = wins
    p = p0
    if n == 0:
        return 1.0
    mean = n * p
    sd = math.sqrt(n * p * (1 - p))
    if sd == 0:
        return 1.0
    # Continuity correction
    z = (k - mean) / sd
    # Approximate two-sided p-value from standard normal
    from statistics import NormalDist
    nd = NormalDist()
    return 2 * (1 - nd.cdf(abs(z)))


def is_wr_significant(wins: int, total: int, threshold: float = 0.5,
                      level: float = 0.95) -> bool:
    """Return True if WR is significantly above threshold at given confidence."""
    _, (lo, _) = normal_ci(wins, total, level)
    return lo > threshold


def format_pnl(amount: float) -> str:
    sign = "+" if amount >= 0 else ""
    return f"{sign}${amount:.2f}"


def format_pct(p: float) -> str:
    return f"{p * 100:.2f}%"


def hr(width=80, char="─"):
    return char * width


def section(title: str) -> str:
    return f"\n{title}\n{hr(len(title))}"


# ── Core Computations ─────────────────────────────────────────────────────────
def compute_overview(results: dict) -> dict:
    trades = results.get("trades", [])
    total  = len(trades)
    if total == 0:
        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "wr": 0.0,
            "wr_lo": 0.0,
            "wr_hi": 1.0,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "start_balance": INITIAL_CAPITAL,
            "current_balance": INITIAL_CAPITAL,
            "days_active": 0,
            "avg_pnl_per_day": 0.0,
            "avg_position": 0.0,
            "p_value": 1.0,
        }

    wins   = sum(1 for t in trades if t["outcome"] == "WIN")
    losses = total - wins
    pnl    = sum(t["pnl"] for t in trades)

    wr, ci = normal_ci(wins, total, CI_LEVEL)
    pval    = binom_pvalue(wins, total, 0.5)

    # Balance
    start_balance   = INITIAL_CAPITAL
    current_balance = results.get("account_balance", INITIAL_CAPITAL)

    # Days active
    if trades:
        first_ts = datetime.fromisoformat(trades[0]["timestamp"])
        last_ts  = datetime.fromisoformat(trades[-1]["timestamp"])
        days_active = max(1, (last_ts - first_ts).days + 1)
    else:
        days_active = 1

    total_pnl   = pnl
    pnl_pct     = (total_pnl / start_balance) * 100
    avg_pnl_day = total_pnl / days_active

    # Avg position
    positions = [t["dollar_amount"] for t in trades if t.get("dollar_amount")]
    avg_pos   = mean(positions) if positions else 0.0

    return {
        "total":            total,
        "wins":             wins,
        "losses":           losses,
        "wr":               wr,
        "wr_lo":            ci[0],
        "wr_hi":            ci[1],
        "pnl":              total_pnl,
        "pnl_pct":          pnl_pct,
        "start_balance":    start_balance,
        "current_balance":  current_balance,
        "days_active":      days_active,
        "avg_pnl_per_day":  avg_pnl_day,
        "avg_position":     avg_pos,
        "p_value":          pval,
    }


def compute_by_token(results: dict) -> dict:
    trades = results.get("trades", [])
    by_t   = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0, "slippages": [], "positions": []})

    for t in trades:
        tok = t.get("token", "unknown")
        by_t[tok]["pnl"]      += t.get("pnl", 0)
        by_t[tok]["positions"].append(t.get("dollar_amount", 0))
        if t.get("pct_worse_than_midpoint") is not None:
            by_t[tok]["slippages"].append(t["pct_worse_than_midpoint"])
        if t["outcome"] == "WIN":
            by_t[tok]["wins"] += 1
        else:
            by_t[tok]["losses"] += 1

    out = {}
    for tok, d in by_t.items():
        total = d["wins"] + d["losses"]
        wr, ci = normal_ci(d["wins"], total, CI_LEVEL)
        out[tok] = {
            "trades":        total,
            "wins":          d["wins"],
            "losses":        d["losses"],
            "pnl":           d["pnl"],
            "wr":            wr,
            "wr_lo":         ci[0],
            "wr_hi":         ci[1],
            "avg_slippage":  mean(d["slippages"]) if d["slippages"] else 0.0,
            "avg_position":  mean(d["positions"]) if d["positions"] else 0.0,
            "significant":   is_wr_significant(d["wins"], total) if total >= MIN_TRADES_FOR_TRUST else None,
        }
    return out


def compute_risk_metrics(results: dict) -> dict:
    trades = results.get("trades", [])
    if not trades:
        return {}

    pnls = [t["pnl"] for t in trades]
    cumulative = []
    running = 0.0
    for p in pnls:
        running += p
        cumulative.append(running)

    peak = INITIAL_CAPITAL
    max_dd = 0.0
    current_dd = 0.0
    for c in cumulative:
        peak = max(peak, c + INITIAL_CAPITAL)
        dd   = (peak - (c + INITIAL_CAPITAL)) / peak
        max_dd = max(max_dd, dd)

    current_balance = results.get("account_balance", INITIAL_CAPITAL)
    current_peak   = max(INITIAL_CAPITAL, max(cumulative) + INITIAL_CAPITAL)
    current_dd      = max(0.0, (current_peak - current_balance) / current_peak)

    max_win = max(pnls) if pnls else 0.0
    max_loss = min(pnls) if pnls else 0.0

    # Consecutive streaks
    max_win_streak  = 0
    max_loss_streak = 0
    cur_win  = 0
    cur_loss = 0
    for t in trades:
        if t["outcome"] == "WIN":
            cur_win  += 1
            cur_loss   = 0
            max_win_streak  = max(max_win_streak,  cur_win)
        else:
            cur_loss += 1
            cur_win   = 0
            max_loss_streak = max(max_loss_streak, cur_loss)

    return {
        "max_drawdown":       max_dd,
        "current_drawdown":   current_dd,
        "largest_win":        max_win,
        "largest_loss":       max_loss,
        "max_win_streak":     max_win_streak,
        "max_loss_streak":    max_loss_streak,
    }


def compute_timing(results: dict) -> dict:
    trades = results.get("trades", [])
    if not trades:
        return {}

    # Hour → {wins, total}
    hourly = defaultdict(lambda: {"wins": 0, "total": 0})
    for t in trades:
        try:
            ts  = datetime.fromisoformat(t["timestamp"])
            hr  = ts.hour
            hourly[hr]["total"] += 1
            if t["outcome"] == "WIN":
                hourly[hr]["wins"] += 1
        except Exception:
            pass

    # Best / worst hour
    best_hr  = max(hourly, key=lambda h: (hourly[h]["wins"] / max(1, hourly[h]["total"])))
    worst_hr = min(hourly, key=lambda h: (hourly[h]["wins"] / max(1, hourly[h]["total"])))

    best_wr  = hourly[best_hr]["wins"]  / max(1, hourly[best_hr]["total"])
    worst_wr = hourly[worst_hr]["wins"] / max(1, hourly[worst_hr]["total"])

    total_trades = sum(d["total"] for d in hourly.values())
    avg_per_hr   = total_trades / 24.0

    return {
        "best_hour":       best_hr,
        "best_wr":         best_wr,
        "worst_hour":      worst_hr,
        "worst_wr":        worst_wr,
        "avg_trades_hour": avg_per_hr,
        "hourly":          dict(hourly),
    }


def compute_fill_quality(results: dict) -> dict:
    trades      = results.get("trades", [])
    fq          = results.get("fill_quality", {})
    pct_list    = fq.get("pct_worse_list", [])
    partial_cnt = fq.get("partial_fill_count", 0)
    fail_cnt    = fq.get("orderbook_fail_count", 0)

    # Also count from trade flags
    partial_from_trades = sum(1 for t in trades if not t.get("fully_filled", True))
    fail_from_trades    = sum(1 for t in trades if "orderbook_fetch_failed" in t.get("liquidity_flags", []))

    total = max(1, len(trades))
    return {
        "median_slippage": median(pct_list) if pct_list else 0.0,
        "avg_slippage":    mean(pct_list)   if pct_list else 0.0,
        "best_fill":       min(pct_list)    if pct_list else 0.0,   # most negative = bought cheaper
        "worst_fill":      max(pct_list)    if pct_list else 0.0,
        "partial_rate":    (partial_from_trades + partial_cnt) / total,
        "fail_rate":       (fail_from_trades + fail_cnt) / total,
    }


def compute_recent_performance(results: dict, now: datetime = None) -> dict:
    trades = results.get("trades", [])
    if not trades or now is None:
        return {}

    cutoff_24h = now.timestamp() - 86400
    cutoff_7d  = now.timestamp() - 86400 * 7

    trades_24h = []
    trades_7d  = []
    for t in trades:
        try:
            ts = datetime.fromisoformat(t["timestamp"]).timestamp()
            if ts >= cutoff_24h:
                trades_24h.append(t)
            if ts >= cutoff_7d:
                trades_7d.append(t)
        except Exception:
            pass

    def summarize(label, subset):
        n   = len(subset)
        if n == 0:
            return {"label": label, "trades": 0, "pnl": 0.0, "wr": None}
        wins  = sum(1 for t in subset if t["outcome"] == "WIN")
        wr, _ = normal_ci(wins, n, 0.95)
        pnl   = sum(t["pnl"] for t in subset)
        return {"label": label, "trades": n, "pnl": pnl, "wr": wr}

    overall_wr = None
    overall_pnl = None
    all_trades = trades
    overall_wins = sum(1 for t in all_trades if t["outcome"] == "WIN")
    overall_wr, _ = normal_ci(overall_wins, len(all_trades), 0.95)

    s24 = summarize("Last 24h", trades_24h)
    s7d = summarize("Last 7d",  trades_7d)

    return {
        "last_24h": s24,
        "last_7d":  s7d,
        "overall_wr_for_comparison": overall_wr,
    }


def break_even_wr(fee: float = POLYMARKET_FEE) -> float:
    """
    Win rate needed to break even given Polymarket's fee on winnings.
    If you win $1 99% of the time but lose $1 1% of the time, net = 0.
    Let WR = w. Fee is applied to winnings.
    Net = w * (1 - fee) * win_size - (1-w) * loss_size
    Assuming equal win/loss sizes = 1:
    Net = w*(1-fee) - (1-w) = 0
    w*(1-fee) = 1-w
    w*(1-fee) + w = 1
    w*(2-fee) = 1
    w = 1/(2-fee)
    """
    return 1.0 / (2.0 - fee)


def generate_recommendations(
    results: dict,
    overview: dict,
    by_token: dict,
    risk: dict,
    fill: dict,
    recent: dict,
) -> list:
    recs = []

    # ── 1. Token pause checks ────────────────────────────────────────────────
    for tok, d in sorted(by_token.items()):
        trades = d["trades"]
        if trades < MIN_TRADES_FOR_TRUST:
            continue
        if d["significant"] is False:
            recs.append(
                f"⏸️  PAUSE {tok.upper()}: WR={format_pct(d['wr'])} "
                f"(95% CI: {format_pct(d['wr_lo'])}–{format_pct(d['wr_hi'])}) over {trades} trades. "
                f"CI does not exceed 50%. Pause until recovery."
            )
        elif d["pnl"] < -5.0:
            recs.append(
                f"⚠️  REVIEW {tok.upper()}: P&L=${d['pnl']:.2f} over {trades} trades. "
                f"Confirm strategy is still appropriate."
            )

    # ── 2. Overall WR significance ───────────────────────────────────────────
    if overview["total"] >= MIN_TRADES_FOR_TRUST:
        wr      = overview["wr"]
        wr_lo   = overview["wr_lo"]
        pval    = overview["p_value"]
        be_wr   = break_even_wr()
        if wr_lo > be_wr:
            recs.append(
                f"✅ Overall WR {format_pct(wr)} is statistically above break-even "
                f"{format_pct(be_wr)} (95% CI lower bound: {format_pct(wr_lo)}, p={pval:.4f}). "
                f"Strategy performing within expected bounds."
            )
        elif wr > 0.45:
            recs.append(
                f"🟡 MARGINAL: Overall WR {format_pct(wr)} is below break-even "
                f"{format_pct(be_wr)} but not yet statistically below. "
                f"Continue monitoring (p={pval:.4f})."
            )
        else:
            recs.append(
                f"🔴 RED FLAG: Overall WR {format_pct(wr)} is statistically below "
                f"break-even {format_pct(be_wr)}. Review strategy immediately."
            )
    else:
        recs.append(f"ℹ️  Not enough trades ({overview['total']}) for statistical significance testing.")

    # ── 3. Drawdown check ────────────────────────────────────────────────────
    if risk.get("max_drawdown", 0) > 0.30:
        recs.append(
            f"⚠️  MAX DRAWDOWN {risk['max_drawdown']*100:.1f}% exceeds 30%. "
            f"Consider reducing position size."
        )

    # ── 4. Fill quality red flags ───────────────────────────────────────────
    if fill.get("fail_rate", 0) > 0.05:
        recs.append(
            f"⚠️  ORDERBOOK FAIL RATE {fill['fail_rate']*100:.1f}% > 5%. "
            f"Check network/API connectivity."
        )
    worst = fill.get("worst_fill", 0)
    if worst > 20:
        recs.append(
            f"⚠️  WORST FILL {worst:+.2f}% — large slippage events detected. "
            f"Review SLIPPAGE_THRESHOLD and MAX_ENTRY_PRICE settings."
        )

    # ── 5. Recent performance ────────────────────────────────────────────────
    if recent.get("last_24h", {}).get("trades", 0) >= 5:
        r24   = recent["last_24h"]
        comp  = recent.get("overall_wr_for_comparison", 0)
        wr24  = r24.get("wr", 0) or 0
        pnl24 = r24.get("pnl", 0)
        if wr24 < comp - 0.1:
            recs.append(
                f"⚠️  LAST 24H: WR={format_pct(wr24)}, P&L={format_pnl(pnl24)} — "
                f"materially below overall WR {format_pct(comp)}. Monitor closely."
            )
        else:
            recs.append(
                f"ℹ️  LAST 24H: WR={format_pct(wr24)}, P&L={format_pnl(pnl24)} — "
                f"consistent with overall performance."
            )

    # ── 6. Default ───────────────────────────────────────────────────────────
    if not recs:
        recs.append("✅ No critical issues detected. Strategy running within normal parameters.")

    return recs


# ── Report Builder ─────────────────────────────────────────────────────────────
def build_report(results: dict, report_date: str = None) -> str:
    now = datetime.now(timezone.utc)
    report_date = report_date or now.strftime("%Y-%m-%d")

    trades = results.get("trades", [])
    total_trades = len(trades)

    # Date range
    if trades:
        first_ts = datetime.fromisoformat(trades[0]["timestamp"])
        last_ts  = datetime.fromisoformat(trades[-1]["timestamp"])
        date_range = f"{first_ts.strftime('%Y-%m-%d %H:%M')} – {last_ts.strftime('%Y-%m-%d %H:%M')} UTC"
    else:
        date_range = "No trades recorded"

    # Compute all sections
    overview  = compute_overview(results)
    by_token  = compute_by_token(results)
    risk      = compute_risk_metrics(results)
    timing    = compute_timing(results)
    fill      = compute_fill_quality(results)
    recent    = compute_recent_performance(results, now)
    be_wr     = break_even_wr()

    lines = []

    # ── HEADER ────────────────────────────────────────────────────────────────
    lines.append(f"# 📊 Polymarket Bot Performance Report")
    lines.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append(f"**Report Date:** {report_date}")
    lines.append(f"**Data Period:** {date_range}")
    lines.append(f"**Total Trades:** {total_trades}")

    # ── ACCOUNT SUMMARY ───────────────────────────────────────────────────────
    lines.append(section("1️⃣  ACCOUNT SUMMARY"))
    s = overview
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Starting Balance | ${s['start_balance']:.2f} |")
    lines.append(f"| Current Balance  | ${s['current_balance']:.2f} |")
    lines.append(f"| Total P&L       | {format_pnl(s['pnl'])} ({s['pnl_pct']:+.2f}%) |")
    lines.append(f"| Days Trading    | {s['days_active']} |")
    lines.append(f"| Avg P&L / Day   | {format_pnl(s['avg_pnl_per_day'])} |")
    lines.append(f"| Avg Position    | ${s['avg_position']:.2f} |")

    # ── WIN RATE ANALYSIS ─────────────────────────────────────────────────────
    lines.append(section("2️⃣  WIN RATE ANALYSIS"))
    s = overview
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Overall Win Rate | {format_pct(s['wr'])} |")
    lines.append(f"| 95% Confidence Interval | {format_pct(s['wr_lo'])} – {format_pct(s['wr_hi'])} |")
    lines.append(f"| Break-even WR (1% fee) | {format_pct(be_wr)} |")
    lines.append(f"| Statistical Significance | p={s['p_value']:.4f} vs H₀: WR=50% |")
    sig_note = "✅ SIGNIFICANT" if s['p_value'] < 0.05 else "❌ NOT SIGNIFICANT"
    lines.append(f"| Significance (α=0.05) | {sig_note} |")

    # ── PER-TOKEN BREAKDOWN ───────────────────────────────────────────────────
    lines.append(section("3️⃣  PER-TOKEN BREAKDOWN"))
    if by_token:
        lines.append(f"| Token | Trades | W / L | Win Rate | 95% CI | P&L | Avg Slip | Status |")
        lines.append(f"|-------|--------|-------|----------|--------|-----|----------|--------|")
        for tok in sorted(by_token):
            d = by_token[tok]
            status = "✅ Profitable" if d["pnl"] > 0 else ("🔴 Losing" if d["pnl"] < -2 else "⚖️  Near zero")
            if d["significant"] is False:
                status = "⏸️  Pause"
            elif d["significant"] is True:
                status = "✅ Significant"
            sig_flag = ""
            if d["trades"] >= MIN_TRADES_FOR_TRUST:
                if d["significant"] is False:
                    sig_flag = " ⚠️ CI<50%"
                elif d["significant"] is True:
                    sig_flag = ""
            lines.append(
                f"| {tok.upper()} | {d['trades']} | {d['wins']}/{d['losses']} | "
                f"{format_pct(d['wr'])}{sig_flag} | "
                f"{format_pct(d['wr_lo'])}–{format_pct(d['wr_hi'])} | "
                f"{format_pnl(d['pnl'])} | {d['avg_slippage']:+.2f}% | {status} |"
            )
    else:
        lines.append("No token data available.")

    # ── RISK METRICS ──────────────────────────────────────────────────────────
    lines.append(section("4️⃣  RISK METRICS"))
    r = risk
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Max Drawdown | {r.get('max_drawdown', 0)*100:.2f}% |")
    lines.append(f"| Current Drawdown | {r.get('current_drawdown', 0)*100:.2f}% |")
    lines.append(f"| Largest Single Win | {format_pnl(r.get('largest_win', 0))} |")
    lines.append(f"| Largest Single Loss | {format_pnl(r.get('largest_loss', 0))} |")
    lines.append(f"| Max Win Streak | {r.get('max_win_streak', 0)} |")
    lines.append(f"| Max Loss Streak | {r.get('max_loss_streak', 0)} |")

    # ── TRADE TIMING ──────────────────────────────────────────────────────────
    lines.append(section("5️⃣  TRADE TIMING (UTC)"))
    t = timing
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    if t:
        lines.append(f"| Best Hour (UTC) | {t['best_hour']:02d}:00 — WR {format_pct(t['best_wr'])} |")
        lines.append(f"| Worst Hour (UTC) | {t['worst_hour']:02d}:00 — WR {format_pct(t['worst_wr'])} |")
        lines.append(f"| Avg Trades / Hour | {t['avg_trades_hour']:.1f} |")

        # Mini hourly table
        lines.append("\n**Hourly Breakdown:**")
        lines.append(f"| Hour (UTC) | Trades | W / L | Win Rate |")
        lines.append(f"|------------|--------|-------|----------|")
        for hr in sorted(t.get("hourly", {}).keys()):
            hd = t["hourly"][hr]
            wr_h = hd["wins"] / max(1, hd["total"])
            lines.append(f"| {hr:02d}:00 | {hd['total']} | {hd['wins']}/{hd['total']-hd['wins']} | {format_pct(wr_h)} |")
    else:
        lines.append("No timing data available.")

    # ── FILL QUALITY ───────────────────────────────────────────────────────────
    lines.append(section("6️⃣  FILL QUALITY"))
    f = fill
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Median Slippage | {f['median_slippage']:+.2f}% |")
    lines.append(f"| Avg Slippage | {f['avg_slippage']:+.2f}% |")
    lines.append(f"| Best Fill | {f['best_fill']:+.2f}% |")
    lines.append(f"| Worst Fill | {f['worst_fill']:+.2f}% |")
    lines.append(f"| Partial Fill Rate | {f['partial_rate']*100:.1f}% |")
    lines.append(f"| Orderbook Fail Rate | {f['fail_rate']*100:.1f}% |")

    # ── RECENT PERFORMANCE ─────────────────────────────────────────────────────
    lines.append(section("7️⃣  RECENT PERFORMANCE"))
    if recent.get("last_24h", {}).get("trades"):
        r24 = recent["last_24h"]
        lines.append(f"| Last 24h | Value |")
        lines.append(f"|----------|-------|")
        lines.append(f"| Trades | {r24['trades']} |")
        lines.append(f"| P&L | {format_pnl(r24['pnl'])} |")
        lines.append(f"| Win Rate | {format_pct(r24['wr'] or 0)} |")
    else:
        lines.append("No trades in last 24h.")

    if recent.get("last_7d", {}).get("trades"):
        r7d = recent["last_7d"]
        lines.append(f"\n| Last 7d | Value |")
        lines.append(f"|---------|-------|")
        lines.append(f"| Trades | {r7d['trades']} |")
        lines.append(f"| P&L | {format_pnl(r7d['pnl'])} |")
        lines.append(f"| Win Rate | {format_pct(r7d['wr'] or 0)} |")
    else:
        lines.append("\nNo trades in last 7 days.")

    # ── RECOMMENDATIONS ────────────────────────────────────────────────────────
    lines.append(section("8️⃣  RECOMMENDATIONS"))
    recs = generate_recommendations(results, overview, by_token, risk, fill, recent)
    for r in recs:
        lines.append(f"- {r}")

    # ── FOOTER ─────────────────────────────────────────────────────────────────
    lines.append(f"\n---")
    lines.append(f"*Report generated by `generate_report.py` at {now.isoformat()}*")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    results = load_results()
    if results is None:
        msg = "# ❌ No Results Found\n\n`orderbook_results.json` not found. Is the bot running?"
        print(msg)
        # Try to save anyway
        out_path = REPORTS_DIR / f"REPORT_{datetime.now().strftime('%Y-%m-%d')}.md"
        with open(out_path, "w") as f:
            f.write(msg)
        print(f"\nSaved to {out_path}")
        return

    now = datetime.now(timezone.utc)
    report_date = now.strftime("%Y-%m-%d")
    report      = build_report(results, report_date)

    # Print to stdout
    print(report)

    # Save to reports/
    out_path = REPORTS_DIR / f"REPORT_{report_date}.md"
    with open(out_path, "w") as f:
        f.write(report)

    print(f"\n✅ Report saved to: {out_path}")


if __name__ == "__main__":
    main()
