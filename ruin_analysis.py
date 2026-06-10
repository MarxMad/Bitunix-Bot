# -*- coding: utf-8 -*-
"""
ruin_analysis.py - Survival / Ruin Analysis for Market Making Bot
Responde: ¿Cuánto durarían $50 antes de perderse?
"""
import sys, math, random, statistics, argparse
from dataclasses import dataclass

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8","utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    from colorama import init, Fore, Style; init(autoreset=True)
except ImportError:
    class Fore: CYAN=GREEN=YELLOW=RED=WHITE=MAGENTA=""
    class Style: BRIGHT=RESET_ALL=""

try:
    from tabulate import tabulate; HAS_TAB = True
except ImportError:
    HAS_TAB = False

MAKER_FEE = 0.0002
TAKER_FEE = 0.0006

def gbm_path(s0, step_sec, vol=0.80, drift=0.00):
    """Yield prices one by one using GBM (infinite generator)."""
    dt = step_sec / (365.25 * 24 * 3600)
    p = s0
    while True:
        p *= math.exp((drift - 0.5*vol**2)*dt + vol*math.sqrt(dt)*random.gauss(0,1))
        yield p

def run_until_ruin(
    capital_usdt=50.0,
    leverage=10,
    spread_pct=0.0004,
    fill_rate=0.35,
    refresh_sec=3.0,
    stop_loss_pct=-0.5,
    ruin_threshold_pct=0.10,   # consider "ruined" when capital < 10% of start
    max_days=180,               # safety ceiling
    start_price=61250.0,
):
    """
    Simulates until ruin or max_days.
    Returns (hours_survived, ruined: bool, reason: str)
    """
    buying_power  = capital_usdt * leverage
    order_usdt    = buying_power * 0.10
    max_pos       = buying_power * 0.30
    ruin_floor    = capital_usdt * ruin_threshold_pct

    capital    = capital_usdt
    pos_usdt   = 0.0
    pos_entry  = 0.0
    pend_b_px  = 0.0
    pend_s_px  = 0.0
    has_buy = has_sell = False

    price_gen  = gbm_path(start_price, refresh_sec)
    max_cycles = int(max_days * 24 * 3600 / refresh_sec)
    cycle      = 0

    for price in price_gen:
        cycle += 1
        if cycle > max_cycles:
            return max_days * 24, False, "max_time"

        # --- Fill checks ---
        if has_buy and price <= pend_b_px and random.random() < fill_rate:
            fee = order_usdt * MAKER_FEE
            capital -= fee
            if pos_usdt < 0:
                pnl = (pos_entry - pend_b_px)/pos_entry * abs(pos_usdt)
                capital += pnl; pos_usdt = 0.0
            else:
                w = pos_usdt; pos_usdt += order_usdt
                pos_entry = (w*pos_entry + order_usdt*pend_b_px)/pos_usdt if pos_usdt else pend_b_px
            has_buy = False

        if has_sell and price >= pend_s_px and random.random() < fill_rate:
            fee = order_usdt * MAKER_FEE
            capital -= fee
            if pos_usdt > 0:
                pnl = (pend_s_px - pos_entry)/pos_entry * pos_usdt
                capital += pnl; pos_usdt = 0.0
            else:
                w = abs(pos_usdt); pos_usdt -= order_usdt
                neg = abs(pos_usdt)
                pos_entry = (w*pos_entry + order_usdt*pend_s_px)/neg if neg else pend_s_px
            has_sell = False

        # --- Stop-loss ---
        if pos_usdt != 0 and pos_entry > 0:
            upct = ((price-pos_entry)/pos_entry*100) if pos_usdt>0 else ((pos_entry-price)/pos_entry*100)
            if upct <= stop_loss_pct:
                notional = abs(pos_usdt)
                pnl      = upct/100 * notional
                fee      = notional * TAKER_FEE
                capital += pnl - fee
                pos_usdt = pos_entry = 0.0

        # --- Ruin check ---
        if capital <= ruin_floor:
            hours = cycle * refresh_sec / 3600
            return hours, True, "ruin"

        # --- Place new orders ---
        hs = price * spread_pct / 2
        if not has_buy  and pos_usdt  < max_pos: pend_b_px = price - hs; has_buy  = True
        if not has_sell and -pos_usdt < max_pos:  pend_s_px = price + hs; has_sell = True

    return max_days * 24, False, "max_time"


