#!/usr/bin/env python3
"""
Range Filter Strategy Implementation (Type 1)
Based on DonovanWall's Range Filter indicator

This implements the key logic:
- Range size based on Average Change
- Filter calculation (Type 1)
- Direction determination
"""

import json
from typing import List, Tuple, Optional

# Range Filter parameters
RNG_QTY = 2.618
RNG_PERIOD = 14
SMOOTH_RANGE = True
SMOOTH_PERIOD = 27


def calculate_range_size(prices: List[float], qty: float = RNG_QTY, period: int = RNG_PERIOD) -> List[float]:
    """
    Calculate range size using Average Change method.
    AC = EMA of |price - price[1]|
    """
    if len(prices) < 2:
        return [0] * len(prices)
    
    # Calculate price changes
    changes = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    
    # EMA of changes
    alpha = 2 / (period + 1)
    ema = [changes[0]]
    for c in changes[1:]:
        ema.append((c - ema[-1]) * alpha + ema[-1])
    
    # Range size = qty * EMA
    result = [0]  # First element has no change
    for i in range(1, len(prices)):
        result.append(qty * ema[i-1])
    
    return result


def calculate_range_filter_type1(
    highs: List[float], 
    lows: List[float], 
    range_sizes: List[float],
    smooth: bool = SMOOTH_RANGE,
    smooth_period: int = SMOOTH_PERIOD
) -> Tuple[List[float], List[float], List[float]]:
    """
    Calculate Range Filter (Type 1).
    
    Returns: (high_band, low_band, filter)
    """
    n = len(highs)
    if n == 0:
        return [], [], []
    
    # Smooth the range if needed
    r = range_sizes[:]
    if smooth and n > smooth_period:
        alpha = 2 / (smooth_period + 1)
        for i in range(smooth_period, n):
            r[i] = (range_sizes[i] - r[i-1]) * alpha + r[i-1]
    
    # Calculate filter
    rfilt = [(highs[i] + lows[i]) / 2 for i in range(n)]
    hi_band = [0.0] * n
    lo_band = [0.0] * n
    
    for i in range(1, n):
        h_r = highs[i] - r[i]
        l_r = lows[i] + r[i]
        
        if h_r > rfilt[i-1]:
            rfilt[i] = h_r
        elif l_r < rfilt[i-1]:
            rfilt[i] = l_r
        else:
            rfilt[i] = rfilt[i-1]
        
        hi_band[i] = rfilt[i] + r[i]
        lo_band[i] = rfilt[i] - r[i]
    
    return hi_band, lo_band, rfilt


