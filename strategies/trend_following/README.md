# Trend-Following / Momentum

> **Hipótesis:** ETH tiende (movimientos direccionales sostenidos). En vez de
> pelear la tendencia (como hace la reversión), la **montamos**: comprar fuerza,
> vender debilidad. Es lo OPUESTO a `mean_reversion`.

## Señal
Cruce de medias móviles exponenciales (EMA rápida vs lenta) sobre un timeframe
alto (1h/4h) para reducir el ruido y el costo de comisiones:
- EMA_rápida > EMA_lenta → **largo**
- EMA_rápida < EMA_lenta → **corto**

Variantes a explorar: banda de separación mínima (anti-whipsaw), filtro de fuerza
de tendencia (slope/ADX), salida por stop ATR.

## Enemigos
- **Whipsaw**: en mercados laterales, la tendencia falsa genera muchos trades perdedores.
- **Comisiones**: cada flip cuesta. Por eso operamos en TF alto (menos flips).
  Entradas a mercado → fee **taker** (0.06%).

## Estado
🟢 **Optimizada, pulida y ejecutable.** Backtester vectorizado + estrategia en vivo
(dry-run) + dashboard dedicado. Resultados y config validada en
[`RESULTS.md`](RESULTS.md). Edge robusto en XLM, no en ETH.

## Archivos
| Archivo | Rol |
|---|---|
| `core.py` | Señal vectorizada (`compute_position`) + `vol_target` (sizing) |
| `backtest.py` | Backtester + `--optimize` (barrido con train/test) |
| `strategy_trend.py` | Estrategia en vivo + dry-run (rebalanceo a posición objetivo) |
| `trend_server.py` + `dashboard/trend.html` | Dashboard dedicado (puerto 8002) |
| `RESULTS.md` | Resultados 360d + polish (vol-target) |

## Uso
```bash
# 1. Optimizar / re-validar
.venv/bin/python strategies/trend_following/backtest.py <DATA.csv> --optimize

# 2. Backtest de la config pulida
.venv/bin/python strategies/trend_following/backtest.py \
  strategies/mean_reversion/research/data/XLMUSDT_1m.csv \
  --tf 1h --fast 12 --slow 100 --min-sep 0.01 --vol-target 0.006 --max-lev 1.5

# 3. Dashboard en vivo (dry-run) — puerto 8002
.venv/bin/python strategies/trend_following/trend_server.py   # abrir http://localhost:8002

# 4. CLI headless
.venv/bin/python bot.py --trend --symbol XLMUSDT --dry-run
```
