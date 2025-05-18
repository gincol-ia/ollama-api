# test_redis_detailed.py
import asyncio
import redis.asyncio as redis
import logging
import os

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("test-redis-detailed")

# Obtener configuración de variables de entorno
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# Hosts a probar
HOSTS_TO_TEST = [
    REDIS_HOST,      # Desde configuración
    "redis",         # Nombre del servicio
    "localhost",     # Localhost
    "127.0.0.1",     # IP local
    "192.168.1.46"   # IP externa
]

async def test_individual_redis_commands(host):
    """Prueba comandos individuales de Redis."""
    logger.info(f"=== Probando comandos individuales en {host}:6379 ===")
    try:
        # Crear cliente Redis
        client = redis.Redis(
            host=host,
            port=6379,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3
        )
        
        # PING
        logger.info(f"[{host}] Enviando comando PING...")
        result = await client.ping()
        logger.info(f"[{host}] PING exitoso: {result}")
        
        # SET
        logger.info(f"[{host}] Enviando comando SET...")
        result = await client.set("test_key", "test_value")
        logger.info(f"[{host}] SET exitoso: {result}")
        
        # GET
        logger.info(f"[{host}] Enviando comando GET...")
        result = await client.get("test_key")
        logger.info(f"[{host}] GET exitoso, valor: {result}")
        
        # DEL
        logger.info(f"[{host}] Enviando comando DEL...")
        result = await client.delete("test_key")
        logger.info(f"[{host}] DEL exitoso: {result}")
        
        # KEYS
        logger.info(f"[{host}] Enviando comando KEYS...")
        result = await client.keys("*")
        logger.info(f"[{host}] KEYS exitoso, encontradas: {len(result)} claves")
        
        # INFO
        logger.info(f"[{host}] Enviando comando INFO...")
        result = await client.info()
        logger.info(f"[{host}] INFO exitoso, versión Redis: {result.get('redis_version', 'desconocida')}")
        
        # Cerrar conexión
        await client.aclose()
        logger.info(f"[{host}] Conexión cerrada correctamente")
        return True
    except Exception as e:
        logger.error(f"[{host}] Error ejecutando comandos Redis: {str(e)}")
        return False

async def simulate_dependency_injection(host):
    """Simula el patrón de inyección de dependencias de FastAPI."""
    logger.info(f"=== Simulando inyección de dependencias en {host}:6379 ===")
    
    async def get_redis_connection():
        """Simula la función de dependencia."""
        logger.info(f"[{host}] Creando conexión a Redis (simulando dependencia)...")
        client = redis.Redis(
            host=host,
            port=6379,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3
        )
        try:
            logger.info(f"[{host}] Verificando conexión con PING...")
            await client.ping()
            logger.info(f"[{host}] Conexión verificada correctamente")
            yield client
        except Exception as e:
            logger.error(f"[{host}] Error en verificación de conexión: {str(e)}")
            raise e
        finally:
            logger.info(f"[{host}] Cerrando conexión en finally...")
            await client.aclose()
            logger.info(f"[{host}] Conexión cerrada")
    
    try:
        # Simular el uso de dependencias en FastAPI
        logger.info(f"[{host}] Iniciando simulación de endpoint...")
        
        # Obtener el generador de conexión
        gen = get_redis_connection()
        
        # Obtener la conexión del generador (simula Depends())
        client = await anext(gen)
        
        # Usar la conexión
        logger.info(f"[{host}] Usando la conexión obtenida para SET/GET...")
        await client.set("test_dependency", "works")
        value = await client.get("test_dependency")
        logger.info(f"[{host}] SET/GET exitoso, valor: {value}")
        
        # Simular finalización del endpoint
        try:
            await gen.aclose()
        except StopAsyncIteration:
            logger.info(f"[{host}] Generador cerrado correctamente")
        
        logger.info(f"[{host}] Simulación completada con éxito")
        return True
    except Exception as e:
        logger.error(f"[{host}] Error en simulación de dependencias: {str(e)}")
        return False

async def main():
    """Ejecuta todas las pruebas."""
    logger.info(f"Variables de entorno: REDIS_HOST={REDIS_HOST}, REDIS_PORT={REDIS_PORT}")
    
    # Probar comandos individuales
    for host in HOSTS_TO_TEST:
        await test_individual_redis_commands(host)
        logger.info("-" * 50)
    
    # Simular inyección de dependencias
    logger.info("\n=== SIMULACIÓN DE INYECCIÓN DE DEPENDENCIAS ===\n")
    for host in HOSTS_TO_TEST:
        await simulate_dependency_injection(host)
        logger.info("-" * 50)
    
    logger.info("Todas las pruebas completadas.")

if __name__ == "__main__":
    asyncio.run(main())