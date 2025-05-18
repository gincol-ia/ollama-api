import os
import logging
import time
import json
import uuid
from typing import Dict, List, Optional, Any, AsyncGenerator, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import httpx
import redis.asyncio as redis

# Configuración
OLLAMA_API_BASE_URL = os.environ.get("OLLAMA_API_BASE_URL", "http://192.168.1.46:11434")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")  # Usar el nombre del servicio en Docker Compose
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
CONVERSATION_TTL = int(os.environ.get("CONVERSATION_TTL", "43200"))  # 12 horas por defecto

# Configuración de logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ollama-api")

# Inicializar FastAPI
app = FastAPI(
    title="Ollama API con memoria Redis",
    description="API REST para interactuar con modelos de lenguaje de Ollama con memoria persistente",
    version="1.1.0",
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Modelos de datos
class GenerateRequest(BaseModel):
    prompt: str
    model: str = "gemma3:27b"
    conversation_id: Optional[str] = None
    stream: bool = True
    options: Optional[Dict[str, Any]] = Field(default_factory=lambda: {"temperature": 0.7})

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: str = "gemma3:27b"
    conversation_id: Optional[str] = None
    stream: bool = True
    options: Optional[Dict[str, Any]] = Field(default_factory=lambda: {"temperature": 0.7})

class ConversationInfo(BaseModel):
    conversation_id: str
    model: str
    message_count: int
    last_updated: str
    time_to_live: int  # Tiempo restante en segundos
    display_name: Optional[str] = None

class RenameConversationRequest(BaseModel):
    new_name: str

# Funciones para Redis - Sin dependencias de FastAPI
async def create_redis_connection():
    """Crea una nueva conexión a Redis."""
    try:
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        # Verificar conexión
        await client.ping()
        return client
    except Exception as e:
        logger.error(f"Error al conectar a Redis: {str(e)}")
        return None

async def close_redis_connection(client):
    """Cierra una conexión a Redis."""
    if client:
        await client.aclose()

def get_conversation_key(conversation_id: str) -> str:
    """Genera una clave para Redis basada en el ID de conversación."""
    return f"conversation:{conversation_id}"

async def save_message(
    conversation_id: str, 
    role: str, 
    content: str,
    model: str
) -> bool:
    """Guarda un mensaje en Redis."""
    redis_client = None
    try:
        # Crear conexión
        redis_client = await create_redis_connection()
        if not redis_client:
            logger.error("No se pudo crear conexión a Redis")
            return False
            
        # Clave para la conversación
        conv_key = get_conversation_key(conversation_id)
        
        # Verificar si la conversación ya existe
        if not await redis_client.exists(conv_key):
            # Crear metadata para la conversación
            await redis_client.hset(
                conv_key,
                mapping={
                    "model": model,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                }
            )
        else:
            # Actualizar timestamp
            await redis_client.hset(conv_key, "updated_at", time.time())
        
        # Índice del mensaje (número total de mensajes actuales)
        msg_count = await redis_client.llen(f"{conv_key}:messages")
        
        # Guardar el mensaje como un objeto JSON
        message_data = json.dumps({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        
        # Añadir mensaje a la lista
        await redis_client.rpush(f"{conv_key}:messages", message_data)
        
        # Establecer TTL (time to live)
        await redis_client.expire(conv_key, CONVERSATION_TTL)
        await redis_client.expire(f"{conv_key}:messages", CONVERSATION_TTL)
        
        logger.debug(f"Mensaje guardado en conversación {conversation_id}, índice {msg_count}")
        return True
    
    except Exception as e:
        logger.error(f"Error al guardar mensaje: {str(e)}")
        return False
    finally:
        # Cerrar conexión
        if redis_client:
            await close_redis_connection(redis_client)

async def get_conversation_messages(conversation_id: str) -> List[Dict[str, str]]:
    """Recupera todos los mensajes de una conversación de Redis."""
    redis_client = None
    try:
        # Crear conexión
        redis_client = await create_redis_connection()
        if not redis_client:
            logger.error("No se pudo crear conexión a Redis")
            return []
            
        conv_key = get_conversation_key(conversation_id)
        
        # Verificar si la conversación existe
        if not await redis_client.exists(conv_key):
            logger.warning(f"Conversación {conversation_id} no encontrada")
            return []
        
        # Obtener todos los mensajes
        messages_raw = await redis_client.lrange(f"{conv_key}:messages", 0, -1)
        
        # Parsear los mensajes de JSON a diccionarios
        messages = []
        for msg_raw in messages_raw:
            try:
                msg = json.loads(msg_raw)
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Error al parsear mensaje: {str(e)}")
                continue
        
        return messages
    
    except Exception as e:
        logger.error(f"Error al obtener mensajes: {str(e)}")
        return []
    finally:
        # Cerrar conexión
        if redis_client:
            await close_redis_connection(redis_client)

async def delete_conversation(conversation_id: str) -> bool:
    """Elimina una conversación completa de Redis."""
    redis_client = None
    try:
        # Crear conexión
        redis_client = await create_redis_connection()
        if not redis_client:
            logger.error("No se pudo crear conexión a Redis")
            return False
            
        conv_key = get_conversation_key(conversation_id)
        
        # Verificar si la conversación existe
        if not await redis_client.exists(conv_key):
            logger.warning(f"Conversación {conversation_id} no encontrada para eliminar")
            return False
        
        # Eliminar la metadata y los mensajes
        await redis_client.delete(conv_key)
        await redis_client.delete(f"{conv_key}:messages")
        
        logger.info(f"Conversación {conversation_id} eliminada")
        return True
    
    except Exception as e:
        logger.error(f"Error al eliminar conversación: {str(e)}")
        return False
    finally:
        # Cerrar conexión
        if redis_client:
            await close_redis_connection(redis_client)

async def get_conversation_info(conversation_id: str) -> Optional[ConversationInfo]:
    """Obtiene información sobre una conversación."""
    redis_client = None
    try:
        # Crear conexión
        redis_client = await create_redis_connection()
        if not redis_client:
            logger.error("No se pudo crear conexión a Redis")
            return None
            
        conv_key = get_conversation_key(conversation_id)
        
        # Verificar si la conversación existe
        if not await redis_client.exists(conv_key):
            return None
        
        # Obtener metadata
        metadata = await redis_client.hgetall(conv_key)
        msg_count = await redis_client.llen(f"{conv_key}:messages")
        
        # Obtener TTL
        ttl = await redis_client.ttl(conv_key)
        
        # Formato de fecha legible
        updated_timestamp = float(metadata.get("updated_at", 0))
        updated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(updated_timestamp))
        
        return ConversationInfo(
            conversation_id=conversation_id,
            model=metadata.get("model", "desconocido"),
            message_count=msg_count,
            last_updated=updated_at,
            time_to_live=ttl,
            display_name=metadata.get("display_name")
        )
    
    except Exception as e:
        logger.error(f"Error al obtener información de conversación: {str(e)}")
        return None
    finally:
        # Cerrar conexión
        if redis_client:
            await close_redis_connection(redis_client)

async def list_conversations() -> List[ConversationInfo]:
    """Lista todas las conversaciones activas."""
    redis_client = None
    try:
        # Crear conexión
        redis_client = await create_redis_connection()
        if not redis_client:
            logger.error("No se pudo crear conexión a Redis")
            return []
            
        # Obtener todas las claves de conversaciones
        conversation_keys = await redis_client.keys("conversation:*")
        
        # Filtrar solo las claves de metadatos (no mensajes)
        metadata_keys = [key for key in conversation_keys if ":messages" not in key]
        
        # Obtener información de cada conversación
        conversations = []
        for key in metadata_keys:
            conversation_id = key.split(":")[-1]
            # Usar la función que ya tenemos
            info = await get_conversation_info(conversation_id)
            if info:
                conversations.append(info)
        
        return conversations
    
    except Exception as e:
        logger.error(f"Error al listar conversaciones: {str(e)}")
        return []
    finally:
        # Cerrar conexión
        if redis_client:
            await close_redis_connection(redis_client)


# Middleware para monitoreo de rendimiento
@app.middleware("http")
async def performance_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    logger.debug(f"Request processed in {process_time:.4f} seconds")
    return response

# Endpoints
@app.get("/")
async def read_root():
    return {
        "status": "online",
        "message": "Ollama API con memoria Redis está funcionando correctamente",
        "documentation": "/docs",
    }

@app.get("/health")
async def health_check():
    try:
        # Verificar conexión con Ollama
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_API_BASE_URL}/api/tags")
            if response.status_code != 200:
                raise HTTPException(status_code=503, detail="Ollama service is not available")
        
        # Verificar conexión con Redis usando una conexión directa
        try:
            # Crear conexión Redis sin usar el pool
            redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # Probar conexión con ping
            ping_result = await redis_client.ping()
            logger.info(f"Ping a Redis exitoso en health check: {ping_result}")
            
            # Probar una operación básica
            test_key = f"health_check_{time.time()}"
            await redis_client.set(test_key, "ok", ex=10)  # Expira en 10 segundos
            test_value = await redis_client.get(test_key)
            logger.info(f"Operación Redis básica exitosa en health check: {test_value}")
            
            # Cerrar conexión
            await redis_client.aclose()
            
            return {
                "status": "healthy",
                "ollama_connection": "connected",
                "redis_connection": "connected",
                "test_value": test_value,
                "conversation_ttl": f"{CONVERSATION_TTL} seconds ({CONVERSATION_TTL/3600:.1f} hours)"
            }
        except Exception as redis_error:
            logger.error(f"Error de conexión a Redis en health check: {str(redis_error)}")
            # Devolver un 200 OK pero con estado "warning" para no bloquear la aplicación
            return {
                "status": "warning",
                "ollama_connection": "connected",
                "redis_connection": "error",
                "redis_error": str(redis_error),
                "message": "La aplicación funciona pero hay problemas con Redis"
            }
    except HTTPException:
        # Re-lanzar HTTPException
        raise
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        # También devolver 200 OK con advertencia
        return {
            "status": "warning",
            "error": str(e),
            "message": "Error en health check pero la aplicación podría seguir funcionando"
        }

# Endpoint para renombrar conversación
@app.put("/conversations/{conversation_id}/rename")
async def rename_conversation(
    conversation_id: str,
    request: RenameConversationRequest
):
    # Crear conexión Redis
    redis_client = None
    try:
        # Crear conexión
        redis_client = await create_redis_connection()
        if not redis_client:
            raise HTTPException(status_code=503, detail="No se pudo conectar a Redis")
            
        # Verificar si la conversación existe
        info = await get_conversation_info(conversation_id)
        if not info:
            raise HTTPException(status_code=404, detail=f"Conversación {conversation_id} no encontrada")
        
        # Guardar el nuevo nombre en Redis
        conv_key = get_conversation_key(conversation_id)
        await redis_client.hset(conv_key, "display_name", request.new_name)
        
        # Actualizar el tiempo de vida
        await redis_client.expire(conv_key, CONVERSATION_TTL)
        await redis_client.expire(f"{conv_key}:messages", CONVERSATION_TTL)
        
        return {
            "status": "success", 
            "message": f"Conversación renombrada a '{request.new_name}'",
            "conversation_id": conversation_id,
            "new_name": request.new_name
        }
    except Exception as e:
        logger.error(f"Error al renombrar conversación: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cerrar conexión
        if redis_client:
            await close_redis_connection(redis_client)

@app.get("/models")
async def list_models():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_API_BASE_URL}/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                return {"models": models}
            else:
                raise HTTPException(status_code=response.status_code, detail="Failed to fetch models")
    except Exception as e:
        logger.error(f"Error listing models: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint para gestión de conversaciones
@app.get("/conversations")
async def get_conversations():
    conversations = await list_conversations()
    return {"conversations": [conv.dict() for conv in conversations]}

@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    # Obtener información de la conversación
    info = await get_conversation_info(conversation_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Conversación {conversation_id} no encontrada")
    
    # Obtener mensajes
    messages = await get_conversation_messages(conversation_id)
    
    return {
        "info": info.dict(),
        "messages": messages
    }

@app.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str):
    # Verificar si la conversación existe
    info = await get_conversation_info(conversation_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Conversación {conversation_id} no encontrada")
    
    # Eliminar la conversación
    success = await delete_conversation(conversation_id)
    if not success:
        raise HTTPException(status_code=500, detail="Error al eliminar la conversación")
    
    return {"status": "success", "message": f"Conversación {conversation_id} eliminada"}

# Endpoint para generar texto con streaming y memoria
@app.post("/generate-stream")
async def generate_stream(request: GenerateRequest):
    # Generar ID de conversación si no se proporciona
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        logger.info(f"Generado nuevo ID de conversación: {conversation_id}")
    
    # Guardar el prompt en Redis
    await save_message(conversation_id, "user", request.prompt, request.model)
    
    # Recuperar el historial de conversación si existe
    conversation_history = await get_conversation_messages(conversation_id)
    
    # Verificar si hay contexto previo
    has_context = len(conversation_history) > 0
    logger.info(f"Conversación {conversation_id} tiene {len(conversation_history)} mensajes previos")
    
    return StreamingResponse(
        stream_text_generation(request, conversation_id, conversation_history if has_context else None),
        media_type="text/event-stream",
    )

# Endpoint para chat con streaming y memoria
@app.post("/chat-stream")
async def chat_stream(request: ChatRequest):
    # Generar ID de conversación si no se proporciona
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        logger.info(f"Generado nuevo ID de conversación: {conversation_id}")
    
    # Guardar mensajes del usuario en Redis
    for message in request.messages:
        await save_message(conversation_id, message.role, message.content, request.model)
    
    # Recuperar el historial de conversación
    conversation_history = await get_conversation_messages(conversation_id)
    
    return StreamingResponse(
        stream_chat(request, conversation_id, conversation_history),
        media_type="text/event-stream",
    )

# Funciones para streaming con memoria
async def stream_text_generation(
    request: GenerateRequest, 
    conversation_id: str,
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> AsyncGenerator[str, None]:
    try:
        # Asegurar que stream sea True para la solicitud a Ollama
        request_data = request.dict(exclude={"conversation_id"})
        request_data["stream"] = True
        
        # Si hay historial, preparar un prompt con contexto
        if conversation_history and len(conversation_history) > 1:
            context_prompt = ""
            for msg in conversation_history[:-1]:  # Excluimos el último mensaje (el actual)
                prefix = "Usuario: " if msg["role"] == "user" else "Asistente: "
                context_prompt += f"{prefix}{msg['content']}\n\n"
            
            context_prompt += f"Usuario: {request.prompt}\n\nAsistente:"
            request_data["prompt"] = context_prompt
            logger.debug(f"Prompt con contexto generado: {len(context_prompt)} caracteres")

        logger.info(f"Generando texto con modelo {request.model} para conversación {conversation_id}")
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_API_BASE_URL}/api/generate",
                json=request_data,
                timeout=60.0,
            ) as response:
                if response.status_code != 200:
                    error_detail = await response.aread()
                    logger.error(f"Error streaming texto: {error_detail}")
                    yield f"data: {json.dumps({'error': error_detail.decode(), 'conversation_id': conversation_id})}\n\n"
                    return

                # Variables para acumular la respuesta
                full_response = ""
                is_done = False

                async for chunk in response.aiter_bytes():
                    if chunk:
                        try:
                            decoded_chunk = chunk.decode("utf-8")
                            # Puede haber múltiples líneas JSON en un solo chunk
                            for line in decoded_chunk.strip().split("\n"):
                                if line.strip():
                                    data = json.loads(line)
                                    response_text = data.get("response", "")
                                    full_response += response_text
                                    is_done = data.get("done", False)
                                    
                                    # Enviar el ID de conversación junto con la respuesta
                                    yield f"data: {json.dumps({'response': response_text, 'done': is_done, 'conversation_id': conversation_id})}\n\n"
                        except json.JSONDecodeError as e:
                            logger.error(f"Error decodificando JSON: {str(e)}")
                            continue
                
                # Si la generación ha terminado, guardar la respuesta en Redis
                if is_done and full_response:
                    await save_message(conversation_id, "assistant", full_response, request.model)
                    logger.info(f"Respuesta guardada en Redis para conversación {conversation_id}")
                
                # Enviar mensaje final si es necesario
                if is_done:
                    yield f"data: {json.dumps({'response': '', 'done': True, 'conversation_id': conversation_id})}\n\n"

    except Exception as e:
        logger.error(f"Error en stream_text_generation: {str(e)}")
        yield f"data: {json.dumps({'error': str(e), 'conversation_id': conversation_id})}\n\n"

async def stream_chat(
    request: ChatRequest,
    conversation_id: str,
    conversation_history: List[Dict[str, str]]
) -> AsyncGenerator[str, None]:
    try:
        # Asegurar que stream sea True para la solicitud a Ollama
        request_data = request.dict(exclude={"conversation_id"})
        request_data["stream"] = True
        
        # Si estamos usando historial de Redis, reemplazar los mensajes de la solicitud
        if conversation_history:
            request_data["messages"] = [
                {"role": msg["role"], "content": msg["content"]} 
                for msg in conversation_history
            ]
            logger.debug(f"Usando {len(conversation_history)} mensajes del historial")

        logger.info(f"Generando chat con modelo {request.model} para conversación {conversation_id}")
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_API_BASE_URL}/api/chat",
                json=request_data,
                timeout=60.0,
            ) as response:
                if response.status_code != 200:
                    error_detail = await response.aread()
                    logger.error(f"Error streaming chat: {error_detail}")
                    yield f"data: {json.dumps({'error': error_detail.decode(), 'conversation_id': conversation_id})}\n\n"
                    return

                # Variables para acumular la respuesta
                full_response = ""
                is_done = False

                async for chunk in response.aiter_bytes():
                    if chunk:
                        try:
                            decoded_chunk = chunk.decode("utf-8")
                            # Puede haber múltiples líneas JSON en un solo chunk
                            for line in decoded_chunk.strip().split("\n"):
                                if line.strip():
                                    data = json.loads(line)
                                    message = data.get("message", {})
                                    response_text = message.get("content", "")
                                    full_response += response_text
                                    is_done = data.get("done", False)
                                    
                                    # Enviar el ID de conversación junto con la respuesta
                                    yield f"data: {json.dumps({'response': response_text, 'done': is_done, 'conversation_id': conversation_id})}\n\n"
                        except json.JSONDecodeError as e:
                            logger.error(f"Error decodificando JSON: {str(e)}")
                            continue
                
                # Si la generación ha terminado, guardar la respuesta en Redis
                if is_done and full_response:
                    await save_message(conversation_id, "assistant", full_response, request.model)
                    logger.info(f"Respuesta guardada en Redis para conversación {conversation_id}")
                
                # Enviar mensaje final si es necesario
                if is_done:
                    yield f"data: {json.dumps({'response': '', 'done': True, 'conversation_id': conversation_id})}\n\n"

    except Exception as e:
        logger.error(f"Error en stream_chat: {str(e)}")
        yield f"data: {json.dumps({'error': str(e), 'conversation_id': conversation_id})}\n\n"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)