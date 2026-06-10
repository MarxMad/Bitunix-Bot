# =====================================================================
#                 ROMPECABEZAS ENSAMBLADO COMPLETAMENTE
# =====================================================================
# Este archivo es el resultado de unir las 5 piezas del tutorial (TUTORIAL.md)
# Diseñado para no-programadores que quieran ver un bot básico funcionar.
#
# Para ejecutar: python mi_primer_bot.py
# =====================================================================

import os
import time
from dotenv import load_dotenv
from bitunix_client import BitunixClient

# 1. Cargar llaves (.env)
load_dotenv()
API_KEY = os.getenv("API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

if not API_KEY or not SECRET_KEY:
    print("❌ ERROR: No se cargaron las llaves en el archivo .env")
    print("Por favor crea el archivo .env con API_KEY y SECRET_KEY en esta carpeta.")
    exit(1)

# Conectamos con el cliente API
cliente = BitunixClient(API_KEY, SECRET_KEY)
print("🔑 ¡Conexión con Bitunix establecida!")

# 2. El ojo del bot: Leer precio del mercado
def obtener_precio_medio(simbolo):
    try:
        datos = cliente.get_depth(simbolo, limit=5)
        ofertas_venta = datos["data"]["asks"]
        ofertas_compra = datos["data"]["bids"]
        
        mejor_precio_venta = float(ofertas_venta[0][0])
        mejor_precio_compra = float(ofertas_compra[0][0])
        
        return (mejor_precio_venta + mejor_precio_compra) / 2
    except Exception as e:
        print(f"❌ Error de red al leer precio: {e}")
        return 0

# 3. Las manos del bot: Colocar órdenes límite
def colocar_orden_limite(simbolo, operacion, cantidad, precio):
    try:
        resultado = cliente.place_order(
            symbol=simbolo,
            side=operacion.upper(),
            order_type="LIMIT",
            qty=str(cantidad),
            price=f"{precio:.2f}"
        )
        order_id = resultado.get("data", {}).get("orderId")
        print(f"✅ Orden {operacion} colocada a {precio:.2f} USDT (ID: {order_id})")
        return order_id
    except Exception as e:
        print(f"❌ Error al ordenar: {e}")
        return None

# 4. El escudo de riesgo: Evitar pérdidas
def verificar_salud_cuenta(perdida, limite):
    if perdida <= -limite:
        print("🚨 DETENCIÓN: Pérdida máxima superada. Cancelando órdenes.")
        try:
            cliente.cancel_all_orders("BTCUSDT")
        except Exception as e:
            print(f"Error cancelando órdenes en cierre de emergencia: {e}")
        return False
    return True

# 5. Ejecutar bot completo (El Motor)
if __name__ == "__main__":
    SIMBOLO = "BTCUSDT"
    CANTIDAD = 0.001       # Operar con 0.001 contratos BTC
    SPREAD = 0.0005        # 0.05% de separación del precio centro
    LIMITE_PERDIDA = 10.0  # Parar si perdemos $10 USDT
    
    pnl_total = 0.0
    corriendo = True
    
    print("🚀 BOT OPERANDO. Presiona Ctrl+C para detener en la terminal.")
    
    while corriendo:
        precio_centro = obtener_precio_medio(SIMBOLO)
        if precio_centro > 0:
            compra_target = precio_centro * (1 - SPREAD)
            venta_target = precio_centro * (1 + SPREAD)
            
            # Cancelar órdenes anteriores para no acumular
            try:
                cliente.cancel_all_orders(SIMBOLO)
            except Exception as e:
                pass
                
            # Colocar nuevas órdenes de compra y venta
            colocar_orden_limite(SIMBOLO, "BUY", CANTIDAD, compra_target)
            colocar_orden_limite(SIMBOLO, "SELL", CANTIDAD, venta_target)
            
        corriendo = verificar_salud_cuenta(pnl_total, LIMITE_PERDIDA)
        if corriendo:
            time.sleep(4)