def run_ruin_study(n=3000, **kwargs):
    results = []
    ruin_count = 0
    for _ in range(n):
        hours, ruined, reason = run_until_ruin(**kwargs)
        results.append((hours, ruined))
        if ruined:
            ruin_count += 1

    ruin_pct     = ruin_count / n * 100
    ruin_times   = [h for h,r in results if r]
    survive_hrs  = [h for h,r in results if not r]

    if ruin_times:
        s = sorted(ruin_times)
        med_ruin = statistics.median(ruin_times)
        p10_ruin = s[int(len(s)*0.10)]
        p90_ruin = s[int(len(s)*0.90)]
    else:
        med_ruin = p10_ruin = p90_ruin = None

    return {
        "n": n,
        "ruin_pct":     ruin_pct,
        "ruin_count":   ruin_count,
        "survive_pct":  100 - ruin_pct,
        "med_ruin_h":   med_ruin,
        "p10_ruin_h":   p10_ruin,
        "p90_ruin_h":   p90_ruin,
        "med_ruin_d":   med_ruin/24 if med_ruin else None,
        "p10_ruin_d":   p10_ruin/24 if p10_ruin else None,
        "p90_ruin_d":   p90_ruin/24 if p90_ruin else None,
    }


def fmt(rows, headers):
    if HAS_TAB:
        print(tabulate(rows, headers=headers, tablefmt="simple"))
    else:
        print("  ".join(f"{h:<28}" for h in headers))
        for r in rows: print("  ".join(f"{str(v):<28}" for v in r))


def bar(pct, width=40, fill="#", empty="-"):
    filled = int(width * pct / 100)
    return f"[{fill*filled}{empty*(width-filled)}] {pct:.1f}%"


