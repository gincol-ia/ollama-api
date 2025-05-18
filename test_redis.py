# test_redis.py
import asyncio
import redis.asyncio as redis

async def test_redis_connection():
    try:
        redis_client = redis.Redis(host='192.168.1.46', port=6379, decode_responses=True)
        result = await redis_client.ping()
        print(f"Conexión exitosa a Redis: {result}")
        await redis_client.aclose()
    except Exception as e:
        print(f"Error al conectar a Redis: {str(e)}")

# Ejecutar el test
asyncio.run(test_redis_connection())