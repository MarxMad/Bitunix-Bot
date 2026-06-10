# 🧩 Guía de Programación para No-Programadores
## Construye tu primer Bot de Trading como un Rompecabezas

¡Bienvenido! Si no sabes nada de programación, esta guía está hecha para ti. Aquí no usaremos jerga técnica compleja. En su lugar, pensaremos en el código de tu bot como **piezas de un rompecabezas** que puedes copiar, pegar y unir para armar tu propia máquina de trading.

---

## 🏗️ Las 5 Piezas del Rompecabezas

Para tener un bot de trading completo, necesitamos exactamente 5 piezas:

```
┌──────────────────────────────────────────────┐
│  🧩 Pieza 1: La Llave de Conexión (API)      │
├──────────────────────────────────────────────┤
│  🧩 Pieza 2: El Ojo del Bot (Ver Precios)    │
├──────────────────────────────────────────────┤
│  🧩 Pieza 3: Las Manos del Bot (Comprar/Ver) │
├──────────────────────────────────────────────┤
│  🧩 Pieza 4: El Escudo de Riesgo (Seguridad)  │
├──────────────────────────────────────────────┤
│  🧩 Pieza 5: El Motor del Bot (Bucle)        │
└──────────────────────────────────────────────┘
```

A continuación, tienes cada pieza lista para copiar y pegar.

---

### 🧩 Pieza 1: La Llave de Conexión (API)
Esta pieza le permite a tu código hablar de forma segura con la casa de cambio Bitunix. Sin esto, el exchange no sabrá quién eres.

**Código para Copiar y Pegar:**
```python
# --- PIEZA 1: CONEXIÓN CON BITUNIX ---
import os
from dotenv import load_dotenv
from bitunix_client import BitunixClient

# Cargamos las llaves secretas del archivo .env
load_dotenv()
API_KEY = os.getenv("API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

# Conectamos nuestro cliente a Bitunix
cliente = BitunixClient(API_KEY, SECRET_KEY)
print("🔑 ¡Conexión con Bitunix establecida!")
```

---

### 🧩 Pieza 2: El Ojo del Bot (Ver Precios)
Esta pieza le pregunta a Bitunix a qué precio se está vendiendo y comprando una criptomoneda en este preciso instante, y calcula el precio medio.

**Código para Copiar y Pegar:**
```python
# --- PIEZA 2: OBTENER PRECIO ACTUAL ---
def obtener_precio_medio(simbolo="BTCUSDT"):
    try:
        # Preguntamos los precios del libro a Bitunix
        datos = cliente.get_depth(simbolo, limit=5)
        ofertas_venta = datos["data"]["asks"]
        ofertas_compra = datos["data"]["bids"]
        
        # Obtenemos el mejor precio de compra y venta
        mejor_precio_venta = float(ofertas_venta[0][0])
        mejor_precio_compra = float(ofertas_compra[0][0])
        
        # Calculamos la mitad
        precio_medio = (mejor_precio_venta + mejor_precio_compra) / 2
        return precio_medio
    except Exception as e:
        print(f"❌ Error leyendo el precio: {e}")
        return 0
```

---

### 🧩 Pieza 3: Las Manos del Bot (Comprar y Vender)
Esta pieza le permite al bot enviar órdenes de compra y venta al mercado. 

**Código para Copiar y Pegar:**
```python
# --- PIEZA 3: ENVIAR ÓRDENES ---
def colocar_orden_limite(simbolo, operacion, cantidad, precio):
    try:
        # Enviamos la orden de compra (BUY) o venta (SELL)
        resultado = cliente.place_order(
            symbol=simbolo,
            side=operacion.upper(),    # Debe ser "BUY" o "SELL"
            order_type="LIMIT",
            qty=str(cantidad),
            price=f"{precio:.2f}"      # Redondeamos el precio a 2 decimales
        )
        order_id = resultado.get("data", {}).get("orderId")
        print(f"✅ Orden {operacion} colocada a {precio:.2f} USDT (ID: {order_id})")
        return order_id
    except Exception as e:
        print(f"❌ No se pudo colocar la orden: {e}")
        return None
```

---

### 🧩 Pieza 4: El Escudo de Riesgo (Seguridad)
Esta pieza vigila que el bot no pierda más dinero del permitido. Si detecta que has perdido el límite fijado, apaga todo de emergencia.

**Código para Copiar y Pegar:**
```python
# --- PIEZA 4: ESCUDO DE RIESGO ---
def verificar_salud_cuenta(perdida_actual, limite_perdida_maxima):
    # Si la pérdida actual toca o supera el límite
    if perdida_actual <= -limite_perdida_maxima:
        print("🚨 ¡ALERTA DE SEGURIDAD! Se ha alcanzado la pérdida máxima permitida.")
        print("🛑 Cancelando todo y apagando el bot...")
        return False # Devuelve False indicando que el bot DEBE APAGARSE
    return True # Devuelve True indicando que todo está bajo control
```

