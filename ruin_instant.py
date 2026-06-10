# -*- coding: utf-8 -*-
"""
ruin_instant.py  –  Survival/Ruin Analysis (analytical, runs in seconds)
=========================================================================
Uses closed-form probability models + a small vectorized simulation
to estimate: how long do your $50 last?
"""
import sys, math, random, statistics

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

# ── Constants ─────────────────────────────────────────────────────────────────
MAKER_FEE   = 0.0002   # 0.020%
TAKER_FEE   = 0.0006   # 0.060%
BTC_ANNUAL_VOL  = 0.80   # 80% annual volatility (BTC)
HOURS_PER_YEAR  = 8760.0
HOURLY_VOL      = BTC_ANNUAL_VOL / math.sqrt(HOURS_PER_YEAR)  # ~0.855% per hour


# ── Probability of a stop-loss triggering in one hour ─────────────────────────
# Price change in 1h ~ N(0, hourly_vol). Stop-loss triggers if |move| >= threshold.
# For a long position: stop triggers if return <= stop_loss_pct/100.
def prob_sl_per_hour(stop_loss_pct_abs: float) -> float:
    """
    Probability that a 1-hour price move exceeds abs(stop_loss_pct) in adverse direction.
    Uses normal distribution approximation.
    """
    import math
    z = stop_loss_pct_abs / 100.0 / HOURLY_VOL
    # Prob that N(0,1) > z  (one tail)
    # Approximation: 0.5 * erfc(z / sqrt(2))
    # Python's math module has erfc
    return 0.5 * math.erfc(z / math.sqrt(2))


# ── Analytical model: expected sessions to ruin ───────────────────────────────
def analytical_ruin(
    capital: float,
    leverage: int,
    spread_pct: float,
    fill_rate: float,
    refresh_sec: float,
    stop_loss_pct_abs: float,   # absolute value, e.g. 0.5 for -0.5%
    ruin_floor_pct: float,
):
    """
    Returns:
      - p_sl_hour:    prob of SL firing each hour
      - loss_per_sl:  USDT lost each time SL fires
      - gain_per_rt:  USDT gained each complete round-trip
      - rt_per_hour:  expected round trips per hour
      - net_hourly_pnl: expected net PnL per hour
      - hours_to_ruin_expected: E[time to ruin] analytically
    """
    buying_power  = capital * leverage
    order_usdt    = buying_power * 0.10   # 10% of buying power per order

    # -- Stop-loss event --
    p_sl_hour    = prob_sl_per_hour(stop_loss_pct_abs)
    loss_per_sl  = order_usdt * (stop_loss_pct_abs / 100.0) + order_usdt * TAKER_FEE

    # -- Round-trip earnings --
    cycles_per_hour = 3600.0 / refresh_sec
    # Expected fills per hour (both sides)
    fills_per_hour = cycles_per_hour * fill_rate * 2  # both sides
    round_trips_per_hour = fills_per_hour / 2          # one RT needs 2 fills
    spread_captured_per_rt = order_usdt * spread_pct   # earn the spread per RT
    maker_fee_per_fill     = order_usdt * MAKER_FEE
    net_per_rt             = spread_captured_per_rt - 2 * maker_fee_per_fill

    # -- Net hourly PnL --
    gross_rt_pnl   = round_trips_per_hour * net_per_rt
    expected_sl_loss = p_sl_hour * loss_per_sl
    net_hourly_pnl = gross_rt_pnl - expected_sl_loss

    # -- Hours to ruin (simple random walk estimate) --
    ruin_floor       = capital * ruin_floor_pct
    capital_at_risk  = capital - ruin_floor
    if net_hourly_pnl >= 0:
        hours_to_ruin = float("inf")
    else:
        # Simple estimate: at avg hourly loss, how long until capital depleted
        hours_to_ruin = capital_at_risk / abs(net_hourly_pnl)

    return {
        "p_sl_hour":              p_sl_hour,
        "loss_per_sl":            loss_per_sl,
        "net_per_rt":             net_per_rt,
        "rt_per_hour":            round_trips_per_hour,
        "gross_rt_pnl_hour":      gross_rt_pnl,
        "expected_sl_loss_hour":  expected_sl_loss,
        "net_hourly_pnl":         net_hourly_pnl,
        "hours_to_ruin":          hours_to_ruin,
        "daily_pnl":              net_hourly_pnl * 24,
    }


def fmt(rows, headers):
    if HAS_TAB:
        print(tabulate(rows, headers=headers, tablefmt="simple"))
    else:
        for r in rows:
            print("  ".join(f"{str(v):<26}" for v in r))


