# 06 · Backtest de 360 días — el edge NO se sostiene (corrección)

> **Resultado crítico.** Re-validando sobre **360 días** de datos reales de Bitunix
> (518,400 barras M1, 2025-06-16 → 2026-06-11), la estrategia que parecía rentable
> en 60 días **pierde -49%**. El +18% del doc 04 era un **espejismo de régimen**.

## Comparación directa

| Periodo | Trades | Win % | Net | Sharpe | maxDD |
|---|---|---|---|---|---|
| 60 días (doc 04) | 132 | 62% | **+18%** | +1.3 | -11% |
| **360 días (FULL)** | 764 | 57.6% | **−49.1%** | −0.98 | **−55.3%** |
| 360d · 1ª mitad | 337 | 55.8% | −45.8% | −1.01 | −47.6% |
| 360d · 2ª mitad | 427 | 59.0% | −6.1% | −0.15 | −23.2% |

Config idéntica y validada: `z_in=3.0, z_exit=0.5, z_stop=4.5, lookback=40,
max_hold=24`, ejecución maker, slippage 2 bps/pata.

## Walk-forward (12 bloques ~mensuales): 3 de 12 positivos

| Bloque | Net | Win % | Sharpe |
|---|---|---|---|
| 2025-06→07 | −9.2% | 50.0% | −0.65 |
| 2025-07→08 | −7.9% | 51.9% | −0.72 |
| 2025-08→09 | −2.1% | 60.0% | −0.25 |
| 2025-09→10 | **−26.7%** | 57.6% | −0.51 |
| 2025-10→11 | −3.1% | 53.4% | −0.29 |
| 2025-11→12 | −7.8% | 60.3% | −0.67 |
| 2025-12→01 | −10.1% | 55.6% | −1.44 |
| 2026-01→02 | **+13.8%** | 61.8% | +1.19 |
| 2026-02→03 | −13.6% | 55.7% | −1.33 |
| 2026-03→04 | −4.4% | 60.6% | −0.55 |
| 2026-04→05 | **+2.7%** | 62.7% | +0.55 |
| 2026-05→06 | **+7.3%** | 57.8% | +0.64 |

La ventana de 60 días que validamos en el doc 04 (abr–jun 2026) coincidió con los
**dos últimos bloques positivos**. Fuera de ahí, la estrategia sangra.

## Diagnóstico: por qué pierde

- **Win rate ~57% en TODOS los bloques**, pero expectativa negativa → la magnitud
  de las pérdidas supera a la de las ganancias. Es la firma de una reversión a la
  media **sin filtro de tendencia**: ganas muchas reversiones pequeñas, pero cuando
  el precio rompe y sigue, el trade se va al `time_stop` (2h) o al stop con pérdida
  grande.
- Los `time_stop` dominan los exits perdedores (276 de 764 trades terminaron por
  tiempo). Son trades que entraron a 3σ y el precio **no revirtió** — siguió la
  tendencia.

## Lección metodológica

> **Validar en 60 días, en un solo régimen, no es validar.** El out-of-sample del
> doc 04 (ambas mitades positivas) daba falsa confianza porque las dos mitades
> caían dentro de la misma ventana alcista/favorable. 360 días con walk-forward
> mensual revela la verdad.

Esto **confirma** el caveat #1 del doc 04 ("60 días / un dataset; habrá rachas
perdedoras") — pero la realidad es peor que "rachas": es **pérdida estructural**
fuera del régimen favorable.

## Qué NO hacer ahora

No volver a optimizar parámetros sobre estos mismos 360 días y declarar "ya jala".
Eso sería **re-sobreajustar** sobre el set de prueba. Cualquier mejora debe validarse
con disciplina train/validación/test separada en el tiempo, idealmente en varios
activos.

## Posibles caminos (a explorar con disciplina, sin prometer nada)

1. **Filtro de tendencia / régimen**: no fadear cuando hay tendencia fuerte
   (ej. ADX alto o precio lejos de EMA larga). Objetivo: evitar los `time_stop`
   que matan. Probarlo en train, validar en test separado.
2. **Filtro de volatilidad**: operar solo cuando la vol está en cierta banda.
3. **Gestión de salida**: stop más ajustado o trailing, para cortar los perdedores
   antes de las 2h.
4. **Aceptar que XLM no tiene un edge de reversión robusto** y volver al pipeline
   de research sobre otros símbolos/estrategias.

## Veredicto

🔴 **La estrategia de reversión XLM, tal cual, NO es desplegable.** Win rate alto
pero expectativa negativa fuera del régimen reciente. **No operar en vivo.**
El dashboard y la infraestructura siguen siendo útiles para iterar; el edge, no.

## Reproducir
```bash
F=strategies/mean_reversion/research/data/XLMUSDT_1m.csv
.venv/bin/python strategies/mean_reversion/research/scripts/fetch_bitunix_klines.py XLMUSDT --days 360 --interval 1m
.venv/bin/python strategies/mean_reversion/backtest.py "$F" --slip-bps 2 --oos --walk 12
```
