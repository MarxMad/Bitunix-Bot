# Volatility Breakout

> **Hipótesis:** los movimientos grandes empiezan cuando el precio **rompe** un
> rango con expansión de volatilidad. Capturamos justo los movimientos que
> *matan* a la reversión a la media.

## Señal
- Definir un rango/canal (ej. Donchian de N barras, o bandas sobre ATR).
- Entrar **largo** al romper el máximo del canal; **corto** al romper el mínimo.
- Confirmar con expansión de volatilidad (ATR creciente / squeeze de Bollinger).
- Salida: stop ATR + trailing; o salida al cerrar dentro del canal.

## Enemigos
- **Falsos breakouts** (fakeouts) en rango → filtrar con volumen/volatilidad.
- Comisiones taker en la entrada.

## Estado
⚪ Scaffolding. Pendiente de implementar tras trend-following (comparten mucha
infra: `shared/backtest_engine.py`).
