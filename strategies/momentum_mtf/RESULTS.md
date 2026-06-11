# Momentum-MTF — resultados (360d)

> Filtro macro (4h/1d: close vs EMA, sin lookahead) + entrada micro (1h EMA cross).
> Solo se opera cuando macro y micro coinciden. Fee taker 0.06%. Split 66/34.

## Resultado

| | XLM | ETH |
|---|---|---|
| Configs robustas (train+ y test+) | **18 / 36** ✅ | **0 / 36** ❌ |
| Pick robusto | 1d/EMA100, 8/100 → train +26% / test +4.4% | — (ninguno generaliza) |

## Lectura

- **XLM:** el filtro macro confirma que hay edge de momentum (18/36 generalizan),
  pero **más débil que el trend simple** (test +4% vs +22% del 1h/12/100). El MTF
  no mejora al trend directo en XLM.
- **ETH: 0/36 generalizan.** Igual que mean-reversion (−63%) y trend (3/96), el
  momentum multi-timeframe **tampoco** tiene edge robusto en ETH. Toda config buena
  en train (hasta +68%) es negativa en test. El periodo reciente (~120d) es hostil
  al momentum en ETH.

## Conclusión sobre ETH

Tres familias de estrategias (reversión, trend, momentum-MTF) **fallan en ETH** a
360 días. No es mala suerte de una estrategia: es el activo. ETH está demasiado
eficiente/choppy en este periodo para un edge clásico simple. Opciones reales:
- Bajar expectativas: ETH como market-making/funding, no direccional.
- `pairs_eth_btc` (market-neutral) o `volatility_breakout` (aún sin probar).
- Aceptar que el edge direccional vive en **XLM**, no en ETH.

## Reproducir
```bash
.venv/bin/python strategies/momentum_mtf/backtest.py <csv> --optimize
```
