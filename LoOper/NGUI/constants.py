# graphui/constants.py

# Color Palette - parity with the Arrow-online marketing site:
# one accent + neutral greys; the base is the darkest layer and cards are
# lighter than it (depth from a hairline border, not from heavy contrast).
ACCENT_COLOR = '#22d3ee'        # single brand accent (cyan): links, focus, highlights
ACCENT_HOVER = '#4fdcf0'
ACCENT_PRESSED = '#0ea5b7'
ACCENT_SOFT = 'rgba(34, 211, 238, 0.12)'
PICKER_ACCENT = '#00e0b8'       # element-picker green (recording/picking affordance)

RED_PRIMARY = '#FF4B4B'         # legacy alias used for destructive actions
RED_DARK = '#D43C3C'
DANGER_COLOR = '#ef4444'        # destructive (stop / delete) hover fills
DANGER_HOVER = '#f87171'

# Three stacked surface planes: canvas (window/dialog) -> card (raised section)
# -> well/control (things inside a card). The contrast between planes is what
# makes a section read as a filled panel rather than an outlined box.
DARK_GREY = '#0a0a0a'           # plane 0 - window / dialog canvas (darkest)
MEDIUM_GREY = '#1a1a1a'         # plane 1 - cards, sections, menus (raised)
BLOCK_COLOR = '#1a1a1a'         # legacy alias used for card fills
BLOCK_HOVER = '#23262b'         # legacy alias used for control hover
CARD_BG = '#1a1a1a'             # filled card / section
WELL_BG = '#101216'             # inset inside a card (lists, logs, code, tables)
CONTROL_BG = '#23262b'          # inputs / buttons sitting on a card
CONTROL_HOVER = '#2b3036'       # control hover
LIGHT_GREY = '#2b2b2b'          # borders / separators
TEXT_COLOR = '#e8eaed'          # primary text
TEXT_SECONDARY = '#d5d9e2'      # secondary text / links
TEXT_MUTED = '#7d8187'          # captions and helper text
HAIRLINE = 'rgba(255, 255, 255, 0.06)'  # 1px hairline border
FALLBACK_COLOR = '#8B4513'

# Buttons: primary = inverted pill (white), secondary = dark pill (site parity)
BTN_PRIMARY_BG = '#ffffff'
BTN_PRIMARY_TEXT = '#0a0a0a'
BTN_SECONDARY_BG = '#2c2c2c'
BTN_SECONDARY_TEXT = '#d5d9e2'

# Radii scale (site: 12 icon / 16 card / pill)
RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 16
RADIUS_PILL = 999

# Standard dialog geometry: every dialog shares one width; the height is fitted
# to its content (clamped), so windows open at a consistent size with no empty
# space and no side-to-side scrollbars. Taller content is tabbed, not scrolled.
DIALOG_W = 720
DIALOG_MIN_W = 620
DIALOG_MIN_H = 480
DIALOG_MAX_H = 860
DIALOG_LARGE_W = 1000
DIALOG_LARGE_H = 800

# Soft "float" shadow shared by the graph's floating bars and the dialogs: a
# cyan glow around the panel so it reads as lifted off its surface.
FLOAT_SHADOW_BLUR = 18
FLOAT_SHADOW_OFFSET_Y = 0   # 0 = centered glow (no downward bias)
FLOAT_SHADOW_ALPHA = 60
# The graph's floating bars are large panels, so a full-size blur on them reads
# as a huge haze. Their float glow is kept much tighter than a dialog's.
BAR_SHADOW_BLUR = 8
BAR_SHADOW_ALPHA = 38
# Transparent room a frameless dialog reserves around its panel for that shadow.
DIALOG_SHADOW_MARGIN = 24

# Type
FONT_FAMILY = ('"Segoe UI", Inter, ui-sans-serif, system-ui, -apple-system, '
               'Roboto, Helvetica, Arial, sans-serif')

# Elevation / piling shades (each level lighter than the one below it)
SIDE_PANEL_BG = '#101216'  # Level 1 - left & right panels (well)
CENTER_BG = '#131619'      # Level 2 - graph area background
BAR_BG = '#1a1a1a'         # Level 3 - top & bottom bars (card)
GRAPH_PLANE = '#0d1117'   # Graph canvas / wall color (graph area shares this)
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
