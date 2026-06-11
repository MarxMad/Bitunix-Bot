# 01 · Análisis cuantitativo de los datos MT4

> Fuente: `XAUUSD1.csv` y `TSLA.us1.csv` (broker MT4). Formato sin header:
> `Fecha, Hora, Open, High, Low, Close, Volume`.
> Reproducible con los scripts en `scripts/`.

---

## A. XAUUSD (Oro) — `XAUUSD1.csv`

### Estructura

| Métrica | Valor |
|---|---|
| Timeframe | **M1 (1 minuto)** — intervalo modal 60 s |
| Total de barras | **65,004** (≈64,948 útiles tras limpiar gaps) |
| Rango de fechas | 2025-07-02 16:52 → 2026-06-11 19:32 |
| **Datos realmente continuos** | **Jul–Sep 2025** (~2.5 meses) |
| Rango de precio | $3,281.59 → $4,083.50 |
| Zona horaria | Broker ≈ **GMT+3** (deducida de los cierres de fin de semana) |

⚠️ **Hueco de 271 días** entre 2025-09-13 y 2026-06-11. El "dataset real" son
2.5 meses. Muestra pequeña → conclusiones con baja confianza estadística.

### Perfil horario (hora del broker)

- **Mayor volumen y volatilidad: 17:00** (avg vol 82.9, rango 1.58 pts).
- **Ventana caliente: 16:00–19:00** ≈ 13:00–16:00 GMT = **solape Londres/NY**.
  Concentra ~24 % del volumen y casi toda la volatilidad explotable.
- Horas muertas: 22:00–04:00 (sesión asiática).

### Carácter estadístico (limpio, sin retornos cruzando gaps)

| Métrica | Valor | Lectura |
|---|---|---|
| Vol anualizada | **13.2 %** | Oro es tranquilo (vs cripto 50-80 %) |
| Std por minuto | 2.18 bps | |
| Variance Ratio (5–60 min) | **≈ 1.0** | **Camino aleatorio** — sin momentum/reversión en M1 |
| Curtosis (exceso) | 25.8 | Colas gordas, clústeres de volatilidad |
| Drift hora 17 | +0.123 bps, **t≈1.94** | Sesgo alcista marginalmente significativo en NY |

### Backtests de hipótesis (comisión 0.06 % taker)

| Estrategia | Resultado | Veredicto |
|---|---|---|
| Opening-Range Breakout (9 configs) | **-0.23 a -0.56 pts/trade** | ❌ Los breakouts se desvanecen |
| Trend-following EMA (M5) | **-22 % neto** (bruto ya -1.2 %) | ❌ Comisiones lo matan (352 flips) |
| Mean-reversion Z-score | **-7 % a -27 %** (win 16-27 %) | ❌ Fadear un trend fuerte = muerte |
| **Session long-bias (NY 16→19h)** | **+2.75 %, Sharpe ~1.79, win 49 %** | ✅ Único con expectativa positiva |
| Short overnight (hora 0) | +1.35 %, win 55.6 % | ✅ Marginal |
| Buy & hold (contexto) | **+8.7 %** | Régimen **alcista fuerte** en la muestra |

### Conclusiones XAU

1. **El fee (0.06 %) es el enemigo #1.** Ninguna estrategia de alta frecuencia
   sobrevive los costos en este activo.
2. A escala de minuto el oro es **camino aleatorio** — no hay edge de micro-timing.
3. Lo único con expectativa positiva es **baja frecuencia + sesgo de sesión NY**,
   pero está **inflado por el régimen alcista** (+8.7 % en la muestra). No es un
   edge garantizado fuera de muestra.

---

## B. TSLA — `TSLA.us1.csv`

### Estructura

| Métrica | Valor |
|---|---|
| Timeframe | **M1 (1 minuto)** |
| Total de barras | **2,048** (¡solo ~1 semana!) |
| Rango de fechas | 2026-06-04 18:19 → 2026-06-11 19:51 |
| Rango de precio | $380.03 → $424.22 |
| Horario presente | **16:00–23:00 broker** = sesión bursátil de EE.UU. (9:30–16:00 ET) |

### Hallazgos

- **NO cotiza 24 h** — es una acción: cierra de noche y fines de semana
  (5 gaps > 2 h en una semana).
- **Pico de volumen y volatilidad: 16:00 broker** = apertura de NY (volatilidad
  clásica de apertura).
- Vol por minuto 13.5 bps → **anualizada ~42 %** (3× más volátil que el oro).
- Autocorrelación lag-1 = -0.03 (leve reversión de microestructura en apertura).

### ⚠️ Advertencia estructural sobre TSLA

1. **Muestra inutilizable**: 1 semana de datos no permite ninguna conclusión robusta.
2. **Mismatch de venue grave**: el TSLA de MT4 cotiza 6.5 h/día; el `TSLAUSDT` de
   Bitunix es un **perp 24/7**. Los gaps nocturnos, el funding y la dinámica de
   precio son **estructuralmente distintos**. Estos datos NO modelan el perp de Bitunix.

---

## Resumen ejecutivo del análisis

- El edge real más sólido detectado es **baja frecuencia + sesgo de sesión en oro**,
  con la salvedad del régimen.
- **Cualquier estrategia debe operar pocas veces** — los fees dominan el resultado.
- Para TSLA no hay datos suficientes ni representativos.
- **Pero** (ver doc 02) nada de esto es automatizable en Bitunix por el bloqueo de API.
