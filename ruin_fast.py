# -*- coding: utf-8 -*-
"""
ruin_fast.py - Survival Analysis Rapida (optimized)
Usa ciclos de 1 hora en vez de 3 segundos para simular meses de manera eficiente.
"""
import sys, math, random, statistics
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


def run_hourly_scenario(
    capital_usdt=50.0,
    leverage=10,
    spread_pct=0.0004,
    fill_rate=0.35,
    refresh_sec=3.0,
    stop_loss_pct=-0.5,
    ruin_floor_pct=0.10,
    max_days=90,
    start_price=61250.0,
    annual_vol=0.80,
):
    """
    Simulates one hour at a time (aggregated).
    Each hour: price follows GBM, orders fill probabilistically.
    Returns: (hours_survived, ruined: bool)
    """
    cycles_per_hour = 3600 / refresh_sec   # e.g. 1200 cycles/hour at 3s refresh
    dt_hour = 1 / (365.25 * 24)            # 1 hour in years for GBM
    vol_hourly = annual_vol * math.sqrt(dt_hour)

    buying_power = capital_usdt * leverage
    order_usdt   = buying_power * 0.10      # 10% of buying power per order
    max_pos      = buying_power * 0.30
    ruin_floor   = capital_usdt * ruin_floor_pct

    capital   = capital_usdt
    price     = start_price
    pos_usdt  = 0.0
    pos_entry = 0.0

    for hour in range(max_days * 24):
        # 1. Evolve price (GBM, 1-hour step)
        price *= math.exp(-0.5 * annual_vol**2 * dt_hour + vol_hourly * random.gauss(0, 1))

        # 2. Expected fills this hour
        #    Each cycle has fill_rate probability on each side.
        #    Expected fills per hour = cycles_per_hour * fill_rate
        expected_fills = cycles_per_hour * fill_rate

        # 3. BUY side fills: fills when price dips below our bid
        #    Bid is placed at price - half_spread. In an hour, price random-walks,
        #    so we model: half the fills come from bid (long) and half from ask (short).
        #    Simplification: each fill alternates to keep position near zero.
        n_fills = int(expected_fills)  # deterministic for speed
        spread_usdt = order_usdt * spread_pct  # profit per round-trip per unit

        for _ in range(n_fills):
            fee = order_usdt * MAKER_FEE
            capital -= fee  # pay maker fee

            # Randomly decide if this is a long or short fill
            if pos_usdt == 0:
                # Open new position
                if random.random() < 0.5:
                    pos_usdt = order_usdt
                else:
                    pos_usdt = -order_usdt
                pos_entry = price
            elif pos_usdt > 0:
                # Close long, capture spread
                pnl = (price * (1 + spread_pct) - pos_entry) / pos_entry * pos_usdt
                capital += pnl
                pos_usdt = 0.0
            else:
                # Close short, capture spread
                pnl = (pos_entry - price * (1 - spread_pct)) / pos_entry * abs(pos_usdt)
                capital += pnl
                pos_usdt = 0.0

        # 4. Stop-loss check on open position
        if pos_usdt != 0 and pos_entry > 0:
            if pos_usdt > 0:
                upct = (price - pos_entry) / pos_entry * 100
            else:
                upct = (pos_entry - price) / pos_entry * 100
            if upct <= stop_loss_pct:
                notional = abs(pos_usdt)
                pnl = upct / 100 * notional
                fee = notional * TAKER_FEE
                capital += pnl - fee
                pos_usdt = pos_entry = 0.0

        # 5. Ruin check
        if capital <= ruin_floor:
            return hour + 1, True

    return max_days * 24, False


def bar(pct, width=36, fill="#", empty="-"):
    filled = int(width * pct / 100)
    return f"[{fill*filled}{empty*(width-filled)}] {pct:.1f}%"


def fmt(rows, headers):
    if HAS_TAB:
        print(tabulate(rows, headers=headers, tablefmt="simple"))
    else:
        print("  ".join(f"{h:<24}" for h in headers))
        for r in rows: print("  ".join(f"{str(v):<24}" for v in r))


