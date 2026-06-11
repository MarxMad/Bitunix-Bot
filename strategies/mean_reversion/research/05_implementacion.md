# 05 · Implementación de la estrategia (Ruta B)

> Estrategia de reversión a la media en extremos (3σ), ejecución maker, validada
> en XLMUSDT (ver doc 04). Implementada reutilizando `BitunixClient`.

## Archivos (en `strategies/mean_reversion/`)

| Archivo | Rol |
|---|---|
| `meanrev_core.py` | **Lógica de señal pura y compartida**: z-score + `decide()` (máquina de estados). La usan TANTO el backtest como el bot en vivo → lo validado == lo que opera. |
| `backtest.py` | Backtester reutilizable. Lee CSV de Bitunix, corre `decide()` barra a barra con fees/slippage reales, reporta métricas y split out-of-sample. |
| `strategy_meanrev.py` | Estrategia en vivo + dry-run. Máquina de estados de ejecución con órdenes maker, stop con escalado a market, y circuit breakers para $20. |
| `meanrev_server.py` + `dashboard/meanrev.html` | Dashboard dedicado (puerto 8001). |
| `../../bot.py` (raíz, modificado) | Flag `--meanrev` + import de `BitunixClient` arreglado. |
| `../../shared/bitunix_client.py` | Cliente REST compartido. |

## Cómo correr

### 1. Backtest (validar/re-validar con datos frescos)
```bash
# Bajar datos frescos de Bitunix (no MT4)
python3 strategies/mean_reversion/research/scripts/fetch_bitunix_klines.py XLMUSDT --days 60 --interval 1m

# Backtest con la config validada (stop maker, slippage 2bps, split OOS)
python3 strategies/mean_reversion/backtest.py strategies/mean_reversion/research/data/XLMUSDT_1m.csv --slip-bps 2 --oos
```
Parámetros: `--z-in --z-exit --z-stop --max-hold --lookback --tf --stop-market`.

### 2. Dry-run en vivo (recomendado ANTES de arriesgar dinero)
Requiere `.env` con `API_KEY`/`SECRET_KEY` (solo para el chequeo de cuenta; **no
coloca órdenes**). Observa las decisiones en tiempo real contra el mercado real:
```bash
python3 bot.py --meanrev --symbol XLMUSDT --dry-run --interval 5m
```

### 3. En vivo (cuando el dry-run confirme buenos fills)
```bash
python3 bot.py --meanrev --symbol XLMUSDT --qty 80 --leverage 3
```

## Parámetros clave (defaults validados)

| Parám | Default | Nota |
|---|---|---|
| `z_in` | 3.0 | El edge vive en ≥3σ. **No bajar a 2.5** (pierde, ver doc 04). |
| `z_exit` | 0.5 | Salir cerca de la media |
| `z_stop` | 4.5 | Stop catastrófico |
| `max_hold_bars` | 24 | Time-stop = 2 h en M5 |
| `order_qty` | 80 | Mínimo de XLMUSDT (~$15 notional) |
| `leverage` | 3 | El riesgo lo fija el stop, no el leverage |

## Gestión de riesgo (para $20)

- `max_total_loss_usdt = 4.0` → circuit breaker de sesión (20 % de la cuenta).
- `daily_loss_limit_usdt = 1.0` → pausa el resto del día UTC.
- `cooldown_after_losses = 2` → 2 pérdidas seguidas → pausa el día.
- Stop catastrófico maker con **escalado a market** si no se llena en 20 s
  (protege la cuenta en un movimiento rápido).

## Diferencias entre backtest y vivo (honestidad de ejecución)

| | Backtest | Vivo |
|---|---|---|
| Fill de entrada/salida | Asumido al cierre de barra | Orden LIMIT real; puede no llenarse |
| Entrada no llenada | N/A | Se cancela tras `entry_timeout_bars` y vuelve a FLAT |
| Stop | maker (o taker con `--stop-market`) | maker + escalado a market |

➡️ El **dry-run es justo para medir esta diferencia** (calidad de fills a 3σ)
antes de comprometer capital. Si los fills en vivo son mucho peores que el
backtest, subir `z_in` o esperar mejores condiciones.

## Dashboard dedicado

Dashboard propio para ESTA estrategia (separado del de volumen), en el puerto 8001:

| Archivo | Rol |
|---|---|
| `meanrev_server.py` | Servidor FastAPI; corre la estrategia instrumentada en un hilo y empuja estado por WebSocket |
| `dashboard/meanrev.html` | UI: z-score en vivo con bandas de entrada/salida/stop, curva de PnL, estado, historial de trades, log |

```bash
# requiere entorno con deps (ver requirements.txt)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python strategies/mean_reversion/meanrev_server.py
# abrir http://localhost:8001  (sirve el HTML directamente)
```
Desde la UI: ajustar símbolo/qty/leverage/timeframe/z y darle ▶ Iniciar (deja
marcado **Dry-run** para no colocar órdenes). Corre en paralelo al dashboard de
volumen (`bot_server.py`, puerto 8000) sin conflicto.

## Pendientes / mejoras futuras

- Registrar fills reales (precio/fee de la API) en vez de aproximar PnL en vivo.
- Integrar al dashboard existente (`bot_server.py`) para ver la estrategia.
- Re-descargar datos y re-validar periódicamente (los edges decaen).
