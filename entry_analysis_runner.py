#!/usr/bin/env python3
"""
Comprehensive Entry Signal Analysis for Range Filter Strategy
"""

import json
import math
from datetime import datetime
from collections import defaultdict
import statistics

# Import from rf_indicator
exec(open('rf_indicator.py').read())

# Load data
print("Loading data...")
with open('btc_4yr.json') as f:
    data = json.load(f)

closes = [d['close'] for d in data]
highs = [d['high'] for d in data]
lows = [d['low'] for d in data]
timestamps = [d['timestamp'] for d in data]

print(f"Loaded {len(closes)} candles")


def analyze_with_params(qty, period, smooth_period, sample_every=1):
    """Run entry analysis with specific parameters."""
    # Subsample for speed
    c = closes[::sample_every]
    h = highs[::sample_every]
    l = lows[::sample_every]
    ts = timestamps[::sample_every]
    
    range_sizes = calculate_range_size(c, qty=qty, period=period)
    hi_band, lo_band, filt = calculate_range_filter_type1(h, l, range_sizes, 
                                                           smooth=smooth_period > 0, 
                                                           smooth_period=smooth_period)
    direction = get_filter_direction(filt)
    
    # Analyze all entries
    signals = find_signals(direction)
    
    entry_stats = []
    for idx, sig_type in signals:
        # Get entry details
        entry_price = c[idx]
        entry_time = datetime.fromtimestamp(ts[idx]/1000)
        
        # Calculate max potential profit
        mpp = calculate_max_potential_profit(c, idx, sig_type, lookback=200)
        
        # Get volatility at entry
        vol_lookback = 20
        if idx >= vol_lookback:
            recent_closes = c[idx-vol_lookback:idx]
            returns = [abs(recent_closes[i] - recent_closes[i-1])/recent_closes[i-1] for i in range(1, len(recent_closes))]
            avg_vol = sum(returns) / len(returns) if returns else 0
        else:
            avg_vol = 0
        
        # Get filter band width at entry (as % of price)
        if hi_band[idx] > 0 and lo_band[idx] > 0:
            band_width_pct = (hi_band[idx] - lo_band[idx]) / entry_price * 100
        else:
            band_width_pct = 0
        
        # Get filter value change
        filt_change = abs(filt[idx] - filt[idx-1]) / filt[idx-1] if filt[idx-1] > 0 else 0
        
        # Get time features
        hour = entry_time.hour
        day_of_week = entry_time.weekday()
        
        entry_stats.append({
            'idx': idx,
            'type': sig_type,
            'price': entry_price,
            'time': entry_time,
            'hour': hour,
            'day_of_week': day_of_week,
            'max_profit_pct': mpp['max_profit'] * 100,
            'max_loss_pct': mpp['max_loss'] * 100,
            'holding_bars': mpp['holding_bars'],
            'volatility': avg_vol * 100,  # as percentage
            'band_width_pct': band_width_pct,
            'filt_change_pct': filt_change * 100,
        })
    
    return entry_stats


def classify_entries_by_profit(entry_stats, thresholds=[1, 3, 5]):
    """Classify entries into profit buckets."""
    buckets = {'excellent': [], 'good': [], 'moderate': [], 'poor': [], 'bad': []}
    
    for e in entry_stats:
        mp = e['max_profit_pct']
        if mp >= thresholds[2]:
            buckets['excellent'].append(e)
        elif mp >= thresholds[1]:
            buckets['good'].append(e)
        elif mp >= thresholds[0]:
            buckets['moderate'].append(e)
        elif mp >= 0:
            buckets['poor'].append(e)
        else:
            buckets['bad'].append(e)
    
    return buckets


def analyze_bucket_patterns(bucket_entries, bucket_name):
    """Analyze patterns within a profit bucket."""
    if not bucket_entries:
        return {}
    
    # Time of day
    hour_counts = defaultdict(int)
    for e in bucket_entries:
        hour_counts[e['hour']] += 1
    
    # Day of week
    dow_counts = defaultdict(int)
    for e in bucket_entries:
        dow_counts[e['day_of_week']] += 1
    
    # Average volatility
    avg_vol = statistics.mean([e['volatility'] for e in bucket_entries])
    
    # Average band width
    avg_bw = statistics.mean([e['band_width_pct'] for e in bucket_entries])
    
    # Average filter change
    avg_fc = statistics.mean([e['filt_change_pct'] for e in bucket_entries])
    
    return {
        'count': len(bucket_entries),
        'avg_profit': statistics.mean([e['max_profit_pct'] for e in bucket_entries]),
        'avg_loss': statistics.mean([e['max_loss_pct'] for e in bucket_entries]),
        'avg_volatility': avg_vol,
        'avg_band_width': avg_bw,
        'avg_filt_change': avg_fc,
        'top_hours': sorted(hour_counts.items(), key=lambda x: -x[1])[:3],
        'top_days': sorted(dow_counts.items(), key=lambda x: -x[1])[:3],
    }


