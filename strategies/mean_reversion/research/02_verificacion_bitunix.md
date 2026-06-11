# 02 · Verificación contra la API real de Bitunix

> Endpoint base: `https://fapi.bitunix.com` · Consultas públicas (sin auth).
> Verificado el 2026-06-11. Docs: https://www.bitunix.com/api-docs/futures/common/introduction.html

---

## 1. ⛔ Los activos objetivo NO son operables por API

`GET /api/v1/futures/market/trading_pairs` devuelve el campo `isApiSupported`:

| Categoría | Símbolos | `isApiSupported` |
|---|---|---|
| Total perps | 639 | — |
| Cripto (BTC, ETH, …) | 591 | ✅ **true** |
| **Tokenizados** (acciones + commodities) | **48** | ❌ **false** |

Los 48 bloqueados incluyen: `AAPLUSDT, NVDAUSDT, GOOGLUSDT, METAUSDT, MSFTUSDT,
TSLAUSDT, COINUSDT, MSTRUSDT, **XAUUSDT** (oro), **XAUTUSDT**, COPPERUSDT,
NATGASUSDT, CLUSDT (crudo)`, etc.

```
Are XAU/TSLA API-tradable?  XAUUSDT=False   TSLAUSDT=False
Is BTC/ETH API-tradable?    BTCUSDT=True     ETHUSDT=True
```

**Implicación:** un intento de `place_order` sobre XAUUSDT/TSLAUSDT será rechazado.
El bot solo puede ejecutar en los 591 perps cripto. Oro y Tesla son operables
**solo manualmente** en la app/web (probable restricción regulatoria de
activos tokenizados).

---

## 2. Especificaciones de contrato (de la API)

| Campo | XAUUSDT | TSLAUSDT | BTCUSDT (ref) |
|---|---|---|---|
| `minTradeVolume` | 0.002 | 0.02 | 0.0001 |
| `basePrecision` (decimales qty) | 3 | 2 | 4 |
| `quotePrecision` (decimales precio) | 2 | 2 | 1 |
| `maxLeverage` | 200 | 50 | 200 |
| `defaultLeverage` | 20 | 10 | 20 |
| `fundingInterval` | **4 h** | **8 h** | 8 h |
| `isApiSupported` | ❌ false | ❌ false | ✅ true |

**Notional mínimo** (a precio actual): XAUUSDT ≈ 0.002 × $4,078 = **$8.16**;
TSLAUSDT ≈ 0.02 × $385 = **$7.70**. Con $20 de capital, una sola posición al
mínimo ya es ~40 % del notional disponible a 1×; manejable con apalancamiento
moderado, pero deja poco margen para escalar/promediar.

---

## 3. Precio Bitunix vs dato MT4 (mismatch de venue)

| Símbolo | Bitunix `lastPrice` | Último bar MT4 | Δ |
|---|---|---|---|
| XAUUSDT | 4,077.88 | 4,081.25 | ~0.08 % |
| TSLAUSDT | 385.04 | 386.64 | ~0.41 % |

El **precio** es similar, pero el **comportamiento** difiere por horario (perp 24/7
vs CFD/equity), funding y liquidez. Para TSLA la diferencia es severa (perp 24/7
vs sesión bursátil). **No se debe calibrar la estrategia del perp con datos MT4.**

---

## 4. Funding rate (observado)

| Símbolo | `fundingRate` | Intervalo | Nota |
|---|---|---|---|
| XAUUSDT | 0.011328 | cada 4 h | Funding alto → un perp largo de oro paga funding seguido |
| TSLAUSDT | -0.004881 | cada 8 h | Negativo → los shorts pagan a los longs |

> Verificar la unidad exacta del `fundingRate` en docs (fracción vs %). El intervalo
> de 4 h del oro es relevante: pagar/cobrar funding 6 veces al día afecta a posiciones
> mantenidas, sobre todo si la estrategia es de sesión (varias horas).

---

## 5. Comisiones — pendiente de confirmar para tokenizados

- La API pública **no expone** la tabla de fees por símbolo.
- Los fees cripto VIP0 documentados en el README del proyecto: **maker 0.020 % /
  taker 0.060 %**. Eso es lo usado en los backtests del doc 01.
- ⚠️ **Los activos tokenizados pueden tener fees distintos.** Hay que confirmarlos
  en la app (Bitunix → Fees / VIP) o en el detalle del contrato antes de operar.
- **Acción pendiente:** validar fee real de XAUUSDT en la UI; si es mayor a 0.06 %
  taker, el ya-de-por-sí-delgado edge de sesión se vuelve aún más difícil.

---

## 6. Endpoints útiles confirmados (públicos, sin auth)

| Endpoint | Uso |
|---|---|
| `/api/v1/futures/market/trading_pairs` | Specs de contrato + `isApiSupported` |
| `/api/v1/futures/market/tickers` | Precio/volumen 24h (filtra `symbol` del lado cliente; el server ignora el filtro) |
| `/api/v1/futures/market/kline?symbol=&interval=1m&limit=` | Velas reales de Bitunix (campo `time` en ms, paso 60000) |
| `/api/v1/futures/market/funding_rate?symbol=` | Funding actual + intervalo |
| `/api/v1/futures/market/depth?symbol=&limit=` | Libro de órdenes |

> Para backtesting **correcto**, descargar klines de Bitunix vía `kline` (datos del
> venue real), no usar los CSV de MT4.
