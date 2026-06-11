# Momentum Multi-Timeframe (MTF)

> **Hipótesis:** filtrar la dirección con un timeframe ALTO (4h/1d) y cronometrar
> la entrada con uno BAJO (15m/1h) da señales de mayor calidad y **menos trades**
> → menos comisiones (el enemigo #1 que vimos en todo el research).

## Señal
- **Filtro macro** (4h/1d): tendencia alcista si precio > EMA larga (y/o pendiente
  positiva). Solo se permiten **largos** en macro-alcista, **cortos** en macro-bajista.
- **Gatillo micro** (15m/1h): entrar a favor del macro en pullback o micro-breakout.
- Salida: stop ATR / trailing, o pérdida del macro.

## Ventaja vs trend-following simple
El filtro macro evita operar contra la corriente y reduce el whipsaw del cruce de
medias en un solo timeframe.

## Estado
⚪ Scaffolding. Evolución natural de `trend_following`; compartirá `core.py`.
