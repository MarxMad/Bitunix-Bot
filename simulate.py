# -*- coding: utf-8 -*-
"""
simulate.py - Bitunix Market Maker Volume & PnL Simulator
Monte Carlo simulation using Geometric Brownian Motion.

Usage:
    python simulate.py
    python simulate.py --capital 50 --leverage 10 --hours 24
    python simulate.py --capital 50 --leverage 10 --days 30 --scenarios 2000
"""
import sys, math, random, statistics, argparse
from dataclasses import dataclass
from typing import List

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8","utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    from colorama import init, Fore, Style; init(autoreset=True)
except ImportError:
    class Fore: CYAN=GREEN=YELLOW=RED=WHITE=""
    class Style: BRIGHT=RESET_ALL=""

try:
    from tabulate import tabulate; HAS_TAB = True
except ImportError:
    HAS_TAB = False

# --- Bitunix Fee Structure (VIP 0, verified June 2026) -----------------------
MAKER_FEE = 0.0002   # 0.020%
TAKER_FEE = 0.0006   # 0.060%

@dataclass
class SimConfig:
    capital_usdt:    float = 50.0
    leverage:        int   = 10
    spread_pct:      float = 0.0004    # half-spread per side
    fill_rate:       float = 0.35      # prob each limit order fills per cycle
    refresh_seconds: float = 3.0
    hours:           float = 24.0
    start_price:     float = 61250.0
    max_pos_pct:     float = 0.30      # max % of buying power as open position
    stop_loss_pct:   float = -0.5      # trigger at -0.5% position PnL
    scenarios:       int   = 1000


@dataclass
class Res:
    volume:       float = 0.0
    fees:         float = 0.0
    pnl:          float = 0.0
    fills:        int   = 0
    round_trips:  int   = 0
    stop_losses:  int   = 0
    max_drawdown: float = 0.0
    capital_end:  float = 0.0


def gbm_path(s0, hours, step_sec, vol=0.80, drift=0.20) -> List[float]:
    """Geometric Brownian Motion price path."""
    n  = int(hours * 3600 / step_sec)
    dt = step_sec / (365.25 * 24 * 3600)
    p, path = s0, [s0]
    for _ in range(n):
        p *= math.exp((drift - 0.5*vol**2)*dt + vol*math.sqrt(dt)*random.gauss(0,1))
        path.append(p)
    return path


def run_one(cfg: SimConfig) -> Res:
    r = Res()
    buying_power  = cfg.capital_usdt * cfg.leverage
    order_usdt    = buying_power * 0.10   # 10% of buying power per order
    max_pos       = buying_power * cfg.max_pos_pct

    capital = cfg.capital_usdt
    peak    = capital
    pos_usdt   = 0.0   # + long / - short
    pos_entry  = 0.0
    pend_b_px  = 0.0
    pend_s_px  = 0.0
    has_buy = has_sell = False

    prices = gbm_path(cfg.start_price, cfg.hours, cfg.refresh_seconds)

    for i, price in enumerate(prices[1:], 1):
        # --- Check buy fill ---
        if has_buy and price <= pend_b_px and random.random() < cfg.fill_rate:
            fee = order_usdt * MAKER_FEE
            capital -= fee; r.fees += fee; r.volume += order_usdt; r.fills += 1
            if pos_usdt < 0:
                pnl = (pos_entry - pend_b_px)/pos_entry * abs(pos_usdt)
                capital += pnl; r.pnl += pnl
                if pos_usdt + order_usdt >= 0: r.round_trips += 1
                pos_usdt = 0.0
            else:
                w = pos_usdt; pos_usdt += order_usdt
                pos_entry = (w*pos_entry + order_usdt*pend_b_px)/pos_usdt if pos_usdt else pend_b_px
            has_buy = False

        # --- Check sell fill ---
        if has_sell and price >= pend_s_px and random.random() < cfg.fill_rate:
            fee = order_usdt * MAKER_FEE
            capital -= fee; r.fees += fee; r.volume += order_usdt; r.fills += 1
            if pos_usdt > 0:
                pnl = (pend_s_px - pos_entry)/pos_entry * pos_usdt
                capital += pnl; r.pnl += pnl
                if pos_usdt - order_usdt <= 0: r.round_trips += 1
                pos_usdt = 0.0
            else:
                w = abs(pos_usdt); pos_usdt -= order_usdt
                neg = abs(pos_usdt)
                pos_entry = (w*pos_entry + order_usdt*pend_s_px)/neg if neg else pend_s_px
            has_sell = False

        # --- Stop-loss ---
        if pos_usdt != 0 and pos_entry > 0:
            upct = ((price-pos_entry)/pos_entry*100) if pos_usdt>0 else ((pos_entry-price)/pos_entry*100)
            if upct <= cfg.stop_loss_pct:
                notional = abs(pos_usdt)
                fee = notional * TAKER_FEE
                pnl = upct/100 * notional
                capital += pnl - fee; r.pnl += pnl; r.fees += fee
                r.volume += notional; r.stop_losses += 1
                pos_usdt = pos_entry = 0.0

        # --- Place new orders ---
        hs = price * cfg.spread_pct / 2
        if not has_buy  and pos_usdt  < max_pos:  pend_b_px = price - hs; has_buy  = True
        if not has_sell and -pos_usdt < max_pos:   pend_s_px = price + hs; has_sell = True

        # Track drawdown
        peak = max(peak, capital)
        r.max_drawdown = max(r.max_drawdown, peak - capital)
        if capital <= 0: break

    # Mark open position
    mark_pnl = (prices[-1]-pos_entry)/pos_entry*pos_usdt if pos_usdt>0 and pos_entry>0 else 0
    r.capital_end = capital + mark_pnl
    return r


