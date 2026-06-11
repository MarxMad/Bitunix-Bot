# Investigación de Estrategia — XAUUSD / TSLA en Bitunix

> Carpeta de research cuantitativo para diseñar una estrategia con profit.
> Capital objetivo: **$20 USD** en futuros. Filosofía: **no reventar la cuenta**.
> Fecha: 2026-06-11 · Responsable: Bitunix México (Gerardo)

## ⛔ Hallazgo crítico (léelo antes que nada)

**XAUUSDT (oro) y TSLAUSDT (Tesla) NO se pueden operar por la API de Bitunix.**

La API marca `isApiSupported: false` para los **48 activos tokenizados** (acciones
y commodities: AAPL, NVDA, GOOGL, META, MSFT, TSLA, XAU oro, COPPER, NATGAS, crudo…).
Solo los **591 perps cripto** (BTC, ETH, etc.) son operables por bot.

➡️ **Un bot automatizado NO puede ejecutar órdenes en oro ni Tesla en Bitunix.**
Se pueden operar **a mano** en la app/web, pero no programáticamente.

Esto redefine las opciones (ver `03_diseno_estrategia.md`).

## ⚠️ Segundo hallazgo: los datos son de MT4, no de Bitunix

Los CSV (`XAUUSD1.csv`, `TSLA.us1.csv`) vienen de un broker MT4, **no de Bitunix**.
Difieren en horario, comisiones, funding y tamaño de contrato. En particular el
TSLA de MT4 cotiza solo en sesión bursátil de EE.UU. (6.5 h/día), mientras que el
perp de Bitunix es 24/7 → comportamiento estructuralmente distinto.

## Índice de documentos

| Doc | Contenido |
|---|---|
| [`01_analisis_datos_mt4.md`](01_analisis_datos_mt4.md) | Análisis cuantitativo de los CSV: timeframe, rango, horarios, volatilidad, backtests de hipótesis |
| [`02_verificacion_bitunix.md`](02_verificacion_bitunix.md) | Verificación contra la API real: símbolos, specs de contrato, comisiones, funding, el bloqueo de API |
| [`03_diseno_estrategia.md`](03_diseno_estrategia.md) | Diseño de estrategia + gestión de riesgo para $20, y las 3 rutas viables |
| [`04_backtest_eth_xlm.md`](04_backtest_eth_xlm.md) | **Ruta B**: backtest con datos REALES de Bitunix. Edge validado en XLM (reversión maker), ETH descartado |
| [`05_implementacion.md`](05_implementacion.md) | Implementación: `meanrev_core.py`, `backtest.py`, `strategy_meanrev.py`, cómo correr dry-run/live |

## ✅ Resultado actual (Ruta B elegida): edge validado en XLMUSDT

Reversión a la media en extremos (3σ) con ejecución **maker**: +18 % en 60 días
(Sharpe 1.3), se sostiene out-of-sample y sobrevive slippage. **ETH no tiene edge.**
Detalle y caveats en [`04_backtest_eth_xlm.md`](04_backtest_eth_xlm.md).

## Scripts

En [`scripts/`](scripts/) — análisis reproducible con pandas:
- `analyze_xauusd.py` — estructura, perfil horario, autocorrelación, variance ratio
- `analyze_xauusd2.py` — stats limpias + backtests (ORB, EMA trend, drift de sesión)
- `analyze_xauusd3.py` — mean-reversion + sesgo de sesión + contexto de régimen
