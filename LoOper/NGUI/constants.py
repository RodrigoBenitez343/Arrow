# graphui/constants.py

# Color Palette (NothingOS-inspired, clean minimal dark)
RED_PRIMARY = '#FF4B4B'
RED_DARK = '#D43C3C'
DARK_GREY = '#1E2329'  # Deep Charcoal - base wall
MEDIUM_GREY = '#1A1A1A'
LIGHT_GREY = '#2E2E2E'
TEXT_COLOR = '#EEEEEE'
ACCENT_COLOR = '#00E0B8'
BLOCK_COLOR = '#1F1F1F'
BLOCK_HOVER = '#242424'
FALLBACK_COLOR = '#8B4513'

# Elevation / piling shades (progressive lightening of DARK_GREY)
SIDE_PANEL_BG = '#242B32'  # Level 1 - left & right panels
CENTER_BG = '#2C333D'      # Level 2 - graph area background
BAR_BG = '#343D48'         # Level 3 - top & bottom bars (highest)
GRAPH_PLANE = '#0D1117'   # Graph canvas / wall color (darkest)
SCREENSHOT_COLOR = '#00008B'
ACTION_COLOR = '#1F1F1F'
CONDITIONAL_COLOR = '#7B61FF'
LLM_COLOR = '#BD6C4D'  # Muted Matte Orange
TTS_COLOR = '#00CED1'
CHAIN_IMPORT_COLOR = '#5C8D68'  # Muted Matte Green
DETERMINISTIC_CHAIN_COLOR = '#3D9970'  # Brighter green: frozen / orchestrator-free chain (chains port)
FORM_FILLER_COLOR = '#DDA0DD'
CODE_NODE_COLOR = '#F1C40F'
CONTAINER_NODE_COLOR = '#4A90E2'
CONTEXT_NODE_COLOR = '#4A7DA5'  # Muted Matte Blue
INPUT_NODE_COLOR = '#E67E22'      # Amber/Orange
HANDLE_NODE_COLOR = '#9B59B6'      # Purple
MCP_NODE_COLOR = '#E74C3C'           # Red (MCP protocol)
OUTPUT_NODE_COLOR = '#FFA726'        # Warm Orange/Gold (Output sink)
WEB_SEQUENCE_COLOR = '#2E86AB'      # Muted Teal (WebSequenceNode)

# Port Color Mapping (visual emphasis; labels are background logic)
INPUT_PORT_COLOR = '#4DB6FF'   # Bright blue for inputs
OUTPUT_PORT_COLOR = '#66BB6A'  # Green for outputs
PORT_TRUE_COLOR = '#2ECC71'    # Green for boolean true
PORT_FALSE_COLOR = '#EF5350'   # Red for boolean false

# Port Types
UNIVERSAL_PORT_TYPE = 'neural_network'