def run_mc(cfg: SimConfig):
    results = [run_one(cfg) for _ in range(cfg.scenarios)]
    def stat(arr):
        s = sorted(arr)
        n = len(s)
        return {"p10":s[int(n*.10)],"p50":s[n//2],"p90":s[int(n*.90)],"mean":statistics.mean(arr)}

    vols  = [r.volume      for r in results]
    fees  = [r.fees        for r in results]
    pnls  = [r.pnl         for r in results]
    ends  = [r.capital_end for r in results]
    dds   = [r.max_drawdown for r in results]
    rts   = [r.round_trips  for r in results]
    sls   = [r.stop_losses  for r in results]

    return {
        "vol":  stat(vols), "fees": stat(fees), "pnl": stat(pnls),
        "end":  stat(ends), "dd_max": max(dds), "dd_med": statistics.median(dds),
        "rt_mean": statistics.mean(rts), "sl_mean": statistics.mean(sls),
        "win_pct": sum(1 for p in pnls if p>0)/len(pnls)*100,
        "n": cfg.scenarios
    }


def fmt_row(rows, headers):
    if HAS_TAB:
        print(tabulate(rows, headers=headers, tablefmt="simple"))
    else:
        print("  ".join(f"{h:<22}" for h in headers))
        for r in rows: print("  ".join(f"{str(v):<22}" for v in r))


def main():
    p = argparse.ArgumentParser(description="Market Maker Volume Simulator")
    p.add_argument("--capital",   type=float, default=50.0)
    p.add_argument("--leverage",  type=int,   default=10)
    p.add_argument("--spread",    type=float, default=0.0004)
    p.add_argument("--fill-rate", type=float, default=0.35)
    p.add_argument("--refresh",   type=float, default=3.0)
    p.add_argument("--hours",     type=float, default=24.0)
    p.add_argument("--days",      type=float, default=None)
    p.add_argument("--price",     type=float, default=61250.0)
    p.add_argument("--scenarios", type=int,   default=1000)
    a = p.parse_args()
    hours = a.days*24 if a.days else a.hours

    cfg = SimConfig(
        capital_usdt=a.capital, leverage=a.leverage, spread_pct=a.spread,
        fill_rate=a.fill_rate, refresh_seconds=a.refresh, hours=hours,
        start_price=a.price, scenarios=a.scenarios
    )
    buying_power = cfg.capital_usdt * cfg.leverage
    order_size   = buying_power * 0.10
    cycles       = int(hours * 3600 / cfg.refresh_seconds)

    print(f"""
{Fore.CYAN}+============================================================+
|   {Fore.YELLOW}BITUNIX MARKET MAKER - VOLUME & PnL SIMULATOR{Fore.CYAN}          |
|   {Fore.WHITE}Monte Carlo | Geometric Brownian Motion | {cfg.scenarios:,} runs{Fore.CYAN}  |
+============================================================+{Style.RESET_ALL}
  Capital       : ${cfg.capital_usdt:,.2f} USDT
  Leverage      : {cfg.leverage}x  => Buying Power ${buying_power:,.2f} USDT
  Order Size    : ${order_size:,.2f} USDT per side
  Spread        : +/-{cfg.spread_pct*100:.3f}%  (~${cfg.start_price*cfg.spread_pct/2:.2f} per side)
  Fill Rate     : {cfg.fill_rate*100:.0f}% per cycle
  Duration      : {hours:.1f}h ({hours/24:.1f} days) | {cycles:,} cycles
  Maker Fee     : {MAKER_FEE*100:.3f}%  |  Taker Fee: {TAKER_FEE*100:.3f}%
""")

    print(f"  {Fore.YELLOW}Running {cfg.scenarios:,} Monte Carlo scenarios...{Style.RESET_ALL}", flush=True)
    s = run_mc(cfg)
    print(f"  {Fore.GREEN}Simulation complete!{Style.RESET_ALL}\n")

    # Volume table
    print(f"{Fore.CYAN}  VOLUME PROJECTIONS ({hours:.0f}h):{Style.RESET_ALL}")
    fmt_row([
        ["Pessimistic (P10)", f"${s['vol']['p10']:>14,.2f}", f"{s['vol']['p10']/cfg.capital_usdt:.0f}x capital"],
        ["Median (P50)",      f"${s['vol']['p50']:>14,.2f}", f"{s['vol']['p50']/cfg.capital_usdt:.0f}x capital"],
        ["Optimistic (P90)",  f"${s['vol']['p90']:>14,.2f}", f"{s['vol']['p90']/cfg.capital_usdt:.0f}x capital"],
        ["Mean",              f"${s['vol']['mean']:>14,.2f}", f"{s['vol']['mean']/cfg.capital_usdt:.0f}x capital"],
    ], ["Scenario", "Volume (USDT)", "vs Capital"])

    # PnL / risk table
    print(f"\n{Fore.CYAN}  PnL & RISK ANALYSIS ({hours:.0f}h):{Style.RESET_ALL}")
    cap = cfg.capital_usdt
    fmt_row([
        ["Fees (median)",       f"-${s['fees']['p50']:.4f}",   "Maker fees paid"],
        ["Net PnL (P10)",       f"${s['pnl']['p10']:.4f}",     "Pessimistic"],
        ["Net PnL (median)",    f"${s['pnl']['p50']:.4f}",     "Typical"],
        ["Net PnL (P90)",       f"${s['pnl']['p90']:.4f}",     "Optimistic"],
        ["Capital End (P10)",   f"${s['end']['p10']:.4f}",     f"({(s['end']['p10']/cap-1)*100:+.2f}%)"],
        ["Capital End (median)",f"${s['end']['p50']:.4f}",     f"({(s['end']['p50']/cap-1)*100:+.2f}%)"],
        ["Capital End (P90)",   f"${s['end']['p90']:.4f}",     f"({(s['end']['p90']/cap-1)*100:+.2f}%)"],
        ["Max Drawdown",        f"-${s['dd_max']:.4f}",        "Worst observed"],
        ["Avg Round-Trips",     f"{s['rt_mean']:.1f}",         "Full buy+sell cycles"],
        ["Avg Stop-Losses",     f"{s['sl_mean']:.2f}",         "Per session"],
        ["Profitable runs",     f"{s['win_pct']:.1f}%",        "Scenarios with PnL>0"],
    ], ["Metric", "Value", "Note"])

    # Sensitivity: Volume vs Leverage x Duration
    print(f"\n{Fore.CYAN}  VOLUME SENSITIVITY — ${cfg.capital_usdt:.0f} capital (analytical estimate):{Style.RESET_ALL}")
    hlist = [1, 8, 24, 168, 720]
    hlabels = ["1h","8h","24h","7d","30d"]
    llevels = [3, 5, 10, 20]
    def est(cap, lev, h):
        bp = cap*lev; oz = bp*0.10; cyc = h*3600/cfg.refresh_seconds
        return cyc*oz*2*cfg.fill_rate
    rows2 = []
    for lev in llevels:
        rows2.append([f"{lev}x"] + [f"${est(cfg.capital_usdt,lev,h):>10,.0f}" for h in hlist])
    fmt_row(rows2, ["Leverage"]+hlabels)

    # 30-day VIP projection
    vol_daily = s['vol']['p50'] / (hours/24) if hours > 0 else 0
    vol_30d   = vol_daily * 30
    print(f"""
{Fore.CYAN}  VIP TIER ANALYSIS (30-day projection from median):{Style.RESET_ALL}
  Daily volume   (median) : ${vol_daily:>12,.2f} USDT
  30-day volume  (median) : ${vol_30d:>12,.2f} USDT
""")
    vip_rows = []
    for vip,(thr,mk,tk) in [("VIP 0",(0,0.020,0.060)),("VIP 1",(1e6,0.020,0.050)),
                              ("VIP 2",(5e6,0.016,0.050)),("VIP 3",(1e7,0.014,0.040)),
                              ("VIP 4",(2e7,0.012,0.0375))]:
        status = "ACTIVE" if thr==0 else ("REACHABLE" if vol_30d>=thr else f"Need ${thr/1e6:.0f}M vol/30d")
        vip_rows.append([vip, f"${thr/1e6:.0f}M" if thr else "Any", f"{mk:.3f}%", f"{tk:.3f}%", status])
    fmt_row(vip_rows, ["Level","30d Vol Req","Maker","Taker","Status"])

    print(f"""
{Fore.CYAN}  NOTES:{Style.RESET_ALL}
  * Fill rate is probabilistic; real fills depend on order book depth
  * Funding rates (~0.01%/8h) are not included in this simulation
  * GBM assumes log-normal returns; real BTC has heavier tails
  * Always run --dry-run first before using real funds
""")

if __name__ == "__main__":
    main()