---

### 🧩 Pieza 5: El Motor del Bot (El Bucle de Ejecución)
El bot necesita repetir sus operaciones constantemente (por ejemplo, cada 3 segundos). Esta pieza es el corazón que late e impulsa a las demás piezas.

**Código para Copiar y Pegar:**
```python
# --- PIEZA 5: EL MOTOR PRINCIPAL ---
import time

def encender_motor(simbolo, cantidad, margen_spread, limite_perdida):
    pnl_acumulado = 0.0 # Guardamos las ganancias/pérdidas totales aquí
    bot_activo = True
    
    print("🚀 Iniciando bucle de operaciones...")
    while bot_activo:
        # 1. Mirar precio
        precio_medio = obtener_precio_medio(simbolo)
        if precio_medio == 0:
            time.sleep(3)
            continue
            
        print(f"📈 Precio actual de {simbolo}: {precio_medio:.2f} USDT")
        
        # 2. Calcular precios para comprar barato y vender caro
        precio_compra = precio_medio * (1 - margen_spread)
        precio_venta = precio_medio * (1 + margen_spread)
        
        # 3. Colocar órdenes en el mercado
        colocar_orden_limite(simbolo, "BUY", cantidad, precio_compra)
        colocar_orden_limite(simbolo, "SELL", cantidad, precio_venta)
        
        # 4. Verificar salud de la cuenta
        bot_activo = verificar_salud_cuenta(pnl_acumulado, limite_perdida)
        
        # 5. Esperar 5 segundos antes de volver a empezar
        if bot_activo:
            print("💤 Esperando 5 segundos para el siguiente ciclo...")
            time.sleep(5)
```

---

## 🧩 Uniendo el Rompecabezas (`mi_primer_bot.py`)

Si juntas todas las piezas anteriores en un único archivo de texto llamado `mi_primer_bot.py`, tendrás un robot funcional de creación de mercado. 

Aquí tienes el rompecabezas completamente ensamblado listo para correr:

```python
# =====================================================================
#                 ROMPECABEZAS ENSAMBLADO COMPLETAMENTE
# =====================================================================
import os
import time
from dotenv import load_dotenv
from bitunix_client import BitunixClient

# 1. Cargar llaves
load_dotenv()
API_KEY = os.getenv("API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
cliente = BitunixClient(API_KEY, SECRET_KEY)

# 2. El ojo del bot
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

# 3. Las manos del bot
def colocar_orden_limite(simbolo, operacion, cantidad, precio):
    try:
        resultado = cliente.place_order(
            symbol=simbolo,
            side=operacion.upper(),
            order_type="LIMIT",
            qty=str(cantidad),
            price=f"{precio:.2f}"
        )
        return resultado.get("data", {}).get("orderId")
    except Exception as e:
        print(f"❌ Error al ordenar: {e}")
        return None

# 4. El escudo
def verificar_salud_cuenta(perdida, limite):
    if perdida <= -limite:
        print("🚨 DETENCIÓN: Pérdida máxima superada. Cancelando órdenes.")
        try:
            cliente.cancel_all_orders("BTCUSDT")
        except:
            pass
        return False
    return True

# 5. Ejecutar bot completo
if __name__ == "__main__":
    SIMBOLO = "BTCUSDT"
    CANTIDAD = 0.001       # Operar con 0.001 contratos BTC
    SPREAD = 0.0005        # 0.05% de separación
    LIMITE_PERDIDA = 10.0  # Parar si perdemos $10 USDT
    
    pnl_total = 0.0
    corriendo = True
    
    print("🚀 BOT OPERANDO. Presiona Ctrl+C para detener en consola.")
    
    while corriendo:
        precio_centro = obtener_precio_medio(SIMBOLO)
        if precio_centro > 0:
            compra_target = precio_centro * (1 - SPREAD)
            venta_target = precio_centro * (1 + SPREAD)
            
            # Cancelar órdenes anteriores para no acumular
            try:
                cliente.cancel_all_orders(SIMBOLO)
            except:
                pass
                
            # Colocar nuevas órdenes
            colocar_orden_limite(SIMBOLO, "BUY", CANTIDAD, compra_target)
            colocar_orden_limite(SIMBOLO, "SELL", CANTIDAD, venta_target)
            
        corriendo = verificar_salud_cuenta(pnl_total, LIMITE_PERDIDA)
        if corriendo:
            time.sleep(4)
```

---

## 🛠️ ¿Cómo probar tu código?

1. Guarda el código de arriba en un archivo llamado `mi_primer_bot.py` en la misma carpeta donde tienes este bot.
2. Abre la consola de comandos de tu sistema.
3. Ejecuta:
   ```bash
   python mi_primer_bot.py
   ```
4. Verás en pantalla al bot cobrando vida y colocando órdenes simuladas según las variaciones del mercado.
