# 03 · Diseño de estrategia y gestión de riesgo ($20)

> Filosofía con $20: **el objetivo #1 es no reventar la cuenta.** El profit viene
> de sobrevivir + capturar un edge pequeño y robusto, operando **poco**.

---

## El dilema central

El análisis de datos (doc 01) apunta a un edge de **baja frecuencia en oro**
(sesgo de sesión NY). Pero la verificación (doc 02) dice que **oro y Tesla NO son
automatizables por la API de Bitunix**. Hay que elegir ruta.

## Las 3 rutas viables

### Ruta A — Bot de SEÑALES para oro/Tesla (ejecución manual)
El bot calcula la señal (sesgo de sesión + filtro de tendencia + sizing) y te
**avisa** (log/Telegram/dashboard). Tú ejecutas a mano en la app de Bitunix.
- ✅ Mantiene oro/Tesla, que es lo que analizaste.
- ✅ Legal y sin chocar con el bloqueo de API.
- ❌ No es automático; dependes de tu reacción.

### Ruta B — Pivotar la estrategia a un perp cripto API-soportado ⭐ recomendada para automatizar
Aplicar la **misma metodología** a un perp líquido cripto (p. ej. BTCUSDT/ETHUSDT),
calibrando con **klines reales de Bitunix** (no MT4).
- ✅ Totalmente automatizable; reutiliza tu `BitunixClient` y motor de riesgo.
- ✅ Datos del venue real → backtest fiable.
- ❌ Hay que re-correr el análisis sobre cripto (el edge de oro no transfiere directo).

### Ruta C — Híbrido
Automatizar en cripto (Ruta B) + usar el bot como generador de señales de oro
(Ruta A) para que tú operes oro manualmente. Diversifica.

---

## Estrategia propuesta: "Session-Drift + Risk-First"

Aplicable como **señal** (oro, Ruta A) o **automatizada** (cripto, Ruta B). Es
deliberadamente de **baja frecuencia** porque los fees matan el scalping (doc 01).

### Lógica de entrada — máx. 1–2 trades/día
1. **Ventana**: operar solo en la franja de mayor liquidez del activo
   (oro: 16:00–19:00 broker ≈ solape Londres/NY; cripto: definir con datos de Bitunix).
2. **Filtro de tendencia**: solo ir **a favor** de la EMA(50) en M15
   (largo si precio > EMA50, corto si <). Evita fadear movimientos fuertes —
   el error que hundió el mean-reversion en los backtests.
3. **Trigger**: entrar en pullback a VWAP de sesión o EMA(20) M5 dentro de la ventana
   (entrar "barato", no perseguir el breakout — que demostró perder).

### Gestión de riesgo (el corazón, con $20)

| Parámetro | Valor | Razón |
|---|---|---|
| Riesgo por trade | **1.5 % = $0.30** | Sobrevivir a rachas de pérdidas |
| Stop | **1 × ATR(14)** | Adaptado a volatilidad |
| Sizing | `qty = riesgo_usd / (dist_stop × valor_punto)` | El stop define el tamaño, NO un qty fijo |
| Take-profit | 1.5–2 × ATR (RR ≥ 1.5) | Esperanza positiva con win 45-50 % |
| Apalancamiento | 3–5× | Solo para margen; el riesgo real lo fija el stop |
| Salida forzada | Al cierre de la ventana | No mantener overnight (funding + gaps) |
| **Circuit breaker diario** | 2 SL seguidos **o** -$1 (5 %) | Parar el día; evitar tilt/ruina |
| Mínimo de contrato | XAU 0.002 / TSLA 0.02 | Ojo: con $20 el notional mínimo ya es grande |

### Matemática de supervivencia
Con riesgo de 1.5 %/trade y circuit breaker de -5 %/día, una racha de 10 pérdidas
seguidas (muy improbable con cualquier edge) deja la cuenta en ~$17. La cuenta
**no se revienta** salvo catástrofe de slippage/gap. Ese es el objetivo con $20:
no buscar 10×, sino **no morir** mientras el edge pequeño compone.

### Expectativa realista (honesta)
- El edge medido en oro es **pequeño y dependiente de régimen**. Con $20 y fees,
  el profit absoluto por trade es de centavos. Esto es un ejercicio de **disciplina
  y validación**, no una máquina de dinero.
- El valor real ahora es **construir la infra correcta** (backtester con datos de
  Bitunix + gestión de riesgo) para escalar cuando el edge esté validado.

---

## Próximos pasos sugeridos

1. **Decidir ruta** (A / B / C).
2. **Confirmar fees reales** de XAUUSDT en la app (doc 02 §5).
3. **Construir backtester con costos** que use **klines de Bitunix** (no MT4) →
   módulo `backtest.py`. Validar el edge sobre datos del venue real.
4. Implementar `strategy_session_drift.py` (señal o ejecución según ruta).
5. Probar en **dry-run** antes de comprometer los $20.

> Sin el paso 3 (validación con datos reales de Bitunix), ir en vivo con $20 es
> apostar, no operar con sistema.
