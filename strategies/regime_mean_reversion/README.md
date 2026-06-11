# Regime-Filtered Mean Reversion

> **Hipótesis:** la reversión a la media pura falló (ver
> `mean_reversion/research/06_backtest_360d.md`: -49% en 360d) porque **fadea
> tendencias fuertes** → los `time_stop` la matan. La corrección: **solo fadear
> cuando el mercado está en RANGO**, no en tendencia.

## Señal
Reutiliza `mean_reversion/meanrev_core.py` (z-score) + un **filtro de régimen**:
- Operar la reversión SOLO si el mercado es lateral, detectado por:
  - ADX bajo (< umbral), o
  - precio cerca de su EMA larga (sin tendencia clara), o
  - pendiente de la EMA larga ≈ plana.
- Si hay tendencia fuerte → **no operar** (o ceder el turno a trend-following).

## Objetivo
Eliminar los trades catastróficos contra-tendencia que produjeron expectativa
negativa, conservando el alto win-rate de las reversiones en rango.

## Estado
⚪ Scaffolding. Es la evolución directa de la estrategia base fallida. Validar con
split train/validación/test SEPARADO en el tiempo (no re-optimizar sobre 360d).
