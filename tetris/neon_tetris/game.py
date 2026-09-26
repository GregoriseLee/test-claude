"""Pure game rules: board, SRS rotation, 7-bag, scoring, T-spins.

The game knows nothing about rendering or sound. Anything interesting that
happens is appended to ``Game.events`` as a small dict which the renderer
and audio system consume every frame.
"""

import random
from collections import deque

from .config import (BOARD_W, BOARD_H, HIDDEN, LOCK_DELAY, MAX_LOCK_RESETS,
                     CLEAR_DELAY, SOFT_DROP_FACTOR)

KINDS = "IOTSZJL"

# Spawn orientation of each piece inside its bounding box (x right, y down).
SPAWN_SHAPES = {
    "I": ((0, 1), (1, 1), (2, 1), (3, 1)),
    "O": ((0, 0), (1, 0), (0, 1), (1, 1)),
    "T": ((1, 0), (0, 1), (1, 1), (2, 1)),
    "S": ((1, 0), (2, 0), (0, 1), (1, 1)),
    "Z": ((0, 0), (1, 0), (1, 1), (2, 1)),
    "J": ((0, 0), (0, 1), (1, 1), (2, 1)),
    "L": ((2, 0), (0, 1), (1, 1), (2, 1)),
}
BOX = {"I": 4, "O": 2, "T": 3, "S": 3, "Z": 3, "J": 3, "L": 3}


def _rotations(kind):
    # Cell order is preserved between rotations, so cell i of rotation r is
    # the same physical block as cell i of rotation r+1 (used for animation).
    n = BOX[kind]
    cells = list(SPAWN_SHAPES[kind])
    out = []
    for _ in range(4):
        out.append(tuple(cells))
        cells = [(n - 1 - y, x) for x, y in cells]
    return out


SHAPES = {k: _rotations(k) for k in KINDS}

# SRS wall kick data, written in the usual y-up notation and converted to
# our y-down board coordinates below.
_KICKS_JLSTZ = {
    (0, 1): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (1, 0): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (1, 2): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (2, 1): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (2, 3): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
    (3, 2): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (3, 0): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (0, 3): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
}
_KICKS_I = {
    (0, 1): [(0, 0), (-2, 0), (1, 0), (-2, -1), (1, 2)],
    (1, 0): [(0, 0), (2, 0), (-1, 0), (2, 1), (-1, -2)],
    (1, 2): [(0, 0), (-1, 0), (2, 0), (-1, 2), (2, -1)],
    (2, 1): [(0, 0), (1, 0), (-2, 0), (1, -2), (-2, 1)],
    (2, 3): [(0, 0), (2, 0), (-1, 0), (2, 1), (-1, -2)],
    (3, 2): [(0, 0), (-2, 0), (1, 0), (-2, -1), (1, 2)],
    (3, 0): [(0, 0), (1, 0), (-2, 0), (1, -2), (-2, 1)],
    (0, 3): [(0, 0), (-1, 0), (2, 0), (-1, 2), (2, -1)],
}
KICKS_JLSTZ = {k: [(x, -y) for x, y in v] for k, v in _KICKS_JLSTZ.items()}
KICKS_I = {k: [(x, -y) for x, y in v] for k, v in _KICKS_I.items()}

LINE_SCORES = {0: 0, 1: 100, 2: 300, 3: 500, 4: 800}
TSPIN_SCORES = {0: 400, 1: 800, 2: 1200, 3: 1600}
TSPIN_MINI_SCORES = {0: 100, 1: 200, 2: 400}
PERFECT_CLEAR_SCORES = {1: 800, 2: 1200, 3: 1800, 4: 2000}


def gravity_interval(level):
    """Seconds per row, from the Tetris guideline formula."""
    level = max(1, min(level, 20))
    return (0.8 - (level - 1) * 0.007) ** (level - 1)


class Piece:
    __slots__ = ("kind", "rot", "x", "y")

    def __init__(self, kind, rot=0, x=0, y=0):
        self.kind, self.rot, self.x, self.y = kind, rot, x, y

    def cells(self, rot=None, x=None, y=None):
        rot = self.rot if rot is None else rot
        x = self.x if x is None else x
        y = self.y if y is None else y
        return [(x + dx, y + dy) for dx, dy in SHAPES[self.kind][rot]]


