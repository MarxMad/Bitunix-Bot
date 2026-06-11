# Trend-Following — resultados de optimización (360d)

> Datos reales de Bitunix, 360 días M1 resampleado. Fee taker 0.06%/pata.
> Optimización honesta: barrido de 96 configs, ranking por **train** Sharpe,
> mostrando **test** al lado (split temporal 66/34). "Robusto" = positivo en AMBAS.

## Resumen

| | XLM | ETH |
|---|---|---|
| Mejor single (FULL) | +39% (1h 12/48) | +44% (1h 12/100) |
| Buy & hold (contexto) | −29% | −37% |
| Configs robustas (train+ y test+) | **22 / 96** ✅ | **3 / 96** ⚠️ |
| Pick robusto | 1h 12/100 sep0.01: train +60% / test +22% | 4h 34/50: train −4.5% / test +14% (marginal) |
| maxDD típico | ~−60% (alto) | ~−42% |

## Lectura

- **XLM: edge de trend REAL y robusto.** 22/96 configs generalizan. La 1h/12/100
  con banda 0.01 da Sharpe ~1.1–1.3 en ambas mitades. Trend-following gana
  **mientras XLM cae** (buy&hold −29%) → captura las tendencias bajistas con cortos.
- **ETH: sin edge de trend robusto.** Solo 3/96 generalizan y la "mejor" tiene
  train plano/negativo. La config ganadora de XLM (1h/12/100) en ETH da
  +60.8% train **pero −19.7% test** → no generaliza. El periodo de test reciente
  (~120d) fue choppy para trend en ETH.
- **Firma de trend (sana):** win rate ~35% pero ganancia media grande
  (+110 a +135 bps). Pocas ganadoras grandes, muchas perdedoras chicas — lo
  opuesto a la reversión.

## La trampa del sobreajuste (visible en el barrido)

Las top por TRAIN explotan en TEST:

| Activo | Config top-train | TRAIN | TEST |
|---|---|---|---|
| ETH | 1h 12/50 sep0.01 | +144% / 2.40 | **−31.6% / −1.67** |
| XLM | 4h 8/100 | +74.5% / 1.46 | **−63.9% / −2.91** |

➡️ **Rankear por train solo = elegir basura sobreajustada.** El filtro
"positivo en train Y test" es lo que separa señal de suerte.

## Polish: volatility targeting (domando el drawdown)

El ganador XLM 1h/12/100 tenía maxDD −57%. Aplicando **volatility targeting**
(escalar la exposición ∝ `target / vol_realizada`, capado a `max_lev`) se reduce
el drawdown sin tocar la señal:

| Config | Net (FULL) | Sharpe | maxDD |
|---|---|---|---|
| Baseline (sin sizing) | +92% | 1.21 | −57% |
| vol-target 0.008, maxLev 2 | +110% | 1.34 | −48% |
| **vol-target 0.006, maxLev 1.5** | +85% | **1.34** | **−37%** ✅ |

➡️ La versión conservadora **baja maxDD de −57% a −37%** y sube el Sharpe a 1.34.
Mejor perfil riesgo/retorno y más operable con $20. Flags:
`--vol-target 0.006 --max-lev 1.5 --vol-lookback 72`.

## Veredicto y siguientes pasos

- **XLM trend-following 1h/12/100 + vol-target** es el **mejor candidato a
  forward-test** (dry-run). El sizing por volatilidad ya domó el DD; queda validar
  fills/ejecución en vivo y, si se quiere, un stop ATR duro adicional.
- **ETH** necesita más que dual-EMA: probar `regime_mean_reversion`,
  `momentum_mtf` (filtro macro 4h + entrada 1h) o `volatility_breakout`.
- ⚠️ Multiple-testing: elegir entre 96 configs infla el mejor resultado. Validar
  hacia adelante (datos nuevos) antes de creer del todo.

## Reproducir
```bash
.venv/bin/python strategies/trend_following/backtest.py \
  strategies/mean_reversion/research/data/XLMUSDT_1m.csv --optimize
.venv/bin/python strategies/trend_following/backtest.py \
  strategies/mean_reversion/research/data/ETHUSDT_1m.csv --optimize
# single config:
.venv/bin/python strategies/trend_following/backtest.py <csv> --tf 1h --fast 12 --slow 100 --min-sep 0.01
```
