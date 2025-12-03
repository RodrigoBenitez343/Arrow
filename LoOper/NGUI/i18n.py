import locale

SUPPORTED_LANGUAGES = {"en", "es"}
_LANG = None

# English uses original strings; Spanish provides translations keyed by English text
TRANSLATIONS = {
    "es": {
        "&File": "&Archivo",
        "&Exit": "&Salir",
        "&Settings": "&Configuración",
        "&AI Configuration": "&Configuración de IA",
        "&View": "&Vista",
        "Show Toolbar": "Mostrar barra de herramientas",
        "&Scheduler": "&Planificador",
        "&Manage Schedules": "&Gestionar programaciones",
        "LoOperV2": "LoOperV2",

        # Context menu / node actions
        "Edit Sequence Properties": "Editar propiedades de la secuencia",
        "Add Action": "Añadir acción",
        "Remove Action": "Eliminar acción",
        "Edit Conditionals": "Editar condicionales",
        "Edit LLM Configuration": "Editar configuración LLM",
        "Edit TTS Properties": "Editar propiedades TTS",
        "Edit Chain Import Configuration": "Editar configuración de importación de cadena",
        "Expand Chain (Edit in Separate View)": "Expandir cadena (editar en vista separada)",
        "Add Conditional Node": "Añadir nodo condicional",
        "Add LLM Node": "Añadir nodo LLM",
        "Add TTS Node": "Añadir nodo TTS",
        "Add Chain Import Node": "Añadir nodo de importación de cadena",

        # Widgets
        "No sequences": "Sin secuencias",

        # Chain import dialogs
        "Chain Import Configuration": "Configuración de importación de cadena",
        "Chain File:": "Archivo de cadena:",
        "Chain Information:": "Información de la cadena:",
        "Import Mode:": "Modo de importación:",
        "Node Prefix:": "Prefijo de nodo:",
        "Optional prefix for imported node names": "Prefijo opcional para nombres de nodos importados",
        "Loop Count:": "Número de iteraciones:",
        "Extra Delay (s):": "Retraso adicional (s):",
        "Edit Imported Chain": "Editar cadena importada",
        "Editing Chain: {chain_name}": "Editando cadena: {chain_name}",
        "Chain file not found or not configured.": "Archivo de cadena no encontrado o no configurado.",
        "No chain file configured.": "Ningún archivo de cadena configurado.",
        "Failed to load chain configuration: {error}": "Error al cargar la configuración de la cadena: {error}",
        "Chain changes saved successfully.": "Cambios de la cadena guardados correctamente.",
        "Failed to save changes: {error}": "Error al guardar cambios: {error}",
        "Maximize/Restore": "Maximizar/Restaurar",
        "Save Changes": "Guardar cambios",

        # AI settings
        "AI Settings": "Ajustes de IA",
        "e.g., 100.64.1.2 or localhost": "p.ej., 100.64.1.2 o localhost",
        "e.g., localhost or 100.64.1.3": "p.ej., localhost o 100.64.1.3",
        "Run the API server locally if you have sufficient resources": "Ejecuta el servidor API localmente si tienes recursos suficientes",
        "Checking connection...": "Comprobando conexión...",
        "Available Models:": "Modelos disponibles:",
        "Test Connection": "Probar conexión",
        "Local API Server": "Servidor API local",
        "Start Local Server": "Iniciar servidor local",
        "Stop Local Server": "Detener servidor local",
        "Connection Status": "Estado de la conexión",
        "Auto-start local server:": "Inicio automático del servidor local:",
        "Connection timeout:": "Tiempo de espera de conexión:",
        "API Configuration": "Configuración de API",
        "Advanced Settings": "Ajustes avanzados",
        "AI Settings Error": "Error de ajustes de IA",

        # LLM dialogs
        "LLM Node Configuration": "Configuración de nodo LLM",
        "Quick Templates": "Plantillas rápidas",
        "Prompt Configuration": "Configuración del prompt",
        "Basic": "Básico",
        "Templates": "Plantillas",
        "Advanced": "Avanzado",
        "Prompt": "Indicaciones",
        "Model Configuration": "Configuración de modelo",
        "Generation Parameters": "Parámetros de generación",
        "Input Source": "Fuente de entrada",
        "Output Configuration": "Configuración de salida",
        "Async Processing": "Procesamiento asíncrono",
        "Model:": "Modelo:",
        "API URL:": "URL de API:",
        "Refresh Models": "Actualizar modelos",
        "Temperature:": "Temperatura:",
        "Max Tokens:": "Máximo de tokens:",
        "Output Variable:": "Variable de salida:",
        "Apply Template:": "Aplicar plantilla:",
        "System Message (optional):": "Mensaje del sistema (opcional):",
        "Enter system instructions for the AI model...": "Introduce instrucciones del sistema para el modelo de IA...",
        "Prompt:": "Indicaciones:",
        "Enter your prompt here...": "Introduce tu prompt aquí...",
        "No input (direct prompt)": "Sin entrada (prompt directo)",
        "Extract text from screen (OCR)": "Extraer texto de la pantalla (OCR)",
        "Use output from previous node": "Usar salida del nodo anterior",
        "Use clipboard content": "Usar contenido del portapapeles",
        "Custom (no template)": "Personalizado (sin plantilla)",
        "Information Extraction (OCR + LLM)": "Extracción de información (OCR + LLM)",
        "Custom Instructions": "Instrucciones personalizadas",
        "Direct Prompt": "Prompt directo",
        "ComoRAG Analysis": "Análisis con ComoRAG",
        "Data Processing Chain": "Cadena de procesamiento de datos",
        "Text Analysis": "Análisis de texto",
        "Content Summarization": "Resumen de contenido",
        "Use Vision:": "Usar visión:",
        "Screenshot:": "Captura de pantalla:",
        "Vision Configuration": "Configuración de visión",
        "Enable vision capabilities for this model": "Habilitar capacidades de visión para este modelo",
        "Take screenshot automatically for vision models": "Tomar captura automáticamente para modelos de visión",
        "OCR Confidence:": "Confianza OCR:",
        "Text Preprocessing:": "Preprocesamiento de texto:",
        "None": "Ninguno",
        "Extract structured data": "Extraer datos estructurados",
        "Write Text:": "Escribir texto:",
        "Automatically write generated text": "Escribir automáticamente el texto generado",
        "Typing Batch Size:": "Tamaño de lote de escritura:",
        "Typing Batch Delay:": "Retraso entre lotes de escritura:",
        "Number of characters to type at once (higher = faster typing)": "Número de caracteres a escribir de una vez (mayor = escritura más rápida)",
        "Delay between typing batches in seconds (lower = faster typing)": "Retraso entre lotes de escritura en segundos (menor = escritura más rápida)",
        "Enable Async:": "Habilitar asíncrono:",
        "Process requests asynchronously": "Procesar solicitudes de forma asíncrona",
        "Enable asynchronous processing for better performance": "Habilitar procesamiento asíncrono para mejor rendimiento",
        "Async processing allows the workflow to continue while waiting for LLM responses.": "El procesamiento asíncrono permite que el flujo continúe mientras se esperan respuestas del LLM.",
        "Use Vision:": "Usar visión:",
        "Screenshot:": "Captura de pantalla:",
        "Note: Vision models can analyze screenshots along with text prompts.": "Nota: Los modelos de visión pueden analizar capturas junto con indicaciones de texto.",
        "Enable ComoRAG:": "Habilitar ComoRAG:",
        "Max Cycles:": "Máximo de ciclos:",
        "Memory Size:": "Tamaño de memoria:",
        "ComoRAG enhances LLM responses with context-aware reasoning and connects to OverWatch API.": "ComoRAG mejora las respuestas del LLM con razonamiento contextual y se conecta al API de OverWatch.",
        "Describe what to extract/analyze from the screen text...": "Describe qué extraer/analizar del texto en pantalla...",
        "Process the previous node's output...": "Procesar la salida del nodo anterior...",
        "Process the clipboard content...": "Procesar el contenido del portapapeles...",
        "Enter instructions for processing the extracted text...": "Introduce instrucciones para procesar el texto extraído...",
        "Enter instructions for processing the input from previous node...": "Introduce instrucciones para procesar la entrada del nodo anterior...",
        "Enter instructions for processing the clipboard content...": "Introduce instrucciones para procesar el contenido del portapapeles...",
        "No input": "Sin entrada",
        "OCR": "OCR",
        "Previous Node": "Nodo anterior",
        "Clipboard": "Portapapeles",
        "Clean formatting": "Limpiar formato",
        "Enhance contrast": "Mejorar contraste",
        "Remove extra spaces": "Eliminar espacios extra",
        "Use custom configuration without predefined template.": "Usa configuración personalizada sin plantilla predefinida.",
        "Extract specific information from screen text using OCR + LLM.": "Extrae información específica del texto en pantalla usando OCR + LLM.",
        "Define your own system message and prompt for the LLM.": "Define tu propio mensaje del sistema y prompt para el LLM.",
        "Simple direct prompt without additional configuration.": "Prompt directo simple sin configuración adicional.",
        "Use ComoRAG for context-aware analysis with memory and reasoning.": "Usa ComoRAG para análisis con conciencia del contexto, memoria y razonamiento.",
        "Process data from previous nodes in a multi-step workflow.": "Procesa datos de nodos anteriores en un flujo de varios pasos.",
        "Analyze text content from clipboard with detailed insights.": "Analiza el texto del portapapeles con información detallada.",
        "Summarize content extracted from screen using OCR.": "Resume contenido extraído de la pantalla usando OCR.",
        "Minimum confidence for OCR text extraction": "Confianza mínima para la extracción de texto OCR",
        "OK": "Aceptar",

        # Sequence dialogs
        "Sequence Properties": "Propiedades de la secuencia",

        # Scheduler dialogs
        "Manage Schedules": "Gestionar programaciones",
        "Schedule Configuration": "Configuración de programación",
        "Schedule Name:": "Nombre de la programación:",
        "Enter a descriptive name for this schedule": "Introduce un nombre descriptivo para esta programación",
        "Schedule Type:": "Tipo de programación:",
        "No file selected": "Ningún archivo seleccionado",
        "📅 Run at specific date and time:": "📅 Ejecutar en fecha y hora específicas:",
        "🌅 Daily at time:": "🌅 Diario a la hora:",
        "📅 Weekly schedule:": "📅 Programación semanal:",
        "Time:": "Hora:",
        "Days of week:": "Días de la semana:",
        "🔄 Repeat every (minutes):": "🔄 Repetir cada (minutos):",
        "Enabled": "Habilitado",
        "Name": "Nombre",
        "Type": "Tipo",
        "When": "Cuándo",
        "Next Run": "Próxima ejecución",
        "Chain": "Cadena",
        "Actions": "Acciones",
        "Add Schedule": "Añadir programación",
        "Refresh": "Actualizar",
        "Close": "Cerrar",
        "Run": "Ejecutar",
        "Edit": "Editar",
        "Del": "Borrar",
        "Run this schedule now": "Ejecutar esta programación ahora",
        "Edit this schedule": "Editar esta programación",
        "Delete this schedule": "Eliminar esta programación",
        "Run Now Failed": "Error al ejecutar ahora",
        "Update Failed": "Error al actualizar",
        "Add Failed": "Error al añadir",
        "📋 Basic Configuration": "📋 Configuración básica",
        "⏰ Timing Configuration": "⏰ Configuración de tiempo",
        "Select Chain JSON...": "Seleccionar JSON de cadena...",
        "Select Chain JSON File": "Seleccionar archivo JSON de cadena",
        "JSON Files (*.json);;All Files (*.*)": "Archivos JSON (*.json);;Todos los archivos (*.*)",
        " minutes": " minutos",
        "Cancel": "Cancelar",
        "Save Schedule": "Guardar programación",
        "Schedule": "Programación",
        "Full path: {path}": "Ruta completa: {path}",
        "Mon": "Lun",
        "Tue": "Mar",
        "Wed": "Mié",
        "Thu": "Jue",
        "Fri": "Vie",
        "Sat": "Sáb",
        "Sun": "Dom",
        "every": "cada",
        "min": "min",

        # Fallback dialogs
        "Fallback Sequence:": "Secuencia de fallback:",
        "Retry Attempts:": "Intentos de reintento:",
        "Retry Delay (s):": "Retraso de reintento (s):",
        "Configure Fallback - Sequence {seq}, Action {action}": "Configurar fallback - Secuencia {seq}, Acción {action}",

        # TTS dialogs
        "TTS Properties": "Propiedades TTS",
        "Text to Speak:": "Texto a pronunciar:",
        "Language:": "Idioma:",

        # Trigger dialogs
        "OCR Trigger Configuration": "Configuración de disparador OCR",
        "Wait Condition": "Condición de espera",
        "Conditional Loop": "Bucle condicional",
        "Image Path:": "Ruta de imagen:",
        "Confidence:": "Confianza:",
        "Timeout (s):": "Tiempo de espera (s):",
        "Timeout (s) - Required:": "Tiempo de espera (s) - Obligatorio:",
        "No timeout": "Sin tiempo de espera",
        "Target Text:": "Texto objetivo:",
        "Wait Type:": "Tipo de espera:",
        "Case Sensitive:": "Distinguir mayúsculas/minúsculas:",
        "Wait Duration (s):": "Duración de espera (s):",
        "Loop Type:": "Tipo de bucle:",
        "Sequence File:": "Archivo de secuencia:",
        "Condition Method:": "Método de condición:",
        "Condition Image:": "Imagen de condición:",
        "Max Iterations:": "Iteraciones máximas:",
        "Iteration Delay (s):": "Retraso entre iteraciones (s):",

        # Conditional dialogs
        "Advanced Conditions - Sequence {num}": "Condiciones avanzadas - Secuencia {num}",
        "Presence Triggers (must be present to execute)": "Disparadores de presencia (deben estar presentes para ejecutar)",
        "Add Presence Trigger": "Añadir disparador de presencia",
        "Absence Triggers (must be absent to execute)": "Disparadores de ausencia (deben estar ausentes para ejecutar)",
        "Add Absence Trigger": "Añadir disparador de ausencia",
        "OCR Triggers (text must be present to execute)": "Disparadores OCR (el texto debe estar presente para ejecutar)",
        "Add OCR Trigger": "Añadir disparador OCR",
        "Pre-Sequence Conditional Loops": "Bucles condicionales previos a la secuencia",
        "Add Pre-Loop": "Añadir bucle previo",
        "Remove Selected": "Eliminar seleccionado",
        "Pre-Conditions": "Precondiciones",
        "Post-Conditions": "Postcondiciones",
        "Conditional Loops": "Bucles condicionales",
        "Wait Conditions (wait after sequence execution)": "Condiciones de espera (esperar tras la ejecución de la secuencia)",
        "Add Wait Condition": "Añadir condición de espera",
        "Image: {path} (Confidence: {conf})": "Imagen: {path} (Confianza: {conf})",
        "Text: '{text}' (Confidence: {conf})": "Texto: '{text}' (Confianza: {conf})",
        "Wait for {ctype}: {path} (Timeout: {sec}s)": "Esperar {ctype}: {path} (Tiempo de espera: {sec}s)",

        # Common buttons and file dialogs
        "Save": "Guardar",
        "Browse...": "Examinar...",
        "Select Image File": "Seleccionar archivo de imagen",
        "Image Files (*.png *.jpg *.jpeg *.bmp)": "Archivos de imagen (*.png *.jpg *.jpeg *.bmp)",
        "Select Sequence File": "Seleccionar archivo de secuencia",

        # Type labels used in dynamic wait condition strings
        "presence": "presencia",
        "absence": "ausencia",
        "ocr": "ocr",
        "wait_time": "tiempo de espera",

        # Generic dialog titles/messages
        "Record New Sequence": "Grabar nueva secuencia",
        "Enter sequence name:": "Introduce el nombre de la secuencia:",
        "new_sequence": "nueva_secuencia",
        "Invalid Input": "Entrada no válida",
        "Please enter a valid sequence name.": "Por favor, introduce un nombre de secuencia válido.",
        "Please select a chain file.": "Por favor, selecciona un archivo de cadena.",
        "Selected chain file does not exist.": "El archivo de cadena seleccionado no existe.",
        "Recording Error": "Error de grabación",
        "No Chain": "Sin cadena",
        "No chain configuration available to run.": "No hay configuración de cadena disponible para ejecutar.",
        "Empty Chain": "Cadena vacía",
        "No nodes found in the current chain.": "No se encontraron nodos en la cadena actual.",
        "Chain Error": "Error de cadena",
        "Open Chain": "Abrir cadena",
        "Graph Not Ready": "Gráfico no listo",
        "The graph view is not fully initialized yet. Please wait a moment and try again.": "La vista del gráfico no está completamente inicializada aún. Espera un momento e inténtalo de nuevo.",
        "Save Chain File": "Guardar archivo de cadena",
        "Chain saved successfully!": "¡Cadena guardada correctamente!",
        "Save Error": "Error al guardar",
        "Error": "Error",
        "Warning": "Advertencia",
        "Success": "Éxito"
    }
}

def detect_os_language():
    """Return a two-letter language code based on OS locale."""
    try:
        loc = locale.getdefaultlocale()
        code = (loc[0] or "").split("_")[0].lower()
    except Exception:
        code = ""
    if code in SUPPORTED_LANGUAGES:
        return code
    return "en"

def set_language(lang_code=None):
    """Set current UI language; defaults to English if unsupported."""
    global _LANG
    if not lang_code:
        lang_code = "en"
    lang_code = lang_code.lower()
    _LANG = lang_code if lang_code in SUPPORTED_LANGUAGES else "en"

def set_language_from_os():
    """Detect and set language from OS locale."""
    set_language(detect_os_language())

def _(text):
    """Translate text to current language; falls back to original."""
    lang = _LANG or "en"
    if lang == "en":
        return text
    return TRANSLATIONS.get(lang, {}).get(text, text)