class Game:
    def __init__(self, start_level=1, seed=None):
        self.rng = random.Random(seed)
        self.board = [[None] * BOARD_W for _ in range(BOARD_H)]
        self.start_level = start_level
        self.level = start_level
        self.score = 0
        self.lines = 0
        self.combo = -1
        self.b2b = False
        self.hold_kind = None
        self.hold_used = False
        self.queue = deque()
        self.bag = []
        self.piece = None
        self.events = []
        self.gravity_timer = 0.0
        self.lock_timer = 0.0
        self.lock_resets = 0
        self.lowest_y = 0
        self.last_rotate = False
        self.last_kick = 0
        self.soft_drop = False
        self.clearing = []
        self.clear_timer = 0.0
        self.over = False
        self.started = False
        self.time = 0.0
        self.stats = {"pieces": 0, "tetris": 0, "tspin": 0, "max_combo": 0}
        self._refill()

    # ------------------------------------------------------------------ utils
    def emit(self, type_, **data):
        data["type"] = type_
        self.events.append(data)

    def pop_events(self):
        ev, self.events = self.events, []
        return ev

    def _refill(self):
        while len(self.queue) < 7:
            if not self.bag:
                self.bag = list(KINDS)
                self.rng.shuffle(self.bag)
            self.queue.append(self.bag.pop())

    def collides(self, kind, rot, x, y):
        for dx, dy in SHAPES[kind][rot]:
            cx, cy = x + dx, y + dy
            if cx < 0 or cx >= BOARD_W or cy >= BOARD_H:
                return True
            if cy >= 0 and self.board[cy][cx] is not None:
                return True
        return False

    def on_ground(self):
        p = self.piece
        return p is not None and self.collides(p.kind, p.rot, p.x, p.y + 1)

    def drop_distance(self):
        p = self.piece
        if p is None:
            return 0
        d = 0
        while not self.collides(p.kind, p.rot, p.x, p.y + d + 1):
            d += 1
        return d

    def ghost_y(self):
        return self.piece.y + self.drop_distance()

    def stack_height(self):
        for y in range(BOARD_H):
            if any(self.board[y]):
                return BOARD_H - y
        return 0

    # --------------------------------------------------------------- actions
    def start(self):
        if not self.started:
            self.started = True
            self.spawn()

    def spawn(self, kind=None):
        if kind is None:
            kind = self.queue.popleft()
            self._refill()
        x = 4 if kind == "O" else 3
        p = Piece(kind, 0, x, 0)
        self.gravity_timer = 0.0
        self.lock_timer = 0.0
        self.lock_resets = 0
        self.last_rotate = False
        if self.collides(kind, 0, x, 0):
            self.piece = None
            self._top_out()
            return
        if not self.collides(kind, 0, x, 1):
            p.y = 1
        self.piece = p
        self.lowest_y = p.y
        self.emit("spawn", kind=kind)

    def move(self, dx):
        p = self.piece
        if p is None or self.over or self.collides(p.kind, p.rot, p.x + dx, p.y):
            return False
        grounded = self.on_ground()
        p.x += dx
        self.last_rotate = False
        self._after_shift(grounded)
        self.emit("move", dx=dx)
        return True

    def rotate(self, direction):
        p = self.piece
        if p is None or self.over or p.kind == "O":
            return False
        new = (p.rot + direction) % 4
        table = KICKS_I if p.kind == "I" else KICKS_JLSTZ
        grounded = self.on_ground()
        for i, (kx, ky) in enumerate(table[(p.rot, new)]):
            if not self.collides(p.kind, new, p.x + kx, p.y + ky):
                p.rot, p.x, p.y = new, p.x + kx, p.y + ky
                self.last_rotate = True
                self.last_kick = i
                self._after_shift(grounded)
                self.emit("rotate", dir=direction, kick=(kx, ky))
                return True
        return False

    def _after_shift(self, grounded):
        p = self.piece
        if p.y > self.lowest_y:
            self.lowest_y = p.y
            self.lock_resets = 0
        if grounded or self.on_ground():
            if self.lock_resets < MAX_LOCK_RESETS:
                self.lock_timer = 0.0
                self.lock_resets += 1

    def hard_drop(self):
        p = self.piece
        if p is None or self.over:
            return
        start = p.cells()
        d = self.drop_distance()
        p.y += d
        self.score += 2 * d
        if d > 0:
            self.last_rotate = False
        self.emit("hard_drop", kind=p.kind, cells=p.cells(), start=start, dist=d)
        self._lock(hard=True)

    def hold(self):
        p = self.piece
        if p is None or self.over or self.hold_used:
            self.emit("hold_fail")
            return False
        kind = p.kind
        if self.hold_kind is None:
            self.hold_kind = kind
            self.spawn()
        else:
            swap, self.hold_kind = self.hold_kind, kind
            self.spawn(swap)
        self.hold_used = True
        self.emit("hold", kind=kind)
        return True

    # ---------------------------------------------------------------- update
    def update(self, dt):
        if self.over or not self.started:
            return
        self.time += dt
        if self.clearing:
            self.clear_timer -= dt
            if self.clear_timer <= 0:
                self._finish_clear()
                self.spawn()
            return
        p = self.piece
        if p is None:
            self.spawn()
            return

        interval = gravity_interval(self.level)
        if self.soft_drop:
            interval = min(interval / SOFT_DROP_FACTOR, 0.05)
        self.gravity_timer += dt
        while self.gravity_timer >= interval:
            self.gravity_timer -= interval
            if self.collides(p.kind, p.rot, p.x, p.y + 1):
                self.gravity_timer = 0.0
                break
            p.y += 1
            self.last_rotate = False
            if p.y > self.lowest_y:
                self.lowest_y = p.y
                self.lock_resets = 0
            if self.soft_drop:
                self.score += 1
                self.emit("soft_drop")

        if self.on_ground():
            self.lock_timer += dt
            if self.lock_timer >= LOCK_DELAY or self.lock_resets >= MAX_LOCK_RESETS:
                self._lock()
        else:
            self.lock_timer = 0.0

    # ------------------------------------------------------------ internals
    def _detect_tspin(self):
        p = self.piece
        if p.kind != "T" or not self.last_rotate:
            return None
        cx, cy = p.x + 1, p.y + 1
        corners = ((cx - 1, cy - 1), (cx + 1, cy - 1), (cx + 1, cy + 1), (cx - 1, cy + 1))

        def occupied(x, y):
            if x < 0 or x >= BOARD_W or y >= BOARD_H:
                return True
            return y >= 0 and self.board[y][x] is not None

        filled = [occupied(x, y) for x, y in corners]
        if sum(filled) < 3:
            return None
        front = ((0, 1), (1, 2), (2, 3), (3, 0))[p.rot]
        if (filled[front[0]] and filled[front[1]]) or self.last_kick == 4:
            return "full"
        return "mini"

    def _lock(self, hard=False):
        p = self.piece
        tspin = self._detect_tspin()
        cells = p.cells()
        for x, y in cells:
            if 0 <= y < BOARD_H:
                self.board[y][x] = p.kind
        self.stats["pieces"] += 1
        self.piece = None
        self.hold_used = False
        self.emit("lock", kind=p.kind, cells=cells, tspin=tspin, hard=hard)

        if all(y < HIDDEN for _, y in cells):     # locked completely out of view
            self._top_out()
            return

        full = [y for y in range(BOARD_H) if all(self.board[y])]
        self._score(len(full), tspin, full)
        if full:
            self.clearing = full
            self.clear_timer = CLEAR_DELAY
        else:
            self.spawn()

    def _score(self, n, tspin, rows):
        level = self.level
        if tspin == "full":
            base = TSPIN_SCORES[n]
        elif tspin == "mini":
            base = TSPIN_MINI_SCORES.get(n, 400)
        else:
            base = LINE_SCORES[n]

        difficult = n == 4 or (tspin is not None and n > 0)
        b2b_bonus = False
        if n:
            if difficult and self.b2b:
                base = base * 3 // 2
                b2b_bonus = True
            self.b2b = difficult
            self.combo += 1
        else:
            self.combo = -1

        points = base * level
        if n and self.combo > 0:
            points += 50 * self.combo * level

        perfect = False
        if n:
            row_set = set(rows)
            perfect = all(not any(self.board[y]) for y in range(BOARD_H) if y not in row_set)
            if perfect:
                points += PERFECT_CLEAR_SCORES[n] * level

        self.score += points
        if n == 4:
            self.stats["tetris"] += 1
        if tspin:
            self.stats["tspin"] += 1
        self.stats["max_combo"] = max(self.stats["max_combo"], self.combo)

        if n or tspin:
            self.emit("clear", lines=n, rows=list(rows), tspin=tspin, b2b=b2b_bonus,
                      combo=self.combo, perfect=perfect, points=points)
        if n:
            old = self.level
            self.lines += n
            self.level = self.start_level + self.lines // 10
            if self.level > old:
                self.emit("level_up", level=self.level)

    def _finish_clear(self):
        rows = sorted(self.clearing)
        for y in rows:
            del self.board[y]
            self.board.insert(0, [None] * BOARD_W)
        self.clearing = []
        self.emit("rows_removed", rows=rows)

    def _top_out(self):
        self.over = True
        self.emit("game_over")
