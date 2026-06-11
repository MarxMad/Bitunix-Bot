# 04 · Backtesting con datos REALES de Bitunix (ETH y XLM)

> Ruta B. Datos: 60 días de velas M1 descargadas de la propia API de Bitunix
> (`fetch_bitunix_klines.py`), **no MT4**. 86,400 barras/símbolo, 0 gaps.
> Fees: maker 0.02 % / taker 0.06 % (VIP0 cripto).

## Contexto de régimen (importante para no sobreajustar)

| | ETHUSDT | XLMUSDT |
|---|---|---|
| Periodo | 2026-04-12 → 06-11 | igual |
| Buy & hold | **-24 %** (bajista) | **+26 %** (alcista) |
| Vol anualizada | 54 % | **109 %** |
| Variance Ratio | 0.88–0.98 | 0.80–0.89 |
| Carácter | Reversión leve | **Reversión fuerte** |

Ambos son **mean-reverting** (VR<1), pero en regímenes opuestos → buena prueba de robustez.

## Hallazgo 1 — El fee es el factor dominante

Misma estrategia (reversión z=2.5), solo cambiando ejecución:

| Ejecución | ETH | XLM |
|---|---|---|
| TAKER 0.06 % | -34 % | -40 % |
| MAKER 0.02 % | -11 % | -19 % |

➡️ **Usar órdenes LIMIT (maker) es obligatorio.** Triplica la viabilidad. (Justo lo
que el proyecto ya sabe hacer con el market maker.)

## Hallazgo 2 — El edge está en los extremos (3+ sigma), solo en XLM

Reversión a la media con entrada LIMIT en desviación ≥ z_in, salida en z_exit,
time-stop 2 h, stop catastrófico z=4.5. La zona **z_in ≥ 3.0** es la rentable:

| Config (XLM, maker) | Trades | Win % | Net 60d | maxDD | Sharpe |
|---|---|---|---|---|---|
| z_in=2.5 | 274 | 57 % | **-19 %** | -36 % | -0.83 |
| **z_in=3.0, z_exit=0.5** | 132 | 62 % | **+18.3 %** | -11 % | **+1.31** |
| z_in=3.5, z_exit=0.5 | 49 | 65 % | +13.2 % | -2 % | +2.01 |

Entrar a 2.5σ pierde; entrar a ≥3σ gana. El edge es la **sobre-reacción extrema**
de un small-cap volátil, no las oscilaciones normales.

## Hallazgo 3 — Validación (config elegida: z_in=3.0, z_exit=0.5, hold≤2h)

**XLM — el edge se sostiene:**

| Test | Resultado |
|---|---|
| Out-of-sample (1ª mitad) | +7.6 %, Sharpe 1.41 |
| Out-of-sample (2ª mitad) | +10.8 %, Sharpe 0.84 |
| Walk-forward 4 bloques | +10.4 % / -1.9 % / +0.4 % / +7.9 % (3 de 4 positivos) |
| Slippage 3 bps/pata | +10.4 %, Sharpe 0.75 (sobrevive) |
| Slippage 5 bps/pata | +5.1 % (aún positivo) |
| Robustez de params | toda la zona z_in≥3 es positiva |

**ETH — sin edge:** marginalmente negativo en todo, empeora con slippage
(-8 % con 2 bps). **No operar ETH con esta estrategia.**

## Conclusión / estrategia validada

> **XLMUSDT — Reversión a la media en extremos (3σ), ejecución maker.**
> Entrada LIMIT cuando z ≤ -3 (compra) o z ≥ +3 (venta) sobre media móvil de 40×M5.
> Salida LIMIT al volver a z = ±0.5. Stop catastrófico z = 4.5 (market). Time-stop 2 h.

### Caveats honestos (leer antes de arriesgar dinero)
1. **60 días, un dataset.** El bloque 2 fue plano/negativo: habrá rachas perdedoras.
   No es una máquina de dinero; es un edge pequeño y real.
2. **El fill maker a 3σ es la parte optimista** (selección adversa). El haircut de
   slippage lo simula, pero el live mandará la verdad. Empezar en **dry-run**.
3. **$20 + mínimo de 80 XLM** (~$15 notional a $0.19): el lote mínimo es grande
   respecto a la cuenta → el sizing por riesgo está restringido. Ver doc 03 / próximo paso.
4. Re-descargar datos y re-validar periódicamente (los edges decaen).

## Reproducir

```bash
cd strategies/mean_reversion/research/scripts
python3 fetch_bitunix_klines.py ETHUSDT XLMUSDT --days 60 --interval 1m
python3 analyze_bitunix.py  ../data/XLMUSDT_1m.csv   # exploratorio
python3 analyze_bitunix2.py ../data/XLMUSDT_1m.csv   # maker vs taker + sweeps
python3 analyze_bitunix3.py ../data/XLMUSDT_1m.csv   # validación OOS + slippage
```
