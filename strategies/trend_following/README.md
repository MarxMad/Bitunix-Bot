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
🟢 **Primera en optimizarse.** Backtester vectorizado con barrido de parámetros y
split train/test. Ver `backtest.py` y `core.py`.

```bash
# Optimizar (barrido con train/test honesto)
.venv/bin/python strategies/trend_following/backtest.py \
  strategies/mean_reversion/research/data/ETHUSDT_1m.csv --optimize
```