def compare_params():
    """Compare different parameter combinations."""
    print("\n=== PARAMETER COMPARISON ===")
    
    params_to_test = [
        # (RNG_QTY, RNG_PERIOD, SMOOTH_PERIOD)
        (1.0, 14, 27),
        (1.5, 14, 27),
        (2.0, 14, 27),
        (2.618, 14, 27),  # default golden ratio
        (3.0, 14, 27),
        (4.0, 14, 27),
        (2.618, 7, 27),
        (2.618, 21, 27),
        (2.618, 28, 27),
        (2.618, 50, 27),
        (2.618, 14, 14),
        (2.618, 14, 50),
        (2.618, 14, 100),
        (2.0, 21, 27),
        (3.0, 21, 27),
        (1.618, 28, 27),
    ]
    
    results = []
    for qty, period, smooth in params_to_test:
        entries = analyze_with_params(qty, period, smooth, sample_every=10)
        buckets = classify_entries_by_profit(entries)
        
        n_excellent = len(buckets['excellent'])
        n_good = len(buckets['good'])
        n_moderate = len(buckets['moderate'])
        n_poor = len(buckets['poor'])
        n_bad = len(buckets['bad'])
        total = len(entries)
        
        # Win rate if we trade with TP=1%, SL=0.5%
        wins = n_excellent + n_good + n_moderate
        win_rate = wins / total * 100 if total > 0 else 0
        
        results.append({
            'qty': qty,
            'period': period,
            'smooth': smooth,
            'total': total,
            'excellent': n_excellent,
            'good': n_good,
            'moderate': n_moderate,
            'poor': n_poor,
            'bad': n_bad,
            'win_rate': win_rate,
            'avg_profit': sum(e['max_profit_pct'] for e in entries) / len(entries) if entries else 0,
            'avg_loss': sum(e['max_loss_pct'] for e in entries) / len(entries) if entries else 0,
        })
        
        print(f"QTY={qty:.3f}, PERIOD={period}, SMOOTH={smooth}: "
              f"Total={total}, Excellent={n_excellent}, Good={n_good}, "
              f"Moderate={n_moderate}, WinRate={win_rate:.1f}%")
    
    return results


def deep_dive_analysis():
    """Deep dive into entry patterns using default params."""
    print("\n=== DEEP DIVE ANALYSIS (Default Params) ===")
    
    # Use default params, sample every 5 for speed
    entries = analyze_with_params(2.618, 14, 27, sample_every=5)
    print(f"Total entries analyzed: {len(entries)}")
    
    buckets = classify_entries_by_profit(entries)
    
    print("\n--- EXCELLENT Entries (>5% max profit) ---")
    ex_stats = analyze_bucket_patterns(buckets['excellent'], 'excellent')
    if ex_stats:
        print(f"Count: {ex_stats['count']}")
        print(f"Avg max profit: {ex_stats['avg_profit']:.2f}%")
        print(f"Avg max loss: {ex_stats['avg_loss']:.2f}%")
        print(f"Avg volatility: {ex_stats['avg_volatility']:.4f}%")
        print(f"Avg band width: {ex_stats['avg_band_width']:.3f}%")
        print(f"Top hours: {[(h, round(c/len(buckets['excellent'])*100, 1)) for h, c in ex_stats['top_hours']]}")
        print(f"Top days: {[(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d], round(c/len(buckets['excellent'])*100, 1)) for d, c in ex_stats['top_days']]}")
    
    print("\n--- BAD Entries (<0% max profit) ---")
    bad_stats = analyze_bucket_patterns(buckets['bad'], 'bad')
    if bad_stats:
        print(f"Count: {bad_stats['count']}")
        print(f"Avg max profit: {bad_stats['avg_profit']:.2f}%")
        print(f"Avg max loss: {bad_stats['avg_loss']:.2f}%")
        print(f"Avg volatility: {bad_stats['avg_volatility']:.4f}%")
        print(f"Avg band width: {bad_stats['avg_band_width']:.3f}%")
        print(f"Top hours: {[(h, round(c/len(buckets['bad'])*100, 1)) for h, c in bad_stats['top_hours']]}")
        print(f"Top days: {[(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d], round(c/len(buckets['bad'])*100, 1)) for d, c in bad_stats['top_days']]}")
    
    print("\n--- POOR Entries (0-1% max profit) ---")
    poor_stats = analyze_bucket_patterns(buckets['poor'], 'poor')
    if poor_stats:
        print(f"Count: {poor_stats['count']}")
        print(f"Avg max profit: {poor_stats['avg_profit']:.2f}%")
        print(f"Avg max loss: {poor_stats['avg_loss']:.2f}%")
        print(f"Avg volatility: {poor_stats['avg_volatility']:.4f}%")
        print(f"Avg band width: {poor_stats['avg_band_width']:.3f}%")
        print(f"Top hours: {[(h, round(c/len(buckets['poor'])*100, 1)) for h, c in poor_stats['top_hours']]}")
        print(f"Top days: {[(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d], round(c/len(buckets['poor'])*100, 1)) for d, c in poor_stats['top_days']]}")
    
    return entries, buckets


