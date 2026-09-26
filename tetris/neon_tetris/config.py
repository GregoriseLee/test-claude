"""Tunable constants for Neon Tetris."""

TITLE = "NEON TETRIS"

# Logical render resolution. Everything is drawn at this size and then
# scaled (letterboxed) to whatever the real window size is.
SCREEN_W, SCREEN_H = 1280, 720
FPS_CAP = 144

# Board geometry
BOARD_W = 10
VISIBLE_H = 20
HIDDEN = 2                      # spawn rows above the visible field
BOARD_H = VISIBLE_H + HIDDEN
CELL = 32
BOARD_X = (SCREEN_W - BOARD_W * CELL) // 2
BOARD_Y = (SCREEN_H - VISIBLE_H * CELL) // 2
BOARD_PX_W = BOARD_W * CELL
BOARD_PX_H = VISIBLE_H * CELL

# Side panels
PANEL_W = 170
LEFT_X = BOARD_X - 30 - PANEL_W
RIGHT_X = BOARD_X + BOARD_PX_W + 30

# Handling / timing (seconds)
DAS = 0.160                     # delayed auto shift
ARR = 0.035                     # auto repeat rate
LOCK_DELAY = 0.5
MAX_LOCK_RESETS = 15
CLEAR_DELAY = 0.40              # line clear animation time
SOFT_DROP_FACTOR = 20
COUNTDOWN_STEP = 0.55

PIECE_COLORS = {
    "I": (0, 235, 255),
    "O": (255, 214, 0),
    "T": (185, 70, 255),
    "S": (60, 240, 100),
    "Z": (255, 55, 85),
    "J": (50, 120, 255),
    "L": (255, 145, 30),
    "gray": (105, 110, 128),
}

# Background palettes (dark base, mid tone, bright accent) - one per level,
# cycling. Values are linear-ish 0..1 floats fed straight to the shader.
PALETTES = [
    ((0.02, 0.03, 0.10), (0.25, 0.10, 0.45), (0.10, 0.75, 1.00)),  # indigo / cyan
    ((0.05, 0.02, 0.08), (0.45, 0.08, 0.40), (1.00, 0.30, 0.75)),  # magenta
    ((0.01, 0.06, 0.06), (0.05, 0.35, 0.35), (0.30, 1.00, 0.70)),  # teal
    ((0.08, 0.02, 0.02), (0.50, 0.15, 0.05), (1.00, 0.60, 0.15)),  # sunset
    ((0.02, 0.04, 0.08), (0.20, 0.30, 0.55), (0.70, 0.90, 1.00)),  # ice
    ((0.03, 0.05, 0.01), (0.25, 0.40, 0.05), (0.75, 1.00, 0.20)),  # acid
    ((0.06, 0.01, 0.06), (0.30, 0.05, 0.50), (0.90, 0.40, 1.00)),  # violet
    ((0.08, 0.05, 0.00), (0.45, 0.30, 0.02), (1.00, 0.85, 0.30)),  # gold
]
