# Pairs / Ratio Trading (ETH–BTC)

> **Hipótesis:** ETH y BTC se mueven juntos (alta correlación). El **spread/ratio**
> ETH/BTC revierte a la media aunque cada precio por separado sea camino aleatorio.
> Reversión, pero sobre algo más estable que un precio suelto.

## Señal
- Construir el ratio `ETH/BTC` (o un spread cointegrado).
- z-score del ratio sobre ventana móvil.
- z alto → ETH caro vs BTC → **corto ETH / largo BTC**.
- z bajo → ETH barato vs BTC → **largo ETH / corto BTC**.
- Salida al revertir el ratio a la media.

## Ventaja
Market-neutral: si todo el mercado sube o baja, el spread no se afecta tanto →
menos riesgo direccional que la reversión sobre un solo activo.

## Enemigos
- Necesita **dos posiciones** (doble comisión, doble margen) — duro con $20.
- La cointegración puede romperse (ETH/BTC en re-rating estructural).

## Estado
⚪ Scaffolding. Requiere descargar BTCUSDT + ETHUSDT alineados y backtestear el spread.
