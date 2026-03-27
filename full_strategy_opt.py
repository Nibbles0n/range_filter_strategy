#!/usr/bin/env python3
"""
Full Range Filter Strategy with Hyperliquid Fees
Re-optimizes both entry filters AND exits with real fee structure.
"""

import json
from datetime import datetime, timezone
from typing import List, Tuple, Optional
from itertools import product

# ============ HYPERLIQUID FEES (Tier 0) ============
# https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
MAKER_FEE = 0.00015   # 0.015% for makers (you earn this)
TAKER_FEE = 0.00045   # 0.045% for takers
SLIPPAGE = 0.0001      # 0.01% assumed slippage on market orders

# For makers: fee is negative (you earn)
# For takers: fee is positive (you pay)
# Net cost = entry_fee + exit_fee + slippage

class RangeFilterStrategy:
    def __init__(self, highs, lows, closes, timestamps):
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.timestamps = timestamps
        
    def calculate_range_filter(self, rng_qty=2.618, rng_period=14, smooth_per=27):
        """Calculate Range Filter Type 1."""
        n = len(self.closes)
        if n < rng_period:
            return None, None, None
        
        # Calculate average change
        changes = [abs(self.closes[i] - self.closes[i-1]) for i in range(1, n)]
        
        # EMA of changes
        alpha = 2 / (rng_period + 1)
        ac_ema = [changes[0]]
        for c in changes[1:]:
            ac_ema.append((c - ac_ema[-1]) * alpha + ac_ema[-1])
        
        rng = [0] * rng_period
        for i in range(rng_period, n):
            rng.append(rng_qty * ac_ema[i - rng_period])
        
        # Smooth range
        if len(rng) > smooth_per:
            alpha_s = 2 / (smooth_per + 1)
            for i in range(smooth_per, n):
                rng[i] = (rng[i] - rng[i-1]) * alpha_s + rng[i-1]
        
        # Calculate filter
        filt = [(self.highs[i] + self.lows[i]) / 2 for i in range(n)]
        for i in range(1, n):
            h_r = self.highs[i] - rng[i]
            l_r = self.lows[i] + rng[i]
            if h_r > filt[i-1]:
                filt[i] = h_r
            elif l_r < filt[i-1]:
                filt[i] = l_r
        
        # Bands
        hi_band = [filt[i] + rng[i] for i in range(n)]
        lo_band = [filt[i] - rng[i] for i in range(n)]
        
        return hi_band, lo_band, filt
    
    def get_direction(self, filt):
        """Get filter direction."""
        direction = [0] * len(filt)
        for i in range(1, len(filt)):
            if filt[i] > filt[i-1]:
                direction[i] = 1
            elif filt[i] < filt[i-1]:
                direction[i] = -1
            else:
                direction[i] = direction[i-1]
        return direction
    
    def get_volatility(self, idx, window=14):
        """Calculate volatility at index."""
        if idx < window:
            return 0.0
        returns = []
        for i in range(idx - window, idx):
            ret = (self.closes[i] - self.closes[i-1]) / self.closes[i-1]
            returns.append(abs(ret))
        return sum(returns) / len(returns)
    
    def get_band_width(self, idx, hi_band, lo_band):
        """Calculate band width as % of price."""
        if self.closes[idx] == 0:
            return 0.0
        return (hi_band[idx] - lo_band[idx]) / self.closes[idx]
    
    def get_hour(self, idx):
        """Get hour of day (UTC) for filtering."""
        ts = self.timestamps[idx] / 1000  # Convert ms to seconds
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.hour
    
    def find_signals(self, direction):
        """Find long/short signals from direction changes."""
        signals = []
        for i in range(1, len(direction)):
            if direction[i] == 1 and direction[i-1] == -1:
                signals.append((i, 'long'))
            elif direction[i] == -1 and direction[i-1] == 1:
                signals.append((i, 'short'))
        return signals
    
    def analyze_entry(self, idx, direction, hi_band, lo_band, lookforward=100):
        """Analyze a single entry - what was max potential profit?"""
        sig_type = None
        for i in range(max(1, idx - 5), idx + 1):
            if direction[i] == 1 and direction[i-1] == -1:
                sig_type = 'long'
                break
            elif direction[i] == -1 and direction[i-1] == 1:
                sig_type = 'short'
                break
        
        if sig_type is None:
            return None
        
        entry_price = self.closes[idx]
        
        # Find opposite signal
        end_idx = min(idx + lookforward, len(self.closes))
        exit_idx = end_idx
        
        max_profit = 0
        max_loss = 0
        exit_price = self.closes[end_idx - 1]
        
        for i in range(idx + 1, end_idx):
            if sig_type == 'long':
                pnl_pct = (self.closes[i] - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - self.closes[i]) / entry_price
            
            if pnl_pct > max_profit:
                max_profit = pnl_pct
                max_loss = min(max_loss, pnl_pct)
            
            # Check for opposite signal
            if direction[i] != direction[i-1]:
                exit_idx = i
                exit_price = self.closes[i]
                break
        
        return {
            'idx': idx,
            'type': sig_type,
            'entry_price': entry_price,
            'exit_idx': exit_idx,
            'exit_price': exit_price,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'holding_bars': exit_idx - idx,
            'volatility': self.get_volatility(idx),
            'band_width': self.get_band_width(idx, hi_band, lo_band),
            'hour': self.get_hour(idx)
        }
    
    def analyze_all_entries(self, hi_band, lo_band, filt, direction):
        """Analyze all entries and classify them."""
        signals = self.find_signals(direction)
        entries = []
        
        for idx, _ in signals:
            result = self.analyze_entry(idx, direction, hi_band, lo_band)
            if result:
                entries.append(result)
        
        return entries
    
    def classify_entry(self, entry):
        """Classify entry as excellent/good/neutral/poor."""
        # Excellent: max_profit >= 2.0% AND holding >= 5 bars
        if entry['max_profit'] >= 0.02 and entry['holding_bars'] >= 5:
            return 'excellent'
        # Good: max_profit >= 1.0%
        elif entry['max_profit'] >= 0.01:
            return 'good'
        # Neutral: max_profit >= 0.5%
        elif entry['max_profit'] >= 0.005:
            return 'neutral'
        else:
            return 'poor'
    
    def run_backtest(
        self,
        hi_band, lo_band, filt, direction,
        initial_capital=10000,
        position_pct=0.01,  # 1% of capital per trade
        trail_pct=0.02,      # 2% trailing stop
        isl_pct=0.015,       # 1.5% initial stop
        use_maker=True,      # Use maker fees (limit orders)
        entry_filters=None    # Dict of filter conditions
    ):
        """
        Run backtest with trailing stop exit.
        """
        if entry_filters is None:
            entry_filters = {}
        
        entries = self.analyze_all_entries(hi_band, lo_band, filt, direction)
        
        # Apply filters
        filtered_entries = []
        for e in entries:
            # Volatility filter
            if 'min_volatility' in entry_filters:
                if e['volatility'] < entry_filters['min_volatility']:
                    continue
            # Band width filter
            if 'min_band_width' in entry_filters:
                if e['band_width'] < entry_filters['min_band_width']:
                    continue
            # Hour filter
            if 'allowed_hours' in entry_filters:
                if e['hour'] not in entry_filters['allowed_hours']:
                    continue
            # Excluded hours
            if 'excluded_hours' in entry_filters:
                if e['hour'] in entry_filters['excluded_hours']:
                    continue
            filtered_entries.append(e)
        
        capital = initial_capital
        position_size = capital * position_pct
        wins = 0
        losses = 0
        trades = []
        
        for entry in filtered_entries:
            idx = entry['idx']
            sig_type = entry['type']
            entry_price = entry['entry_price']
            
            # Entry fee (assume maker - limit order)
            entry_fee = position_size * MAKER_FEE if use_maker else position_size * TAKER_FEE
            
            # Find exit using trailing stop
            trail_active = False
            trail_price = 0
            exit_idx = entry['exit_idx']
            exit_reason = 'timeout'
            exit_price = self.closes[exit_idx - 1] if exit_idx < len(self.closes) else self.closes[-1]
            pnl_pct = 0
            
            for i in range(idx + 1, min(idx + 288, len(self.closes))):  # Max 24h (288 5m candles)
                current_price = self.closes[i]
                
                # Check initial stop
                if sig_type == 'long':
                    pnl_pct = (current_price - entry_price) / entry_price
                    # Update trail
                    if not trail_active and pnl_pct >= isl_pct:
                        trail_active = True
                        trail_price = current_price * (1 - trail_pct)
                    
                    if trail_active:
                        # Trail moves up only
                        new_trail = current_price * (1 - trail_pct)
                        if new_trail > trail_price:
                            trail_price = new_trail
                        # Exit if trail hit
                        if current_price <= trail_price:
                            exit_idx = i
                            exit_price = current_price
                            exit_reason = 'trail'
                            break
                    
                    # Exit if ISL hit
                    if pnl_pct <= -isl_pct:
                        exit_idx = i
                        exit_price = current_price
                        exit_reason = 'isl'
                        break
                
                else:  # short
                    pnl_pct = (entry_price - current_price) / entry_price
                    if not trail_active and pnl_pct >= isl_pct:
                        trail_active = True
                        trail_price = current_price * (1 + trail_pct)
                    
                    if trail_active:
                        new_trail = current_price * (1 + trail_pct)
                        if new_trail < trail_price:
                            trail_price = new_trail
                        if current_price >= trail_price:
                            exit_idx = i
                            exit_price = current_price
                            exit_reason = 'trail'
                            break
                    
                    if pnl_pct <= -isl_pct:
                        exit_idx = i
                        exit_price = current_price
                        exit_reason = 'isl'
                        break
                
                # Exit on opposite signal
                if direction[i] != direction[i-1]:
                    exit_idx = i
                    exit_price = current_price
                    exit_reason = 'signal'
                    break
            
            # Calculate P&L
            if sig_type == 'long':
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - exit_price) / entry_price
            
            # Subtract fees (entry + exit)
            total_fees = position_size * (MAKER_FEE + TAKER_FEE)  # Assume both ends
            net_pnl = position_size * pnl_pct - total_fees
            
            capital += net_pnl
            position_size = capital * position_pct  # Recalculate for next trade
            
            trades.append({
                'entry_idx': idx,
                'exit_idx': exit_idx,
                'type': sig_type,
                'exit_reason': exit_reason,
                'pnl_pct': pnl_pct,
                'net_pnl': net_pnl,
                'fees': total_fees
            })
            
            if net_pnl > 0:
                wins += 1
            else:
                losses += 1
        
        total_trades = wins + losses
        
        return {
            'final_capital': capital,
            'total_return': (capital - initial_capital) / initial_capital * 100,
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': wins / total_trades * 100 if total_trades > 0 else 0,
            'trades': trades,
            'entries_before_filter': len(entries),
            'entries_after_filter': len(filtered_entries)
        }


