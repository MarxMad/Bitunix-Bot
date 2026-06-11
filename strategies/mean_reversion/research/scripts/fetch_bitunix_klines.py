"""
Download REAL Bitunix futures klines (not MT4) for backtesting.
Walks backwards via endTime pagination (~200 bars/call) and writes CSV.

Usage:
    python3 fetch_bitunix_klines.py ETHUSDT XLMUSDT --days 60 --interval 1m
Output: research/data/<SYMBOL>_<interval>.csv  (cols: time,datetime,open,high,low,close,baseVol,quoteVol)
"""
import requests, time, csv, os, sys, argparse

BASE = "https://fapi.bitunix.com"
KLINE = "/api/v1/futures/market/kline"
OUTDIR = os.path.join(os.path.dirname(__file__), "..", "data")


def fetch_symbol(symbol, interval="1m", days=60, sleep=0.12):
    os.makedirs(OUTDIR, exist_ok=True)
    now = int(time.time() * 1000)
    target_start = now - days * 24 * 3600 * 1000
    end = now
    rows = {}  # time -> bar (dedup)
    sess = requests.Session()
    calls = 0
    while end > target_start:
        try:
            r = sess.get(BASE + KLINE, params={
                "symbol": symbol, "interval": interval,
                "endTime": end, "limit": 200,
            }, timeout=20)
            j = r.json()
        except Exception as e:
            print(f"  ! request error: {e}; retrying in 2s")
            time.sleep(2); continue
        data = j.get("data", [])
        if not data:
            print(f"  no more data (msg={j.get('msg')})"); break
        for b in data:
            t = int(b["time"])
            rows[t] = b
        oldest = min(int(b["time"]) for b in data)
        calls += 1
        if calls % 25 == 0:
            print(f"  {symbol}: {len(rows):,} bars, at "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(oldest/1000))}")
        if oldest <= target_start:
            break
        if oldest >= end:  # no progress -> stop to avoid infinite loop
            break
        end = oldest - 1
        time.sleep(sleep)

    bars = sorted(rows.values(), key=lambda b: int(b["time"]))
    out = os.path.join(OUTDIR, f"{symbol}_{interval}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "datetime", "open", "high", "low", "close", "baseVol", "quoteVol"])
        for b in bars:
            t = int(b["time"])
            w.writerow([t, time.strftime("%Y-%m-%d %H:%M", time.gmtime(t/1000)),
                        b["open"], b["high"], b["low"], b["close"],
                        b.get("baseVol", ""), b.get("quoteVol", "")])
    span_days = (int(bars[-1]["time"]) - int(bars[0]["time"])) / 86400000 if bars else 0
    print(f"✅ {symbol}: {len(bars):,} bars ({span_days:.1f} days) -> {out}")
    return len(bars)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--interval", default="1m")
    a = ap.parse_args()
    for s in a.symbols:
        print(f"\n=== Downloading {s} {a.interval} ({a.days}d) ===")
        fetch_symbol(s, a.interval, a.days)