def bar(pct, width=36, fill="#", empty="-"):
    pct = min(max(pct, 0), 100)
    filled = int(width * pct / 100)
    return f"[{fill*filled}{empty*(width-filled)}] {pct:.1f}%"


def h2str(h):
    """Convert hours to human-readable string."""
    if h == float("inf"): return "infinito (rentable!)"
    if h < 1:             return f"{h*60:.0f} minutos"
    if h < 24:            return f"{h:.1f} horas"
    if h < 168:           return f"{h/24:.1f} dias"
    if h < 720:           return f"{h/168:.1f} semanas"
    return f"{h/720:.1f} meses"


def run_fast_mc(capital, leverage, spread_pct, fill_rate, refresh_sec,
                stop_loss_pct, ruin_floor_pct, n=500, max_days=90):
    """
    Fast Monte Carlo: simulate day-by-day using analytical hourly PnL.
    Each day: apply net hourly PnL × 24 + random stop-loss shocks.
    """
    buying_power = capital * leverage
    order_usdt   = buying_power * 0.10
    sl_abs       = abs(stop_loss_pct)
    p_sl_hour    = prob_sl_per_hour(sl_abs)
    loss_per_sl  = order_usdt * (sl_abs / 100.0) + order_usdt * TAKER_FEE
    cycles_ph    = 3600.0 / refresh_sec
    rt_ph        = cycles_ph * fill_rate
    net_rt       = order_usdt * spread_pct - 2 * order_usdt * MAKER_FEE
    gross_ph     = rt_ph * net_rt
    ruin_floor   = capital * ruin_floor_pct

    ruin_days = []
    for _ in range(n):
        cap = capital
        ruined_day = None
        for day in range(max_days):
            # Expected spread income this day
            cap += gross_ph * 24
            # Stop-loss shocks: Poisson number of SL events per day
            n_sl = sum(1 for _ in range(24) if random.random() < p_sl_hour)
            cap -= n_sl * loss_per_sl
            # Add noise: daily price return affects mark-to-market
            # (Small random PnL from inventory exposure)
            daily_vol_shock = capital * leverage * 0.002 * random.gauss(0, 1)
            cap += daily_vol_shock * 0.01  # small residual inventory exposure
            if cap <= ruin_floor:
                ruined_day = day + 1
                break
        ruin_days.append(ruined_day)

    ruined   = [d for d in ruin_days if d is not None]
    survived = n - len(ruined)
    ruin_pct = len(ruined) / n * 100

    if ruined:
        sr = sorted(ruined)
        return ruin_pct, sr[int(len(sr)*.10)], statistics.median(sr), sr[int(len(sr)*.90)]
    return ruin_pct, None, None, None