def main():
    p = argparse.ArgumentParser(description="Ruin / Survival Analysis")
    p.add_argument("--capital",    type=float, default=50.0)
    p.add_argument("--leverage",   type=int,   default=10)
    p.add_argument("--spread",     type=float, default=0.0004)
    p.add_argument("--fill-rate",  type=float, default=0.35)
    p.add_argument("--stop-loss",  type=float, default=-0.5)
    p.add_argument("--scenarios",  type=int,   default=3000)
    p.add_argument("--max-days",   type=int,   default=180)
    p.add_argument("--ruin-pct",   type=float, default=0.10,
                   help="Ruin = capital below this fraction of start (0.10 = 10%)")
    a = p.parse_args()

    print(f"""
{Fore.CYAN}+============================================================+
|   {Fore.YELLOW}BITUNIX BOT - SURVIVAL & RUIN ANALYSIS{Fore.CYAN}                 |
|   {Fore.WHITE}How long do your ${a.capital:.0f} last before running out?{Fore.CYAN}      |
+============================================================+{Style.RESET_ALL}
  Capital      : ${a.capital:.2f} USDT
  Leverage     : {a.leverage}x  =>  Buying Power ${a.capital*a.leverage:.2f} USDT
  Spread       : +/-{a.spread*100:.3f}%
  Fill Rate    : {a.fill_rate*100:.0f}% per cycle
  Stop-Loss    : {a.stop_loss:.1f}% per position
  Ruin floor   : ${a.capital * a.ruin_pct:.2f} USDT  ({a.ruin_pct*100:.0f}% of capital)
  Scenarios    : {a.scenarios:,}
  Max horizon  : {a.max_days} days
""")

    # ── MAIN ANALYSIS ─────────────────────────────────────────────────────────
    print(f"  {Fore.YELLOW}Running {a.scenarios:,} ruin scenarios...{Style.RESET_ALL}", flush=True)
    s = run_ruin_study(
        n=a.scenarios,
        capital_usdt=a.capital,
        leverage=a.leverage,
        spread_pct=a.spread,
        fill_rate=a.fill_rate,
        stop_loss_pct=a.stop_loss,
        ruin_threshold_pct=a.ruin_pct,
        max_days=a.max_days,
    )
    print(f"  {Fore.GREEN}Done!{Style.RESET_ALL}\n")

    # ── RUIN PROBABILITY ──────────────────────────────────────────────────────
    ruin_color = Fore.RED if s["ruin_pct"] > 50 else (Fore.YELLOW if s["ruin_pct"] > 25 else Fore.GREEN)
    print(f"{Fore.CYAN}  RUIN PROBABILITY (within {a.max_days} days):{Style.RESET_ALL}")
    print(f"  {ruin_color}{bar(s['ruin_pct'])}{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}{bar(s['survive_pct'], fill='=', empty=' ')}{Style.RESET_ALL}  <- Survivors\n")

    print(f"  {Fore.WHITE}Ruined:   {s['ruin_count']:>5,} / {s['n']:,} scenarios  ({s['ruin_pct']:.1f}%){Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Survived: {s['n']-s['ruin_count']:>5,} / {s['n']:,} scenarios  ({s['survive_pct']:.1f}%){Style.RESET_ALL}\n")

    # ── TIME TO RUIN ──────────────────────────────────────────────────────────
    print(f"{Fore.CYAN}  WHEN DOES RUIN HAPPEN? (only among ruined scenarios):{Style.RESET_ALL}")
    if s["med_ruin_h"] is not None:
        rows = [
            ["Fastest ruin (P10)",   f"{s['p10_ruin_h']:.1f} hours",  f"{s['p10_ruin_d']:.2f} days",  "10% ruined by this time"],
            ["Median ruin (P50)",    f"{s['med_ruin_h']:.1f} hours",  f"{s['med_ruin_d']:.2f} days",  "Half of ruin cases"],
            ["Slow ruin (P90)",      f"{s['p90_ruin_h']:.1f} hours",  f"{s['p90_ruin_d']:.2f} days",  "90% ruined by this time"],
        ]
        fmt(rows, ["Scenario", "Hours", "Days", "Meaning"])
    else:
        print(f"  {Fore.GREEN}  No ruin events in this simulation set!{Style.RESET_ALL}")

    # ── LEVERAGE COMPARISON ───────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}  HOW LEVERAGE CHANGES YOUR SURVIVAL (${a.capital:.0f} capital):{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Running quick study across leverage levels...{Style.RESET_ALL}", flush=True)
    lev_rows = []
    for lev in [3, 5, 10, 20]:
        q = run_ruin_study(
            n=500,  # smaller for speed
            capital_usdt=a.capital, leverage=lev,
            spread_pct=a.spread, fill_rate=a.fill_rate,
            stop_loss_pct=a.stop_loss, ruin_threshold_pct=a.ruin_pct,
            max_days=a.max_days,
        )
        med_d = f"{q['med_ruin_d']:.1f}d" if q["med_ruin_d"] else f">{a.max_days}d"
        risk  = "HIGH" if q["ruin_pct"]>60 else ("MED" if q["ruin_pct"]>30 else "LOW")
        lev_rows.append([f"{lev}x", f"${a.capital*lev:.0f}", f"{q['ruin_pct']:.1f}%", med_d, risk])

    fmt(lev_rows, ["Leverage", "Buying Power", "Ruin Probability", "Median Ruin Time", "Risk"])

    # ── STOP-LOSS COMPARISON ──────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}  HOW STOP-LOSS TIGHTNESS AFFECTS SURVIVAL ({a.leverage}x leverage):{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Running study across stop-loss levels...{Style.RESET_ALL}", flush=True)
    sl_rows = []
    for sl in [-0.25, -0.5, -1.0, -2.0, -5.0]:
        q = run_ruin_study(
            n=500, capital_usdt=a.capital, leverage=a.leverage,
            spread_pct=a.spread, fill_rate=a.fill_rate,
            stop_loss_pct=sl, ruin_threshold_pct=a.ruin_pct,
            max_days=a.max_days,
        )
        med_d = f"{q['med_ruin_d']:.1f}d" if q["med_ruin_d"] else f">{a.max_days}d"
        sl_rows.append([f"{sl}%", f"{q['ruin_pct']:.1f}%", med_d,
                        "Fires often" if abs(sl)<0.5 else ("Balanced" if abs(sl)<2 else "Rare")])
    fmt(sl_rows, ["Stop-Loss", "Ruin Probability", "Median Ruin Time", "Note"])

    # ── KEY INSIGHTS ──────────────────────────────────────────────────────────
    print(f"""
{Fore.CYAN}+============================================================+
|  {Fore.YELLOW}KEY TAKEAWAYS{Fore.CYAN}                                              |
+============================================================+{Style.RESET_ALL}

  {Fore.GREEN}WHAT PROTECTS YOUR CAPITAL:{Style.RESET_ALL}
  - Lower leverage (3x-5x) dramatically increases survival time
  - Tighter stop-loss (-0.25%) fires often but limits each loss
  - Loose stop-loss (-5.0%) rarely fires but each hit is devastating
  - Wider spread (+0.05-0.06%) earns more per fill, offsets fees faster

  {Fore.YELLOW}WHAT KILLS YOUR CAPITAL:{Style.RESET_ALL}
  - Strong trending markets: price moves one direction, stop-loss fires
  - High leverage: small adverse move = large position loss
  - Multiple stop-losses in a row: each one compounds the damage
  - Fees accumulate: even maker fees (0.02%) add up with high volume

  {Fore.CYAN}RECOMMENDED SAFE SETTINGS FOR $50:{Style.RESET_ALL}
  --leverage 5 --spread 0.0005 --stop-loss -1.0 --max-loss 10.0

  {Fore.GREEN}RUN THE SAFE CONFIG SIMULATION:{Style.RESET_ALL}
  python -X utf8 simulate.py --capital 50 --leverage 5 --spread 0.0005 --hours 24
  python -X utf8 bot.py --symbol BTCUSDT --leverage 5 --spread 0.0005 --stop-loss -1.0 --max-loss 10
""")

if __name__ == "__main__":
    main()