def study(n, capital, leverage, spread, fill_rate, stop_loss, ruin_pct, max_days):
    """Run N scenarios and return summary statistics."""
    ruin_hours = []
    survive    = 0
    for _ in range(n):
        h, ruined = run_hourly_scenario(
            capital_usdt=capital, leverage=leverage, spread_pct=spread,
            fill_rate=fill_rate, stop_loss_pct=stop_loss,
            ruin_floor_pct=ruin_pct, max_days=max_days,
        )
        if ruined:
            ruin_hours.append(h)
        else:
            survive += 1

    ruin_pct_out = len(ruin_hours) / n * 100
    if ruin_hours:
        s = sorted(ruin_hours)
        return ruin_pct_out, s[int(len(s)*.10)], statistics.median(s), s[int(len(s)*.90)]
    return ruin_pct_out, None, None, None


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--capital",   type=float, default=50.0)
    p.add_argument("--leverage",  type=int,   default=10)
    p.add_argument("--spread",    type=float, default=0.0004)
    p.add_argument("--fill-rate", type=float, default=0.35)
    p.add_argument("--stop-loss", type=float, default=-0.5)
    p.add_argument("--scenarios", type=int,   default=2000)
    p.add_argument("--max-days",  type=int,   default=90)
    p.add_argument("--ruin-pct",  type=float, default=0.10)
    a = p.parse_args()

    print(f"""
{Fore.CYAN}+============================================================+
|   {Fore.YELLOW}BITUNIX BOT - RUIN / SURVIVAL ANALYSIS (FAST){Fore.CYAN}          |
|   {Fore.WHITE}How long do your ${a.capital:.0f} last before running out?{Fore.CYAN}      |
+============================================================+{Style.RESET_ALL}
  Capital     : ${a.capital:.2f} USDT
  Leverage    : {a.leverage}x  (buying power: ${a.capital*a.leverage:.0f})
  Spread      : +/-{a.spread*100:.3f}%
  Stop-Loss   : {a.stop_loss:.1f}% per position
  Ruin floor  : ${a.capital*a.ruin_pct:.2f} USDT ({a.ruin_pct*100:.0f}% of start)
  Scenarios   : {a.scenarios:,}
  Max horizon : {a.max_days} days
""")

    # ── MAIN STUDY ────────────────────────────────────────────────────────────
    print(f"  {Fore.YELLOW}Analyzing {a.scenarios:,} scenarios...{Style.RESET_ALL}", flush=True)
    rp, p10h, med_h, p90h = study(
        a.scenarios, a.capital, a.leverage, a.spread,
        a.fill_rate, a.stop_loss, a.ruin_pct, a.max_days
    )
    survive_pct = 100 - rp
    print(f"  {Fore.GREEN}Done!{Style.RESET_ALL}\n")

    ruin_color = Fore.RED if rp > 60 else (Fore.YELLOW if rp > 30 else Fore.GREEN)

    print(f"{Fore.CYAN}  RUIN PROBABILITY (within {a.max_days} days):{Style.RESET_ALL}")
    print(f"  {ruin_color}{bar(rp)}{Style.RESET_ALL}   <- Ruined")
    print(f"  {Fore.GREEN}{bar(survive_pct, fill='=', empty=' ')}{Style.RESET_ALL}   <- Survived\n")

    if med_h:
        def fmt_time(h):
            if h < 24:   return f"{h:.0f} horas"
            if h < 168:  return f"{h/24:.1f} dias"
            if h < 720:  return f"{h/168:.1f} semanas"
            return f"{h/720:.1f} meses"

        print(f"{Fore.CYAN}  CUANTO DURAN LOS $50 (escenarios que perdieron):{Style.RESET_ALL}")
        rows = [
            ["Rapido (P10)",  f"{p10h:.0f}h",  fmt_time(p10h),  "El 10% pierde en este tiempo o menos"],
            ["Mediano (P50)", f"{med_h:.0f}h",  fmt_time(med_h), "La mitad de los perdedores llegan aqui"],
            ["Lento (P90)",   f"{p90h:.0f}h",  fmt_time(p90h),  "El 90% ya perdio en este tiempo"],
        ]
        fmt(rows, ["Velocidad", "Horas", "Tiempo Humano", "Significado"])
    else:
        print(f"  {Fore.GREEN}  Ningun escenario llego a ruin en {a.max_days} dias!{Style.RESET_ALL}")

    # ── LEVERAGE COMPARISON ───────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}  COMPARACION POR LEVERAGE (${a.capital:.0f} capital, {a.max_days} dias):{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Corriendo...{Style.RESET_ALL}", flush=True)
    lev_rows = []
    for lev in [3, 5, 10, 20]:
        rp2, _, med2, _ = study(600, a.capital, lev, a.spread, a.fill_rate, a.stop_loss, a.ruin_pct, a.max_days)
        buying = a.capital * lev
        med_str = (f"{med2/24:.0f} dias" if med2 and med2 >= 24 else (f"{med2:.0f}h" if med2 else f">{a.max_days}d"))
        ruin_c = Fore.RED if rp2>60 else (Fore.YELLOW if rp2>30 else Fore.GREEN)
        lev_rows.append([f"{lev}x", f"${buying:.0f}", f"{rp2:.0f}%", med_str,
                         "ALTO RIESGO" if rp2>60 else ("MEDIO" if rp2>30 else "BAJO RIESGO")])
    fmt(lev_rows, ["Leverage", "Poder Compra", "% Pierde", "Tiempo Mediano Ruina", "Nivel Riesgo"])

    # ── STOP-LOSS COMPARISON ──────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}  IMPACTO DEL STOP-LOSS ({a.leverage}x leverage, ${a.capital:.0f}):{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Corriendo...{Style.RESET_ALL}", flush=True)
    sl_rows = []
    for sl in [-0.25, -0.5, -1.0, -2.0, -5.0]:
        rp3, _, med3, _ = study(600, a.capital, a.leverage, a.spread, a.fill_rate, sl, a.ruin_pct, a.max_days)
        med_str = (f"{med3/24:.0f} dias" if med3 and med3>=24 else (f"{med3:.0f}h" if med3 else f">{a.max_days}d"))
        desc = "Se activa mucho" if abs(sl)<0.5 else ("Balanceado" if abs(sl)<2 else "Raro, golpe grande")
        sl_rows.append([f"{sl}%", f"{rp3:.0f}%", med_str, desc])
    fmt(sl_rows, ["Stop-Loss", "% Pierde", "Tiempo Mediano Ruina", "Nota"])

    # ── CONCLUSIONS ───────────────────────────────────────────────────────────
    rec_lev = 5
    print(f"""
{Fore.CYAN}+============================================================+
|  {Fore.YELLOW}RESPUESTA DIRECTA{Fore.CYAN}                                          |
+============================================================+{Style.RESET_ALL}

  {Fore.WHITE}Con 10x leverage y stop-loss de -0.5%:{Style.RESET_ALL}
  {ruin_color}  Probabilidad de perder los $50: {rp:.0f}% en {a.max_days} dias{Style.RESET_ALL}
  {Fore.GREEN if med_h and med_h > 48 else Fore.YELLOW}  Tiempo tipico antes de perder:  {fmt_time(med_h) if med_h else '>'+str(a.max_days)+'d'}{Style.RESET_ALL}

  {Fore.GREEN}CONFIGURACION MAS SEGURA RECOMENDADA:{Style.RESET_ALL}
  --leverage 5 --spread 0.0005 --stop-loss -1.0 --max-loss 10.0

  {Fore.WHITE}La clave para no perder:{Style.RESET_ALL}
  1. Usa leverage bajo (3x-5x): cada stop-loss duele menos
  2. Stop-loss amplio (-1% a -2%): no se activa por ruido normal
  3. Circuit-breaker: --max-loss 10 (max $10 de perdida total)
  4. Opera en mercados laterales, pausa en tendencias fuertes
  5. Monitorea el bot y detienlo si el mercado hace breakout
""")


if __name__ == "__main__":
    main()