def load_data():
    """Load BTC 5m candles from file."""
    with open("/home/picaso/.openclaw/workspace/range_filter_strategy/btc_4yr.json") as f:
        data = json.load(f)
    
    highs = [c['high'] for c in data]
    lows = [c['low'] for c in data]
    closes = [c['close'] for c in data]
    timestamps = [c['timestamp'] for c in data]
    
    return highs, lows, closes, timestamps


def main():
    print("=" * 70)
    print("RANGE FILTER STRATEGY - FULL OPTIMIZATION WITH HYPERLIQUID FEES")
    print("=" * 70)
    
    print("\nLoading 4 years of BTCUSDT data...")
    highs, lows, closes, timestamps = load_data()
    print(f"Loaded {len(closes)} candles")
    
    # Create strategy
    strategy = RangeFilterStrategy(highs, lows, closes, timestamps)
    
    # Calculate filter
    print("\nCalculating Range Filter...")
    hi_band, lo_band, filt = strategy.calculate_range_filter(
        rng_qty=2.618, rng_period=14, smooth_per=27
    )
    direction = strategy.get_direction(filt)
    
    # Analyze entries
    print("Analyzing entries...")
    entries = strategy.analyze_all_entries(hi_band, lo_band, filt, direction)
    
    # Classify entries
    for e in entries:
        e['classification'] = strategy.classify_entry(e)
    
    # Count by classification
    counts = {'excellent': 0, 'good': 0, 'neutral': 0, 'poor': 0}
    for e in entries:
        counts[e['classification']] += 1
    
    print(f"\n=== ENTRY CLASSIFICATION ===")
    print(f"Total entries: {len(entries)}")
    print(f"Excellent: {counts['excellent']} ({counts['excellent']/len(entries)*100:.1f}%)")
    print(f"Good: {counts['good']} ({counts['good']/len(entries)*100:.1f}%)")
    print(f"Neutral: {counts['neutral']} ({counts['neutral']/len(entries)*100:.1f}%)")
    print(f"Poor: {counts['poor']} ({counts['poor']/len(entries)*100:.1f}%)")
    
    # Hour analysis
    print(f"\n=== HOUR ANALYSIS ===")
    hour_stats = {}
    for e in entries:
        h = e['hour']
        if h not in hour_stats:
            hour_stats[h] = {'excellent': 0, 'total': 0}
        hour_stats[h]['total'] += 1
        if e['classification'] == 'excellent':
            hour_stats[h]['excellent'] += 1
    
    print("Hour (UTC) | Total | Excellent | Rate")
    for h in sorted(hour_stats.keys()):
        total = hour_stats[h]['total']
        exc = hour_stats[h]['excellent']
        rate = exc / total * 100 if total > 0 else 0
        print(f"    {h:02d}:00    | {total:5d} | {exc:9d} | {rate:.1f}%")
    
    # ============ OPTIMIZE ENTRY FILTERS ============
    print(f"\n=== OPTIMIZING ENTRY FILTERS ===")
    
    best_result = None
    best_filters = None
    best_return = -float('inf')
    
    # Test different filter combinations
    volatility_thresholds = [0.0, 0.003, 0.005, 0.006, 0.008, 0.01]
    band_width_thresholds = [0.0, 0.005, 0.01, 0.015, 0.02]
    
    # Test with and without hour filtering
    excluded_hours_sets = [
        None,
        [5],  # Just 5am
        [5, 6],  # 5-6am
        [5, 6, 7],  # Low volatility hours
        list(range(0, 24)),  # All hours (no filtering)
    ]
    
    for min_vol in volatility_thresholds:
        for min_bw in band_width_thresholds:
            for excl_hours in excluded_hours_sets:
                filters = {
                    'min_volatility': min_vol if min_vol > 0 else None,
                    'min_band_width': min_bw if min_bw > 0 else None,
                    'excluded_hours': excl_hours
                }
                # Remove None values
                filters = {k: v for k, v in filters.items() if v is not None}
                
                result = strategy.run_backtest(
                    hi_band, lo_band, filt, direction,
                    initial_capital=10000,
                    position_pct=0.01,
                    trail_pct=0.0225,
                    isl_pct=0.015,
                    entry_filters=filters
                )
                
                if result['total_return'] > best_return:
                    best_return = result['total_return']
                    best_result = result
                    best_filters = filters
    
    print(f"\nBest Entry Filters Found:")
    print(f"  Filters: {best_filters}")
    print(f"  Return: {best_result['total_return']:.2f}%")
    print(f"  Trades: {best_result['total_trades']}")
    print(f"  Win Rate: {best_result['win_rate']:.1f}%")
    print(f"  Entries: {best_result['entries_before_filter']} -> {best_result['entries_after_filter']}")
    
    # ============ OPTIMIZE EXIT (TRAILING STOP) ============
    print(f"\n=== OPTIMIZING EXIT (TRAILING STOP) ===")
    
    trail_pcts = [0.01, 0.015, 0.02, 0.0225, 0.025, 0.03, 0.035, 0.04]
    isl_pcts = [0.005, 0.01, 0.012, 0.015, 0.02]
    
    best_exit_result = None
    best_exit_params = None
    best_exit_return = -float('inf')
    
    for trail in trail_pcts:
        for isl in isl_pcts:
            if isl >= trail:  # ISL must be less than trail
                continue
            
            result = strategy.run_backtest(
                hi_band, lo_band, filt, direction,
                initial_capital=10000,
                position_pct=0.01,
                trail_pct=trail,
                isl_pct=isl,
                entry_filters=best_filters
            )
            
            if result['total_return'] > best_exit_return:
                best_exit_return = result['total_return']
                best_exit_result = result
                best_exit_params = {'trail_pct': trail, 'isl_pct': isl}
    
    print(f"\nBest Exit Parameters Found:")
    print(f"  Trail: {best_exit_params['trail_pct']*100:.2f}%")
    print(f"  ISL: {best_exit_params['isl_pct']*100:.2f}%")
    print(f"  Return: {best_exit_result['total_return']:.2f}%")
    print(f"  Trades: {best_exit_result['total_trades']}")
    print(f"  Win Rate: {best_exit_result['win_rate']:.1f}%")
    
    # ============ FINAL STRATEGY ============
    print(f"\n{'='*70}")
    print("FINAL OPTIMIZED STRATEGY")
    print(f"{'='*70}")
    print(f"\nEntry Filters:")
    for k, v in best_filters.items():
        if k == 'excluded_hours':
            print(f"  Excluded Hours: {v}")
        else:
            print(f"  {k}: {v}")
    print(f"\nExit Strategy:")
    print(f"  Type: Trailing Stop")
    print(f"  Trail: {best_exit_params['trail_pct']*100:.2f}%")
    print(f"  ISL: {best_exit_params['isl_pct']*100:.2f}%")
    print(f"\nResults:")
    print(f"  Return: {best_exit_result['total_return']:.2f}%")
    print(f"  Final Capital: ${best_exit_result['final_capital']:,.2f}")
    print(f"  Total Trades: {best_exit_result['total_trades']}")
    print(f"  Win Rate: {best_exit_result['win_rate']:.1f}%")
    print(f"  Entries Used: {best_exit_result['entries_after_filter']}/{best_exit_result['entries_before_filter']}")
    
    # Save results
    results = {
        'best_entry_filters': best_filters,
        'best_exit_params': best_exit_params,
        'result': {
            'total_return': best_exit_result['total_return'],
            'final_capital': best_exit_result['final_capital'],
            'total_trades': best_exit_result['total_trades'],
            'win_rate': best_exit_result['win_rate'],
            'entries_used': best_exit_result['entries_after_filter'],
            'entries_total': best_exit_result['entries_before_filter']
        }
    }
    
    with open('/home/picaso/.openclaw/workspace/range_filter_strategy/optimization_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to optimization_results.json")


if __name__ == "__main__":
    main()