# Funding Rate Harvesting (Carry)

> **Hipótesis:** los perps pagan/cobran *funding* cada 8h. Si te posicionas en el
> lado que **cobra** el funding, ganas un flujo recurrente — un edge **no
> especulativo** (no dependes de adivinar la dirección).

## Señal / mecánica
- Leer el funding rate (`shared/bitunix_client.get_funding_rate`).
- Si funding es muy positivo (longs pagan a shorts) → estar **corto** cobra funding.
- Si es muy negativo (shorts pagan a longs) → estar **largo** cobra funding.
- Idealmente **delta-neutral** (hedge del riesgo direccional). En Bitunix solo-perp
  no hay spot fácil para hedge → versión direccional con riesgo de precio, o
  hedge BTC/ETH aproximado.

## Enemigos
- El riesgo direccional puede superar al funding cobrado si no hay hedge.
- Funding puede cambiar de signo.

## Estado
⚪ Scaffolding. Requiere serie histórica de funding (endpoint o registro propio)
para backtestear el carry neto vs el movimiento de precio.