def analyze_time_patterns(entries):
    """Analyze time-of-day patterns in detail."""
    print("\n=== TIME-OF-DAY ANALYSIS ===")
    
    # Group by hour
    hourly = defaultdict(list)
    for e in entries:
        hourly[e['hour']].append(e)
    
    hourly_stats = []
    for hour in range(24):
        if hourly[hour]:
            entries_in_hour = hourly[hour]
            avg_profit = statistics.mean([e['max_profit_pct'] for e in entries_in_hour])
            avg_loss = statistics.mean([e['max_loss_pct'] for e in entries_in_hour])
            count = len(entries_in_hour)
            excellent_rate = len([e for e in entries_in_hour if e['max_profit_pct'] >= 5]) / count * 100
            
            hourly_stats.append({
                'hour': hour,
                'count': count,
                'avg_profit': avg_profit,
                'avg_loss': avg_loss,
                'excellent_rate': excellent_rate,
                'avg_volatility': statistics.mean([e['volatility'] for e in entries_in_hour])
            })
    
    # Sort by excellent rate
    hourly_stats.sort(key=lambda x: -x['excellent_rate'])
    
    print("Hours sorted by Excellent entry rate (>5% max profit):")
    for s in hourly_stats:
        print(f"  Hour {s['hour']:02d}: count={s['count']:3d}, excellent={s['excellent_rate']:.1f}%, "
              f"avg_profit={s['avg_profit']:.2f}%, avg_vol={s['avg_volatility']:.3f}%")
    
    return hourly_stats


def analyze_volatility_patterns(entries):
    """Analyze volatility at entry vs outcome."""
    print("\n=== VOLATILITY PATTERN ANALYSIS ===")
    
    # Bin by volatility
    vol_bins = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 2.0), (2.0, 100)]
    
    for low, high in vol_bins:
        entries_in_bin = [e for e in entries if low <= e['volatility'] < high]
        if entries_in_bin:
            avg_profit = statistics.mean([e['max_profit_pct'] for e in entries_in_bin])
            avg_loss = statistics.mean([e['max_loss_pct'] for e in entries_in_bin])
            excellent_rate = len([e for e in entries_in_bin if e['max_profit_pct'] >= 5]) / len(entries_in_bin) * 100
            print(f"Vol {low:.1f}-{high:.1f}%: n={len(entries_in_bin):4d}, avg_profit={avg_profit:6.2f}%, "
                  f"avg_loss={avg_loss:6.2f}%, excellent={excellent_rate:.1f}%")