def main():
    CAPITAL    = 50.0
    LEVERAGE   = 10
    SPREAD     = 0.0004
    FILL_RATE  = 0.35
    REFRESH    = 3.0
    SL         = -0.5
    RUIN_PCT   = 0.10
    MAX_DAYS   = 90
    N          = 800

    print(f"""
{Fore.CYAN}+============================================================+
|   {Fore.YELLOW}BITUNIX BOT - ANALISIS DE SUPERVIVENCIA{Fore.CYAN}                 |
|   {Fore.WHITE}Cuanto duran tus ${CAPITAL:.0f} antes de perderse?{Fore.CYAN}            |
+============================================================+{Style.RESET_ALL}
  Capital     : ${CAPITAL:.2f} USDT
  Leverage    : {LEVERAGE}x  =>  Poder de compra: ${CAPITAL*LEVERAGE:.0f} USDT
  Spread      : +/-{SPREAD*100:.3f}%  por lado
  Stop-Loss   : {SL}% por posicion
  Referencia  : BTC volatilidad anual ~80%
  Horizontes  : hasta {MAX_DAYS} dias
""")

    # ── ANALYSIS 1: Analytical hourly breakdown ────────────────────────────────
    print(f"{Fore.CYAN}  DESGLOSE ANALITICO POR HORA:{Style.RESET_ALL}")
    a = analytical_ruin(CAPITAL, LEVERAGE, SPREAD, FILL_RATE, REFRESH, abs(SL), RUIN_PCT)

    rows = [
        ["Ciclos por hora",            f"{3600/REFRESH:,.0f}"],
        ["Round-trips esperados/hora",  f"{a['rt_per_hour']:.1f}"],
        ["Ganancia por round-trip",     f"+${a['net_per_rt']:.4f}"],
        ["Ingreso bruto spread/hora",   f"+${a['gross_rt_pnl_hour']:.4f}"],
        ["Prob. stop-loss por hora",    f"{a['p_sl_hour']*100:.2f}%"],
        ["Perdida por stop-loss",       f"-${a['loss_per_sl']:.4f}"],
        ["Perdida esperada SL/hora",    f"-${a['expected_sl_loss_hour']:.4f}"],
        ["PnL NETO esperado/hora",      f"{'+'if a['net_hourly_pnl']>=0 else ''}${a['net_hourly_pnl']:.4f}"],
        ["PnL NETO esperado/dia",       f"{'+'if a['daily_pnl']>=0 else ''}${a['daily_pnl']:.4f}"],
    ]
    color_pnl = Fore.GREEN if a['net_hourly_pnl'] >= 0 else Fore.RED
    fmt(rows, ["Metrica", "Valor"])

    print(f"\n  {color_pnl}PnL diario esperado: {'+'if a['daily_pnl']>=0 else ''}${a['daily_pnl']:.3f} USDT{Style.RESET_ALL}")
    if a['net_hourly_pnl'] < 0:
        print(f"  {Fore.RED}Tiempo analitico hasta ruina: {h2str(a['hours_to_ruin'])}{Style.RESET_ALL}")
    else:
        print(f"  {Fore.GREEN}El bot es RENTABLE en promedio con esta configuracion{Style.RESET_ALL}")

    # ── ANALYSIS 2: Fast Monte Carlo ──────────────────────────────────────────
    print(f"\n{Fore.CYAN}  SIMULACION MONTE CARLO ({N} escenarios x {MAX_DAYS} dias):{Style.RESET_ALL}")
    print(f"  {Fore.YELLOW}Corriendo...{Style.RESET_ALL}", flush=True)
    rp, p10d, med_d, p90d = run_fast_mc(
        CAPITAL, LEVERAGE, SPREAD, FILL_RATE, REFRESH, SL, RUIN_PCT, N, MAX_DAYS
    )
    survive_pct = 100 - rp
    print(f"  {Fore.GREEN}Listo!{Style.RESET_ALL}\n")

    ruin_color = Fore.RED if rp > 60 else (Fore.YELLOW if rp > 30 else Fore.GREEN)
    surv_color = Fore.GREEN if survive_pct > 60 else (Fore.YELLOW if survive_pct > 30 else Fore.RED)

    print(f"  {ruin_color}{bar(rp)}  Perdieron todo{Style.RESET_ALL}")
    print(f"  {surv_color}{bar(survive_pct, fill='=', empty=' ')}  Sobrevivieron {MAX_DAYS}d{Style.RESET_ALL}\n")

    if med_d:
        print(f"  {Fore.WHITE}De los que perdieron:{Style.RESET_ALL}")
        fmt([
            ["Ruina rapida  (P10)", f"{p10d:.0f} dias",  h2str(p10d*24)],
            ["Ruina mediana (P50)", f"{med_d:.0f} dias", h2str(med_d*24)],
            ["Ruina lenta   (P90)", f"{p90d:.0f} dias",  h2str(p90d*24)],
        ], ["Escenario", "Dias", "Equivalente"])

    # ── ANALYSIS 3: Leverage comparison ───────────────────────────────────────
    print(f"\n{Fore.CYAN}  CUANTO DURA SEGUN EL LEVERAGE (${CAPITAL:.0f} capital, {MAX_DAYS} dias):{Style.RESET_ALL}")
    lev_rows = []
    for lev in [3, 5, 10, 20]:
        a2 = analytical_ruin(CAPITAL, lev, SPREAD, FILL_RATE, REFRESH, abs(SL), RUIN_PCT)
        rp2, _, med2, _ = run_fast_mc(CAPITAL, lev, SPREAD, FILL_RATE, REFRESH, SL, RUIN_PCT, 400, MAX_DAYS)
        med_str = f"{med2:.0f}d" if med2 else f">{MAX_DAYS}d"
        daily_str = f"{'+'if a2['daily_pnl']>=0 else ''}${a2['daily_pnl']:.3f}"
        risk = "ALTO" if rp2 > 60 else ("MEDIO" if rp2 > 30 else "BAJO")
        risk_c = Fore.RED if risk=="ALTO" else (Fore.YELLOW if risk=="MEDIO" else Fore.GREEN)
        lev_rows.append([
            f"{lev}x",
            f"${CAPITAL*lev:.0f}",
            daily_str,
            f"{rp2:.0f}%",
            med_str,
            f"{risk_c}{risk}{Style.RESET_ALL}"
        ])
    fmt(lev_rows, ["Leverage", "Poder Compra", "PnL/dia", "% Pierde (90d)", "Ruina Mediana", "Riesgo"])

    # ── ANALYSIS 4: Stop-loss comparison ──────────────────────────────────────
    print(f"\n{Fore.CYAN}  IMPACTO DEL STOP-LOSS ({LEVERAGE}x leverage, ${CAPITAL:.0f}, {MAX_DAYS} dias):{Style.RESET_ALL}")
    sl_rows = []
    for sl_val in [-0.25, -0.5, -1.0, -2.0, -5.0]:
        a3 = analytical_ruin(CAPITAL, LEVERAGE, SPREAD, FILL_RATE, REFRESH, abs(sl_val), RUIN_PCT)
        rp3, _, med3, _ = run_fast_mc(CAPITAL, LEVERAGE, SPREAD, FILL_RATE, REFRESH, sl_val, RUIN_PCT, 400, MAX_DAYS)
        med_str3 = f"{med3:.0f}d" if med3 else f">{MAX_DAYS}d"
        freq = f"{a3['p_sl_hour']*100:.1f}%/hora"
        sl_rows.append([f"{sl_val}%", freq, f"-${a3['loss_per_sl']:.3f}", f"{rp3:.0f}%", med_str3])
    fmt(sl_rows, ["Stop-Loss", "Frecuencia", "Perdida c/vez", "% Pierde (90d)", "Ruina Mediana"])

    # ── RECOMMENDED CONFIG ─────────────────────────────────────────────────────
    print(f"\n{Fore.CYAN}  CONFIG RECOMENDADA PARA MAX SUPERVIVENCIA + VOLUMEN:{Style.RESET_ALL}")
    a_rec = analytical_ruin(CAPITAL, 5, 0.0005, FILL_RATE, REFRESH, 1.0, RUIN_PCT)
    rp_rec, _, med_rec, _ = run_fast_mc(CAPITAL, 5, 0.0005, FILL_RATE, REFRESH, -1.0, RUIN_PCT, 500, MAX_DAYS)
    rec_color = Fore.GREEN if a_rec['daily_pnl'] >= 0 else Fore.YELLOW

    print(f"""
  {Fore.WHITE}--leverage 5 --spread 0.0005 --stop-loss -1.0 --max-loss 10.0{Style.RESET_ALL}

  Poder de compra : ${CAPITAL*5:.0f} USDT
  PnL diario esp  : {rec_color}{'+'if a_rec['daily_pnl']>=0 else ''}${a_rec['daily_pnl']:.3f} USDT/dia{Style.RESET_ALL}
  % pierde (90d)  : {rp_rec:.0f}%
  Ruina mediana   : {f"{med_rec:.0f} dias" if med_rec else ">90 dias"}
""")

    # ── RESPUESTA DIRECTA ──────────────────────────────────────────────────────
    print(f"""{Fore.CYAN}+============================================================+
|  {Fore.YELLOW}RESPUESTA DIRECTA A TU PREGUNTA{Fore.CYAN}                            |
+============================================================+{Style.RESET_ALL}

  {Fore.WHITE}Con 10x leverage y stop-loss de -0.5%:{Style.RESET_ALL}
  {ruin_color}  Probabilidad de perder los $50 en 90 dias: {rp:.0f}%{Style.RESET_ALL}
  {'  Tiempo tipico hasta perder: ' + (f'{med_d:.0f} dias' if med_d else '>90 dias')}
  {'  PnL diario esperado: ' + (f'+${a["daily_pnl"]:.3f}' if a["daily_pnl"]>=0 else f'${a["daily_pnl"]:.3f}') + ' USDT'}

  {Fore.GREEN}EL VERDADERO ENEMIGO NO SON LAS FEES, SON LOS STOP-LOSSES:{Style.RESET_ALL}
  Cada stop-loss en 10x cuesta ~${a['loss_per_sl']:.2f} USDT
  Con $50, solo aguantas ~{int(CAPITAL * (1-RUIN_PCT) / a['loss_per_sl'])} stop-losses consecutivos

  {Fore.GREEN}CONSEJO:{Style.RESET_ALL}
  -> Usa 5x leverage y stop-loss de -1.0%
  -> Tu bot puede durar semanas o meses en vez de dias
  -> Corre: python -X utf8 bot.py --leverage 5 --stop-loss -1.0 --max-loss 10
""")


if __name__ == "__main__":
    main()
