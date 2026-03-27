#!/usr/bin/env python3
"""Background tick collector - runs continuously, saves to file"""
import json
import time
import requests
import os
from datetime import datetime

DATA_FILE = "/home/picaso/.openclaw/workspace/range_filter_strategy/tick_data.json"
BATCH_SIZE = 100  # Save every N ticks

def get_recent_trades():
    """Get recent BTCUSDT trades from Binance"""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/trades",
            params={"symbol": "BTCUSDT", "limit": 1000},
            timeout=10
        )
        return resp.json()
    except:
        return []

def load_existing_data():
    """Load existing tick data"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"trades": [], "started": time.time()}

def save_data(data):
    """Save tick data"""
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def main():
    print(f"Tick collector starting...")
    print(f"Data file: {DATA_FILE}")
    
    data = load_existing_data()
    print(f"Loaded {len(data.get('trades', []))} existing trades")
    print(f"Running for: {time.time() - data.get('started', time.time()):.0f}s")
    
    last_id = None
    if data['trades']:
        last_id = data['trades'][-1].get('id')
    
    batch = []
    
    while True:
        try:
            trades = get_recent_trades()
            if isinstance(trades, list) and len(trades) > 0:
                for t in trades:
                    trade_id = t['id']
                    if last_id is None or trade_id > last_id:
                        batch.append({
                            'id': trade_id,
                            'time': t['time'],
                            'price': float(t['price']),
                            'qty': float(t['qty']),
                            'side': 'sell' if t['isBuyerMaker'] else 'buy'
                        })
                        last_id = trade_id
                
                if len(batch) >= BATCH_SIZE:
                    data['trades'].extend(batch)
                    # Keep only last 100K trades to avoid file bloat
                    if len(data['trades']) > 100000:
                        data['trades'] = data['trades'][-50000:]
                    save_data(data)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Saved {len(batch)} trades (total: {len(data['trades'])})")
                    batch = []
            else:
                print(f"Error or empty: {trades}")
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(5)  # Poll every 5 seconds

if __name__ == "__main__":
    main()