def get_filter_direction(filt: List[float]) -> List[int]:
    """
    Get filter direction: 1 for bullish, -1 for bearish, 0 for initial
    """
    direction = [0] * len(filt)
    for i in range(1, len(filt)):
        if filt[i] > filt[i-1]:
            direction[i] = 1
        elif filt[i] < filt[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
    return direction


def find_signals(direction: List[int]) -> List[Tuple[int, str]]:
    """
    Find entry signals from direction changes.
    Returns list of (index, signal) where signal is 'long' or 'short'
    """
    signals = []
    for i in range(1, len(direction)):
        if direction[i] == 1 and direction[i-1] == -1:
            signals.append((i, 'long'))
        elif direction[i] == -1 and direction[i-1] == 1:
            signals.append((i, 'short'))
    return signals


def calculate_max_potential_profit(
    prices: List[float],
    entry_idx: int,
    direction: str,
    lookback: int = 100
) -> dict:
    """
    Calculate max potential profit from an entry point.
    Looks forward until opposite signal or lookback limit.
    """
    if entry_idx >= len(prices) - 1:
        return {'max_profit': 0, 'max_loss': 0, 'exit_idx': entry_idx}
    
    end_idx = min(entry_idx + lookback, len(prices))
    
    entry_price = prices[entry_idx]
    max_profit = 0
    max_loss = 0
    exit_idx = entry_idx
    
    for i in range(entry_idx + 1, end_idx):
        if direction == 'long':
            pnl_pct = (prices[i] - entry_price) / entry_price
        else:  # short
            pnl_pct = (entry_price - prices[i]) / entry_price
        
        if pnl_pct > max_profit:
            max_profit = pnl_pct
            exit_idx = i
        if pnl_pct < max_loss:
            max_loss = pnl_pct
    
    return {
        'max_profit': max_profit,
        'max_loss': max_loss,
        'exit_idx': exit_idx,
        'holding_bars': exit_idx - entry_idx
    }


def run_full_backtest(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    tp_pct: float = 0.01,  # Take profit percentage
    sl_pct: float = 0.005,  # Stop loss percentage
    trailing_sl: bool = False,
    trailing_atr_mult: float = 2.0
) -> dict:
    """
    Run complete backtest with the Range Filter strategy.
    """
    # Calculate filter
    range_sizes = calculate_range_size(closes)
    hi_band, lo_band, filt = calculate_range_filter_type1(highs, lows, range_sizes)
    direction = get_filter_direction(filt)
    signals = find_signals(direction)
    
    # Run backtest
    trades = []
    capital = 10000.0
    position = None
    entry_price = 0
    entry_idx = 0
    
    wins = 0
    losses = 0
    max_drawdown = 0
    peak = capital
    
    for i in range(len(closes)):
        price = closes[i]
        
        # Check for entry
        if position is None:
            # Look for signal at this index
            for sig_idx, sig_type in signals:
                if sig_idx == i:
                    position = sig_type
                    entry_price = price
                    entry_idx = i
                    break
        
        # Check for exit
        if position is not None:
            if position == 'long':
                pnl_pct = (price - entry_price) / entry_price
                if pnl_pct >= tp_pct or pnl_pct <= -sl_pct:
                    # Exit
                    pnl = capital * pnl_pct
                    capital += pnl
                    trades.append({
                        'type': 'long',
                        'entry_idx': entry_idx,
                        'exit_idx': i,
                        'entry_price': entry_price,
                        'exit_price': price,
                        'pnl_pct': pnl_pct,
                        'pnl': pnl,
                        'result': 'win' if pnl_pct > 0 else 'loss'
                    })
                    if pnl_pct > 0:
                        wins += 1
                    else:
                        losses += 1
                    position = None
            else:  # short
                pnl_pct = (entry_price - price) / entry_price
                if pnl_pct >= tp_pct or pnl_pct <= -sl_pct:
                    pnl = capital * pnl_pct
                    capital += pnl
                    trades.append({
                        'type': 'short',
                        'entry_idx': entry_idx,
                        'exit_idx': i,
                        'entry_price': entry_price,
                        'exit_price': price,
                        'pnl_pct': pnl_pct,
                        'pnl': pnl,
                        'result': 'win' if pnl_pct > 0 else 'loss'
                    })
                    if pnl_pct > 0:
                        wins += 1
                    else:
                        losses += 1
                    position = None
        
        # Track drawdown
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak
        if dd > max_drawdown:
            max_drawdown = dd
    
    return {
        'final_capital': capital,
        'total_return': (capital - 10000) / 10000 * 100,
        'total_trades': wins + losses,
        'wins': wins,
        'losses': losses,
        'win_rate': wins / (wins + losses) * 100 if wins + losses > 0 else 0,
        'max_drawdown': max_drawdown * 100,
        'trades': trades
    }


def analyze_entries(closes: List[float], direction: List[int]) -> dict:
    """
    Analyze all potential entries - max potential profit before opposite signal.
    """
    signals = find_signals(direction)
    
    entry_stats = []
    for idx, sig_type in signals:
        mpp = calculate_max_potential_profit(closes, idx, sig_type)
        entry_stats.append({
            'index': idx,
            'type': sig_type,
            'max_profit_pct': mpp['max_profit'] * 100,
            'max_loss_pct': mpp['max_loss'] * 100,
            'holding_bars': mpp['holding_bars'],
            'exit_idx': mpp['exit_idx']
        })
    
    if not entry_stats:
        return {}
    
    profits = [e['max_profit_pct'] for e in entry_stats]
    losses = [e['max_loss_pct'] for e in entry_stats]
    bars = [e['holding_bars'] for e in entry_stats]
    
    return {
        'count': len(entry_stats),
        'profit': {
            'min': min(profits),
            'max': max(profits),
            'avg': sum(profits) / len(profits),
            'median': sorted(profits)[len(profits)//2],
            'total': sum(profits),
        },
        'loss': {
            'min': min(losses),
            'max': max(losses),
            'avg': sum(losses) / len(losses),
            'median': sorted(losses)[len(losses)//2],
        },
        'holding_bars': {
            'min': min(bars),
            'max': max(bars),
            'avg': sum(bars) / len(bars),
            'median': sorted(bars)[len(bars)//2],
        }
    }


# === ENTRY QUALITY FILTERS ===

def calculate_entry_quality_score(
    closes: List[float],
    hi_band: List[float],
    lo_band: List[float],
    idx: int,
    lookback_vol: int = 20
) -> dict:
    """
    Calculate quality score for an entry at idx.
    Returns dict with individual metrics and combined score.
    
    Score components:
    - vol_score (0-1): 0.6% volatility = perfect score
    - bw_score (0-1): 1.5% band width = perfect score
    - combined (0-1): weighted average
    """
    entry_price = closes[idx]
    
    # Volatility score (0-1)
    if idx >= lookback_vol:
        recent = closes[idx-lookback_vol:idx]
        changes = [abs(recent[i] - recent[i-1])/recent[i-1] for i in range(1, len(recent))]
        avg_vol = sum(changes) / len(changes)
        vol_score = min(avg_vol / 0.006, 1.0)  # 0.6% = perfect score
    else:
        avg_vol = None
        vol_score = 0.5  # neutral if not enough data
    
    # Band width score (0-1)
    if hi_band[idx] > 0 and lo_band[idx] > 0:
        bw_pct = (hi_band[idx] - lo_band[idx]) / entry_price
        bw_score = min(bw_pct / 0.015, 1.0)  # 1.5% = perfect score
    else:
        bw_pct = None
        bw_score = 0.5
    
    # Combined score (weighted average)
    combined = vol_score * 0.4 + bw_score * 0.4 + 0.2  # 0.2 = neutral time
    
    return {
        'volatility': avg_vol,
        'vol_score': vol_score,
        'band_width_pct': bw_pct,
        'bw_score': bw_score,
        'combined_score': combined,
        'quality_grade': 'A' if combined > 0.7 else 'B' if combined > 0.5 else 'C'
    }


def find_filtered_signals(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    hi_band: List[float],
    lo_band: List[float],
    direction: List[int],
    timestamps: List[int] = None,
    min_volatility: float = 0.004,  # 0.4%
    min_band_width_pct: float = 1.0,  # 1.0%
    good_hours: List[int] = None,  # UTC hours
    vol_lookback: int = 20
) -> List[Tuple[int, str, dict]]:
    """
    Find signals that pass quality filters.
    
    Args:
        closes: Close prices
        highs: High prices  
        lows: Low prices
        hi_band: Range filter upper band
        lo_band: Range filter lower band
        direction: Filter direction list
        timestamps: Unix timestamps (required for time filtering)
        min_volatility: Minimum 20-bar avg volatility (as decimal, 0.004 = 0.4%)
        min_band_width_pct: Minimum band width as percentage (1.0 = 1.0%)
        good_hours: List of UTC hours to allow entries (e.g., [4, 6, 9])
        vol_lookback: Lookback period for volatility calculation
    
    Returns:
        List of (idx, signal_type, quality_metrics) tuples for signals that pass filters
    
    Example:
        >>> signals = find_filtered_signals(closes, highs, lows, hi_band, lo_band, 
        ...                                  direction, timestamps,
        ...                                  min_volatility=0.004,
        ...                                  min_band_width_pct=1.0,
        ...                                  good_hours=[4, 6, 9])
        >>> for idx, sig_type, quality in signals:
        ...     print(f"{sig_type} at {idx}: score={quality['combined_score']:.2f}")
    """
    from datetime import datetime
    
    if good_hours is None:
        good_hours = [4, 6, 9, 19, 21]  # Default high-quality hours
    
    raw_signals = find_signals(direction)
    filtered = []
    
    for idx, sig_type in raw_signals:
        # Skip if not enough data for volatility
        if idx < vol_lookback:
            continue
        
        # Time filter
        if timestamps and good_hours:
            dt = datetime.fromtimestamp(timestamps[idx] / 1000)
            if dt.hour not in good_hours:
                continue
        
        # Volatility filter
        recent = closes[idx-vol_lookback:idx]
        changes = [abs(recent[i] - recent[i-1])/recent[i-1] for i in range(1, len(recent))]
        avg_vol = sum(changes) / len(changes)
        if avg_vol < min_volatility:
            continue
        
        # Band width filter
        if hi_band[idx] > 0 and lo_band[idx] > 0:
            bw_pct = (hi_band[idx] - lo_band[idx]) / closes[idx] * 100
            if bw_pct < min_band_width_pct:
                continue
        else:
            continue
        
        # Calculate quality score
        quality = calculate_entry_quality_score(
            closes, hi_band, lo_band, idx,
            lookback_vol=vol_lookback
        )
        
        filtered.append((idx, sig_type, quality))
    
    return filtered


def run_filtered_backtest(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    timestamps: List[int],
    tp_pct: float = 0.01,
    sl_pct: float = 0.005,
    min_volatility: float = 0.004,
    min_band_width_pct: float = 1.0,
    min_quality_score: float = 0.0,
    good_hours: List[int] = None
) -> dict:
    """
    Backtest with entry quality filters.
    
    Args:
        highs, lows, closes: Price data
        timestamps: Unix timestamps for time filtering
        tp_pct: Take profit percentage (0.01 = 1%)
        sl_pct: Stop loss percentage (0.005 = 0.5%)
        min_volatility: Minimum 20-bar volatility (0.004 = 0.4%)
        min_band_width_pct: Minimum band width as % (1.0 = 1.0%)
        min_quality_score: Minimum combined quality score (0.0-1.0)
        good_hours: Allowed UTC hours for entries
    
    Returns:
        Backtest results dict with trades and summary stats
    """
    range_sizes = calculate_range_size(closes)
    hi_band, lo_band, filt = calculate_range_filter_type1(highs, lows, range_sizes)
    direction = get_filter_direction(filt)
    
    # Get filtered signals
    signals = find_filtered_signals(
        closes, highs, lows, hi_band, lo_band, direction,
        timestamps=timestamps,
        min_volatility=min_volatility,
        min_band_width_pct=min_band_width_pct,
        good_hours=good_hours
    )
    
    # Run backtest on filtered signals
    trades = []
    capital = 10000.0
    position = None
    entry_price = 0
    entry_idx = 0
    
    wins = 0
    losses = 0
    max_drawdown = 0
    peak = capital
    
    for i in range(len(closes)):
        price = closes[i]
        
        # Check for entry (only on signal bars, and if no position)
        if position is None:
            for sig_idx, sig_type, quality in signals:
                if sig_idx == i:
                    # Additional quality filter
                    if quality['combined_score'] < min_quality_score:
                        continue
                    position = sig_type
                    entry_price = price
                    entry_idx = i
                    break
        
        # Check for exit
        if position is not None:
            if position == 'long':
                pnl_pct = (price - entry_price) / entry_price
                if pnl_pct >= tp_pct or pnl_pct <= -sl_pct:
                    pnl = capital * pnl_pct
                    capital += pnl
                    trades.append({
                        'type': 'long',
                        'entry_idx': entry_idx,
                        'exit_idx': i,
                        'entry_price': entry_price,
                        'exit_price': price,
                        'pnl_pct': pnl_pct,
                        'pnl': pnl,
                        'result': 'win' if pnl_pct > 0 else 'loss'
                    })
                    if pnl_pct > 0:
                        wins += 1
                    else:
                        losses += 1
                    position = None
            else:  # short
                pnl_pct = (entry_price - price) / entry_price
                if pnl_pct >= tp_pct or pnl_pct <= -sl_pct:
                    pnl = capital * pnl_pct
                    capital += pnl
                    trades.append({
                        'type': 'short',
                        'entry_idx': entry_idx,
                        'exit_idx': i,
                        'entry_price': entry_price,
                        'exit_price': price,
                        'pnl_pct': pnl_pct,
                        'pnl': pnl,
                        'result': 'win' if pnl_pct > 0 else 'loss'
                    })
                    if pnl_pct > 0:
                        wins += 1
                    else:
                        losses += 1
                    position = None
        
        # Track drawdown
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak
        if dd > max_drawdown:
            max_drawdown = dd
    
    return {
        'final_capital': capital,
        'total_return': (capital - 10000) / 10000 * 100,
        'total_trades': wins + losses,
        'wins': wins,
        'losses': losses,
        'win_rate': wins / (wins + losses) * 100 if wins + losses > 0 else 0,
        'max_drawdown': max_drawdown * 100,
        'trades': trades,
        'filtered_signals': len(signals)
    }


if __name__ == "__main__":
    # Test with sample data
    import random
    random.seed(42)
    
    # Generate sample BTC-like prices
    prices = [60000.0]
    for _ in range(999):
        change = random.gauss(0, 100)
        prices.append(prices[-1] + change)
    
    closes = prices
    highs = [p + abs(random.gauss(0, 50)) for p in prices]
    lows = [p - abs(random.gauss(0, 50)) for p in prices]
    
    # Analyze entries
    range_sizes = calculate_range_size(closes)
    hi_band, lo_band, filt = calculate_range_filter_type1(highs, lows, range_sizes)
    direction = get_filter_direction(filt)
    
    analysis = analyze_entries(closes, direction)
    
    print("=== ENTRY ANALYSIS ===")
    print(f"Total entries: {analysis['count']}")
    print(f"\nMax Potential Profit:")
    print(f"  Min: {analysis['profit']['min']:.2f}%")
    print(f"  Max: {analysis['profit']['max']:.2f}%")
    print(f"  Avg: {analysis['profit']['avg']:.2f}%")
    print(f"  Median: {analysis['profit']['median']:.2f}%")
    print(f"  Total: {analysis['profit']['total']:.2f}%")
    
    print(f"\nMax Potential Loss:")
    print(f"  Min: {analysis['loss']['min']:.2f}%")
    print(f"  Max: {analysis['loss']['max']:.2f}%")
    print(f"  Avg: {analysis['loss']['avg']:.2f}%")
    
    print(f"\nHolding Bars:")
    print(f"  Min: {analysis['holding_bars']['min']}")
    print(f"  Max: {analysis['holding_bars']['max']}")
    print(f"  Avg: {analysis['holding_bars']['avg']:.1f}")
    print(f"  Median: {analysis['holding_bars']['median']}")
    
    # Run backtest
    print("\n=== BACKTEST (TP=1%, SL=0.5%) ===")
    result = run_full_backtest(highs, lows, closes, tp_pct=0.01, sl_pct=0.005)
    print(f"Final capital: ${result['final_capital']:.2f}")
    print(f"Return: {result['total_return']:.2f}%")
    print(f"Trades: {result['total_trades']}")
    print(f"Win rate: {result['win_rate']:.1f}%")
    print(f"Max drawdown: {result['max_drawdown']:.2f}%")