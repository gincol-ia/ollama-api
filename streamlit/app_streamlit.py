import os
import sys
import json
import time
import uuid
import re
from typing import List, Dict, Any, Optional

# Configurar codificación para salida estándar
sys.stdout.reconfigure(encoding='utf-8')

import streamlit as st
from streamlit import session_state as ss
import requests
from requests.exceptions import RequestException

# Configuración desde variables de entorno
FASTAPI_BASE_URL = os.environ.get("FASTAPI_URL", "http://fastapi:8000")

# Configuración de la página de Streamlit
st.set_page_config(
    page_title="Chat con Modelos LLM",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS para estilizar la interfaz
st.markdown("""
<style>
    /* Estilos para los bloques de pensamiento */
    .thinking-block {
        background-color: #f6f8fa;
        border-left: 3px solid #2f80ed;
        padding: 10px;
        margin: 10px 0;
        font-size: 0.95em;
        color: #333;
        border-radius: 4px;
    }
    
    .thinking-block summary {
        cursor: pointer;
        font-weight: 500;
        margin-bottom: 8px;
        color: #2f80ed;
    }
    
    .thinking-block summary:hover {
        color: #1a56db;
    }
    
    .think-content {
        margin-left: 10px;
        padding-left: 10px;
        border-left: 2px solid #e0e0e0;
    }
    
    /* Estilo para streaming de texto think */
    .streaming-block {
        border-left: 3px solid #1E88E5;
        background-color: #E3F2FD;
        padding: 10px;
        margin: 8px 0;
        font-style: italic;
        border-radius: 4px;
    }
    
    /* Estilos para el formulario de renombrado */
    .rename-container {
        background-color: #f5f7f9;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
        padding: 15px;
        margin: 12px 0;
    }
    
    .rename-form {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 6px;
        padding: 10px;
        margin: 10px 0;
        width: 100%;
    }
    
    .rename-title {
        font-weight: 600;
        color: #212529;
    }
    
    /* Botones alineados mejor */
    .button-row {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        margin-top: 10px;
    }
    
    .btn {
        padding: 8px 12px;
        border-radius: 4px;
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
        text-align: center;
        width: 100%;
    }
    
    .btn-cancel {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        color: #212529;
    }
    
    .btn-confirm {
        background-color: #ff3a5e;
        border: none;
        color: white;
    }
    
    /* Alerta de confirmación de eliminación */
    .delete-warning {
        background-color: #fff3cd;
        border: 1px solid #ffeeba;
        border-radius: 8px;
        padding: 10px 15px;
        margin: 10px 0;
        color: #856404;
    }
</style>
""", unsafe_allow_html=True)

# Funciones de utilidad
def get_available_models() -> List[str]:
    """Obtiene la lista de modelos disponibles desde la API."""
    try:
        response = requests.get(f"{FASTAPI_BASE_URL}/models", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            # Extraer solo los nombres de los modelos
            return [model.get("name", "unknown") for model in models if "name" in model]
        else:
            st.warning(f"No se pudieron obtener los modelos: {response.status_code}")
            return ["gemma3:27b"]  # Valor por defecto
    except Exception as e:
        st.warning(f"Error al obtener modelos: {str(e)}")
        return ["gemma3:27b"]  # Valor por defecto

def check_api_health() -> Dict[str, Any]:
    """Verifica que la API esté funcionando correctamente."""
    try:
        response = requests.get(f"{FASTAPI_BASE_URL}/health", timeout=2)
        if response.status_code == 200:
            return response.json()
        return {"status": "unhealthy", "error": f"Código HTTP: {response.status_code}"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

def get_or_create_conversation_id() -> str:
    """Obtiene el ID de conversación existente o crea uno nuevo."""
    if "conversation_id" not in ss:
        ss.conversation_id = str(uuid.uuid4())
        ss.conversation_created = time.time()
        ss.conversation_name = "Nueva conversación"
        
    return ss.conversation_id

def generate_conversation_name(messages: List[Dict[str, str]]) -> str:
    """Genera un nombre descriptivo basado en el contenido de la conversación."""
    if not messages:
        return "Nueva conversación"
    
    # Extraer el primer mensaje del usuario
    for msg in messages:
        if msg["role"] == "user":
            # Extraer las primeras palabras del mensaje (hasta 5 palabras o 30 caracteres)
            content = msg["content"].strip()
            words = content.split()[:5]
            name = " ".join(words)
            
            # Limitar a 30 caracteres
            if len(name) > 30:
                name = name[:27] + "..."
            
            # Si es muy corto, añadir un timestamp
            if len(name) < 10:
                name += f" ({time.strftime('%H:%M')})"
                
            return name
    
    # Si no hay mensajes del usuario
    return f"Conversación {time.strftime('%d/%m %H:%M')}"

def load_conversation(conversation_id: str, auto_load: bool = False) -> bool:
    """Carga una conversación existente desde la API."""
    try:
        response = requests.get(f"{FASTAPI_BASE_URL}/conversations/{conversation_id}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            messages = data.get("messages", [])
            
            # Actualizar el estado de la sesión
            ss.messages = messages
            ss.conversation_id = conversation_id
            
            # Obtener nombre de la conversación
            info = data.get("info", {})
            
            # Intentar obtener el nombre personalizado
            if info.get("display_name"):
                ss.conversation_name = info.get("display_name")
            else:
                # Generar nombre o usar el existente
                if "conversation_names" in ss and conversation_id in ss.conversation_names:
                    ss.conversation_name = ss.conversation_names[conversation_id]
                else:
                    ss.conversation_name = generate_conversation_name(messages)
            
            # Obtener el modelo de la conversación
            model = info.get("model", "gemma3:27b")
            if model in get_available_models():
                ss.current_model = model
            
            # Mensaje de éxito solo si no es carga automática
            if not auto_load:
                st.success(f"Conversación '{ss.conversation_name}' cargada")
                st.rerun()
            
            return True
        else:
            if not auto_load:
                st.error(f"No se pudo cargar la conversación: {response.status_code}")
            return False
    except Exception as e:
        if not auto_load:
            st.error(f"Error al cargar la conversación: {str(e)}")
        return False

def update_conversation_name(conversation_id: str, new_name: str) -> bool:
    """Actualiza el nombre de una conversación en la API."""
    try:
        # Enviar solicitud al backend
        response = requests.put(
            f"{FASTAPI_BASE_URL}/conversations/{conversation_id}/rename",
            json={"new_name": new_name},
            timeout=5
        )
        
        # Verificar respuesta
        if response.status_code == 200:
            # Actualizar también el nombre en el estado local
            if "conversation_names" not in ss:
                ss.conversation_names = {}
            
            ss.conversation_names[conversation_id] = new_name
            
            if ss.conversation_id == conversation_id:
                ss.conversation_name = new_name
            
            return True
        else:
            st.error(f"Error del servidor: {response.status_code}")
            return False
    except Exception as e:
        st.error(f"Error al actualizar nombre: {str(e)}")
        return False

def list_conversations() -> List[Dict[str, Any]]:
    """Lista todas las conversaciones disponibles."""
    try:
        response = requests.get(f"{FASTAPI_BASE_URL}/conversations", timeout=5)
        if response.status_code == 200:
            conversations = response.json().get("conversations", [])
            
            # Añadir nombres personalizados si existen
            if "conversation_names" in ss:
                for conv in conversations:
                    conv_id = conv["conversation_id"]
                    if conv_id in ss.conversation_names:
                        conv["display_name"] = ss.conversation_names[conv_id]
                    else:
                        # Usar display_name si ya existe en la respuesta
                        if "display_name" in conv and conv["display_name"]:
                            continue
                        # Si no, generar nombre a partir del primer mensaje
                        try:
                            detail_response = requests.get(
                                f"{FASTAPI_BASE_URL}/conversations/{conv_id}", 
                                timeout=3
                            )
                            if detail_response.status_code == 200:
                                messages = detail_response.json().get("messages", [])
                                display_name = generate_conversation_name(messages)
                                conv["display_name"] = display_name
                                # Actualizar el diccionario local
                                if "conversation_names" not in ss:
                                    ss.conversation_names = {}
                                ss.conversation_names[conv_id] = display_name
                            else:
                                conv["display_name"] = f"Conversación {conv_id[:8]}..."
                        except:
                            conv["display_name"] = f"Conversación {conv_id[:8]}..."
            else:
                # Si no hay nombres personalizados, generar para todos
                ss.conversation_names = {}
                for conv in conversations:
                    # Si ya tiene display_name, usarlo
                    if "display_name" in conv and conv["display_name"]:
                        ss.conversation_names[conv["conversation_id"]] = conv["display_name"]
                        continue
                        
                    conv_id = conv["conversation_id"]
                    try:
                        detail_response = requests.get(
                            f"{FASTAPI_BASE_URL}/conversations/{conv_id}", 
                            timeout=3
                        )
                        if detail_response.status_code == 200:
                            messages = detail_response.json().get("messages", [])
                            display_name = generate_conversation_name(messages)
                            conv["display_name"] = display_name
                            ss.conversation_names[conv_id] = display_name
                        else:
                            conv["display_name"] = f"Conversación {conv_id[:8]}..."
                    except:
                        conv["display_name"] = f"Conversación {conv_id[:8]}..."
            
            return conversations
        else:
            st.warning(f"No se pudieron listar las conversaciones: {response.status_code}")
            return []
    except Exception as e:
        st.warning(f"Error al listar conversaciones: {str(e)}")
        return []

def delete_conversation(conversation_id: str) -> bool:
    """Elimina una conversación."""
    try:
        response = requests.delete(f"{FASTAPI_BASE_URL}/conversations/{conversation_id}", timeout=5)
        success = response.status_code == 200
        
        if success and "conversation_names" in ss and conversation_id in ss.conversation_names:
            del ss.conversation_names[conversation_id]
        
        return success
    except Exception as e:
        st.error(f"Error al eliminar conversación: {str(e)}")
        return False

def format_message_with_think_blocks(message: str, is_streaming: bool = False) -> str:
    """
    Formatea un mensaje reemplazando etiquetas <think> con divs estilizados.
    Evita mostrar HTML crudo durante el streaming.
    """
    if "<think>" not in message:
        return message
    
    # Patrón para extraer contenido entre <think> y </think>
    pattern = r'<think>(.*?)</think>'
    
    # Función para reemplazar cada coincidencia
    def replace_think_block(match):
        think_content = match.group(1).strip()
        return f"""
        <details class="thinking-block">
            <summary>💭 Ver pensamiento</summary>
            <div class="think-content">
                {think_content}
            </div>
        </details>
        """
    
    # Reemplazar todos los bloques <think> completos
    result = re.sub(pattern, replace_think_block, message, flags=re.DOTALL)
    
    # Si estamos en streaming y hay un bloque <think> sin cerrar
    if is_streaming and message.count("<think>") > message.count("</think>"):
        # Encontrar la última apertura de <think>
        last_think_pos = message.rfind("<think>")
        last_partial_content = ""
        
        if last_think_pos >= 0:
            partial_think = message[last_think_pos + 7:] 
            partial_think = re.sub(r'</?[a-zA-Z]+[^>]*$', '', partial_think)
            partial_think = re.sub(r'<$', '', partial_think)
            partial_think = partial_think.replace("<", "&lt;").replace(">", "&gt;")
            last_partial_content = partial_think
            
            prefix = result[:last_think_pos]
            result = prefix + f"""
            <div class="streaming-block">
                <strong>💭 Pensando...</strong>
                <div class="think-content">
                    {last_partial_content}
                </div>
            </div>
            """
    
    # Asegurarse de que no haya HTML incompleto al final del resultado
    result = re.sub(r'</?[a-zA-Z]+[^>]*$', '', result)
    
    return result

# Inicialización del estado de la sesión
if "messages" not in ss:
    ss.messages = []

if "current_model" not in ss:
    ss.current_model = "gemma3:27b"

if "temperature" not in ss:
    ss.temperature = 0.7

if "conversation_names" not in ss:
    ss.conversation_names = {}

if "rename_conversation" not in ss:
    ss.rename_conversation = False

if "show_delete_confirm" not in ss:
    ss.show_delete_confirm = False

# Obtener o crear ID de conversación
conversation_id = get_or_create_conversation_id()

# Interfaz de usuario
st.title("💬 Chat con Modelos LLM")
if "conversation_name" in ss:
    st.caption(f"{ss.conversation_name} (ID: {conversation_id})")
else:
    st.caption(f"Conversación ID: {conversation_id}")

# Verificar la conexión con la API
health_info = check_api_health()
if health_info.get("status") != "healthy":
    st.error(f"❌ No se puede conectar con la API: {health_info.get('error', 'Error desconocido')}")
    st.stop()
else:
    st.success(f"✅ API conectada - Redis TTL: {health_info.get('conversation_ttl', 'N/A')}")

# Barra lateral para configuración
with st.sidebar:
    st.header("Configuración")
    
    # Sección de conversaciones
    st.subheader("Conversaciones")
    
    # Lista de conversaciones disponibles
    conversations = list_conversations()
    
    if conversations:
        st.write(f"Tienes {len(conversations)} conversaciones guardadas")
    
        # Selector de conversación con nombres descriptivos
        conversation_options = {
            f"{c.get('display_name', c['conversation_id'][:8])}": c['conversation_id'] 
            for c in conversations
        }
        
        # Añadir opción para nueva conversación
        all_options = {"Nueva conversación": "new"} | conversation_options
        
        # Encontrar el índice de la conversación actual
        current_index = 0  # Default a "Nueva conversación"
        for i, (name, conv_id) in enumerate(all_options.items()):
            if conv_id == ss.conversation_id:
                current_index = i
                break
        
        selected_conversation_name = st.selectbox(
            "Cargar conversación",
            options=list(all_options.keys()),
            index=current_index
        )
        
        selected_conversation_id = all_options[selected_conversation_name]
        
        # Si se selecciona una conversación (que no sea "Nueva conversación"),
        # cargarla automáticamente si cambia la selección
        if selected_conversation_id != "new" and selected_conversation_id != ss.conversation_id:
            load_conversation(selected_conversation_id)
        
        # Verificar si hay una conversación cargada y si no es la opción "Nueva conversación"
        if selected_conversation_id != "new" and ss.messages:
            # Mostrar acciones solo si hay una conversación cargada
            st.write("Acciones para la conversación actual:")
            
            col1, col2 = st.columns(2)
            
            # Renombrar conversación
            with col1:
                if st.button("✏️ Renombrar", key="rename_button"):
                    ss.rename_conversation = True
                    ss.show_delete_confirm = False  # Ocultar diálogo de eliminar si estaba visible
                    st.rerun()
            
            # Eliminar conversación 
            with col2:
                if st.button("🗑️ Eliminar", key="delete_button"):
                    ss.show_delete_confirm = True
                    ss.rename_conversation = False  # Ocultar formulario de renombrado si estaba visible
                    st.rerun()
        else:
            # Si no hay conversación seleccionada, mostrar un mensaje
            st.write("Selecciona una conversación o crea una nueva para ver las acciones disponibles.")

        # Mostrar diálogo de confirmación de eliminación sin usar columnas anidadas
        if ss.show_delete_confirm:
            st.markdown("""
            <div class="form-box delete-warning">
                <div class="form-title">¿Estás seguro de que deseas eliminar esta conversación?</div>
                <div style="display: flex; justify-content: space-between; margin-top: 10px; gap: 10px;">
                    <div id="placeholder-cancel" style="flex: 1;"></div>
                    <div id="placeholder-confirm" style="flex: 1;"></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # Creamos un contenedor para cada botón
            container = st.container()
            
            # Dentro del contenedor, creamos dos columnas a nivel de Streamlit (no anidadas)
            cancel_col, confirm_col = st.columns(2)
            
            with cancel_col:
                cancel_clicked = st.button("Cancelar", key="cancel_delete_btn", use_container_width=True)
                if cancel_clicked:
                    ss.show_delete_confirm = False
                    st.rerun()
            
            with confirm_col:
                confirm_clicked = st.button("Confirmar", key="confirm_delete_btn", type="primary", use_container_width=True)
                if confirm_clicked:
                    if delete_conversation(ss.conversation_id):
                        ss.messages = []
                        ss.conversation_id = str(uuid.uuid4())
                        ss.conversation_name = "Nueva conversación"
                        ss.show_delete_confirm = False
                        st.success("Conversación eliminada. Se ha creado una nueva.")
                        st.rerun()
                    else:
                        st.error("No se pudo eliminar la conversación")
                        ss.show_delete_confirm = False
                        st.rerun()
        
        # Mostrar formulario de renombrado si es necesario
        if ss.rename_conversation:
            st.markdown('<div class="rename-form"><div class="rename-title">Renombrar conversación</div>', unsafe_allow_html=True)
            
            # Input para el nuevo nombre
            new_name = st.text_input(
                "Nuevo nombre:", 
                value=ss.conversation_name,
                key="new_name_input",
                label_visibility="collapsed"  # Ocultar la etiqueta "Nuevo nombre:"
            )
            
            # En lugar de usar columnas (que pueden causar problemas), usar HTML para los botones
            st.markdown('<div class="button-row">', unsafe_allow_html=True)
            
            # Botones sin columnas
            cancel_rename = st.button("Cancelar", key="cancel_rename_btn")
            save_rename = st.button("Guardar", key="save_rename_btn")
            
            if cancel_rename:
                ss.rename_conversation = False
                st.rerun()
            
            if save_rename:
                if new_name.strip():
                    if update_conversation_name(ss.conversation_id, new_name):
                        ss.conversation_name = new_name
                        ss.rename_conversation = False
                        st.success(f"Nombre actualizado a '{new_name}'")
                        st.rerun()
                    else:
                        st.error("Error al actualizar el nombre")
                else:
                    st.warning("El nombre no puede estar vacío")
            
            st.markdown('</div>', unsafe_allow_html=True)  # Cierre de button-row
            st.markdown('</div>', unsafe_allow_html=True)  # Cierre de rename-form
    else:
        st.write("No hay conversaciones guardadas")
    
    st.divider()
    
    # Intentar obtener modelos disponibles
    available_models = get_available_models()
    
    # Selector de modelo
    selected_model = st.selectbox(
        "Seleccionar modelo",
        options=available_models,
        index=available_models.index(ss.current_model) if ss.current_model in available_models else 0
    )
    
    if selected_model != ss.current_model:
        ss.current_model = selected_model
    
    # Control de temperatura
    temperature = st.slider(
        "Temperatura",
        min_value=0.0,
        max_value=1.0,
        value=ss.temperature,
        step=0.1,
        help="Valores más altos = más creatividad, valores más bajos = más determinismo"
    )
    
    if temperature != ss.temperature:
        ss.temperature = temperature
    
    # Botón para nueva conversación
    if st.button("🆕 Nueva conversación", key="new_conversation_btn"):
        ss.messages = []
        ss.conversation_id = str(uuid.uuid4())
        ss.conversation_name = "Nueva conversación"
        st.success("Se ha creado una nueva conversación")
        st.rerun()
    
    st.divider()
    st.caption("Desarrollado con memoria Redis para MacOS M4 Max")

# Función para generar respuestas con streaming
def generate_streaming_response(prompt: str, model: str, temperature: float = 0.7):
    """Genera una respuesta con streaming usando la API de FastAPI."""
    
    # Obtener ID de conversación
    conversation_id = get_or_create_conversation_id()
    
    # Mostrar el mensaje del usuario inmediatamente
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Contenedor para la respuesta del asistente
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = ""
        
        try:
            # Realizar la solicitud con streaming
            with requests.post(
                f"{FASTAPI_BASE_URL}/generate-stream",
                json={
                    "prompt": prompt,
                    "model": model,
                    "conversation_id": conversation_id,
                    "stream": True,
                    "options": {"temperature": temperature}
                },
                stream=True,
                timeout=120  # 2 minutos para respuesta completa
            ) as response:
                # Verificar si la respuesta es exitosa
                if response.status_code != 200:
                    st.error(f"Error del servidor: {response.status_code}")
                    if response.headers.get('content-type') == 'application/json':
                        st.error(response.json().get('detail', 'Sin detalles'))
                    return
                
                # Procesar el streaming
                for line in response.iter_lines():
                    if line:
                        line = line.decode('utf-8').strip()
                        if line.startswith('data: '):
                            try:
                                data = json.loads(line[6:])  # Eliminar 'data: ' del principio
                                chunk = data.get("response", "")
                                full_response += chunk
                                
                                # Formatear respuesta con manejo de bloques think
                                formatted_response = format_message_with_think_blocks(full_response, is_streaming=True)
                                message_placeholder.markdown(formatted_response + "▌", unsafe_allow_html=True)
                                
                                # Actualizar el ID de conversación por si es nuevo
                                if "conversation_id" in data:
                                    new_conversation_id = data.get("conversation_id")
                                    if new_conversation_id != conversation_id:
                                        ss.conversation_id = new_conversation_id
                                        conversation_id = new_conversation_id
                                        
                                        # Si es una nueva conversación, generar nombre
                                        if len(ss.messages) == 0:
                                            new_name = generate_conversation_name([{"role": "user", "content": prompt}])
                                            ss.conversation_name = new_name
                                            update_conversation_name(conversation_id, new_name)
                            except json.JSONDecodeError:
                                continue
                
                # Mostrar mensaje final sin cursor
                final_formatted = format_message_with_think_blocks(full_response, is_streaming=False)
                message_placeholder.markdown(final_formatted, unsafe_allow_html=True)
        
        except RequestException as e:
            st.error(f"Error de conexión: {str(e)}")
            return
        except Exception as e:
            st.error(f"Error inesperado: {str(e)}")
            return
    
    # Actualizar el historial de mensajes (ya se ha guardado en Redis)
    ss.messages.append({"role": "user", "content": prompt})
    ss.messages.append({"role": "assistant", "content": full_response})
    # Si es una nueva conversación, actualizar el nombre
    if len(ss.messages) <= 2:
        new_name = generate_conversation_name(ss.messages)
        ss.conversation_name = new_name
        # Actualizar el nombre en la API sin mostrar mensajes de depuración
        update_conversation_name(ss.conversation_id, new_name)

        # Actualizar la lista de conversaciones
        if "conversations_list" in ss:
            ss.conversations_list = list_conversations()
        else:
            ss.conversations_list = list_conversations()
        
        # Forzar recargar la página para que se muestre la nueva conversación
        st.rerun()

# Mostrar historial de mensajes
for message in ss.messages:
    with st.chat_message(message["role"]):
        # Formatear mensaje con bloques think
        formatted_message = format_message_with_think_blocks(message["content"])
        st.markdown(formatted_message, unsafe_allow_html=True)

# Área de entrada del usuario
prompt = st.chat_input("Escribe tu mensaje aquí...")
if prompt:
    generate_streaming_response(
        prompt=prompt, 
        model=ss.current_model,
        temperature=ss.temperature
    )