def analyze_bandwidth_patterns(entries):
    """Analyze band width at entry vs outcome."""
    print("\n=== BAND WIDTH PATTERN ANALYSIS ===")
    
    # Bin by band width
    bw_bins = [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0), (1.0, 100)]
    
    for low, high in bw_bins:
        entries_in_bin = [e for e in entries if low <= e['band_width_pct'] < high]
        if entries_in_bin:
            avg_profit = statistics.mean([e['max_profit_pct'] for e in entries_in_bin])
            avg_loss = statistics.mean([e['max_loss_pct'] for e in entries_in_bin])
            excellent_rate = len([e for e in entries_in_bin if e['max_profit_pct'] >= 5]) / len(entries_in_bin) * 100
            print(f"BW {low:.1f}-{high:.1f}%: n={len(entries_in_bin):4d}, avg_profit={avg_profit:6.2f}%, "
                  f"avg_loss={avg_loss:6.2f}%, excellent={excellent_rate:.1f}%")


def find_filter_signal_quality(entries):
    """Analyze if filter direction change magnitude predicts quality."""
    print("\n=== FILTER CHANGE MAGNITUDE ANALYSIS ===")
    
    fc_bins = [(0, 0.01), (0.01, 0.02), (0.02, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 100)]
    
    for low, high in fc_bins:
        entries_in_bin = [e for e in entries if low <= e['filt_change_pct'] < high]
        if entries_in_bin:
            avg_profit = statistics.mean([e['max_profit_pct'] for e in entries_in_bin])
            avg_loss = statistics.mean([e['max_loss_pct'] for e in entries_in_bin])
            excellent_rate = len([e for e in entries_in_bin if e['max_profit_pct'] >= 5]) / len(entries_in_bin) * 100
            print(f"FC {low:.3f}-{high:.3f}%: n={len(entries_in_bin):4d}, avg_profit={avg_profit:6.2f}%, "
                  f"avg_loss={avg_loss:6.2f}%, excellent={excellent_rate:.1f}%")


def analyze_holding_bars_patterns(entries):
    """Analyze if holding bars relates to quality."""
    print("\n=== HOLDING BARS ANALYSIS ===")
    
    bars_bins = [(1, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 100), (100, 1000)]
    
    for low, high in bars_bins:
        entries_in_bin = [e for e in entries if low <= e['holding_bars'] < high]
        if entries_in_bin:
            avg_profit = statistics.mean([e['max_profit_pct'] for e in entries_in_bin])
            avg_loss = statistics.mean([e['max_loss_pct'] for e in entries_in_bin])
            print(f"Bars {low:3d}-{high:3d}: n={len(entries_in_bin):4d}, avg_profit={avg_profit:6.2f}%, avg_loss={avg_loss:6.2f}%")


def correlation_analysis(entries):
    """Simple correlation between features and max profit."""
    print("\n=== CORRELATION ANALYSIS ===")
    
    if len(entries) < 10:
        return
    
    n = len(entries)
    profits = [e['max_profit_pct'] for e in entries]
    
    def pearson_corr(x, y):
        n = len(x)
        if n < 2:
            return 0
        mx, my = sum(x)/n, sum(y)/n
        num = sum((xi-mx)*(yi-my) for xi, yi in zip(x, y))
        dx = math.sqrt(sum((xi-mx)**2 for xi in x))
        dy = math.sqrt(sum((yi-my)**2 for yi in y))
        return num / (dx * dy + 1e-10)
    
    # Features to correlate
    features = ['volatility', 'band_width_pct', 'filt_change_pct', 'holding_bars']
    
    for feat in features:
        vals = [e[feat] for e in entries]
        corr = pearson_corr(vals, profits)
        print(f"  {feat:20s}: corr = {corr:+.4f}")


if __name__ == "__main__":
    # Run parameter comparison
    param_results = compare_params()
    
    # Deep dive with default params
    entries, buckets = deep_dive_analysis()
    
    # Time patterns
    hourly_stats = analyze_time_patterns(entries)
    
    # Volatility patterns
    analyze_volatility_patterns(entries)
    
    # Band width patterns
    analyze_bandwidth_patterns(entries)
    
    # Filter change patterns
    find_filter_signal_quality(entries)
    
    # Holding bars patterns
    analyze_holding_bars_patterns(entries)
    
    # Correlation analysis
    correlation_analysis(entries)
    
    # Save results
    with open('entry_analysis_results.json', 'w') as f:
        json.dump({
            'param_results': param_results,
            'total_entries': len(entries),
            'bucket_counts': {k: len(v) for k, v in buckets.items()},
        }, f, indent=2, default=str)
    
    print("\n=== ANALYSIS COMPLETE ===")
    print("Results saved to entry_analysis_results.json")
