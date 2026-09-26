"""Draws the game into a transparent pygame surface (the "game layer").

The shader pipeline composites this layer over the animated background and
adds particles/post-processing on top. All the per-object animation lives
here: smooth piece motion, rotation tweens, line clear sweeps, row
collapse, pop-up text, panels, the title screen and the game-over sequence.
"""

import colorsys
import math
import random

import pygame

from .config import (SCREEN_W, SCREEN_H, BOARD_W, BOARD_H, HIDDEN, CELL, BOARD_X, BOARD_Y,
                     BOARD_PX_W, BOARD_PX_H, PANEL_W, LEFT_X, RIGHT_X, LOCK_DELAY,
                     CLEAR_DELAY, COUNTDOWN_STEP, PIECE_COLORS)
from .effects import (K_GLOW, K_SPARK, K_SHARD, K_BEAM, K_TRAIL, K_RING, TAU, Spring,
                      clamp01, lerp, smoothstep, ease_out_back, ease_out_cubic,
                      ease_out_elastic, ease_in_cubic)
from .game import SHAPES, BOX, KINDS

BOARD_CX = BOARD_X + BOARD_PX_W / 2


def fcol(rgb, mul=1.0):
    return (rgb[0] / 255 * mul, rgb[1] / 255 * mul, rgb[2] / 255 * mul)


def shade(c, amt):
    if amt >= 0:
        return tuple(int(v + (255 - v) * amt) for v in c)
    return tuple(max(0, int(v * (1 + amt))) for v in c)


def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


RAINBOW = [PIECE_COLORS[k] for k in "ZLOSIJT"]


# ------------------------------------------------------------------ sprites
def make_block(color, s):
    surf = pygame.Surface((s, s), pygame.SRCALPHA)
    bw = max(2, s // 7)
    surf.fill(shade(color, -0.55))
    pygame.draw.polygon(surf, shade(color, 0.55), [(0, 0), (s, 0), (s - bw, bw), (bw, bw)])
    pygame.draw.polygon(surf, shade(color, 0.25), [(0, 0), (bw, bw), (bw, s - bw), (0, s)])
    pygame.draw.polygon(surf, shade(color, -0.5), [(0, s), (bw, s - bw), (s - bw, s - bw), (s, s)])
    pygame.draw.polygon(surf, shade(color, -0.28), [(s, 0), (s, s), (s - bw, s - bw), (s - bw, bw)])
    inner = s - 2 * bw
    for y in range(inner):
        t = y / max(1, inner - 1)
        c = tuple(int(lerp(a, b, t)) for a, b in zip(shade(color, 0.15), shade(color, -0.15)))
        pygame.draw.line(surf, c, (bw, bw + y), (s - bw - 1, bw + y))
    gloss = pygame.Surface((inner, max(1, inner // 2)), pygame.SRCALPHA)
    gloss.fill((255, 255, 255, 40))
    surf.blit(gloss, (bw, bw))
    shine = max(2, s // 10)
    surf.fill((255, 255, 255), (bw + 1, bw + 1, shine, shine))
    pygame.draw.rect(surf, shade(color, -0.7), surf.get_rect(), 1)
    for cx, cy in ((0, 0), (s - 1, 0), (0, s - 1), (s - 1, s - 1)):
        surf.set_at((cx, cy), (0, 0, 0, 0))
    return surf


def make_ghost(color, s):
    surf = pygame.Surface((s, s), pygame.SRCALPHA)
    surf.fill((*color, 34))
    pygame.draw.rect(surf, (*shade(color, 0.3), 220), (1, 1, s - 2, s - 2), 2, border_radius=4)
    return surf


class Sprites:
    def __init__(self):
        self._cache = {}

    def block(self, kind, size=CELL):
        key = ("b", kind, size)
        s = self._cache.get(key)
        if s is None:
            s = self._cache[key] = make_block(PIECE_COLORS[kind], size)
        return s

    def ghost(self, kind):
        key = ("g", kind)
        s = self._cache.get(key)
        if s is None:
            s = self._cache[key] = make_ghost(PIECE_COLORS[kind], CELL)
        return s

    def flash(self, size=CELL):
        key = ("f", size)
        s = self._cache.get(key)
        if s is None:
            s = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.rect(s, (255, 255, 255, 255), s.get_rect(), border_radius=max(1, size // 8))
            self._cache[key] = s
        return s

    def rotated(self, kind, angle):
        q = int(round(angle / 5.0)) * 5
        key = ("r", kind, q)
        s = self._cache.get(key)
        if s is None:
            s = self._cache[key] = pygame.transform.rotate(self.block(kind), -q)
        return s

    def preview(self, kind, size, gray=False):
        key = ("p", kind, size, gray)
        s = self._cache.get(key)
        if s is None:
            cells = SHAPES[kind][0]
            xs = [x for x, _ in cells]
            ys = [y for _, y in cells]
            w = (max(xs) - min(xs) + 1) * size
            h = (max(ys) - min(ys) + 1) * size
            s = pygame.Surface((w, h), pygame.SRCALPHA)
            blk = self.block("gray" if gray else kind, size)
            for x, y in cells:
                s.blit(blk, ((x - min(xs)) * size, (y - min(ys)) * size))
            self._cache[key] = s
        return s


# --------------------------------------------------------------------- text
class Fonts:
    def __init__(self):
        self._fonts = {}
        self._cache = {}

    def get(self, size):
        f = self._fonts.get(size)
        if f is None:
            f = self._fonts[size] = pygame.font.Font(None, size)
        return f

    def render(self, text, size, color, spacing=0):
        key = (text, size, color, spacing)
        s = self._cache.get(key)
        if s is None:
            if len(self._cache) > 800:
                self._cache.clear()
            font = self.get(size)
            if not spacing:
                s = font.render(text, True, color)
            else:
                glyphs = [font.render(ch, True, color) for ch in text]
                w = sum(g.get_width() for g in glyphs) + spacing * (len(glyphs) - 1)
                s = pygame.Surface((max(1, w), font.get_height()), pygame.SRCALPHA)
                x = 0
                for g in glyphs:
                    s.blit(g, (x, 0))
                    x += g.get_width() + spacing
            self._cache[key] = s
        return s

    def fancy(self, text, size, colors, outline=3, spacing=2, outline_color=(5, 5, 15)):
        """Outlined text, optionally with per-letter colours. Not cached."""
        font = self.get(size)
        if isinstance(colors[0], int):
            colors = [colors]
        glyphs = [font.render(ch, True, colors[i % len(colors)]) for i, ch in enumerate(text)]
        shadows = [font.render(ch, True, outline_color) for ch in text]
        w = sum(g.get_width() for g in glyphs) + spacing * (len(text) - 1) + outline * 2 + 4
        h = font.get_height() + outline * 2 + 4
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        x = outline + 2
        offsets = [(dx, dy) for dx in (-outline, 0, outline) for dy in (-outline, 0, outline) if dx or dy]
        offsets += [(0, outline + 2), (outline, outline + 2)]
        for g, sh in zip(glyphs, shadows):
            for dx, dy in offsets:
                surf.blit(sh, (x + dx, outline + dy))
            surf.blit(g, (x, outline))
            x += g.get_width() + spacing
        return surf


class Popup:
    def __init__(self, surf, x, y, t0, dur=1.4, delay=0.0, rise=50, style="pop"):
        self.surf, self.x, self.y = surf, x, y
        self.t0, self.dur, self.delay, self.rise, self.style = t0 + delay, dur, delay, rise, style

    def done(self, t):
        return t - self.t0 > self.dur

    def draw(self, target, t):
        age = t - self.t0
        if age < 0 or age > self.dur:
            return
        p = age / self.dur
        if self.style == "pop":
            scale = lerp(0.3, 1.0, ease_out_elastic(age / 0.45))
        else:
            scale = lerp(0.6, 1.0, ease_out_cubic(age / 0.2))
        alpha = 1.0 - smoothstep(0.7, 1.0, p)
        yoff = -self.rise * ease_in_cubic(max(0.0, (p - 0.5) / 0.5))
        if self.style == "float":
            yoff = -self.rise * ease_out_cubic(p)
        s = self.surf
        if abs(scale - 1.0) > 0.01:
            w, h = s.get_size()
            s = pygame.transform.smoothscale(s, (max(1, int(w * scale)), max(1, int(h * scale))))
        s.set_alpha(int(255 * alpha))
        target.blit(s, (int(self.x - s.get_width() / 2), int(self.y + yoff - s.get_height() / 2)))


# Block letters for the title logo
LOGO = {
    "T": ["#####", "..#..", "..#..", "..#..", "..#.."],
    "E": ["####", "#...", "###.", "#...", "####"],
    "R": ["####", "#..#", "####", "#.#.", "#..#"],
    "I": ["###", ".#.", ".#.", ".#.", "###"],
    "S": [".####", "#....", ".###.", "....#", "####."],
}
LOGO_WORD = "TETRIS"
LOGO_KINDS = "ZLOSIT"
LOGO_CELL = 22


# ------------------------------------------------------------------ renderer
class Renderer:
    def __init__(self, fx):
        self.fx = fx
        self.surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        self.fonts = Fonts()
        self.sprites = Sprites()
        self.sounds = []            # sound requests for the app to play
        self._panels = {}
        self.kick_x = Spring(560, 22)
        self.kick_y = Spring(520, 19)
        self._build_static()
        self._init_title()
        self.reset()

    # ------------------------------------------------------------ set-up
    def _build_static(self):
        bg = pygame.Surface((BOARD_PX_W, BOARD_PX_H), pygame.SRCALPHA)
        bg.fill((6, 8, 20, 200))
        for x in range(1, BOARD_W):
            pygame.draw.line(bg, (28, 34, 62, 210), (x * CELL, 0), (x * CELL, BOARD_PX_H))
        for y in range(1, BOARD_H - HIDDEN):
            pygame.draw.line(bg, (28, 34, 62, 210), (0, y * CELL), (BOARD_PX_W, y * CELL))
        for x in range(0, BOARD_W + 1):
            for y in range(0, BOARD_H - HIDDEN + 1):
                bg.fill((70, 80, 130, 230), (x * CELL - 1, y * CELL - 1, 2, 2))
        self.board_bg = bg

        band = pygame.Surface((BOARD_PX_W, 140), pygame.SRCALPHA)
        for y in range(140):
            a = int(22 * math.sin(math.pi * y / 139) ** 2)
            band.fill((120, 160, 255, a), (0, y, BOARD_PX_W, 1))
        self.scan_band = band

        strip = pygame.Surface((CELL - 6, 256), pygame.SRCALPHA)
        for y in range(256):
            strip.fill((255, 255, 255, int(45 * (y / 255) ** 1.5)), (0, y, CELL - 6, 1))
        self.beam_strip = strip

        row = pygame.Surface((BOARD_PX_W, CELL), pygame.SRCALPHA)
        row.fill((255, 255, 255, 255))
        self.row_flash = row

        self.dim = pygame.Surface((BOARD_PX_W, BOARD_PX_H), pygame.SRCALPHA)
        self.dim.fill((3, 4, 12, 215))

    def _init_title(self):
        self.title_t0 = 0.0
        self.rain = [self._new_rain(random.uniform(-100, SCREEN_H)) for _ in range(18)]
        blocks = []
        widths = [len(LOGO[ch][0]) for ch in LOGO_WORD]
        total = sum(widths) + len(widths) - 1
        x0 = (SCREEN_W - total * LOGO_CELL) // 2
        col = 0
        for li, ch in enumerate(LOGO_WORD):
            for ry, line in enumerate(LOGO[ch]):
                for rx, c in enumerate(line):
                    if c == "#":
                        blocks.append({"tx": x0 + (col + rx) * LOGO_CELL, "ty": 130 + ry * LOGO_CELL,
                                       "col": col + rx, "kind": LOGO_KINDS[li]})
            col += widths[li] + 1
        self.logo = blocks
        self._scatter_logo()

    def _scatter_logo(self):
        for b in self.logo:
            a = random.uniform(0, TAU)
            r = random.uniform(500, 900)
            b["sx"] = SCREEN_W / 2 + math.cos(a) * r
            b["sy"] = 200 + math.sin(a) * r
            b["delay"] = b["col"] * 0.035 + random.uniform(0, 0.25)
            b["spin"] = random.uniform(-720, 720)

    def _new_rain(self, y=None):
        depth = random.uniform(0.35, 1.0)
        return {"kind": random.choice(KINDS), "x": random.uniform(0, SCREEN_W),
                "y": -120 if y is None else y, "depth": depth,
                "speed": 30 + 90 * depth, "angle": random.uniform(0, 360),
                "spin": random.uniform(-40, 40)}

    def enter_title(self):
        self.title_t0 = self.fx.time
        self._scatter_logo()

    def reset(self):
        self.vis_x, self.vis_y, self.vis_angle = 3.0, 0.0, 0.0
        self.collapse = None
        self.lock_flashes = []
        self.popups = []
        self.score_disp = 0.0
        self.lines_bar = 0.0
        self.queue_anim = 0.0
        self.hold_anim = 0.0
        self.exploded = set()
        self.go_t0 = None
        self.go_exploded = False
        self.banner = None
        self.countdown = None
        self._soft_t = 0.0
        self._ambient_t = 0.0
        self.kick_x = Spring(560, 22)
        self.kick_y = Spring(520, 19)

    # ---------------------------------------------------------- helpers
    def cell_center(self, x, y):
        return BOARD_X + (x + 0.5) * CELL, BOARD_Y + (y - HIDDEN + 0.5) * CELL

    def text(self, s, size, color, pos, anchor="topleft", alpha=255, spacing=0):
        surf = self.fonts.render(s, size, color, spacing)
        surf.set_alpha(alpha)
        r = surf.get_rect(**{anchor: (int(pos[0]), int(pos[1]))})
        self.surf.blit(surf, r)
        return r

    def panel(self, rect, accent):
        key = (rect.w, rect.h)
        bg = self._panels.get(key)
        if bg is None:
            bg = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(bg, (7, 9, 24, 190), bg.get_rect(), border_radius=12)
            pygame.draw.line(bg, (60, 70, 120, 200), (14, 2), (rect.w - 14, 2))
            self._panels[key] = bg
        self.surf.blit(bg, rect)
        pygame.draw.rect(self.surf, accent, rect, 2, border_radius=12)

    def _vanish_at(self, x):
        return 0.12 + abs(x - 4.5) / 4.5 * 0.5

    # ------------------------------------------------------------ events
    def on_event(self, ev, game):
        fx = self.fx
        P = fx.particles
        t = fx.time
        kind = ev["type"]

        if kind == "spawn":
            p = game.piece
            self.vis_x, self.vis_y, self.vis_angle = float(p.x), p.y - 1.4, 0.0
            self.queue_anim = 1.0

        elif kind == "rotate":
            p = game.piece
            self.vis_angle = -90.0 * ev["dir"]
            n = BOX[p.kind]
            cx = BOARD_X + (p.x + n / 2) * CELL
            cy = BOARD_Y + (p.y + n / 2 - HIDDEN) * CELL
            c = fcol(PIECE_COLORS[p.kind], 1.4)
            P.emit(1, cx, cy, c, K_RING, speed=(0, 0), life=(0.28, 0.28), size=(n * CELL * 1.6,) * 2)
            P.emit(6, cx, cy, fcol(PIECE_COLORS[p.kind], 2.0), K_SPARK, speed=(120, 260),
                   life=(0.15, 0.3), size=(1.2, 2.0), drag=4)

        elif kind == "hard_drop":
            color = PIECE_COLORS[ev["kind"]]
            cells = ev["cells"]
            cols = {}
            for x, y in ev["start"]:
                cols.setdefault(x, [y, y])
                cols[x][0] = min(cols[x][0], y)
            for x, y in cells:
                cols[x][1] = max(cols[x][1], y)
            for x, (y0, y1) in cols.items():
                top = BOARD_Y + (y0 - HIDDEN) * CELL
                bot = BOARD_Y + (y1 - HIDDEN + 1) * CELL
                h = bot - top
                if h > CELL * 1.5:
                    P.emit(1, BOARD_X + (x + 0.5) * CELL, top + h / 2, fcol(color, 1.6), K_TRAIL,
                           speed=(0, 0), life=(0.38, 0.38), size=(CELL * 0.95, CELL * 0.95), size_y=(h, h), drag=0)
            cell_set = set(cells)
            for x, y in cells:
                if (x, y + 1) in cell_set:
                    continue
                px, py = BOARD_X + (x + 0.5) * CELL, BOARD_Y + (y - HIDDEN + 1) * CELL
                P.emit(7, px, py, fcol(color, 2.4), K_SPARK, speed=(180, 520), angle=(-math.pi, 0),
                       life=(0.2, 0.45), size=(1.2, 2.2), grav=1100, drag=2.5, spread=(CELL / 2, 0))
                P.emit(2, px, py, (0.45, 0.5, 0.65), K_GLOW, speed=(20, 70), angle=(-math.pi, 0),
                       life=(0.5, 0.9), size=(16, 28), drag=2, spread=(CELL / 2, 0))
            dist = ev["dist"]
            self.kick_y.kick(140 + dist * 14)
            fx.shake.add(0.1 + dist * 0.012)
            fx.chroma.hit(0.2 + dist * 0.02)
            if dist >= 8:
                xs = [x for x, _ in cells]
                ys = [y for _, y in cells]
                cx, cy = self.cell_center(sum(xs) / 4, max(ys))
                fx.wave(cx, cy + CELL / 2, strength=0.35, duration=0.5, radius=0.25)

        elif kind == "lock":
            self.lock_flashes.append((ev["cells"], t))
            if not ev.get("hard"):
                self.kick_y.kick(45)
                for x, y in ev["cells"]:
                    px, py = self.cell_center(x, y)
                    P.emit(1, px, py + CELL / 2, (0.4, 0.45, 0.6), K_GLOW, speed=(10, 40),
                           angle=(-math.pi, 0), life=(0.3, 0.5), size=(14, 20), drag=2)

        elif kind == "clear":
            self._on_clear(ev, game)

        elif kind == "rows_removed":
            rows = ev["rows"]
            shifts = {}
            for old_y in range(BOARD_H):
                if old_y in rows:
                    continue
                below = sum(1 for r in rows if r > old_y)
                if below:
                    shifts[old_y + below] = below
            self.collapse = {"t0": t, "shifts": shifts, "landed": False}

        elif kind == "level_up":
            lvl = ev["level"]
            self.banner = (lvl, t)
            fx.set_palette(lvl - 1)
            fx.wave(BOARD_CX, SCREEN_H / 2, strength=1.0, duration=1.2, radius=1.1)
            fx.flash.hit(0.35)
            fx.flash_color = fcol(fx.accent(), 0.8)
            fx.glitch.hit(0.35)
            cols = [fcol(RAINBOW[i % 7], 1.3) for i in range(7)]
            for sx, a0 in ((BOARD_X - 10, -math.pi / 2 - 0.45), (BOARD_X + BOARD_PX_W + 10, -math.pi / 2 + 0.45)):
                P.emit(45, sx, BOARD_Y + BOARD_PX_H, cols, K_SPARK, speed=(450, 1000),
                       angle=(a0 - 0.3, a0 + 0.3), life=(0.8, 1.5), size=(1.0, 1.8), grav=800,
                       drag=1.2, spread=(8, 30))

        elif kind == "hold":
            self.hold_anim = 1.0
            hx, hy = LEFT_X + PANEL_W / 2, BOARD_Y + 75
            c = PIECE_COLORS[game.hold_kind]
            P.emit(1, hx, hy, fcol(c, 1.5), K_RING, speed=(0, 0), life=(0.35, 0.35), size=(150, 150))
            P.emit(18, hx, hy, fcol(c, 2.0), K_SPARK, speed=(120, 320), life=(0.2, 0.45),
                   size=(1.2, 2.0), drag=3)

        elif kind == "game_over":
            self.go_t0 = t
            self.go_exploded = False
            fx.glitch.hit(1.0)
            fx.shake.add(0.5)
            fx.chroma.hit(1.2)
            fx.flash.hit(0.4)
            fx.flash_color = (1.0, 0.1, 0.15)

    def _on_clear(self, ev, game):
        fx, P, t = self.fx, self.fx.particles, self.fx.time
        n, rows, tspin = ev["lines"], ev["rows"], ev["tspin"]
        self.exploded = set()
        if n:
            cy = BOARD_Y + (sum(rows) / n - HIDDEN + 0.5) * CELL
            bright = 1.0 / math.sqrt(n)
            for y in rows:
                _, ry = self.cell_center(0, y)
                P.emit(1, BOARD_CX, ry, (0.9 * bright, 1.0 * bright, 1.25 * bright), K_BEAM, speed=(0, 0), life=(0.5, 0.5),
                       size=(BOARD_PX_W * 1.35, BOARD_PX_W * 1.35), size_y=(CELL * 1.8, CELL * 1.8))
            k = [0, 0.35, 0.5, 0.7, 1.0][n]
            fx.wave(BOARD_CX, cy, strength=0.45 + 0.7 * k, duration=0.8 + 0.3 * k, radius=0.45 + 0.4 * k)
            fx.shake.add(0.12 + 0.18 * n)
            fx.chroma.hit(0.35 + 0.3 * n)
            fx.pulse.hit(0.35 + 0.16 * n)
            self.kick_y.kick(50 * n)
        else:
            cy = BOARD_Y + BOARD_PX_H * 0.45

        if n == 4:
            fx.flash.hit(0.35)
            fx.flash_color = (0.55, 0.85, 1.0)
            fx.glitch.hit(0.55)
        if tspin:
            fx.flash.hit(0.2)
            fx.flash_color = (0.75, 0.35, 1.0)
            fx.glitch.hit(0.3)

        y = max(170, min(420, cy - 90))
        if tspin:
            name = "T-SPIN" + (" MINI" if tspin == "mini" else "")
            if n:
                name += " " + ["", "SINGLE", "DOUBLE", "TRIPLE"][n]
            surf = self.fonts.fancy(name, 52, [(215, 120, 255), (255, 170, 255)])
        elif n == 4:
            surf = self.fonts.fancy("TETRIS!", 86, RAINBOW, outline=4, spacing=3)
        else:
            name = ["", "SINGLE", "DOUBLE", "TRIPLE"][n]
            color = [(0, 0, 0), (235, 240, 255), (120, 230, 255), (255, 225, 80)][n]
            surf = self.fonts.fancy(name, [0, 44, 52, 60][n], color)
        self.popups.append(Popup(surf, BOARD_CX, y, t))
        extra = 0
        ny = y + surf.get_height() / 2 + 6
        if ev["b2b"]:
            extra += 1
            s2 = self.fonts.fancy("BACK-TO-BACK", 34, (255, 95, 160))
            self.popups.append(Popup(s2, BOARD_CX, ny + s2.get_height() / 2, t, delay=0.08 * extra))
            ny += s2.get_height()
        if ev["combo"] > 0:
            extra += 1
            s2 = self.fonts.fancy("%d COMBO" % ev["combo"], 38, (255, 170, 40))
            self.popups.append(Popup(s2, BOARD_CX, ny + s2.get_height() / 2, t, delay=0.08 * extra))
            ny += s2.get_height()
        if ev["points"]:
            extra += 1
            s2 = self.fonts.fancy("+{:,}".format(ev["points"]), 32, (255, 255, 255), outline=2)
            self.popups.append(Popup(s2, BOARD_CX, ny + s2.get_height() / 2 + 4, t, dur=1.3,
                                     delay=0.1 * extra, rise=50))
        if ev["perfect"]:
            self.popups.append(Popup(self.fonts.fancy("ALL CLEAR", 92, RAINBOW, outline=4, spacing=3),
                                     BOARD_CX, SCREEN_H / 2 - 120, t, dur=2.2, delay=0.2))
            fx.flash.hit(0.6)
            fx.flash_color = (1.0, 0.95, 0.7)
            fx.wave(BOARD_CX, SCREEN_H / 2, strength=1.2, duration=1.4, radius=1.2)
            cols = [fcol(c, 2.2) for c in RAINBOW]
            P.emit(260, BOARD_CX, SCREEN_H / 2, cols, K_SPARK, speed=(300, 1100), life=(0.8, 1.8),
                   size=(1.5, 3.0), grav=500, drag=1.2)

    def _explode_cell(self, x, y, kind):
        P = self.fx.particles
        cx, cy = self.cell_center(x, y)
        c = PIECE_COLORS[kind or "gray"]
        P.emit(3, cx, cy, fcol(c), K_SHARD, speed=(120, 440), angle=(-math.pi + 0.25, -0.25),
               life=(0.7, 1.3), size=(7, 13), grav=1500, drag=0.4, spin=(-14, 14), spread=(CELL * 0.3, CELL * 0.3))
        P.emit(4, cx, cy, fcol(c, 2.8), K_SPARK, speed=(200, 720), life=(0.25, 0.55),
               size=(1.2, 2.2), grav=500, drag=2.5)
        P.emit(1, cx, cy, fcol(c, 1.2), K_GLOW, speed=(0, 20), life=(0.35, 0.5), size=(50, 64), drag=1)

    # ------------------------------------------------------------ update
    def update(self, dt, state, game):
        fx = self.fx
        t = fx.time
        self.kick_x.update(dt)
        self.kick_y.update(dt)
        self.queue_anim = max(0.0, self.queue_anim - dt * 5.5)
        self.hold_anim = max(0.0, self.hold_anim - dt * 3.5)
        self.lock_flashes = [f for f in self.lock_flashes if t - f[1] < 0.3]
        self.popups = [p for p in self.popups if not p.done(t)]

        # ambient motes drifting up the screen
        self._ambient_t += dt
        while self._ambient_t > 0.09:
            self._ambient_t -= 0.09
            fx.particles.emit(1, random.uniform(0, SCREEN_W), SCREEN_H + 10, fcol(fx.accent(), 0.7),
                              K_GLOW, speed=(25, 70), angle=(-math.pi / 2 - 0.3, -math.pi / 2 + 0.3),
                              life=(6, 11), size=(5, 11), drag=0.05)

        if state == "title":
            for r in self.rain:
                r["y"] += r["speed"] * dt
                r["angle"] += r["spin"] * dt
            self.rain = [r if r["y"] < SCREEN_H + 120 else self._new_rain() for r in self.rain]
            return

        if game is None:
            return
        p = game.piece
        if p is not None:
            kx = 1 - math.exp(-dt * 30)
            ky = 1 - math.exp(-dt * 34)
            self.vis_x += (p.x - self.vis_x) * kx
            self.vis_y += (p.y - self.vis_y) * ky
            self.vis_angle *= math.exp(-dt * 20)
            if abs(self.vis_angle) < 0.3:
                self.vis_angle = 0.0

        diff = game.score - self.score_disp
        self.score_disp += diff * (1 - math.exp(-dt * 7))
        if abs(diff) < 1:
            self.score_disp = game.score
        target = (game.lines % 10) / 10.0
        if target < self.lines_bar - 0.05:
            self.lines_bar = 0.0
        self.lines_bar += (target - self.lines_bar) * (1 - math.exp(-dt * 8))

        if state == "play" and p is not None and game.soft_drop and t - self._soft_t > 0.05:
            self._soft_t = t
            cells = p.cells()
            bottom = max(y for _, y in cells)
            for x, y in cells:
                if y == bottom:
                    px, py = self.cell_center(x, y)
                    fx.particles.emit(1, px, py + CELL / 2, fcol(PIECE_COLORS[p.kind], 2.0), K_SPARK,
                                      speed=(60, 160), angle=(-math.pi / 2 - 0.5, -math.pi / 2 + 0.5),
                                      life=(0.15, 0.3), size=(1, 1.8), drag=3)

        # progressive line clear explosion (sweeps out from the centre)
        if game.clearing:
            prog = 1 - game.clear_timer / CLEAR_DELAY
            for y in game.clearing:
                for x in range(BOARD_W):
                    if (x, y) not in self.exploded and prog >= self._vanish_at(x):
                        self.exploded.add((x, y))
                        self._explode_cell(x, y, game.board[y][x])

        c = self.collapse
        if c:
            prog = (t - c["t0"]) / 0.26
            if not c["landed"] and prog >= 0.6:
                c["landed"] = True
                if c["shifts"]:
                    self.kick_y.kick(70 + 25 * max(c["shifts"].values()))
                    self.sounds.append("thud")
            if prog >= 1:
                self.collapse = None

        if self.go_t0 is not None and not self.go_exploded and t - self.go_t0 > 1.05:
            self.go_exploded = True
            P = fx.particles
            for y in range(BOARD_H):
                for x in range(BOARD_W):
                    if game.board[y][x]:
                        cx, cy = self.cell_center(x, y)
                        P.emit(1, cx, cy, fcol(PIECE_COLORS["gray"], 1.1), K_SHARD,
                               speed=(80, 380), angle=(-math.pi + 0.2, -0.2), life=(1.8, 2.6),
                               size=(CELL - 3, CELL - 3), grav=1400, drag=0.2, spin=(-7, 7),
                               vel=((cx - BOARD_CX) * 1.8, -250), rot=0.0)
            fx.shake.add(0.45)
            fx.wave(BOARD_CX, SCREEN_H / 2, strength=0.9, duration=1.0, radius=0.9)
            self.sounds.append("crumble")

    # -------------------------------------------------------------- draw
    def draw(self, state, game, info):
        self.surf.fill((0, 0, 0, 0))
        if state == "title":
            self._draw_title(info)
        else:
            self._draw_play(state, game, info)
        if info.get("fps") is not None:
            self.text("%d FPS" % info["fps"], 22, (140, 150, 190), (10, 8))
        return self.surf

    def _row_offset(self, y, t):
        c = self.collapse
        if not c:
            return 0.0
        sh = c["shifts"].get(y)
        if not sh:
            return 0.0
        p = (t - c["t0"]) / 0.26
        if p >= 1:
            return 0.0
        if p < 0.6:
            return -sh * (1 - (p / 0.6) ** 2)
        q = (p - 0.6) / 0.4
        return -0.16 * min(sh, 2) * math.sin(q * math.pi) * (1 - q)

    def _draw_play(self, state, game, info):
        fx = self.fx
        t = fx.time
        s = self.surf
        accent = fx.accent()
        kx, ky = self.kick_x.x, self.kick_y.x
        bx, by = int(BOARD_X + kx), int(BOARD_Y + ky)

        self._draw_hold(game, t, accent)
        self._draw_stats(game, t, accent, info)
        self._draw_next(game, t, accent)
        self._draw_help(accent)

        s.blit(self.board_bg, (bx, by))
        s.set_clip(pygame.Rect(bx, by, BOARD_PX_W, BOARD_PX_H))
        s.blit(self.scan_band, (bx, by + int((t * 110) % (BOARD_PX_H + 280)) - 140))
        s.set_clip(None)
        self._draw_frame(game, bx, by, t, accent)

        paused = state == "pause"
        if not paused:
            self._draw_stack(game, bx, by, t)
            self._draw_clearing(game, bx, by, t)
            if game.piece is not None and state == "play":
                self._draw_piece(game, bx, by, t)
            for cells, t0 in self.lock_flashes:
                a = 1 - (t - t0) / 0.25
                if a <= 0:
                    continue
                fl = self.sprites.flash()
                fl.set_alpha(int(210 * a * a))
                for x, y in cells:
                    s.blit(fl, (bx + x * CELL, by + (y - HIDDEN) * CELL))

        for p in self.popups:
            p.draw(s, t)
        self._draw_banner(t)
        self._draw_countdown(t)
        if paused:
            self._draw_pause(bx, by, t)
        if state == "over":
            self._draw_game_over(game, t, info)

    def _draw_frame(self, game, bx, by, t, accent):
        s = self.surf
        danger = self.fx.danger
        pulse = 0.75 + 0.25 * math.sin(t * 2.2)
        col = tuple(int(c * pulse) for c in accent)
        if danger > 0.02:
            d = danger * (0.6 + 0.4 * math.sin(t * 9))
            col = tuple(int(lerp(c, r, d)) for c, r in zip(col, (255, 40, 60)))
        rect = pygame.Rect(bx - 5, by - 5, BOARD_PX_W + 10, BOARD_PX_H + 10)
        pygame.draw.rect(s, (4, 5, 12), rect.inflate(6, 6), 3, border_radius=8)
        pygame.draw.rect(s, col, rect, 3, border_radius=6)
        hi = shade(col, 0.6)
        L = 22
        for (cx, cy, dx, dy) in ((rect.left, rect.top, 1, 1), (rect.right - 1, rect.top, -1, 1),
                                 (rect.left, rect.bottom - 1, 1, -1), (rect.right - 1, rect.bottom - 1, -1, -1)):
            pygame.draw.line(s, hi, (cx, cy), (cx + dx * L, cy), 3)
            pygame.draw.line(s, hi, (cx, cy), (cx, cy + dy * L), 3)
        # travelling highlight along the bottom edge
        hx = rect.left + (t * 180) % rect.w
        pygame.draw.line(s, (255, 255, 255), (max(rect.left, hx - 30), rect.bottom - 2),
                         (min(rect.right, hx + 30), rect.bottom - 2), 2)

    def _draw_stack(self, game, bx, by, t):
        if self.go_exploded:
            return
        s = self.surf
        clearing = set(game.clearing)
        go_age = t - self.go_t0 if self.go_t0 is not None else -1
        for y in range(BOARD_H):
            if y in clearing:
                continue
            row = game.board[y]
            if not any(row):
                continue
            py = int(by + (y - HIDDEN + self._row_offset(y, t)) * CELL)
            gray = go_age >= 0 and go_age > (BOARD_H - 1 - y) * 0.035
            for x, k in enumerate(row):
                if k:
                    s.blit(self.sprites.block("gray" if gray else k), (bx + x * CELL, py))
            if go_age >= 0 and gray and go_age - (BOARD_H - 1 - y) * 0.035 < 0.12:
                self.row_flash.set_alpha(140)
                s.blit(self.row_flash, (bx, py))

    def _draw_clearing(self, game, bx, by, t):
        if not game.clearing:
            return
        s = self.surf
        prog = 1 - game.clear_timer / CLEAR_DELAY
        for y in game.clearing:
            py = by + (y - HIDDEN) * CELL
            for x in range(BOARD_W):
                va = self._vanish_at(x)
                if prog >= va:
                    continue
                k = game.board[y][x]
                if k:
                    s.blit(self.sprites.block(k), (bx + x * CELL, py))
                shrink = smoothstep(va - 0.14, va, prog)
                size = max(2, int(CELL * (1 - 0.6 * shrink)))
                fl = self.sprites.flash(size)
                fl.set_alpha(int(210 * min(1.0, 0.3 + prog * 2.5)))
                off = (CELL - size) // 2
                s.blit(fl, (bx + x * CELL + off, py + off))
            self.row_flash.set_alpha(int(110 * (1 - prog) ** 2))
            s.blit(self.row_flash, (bx, py))

    def _draw_piece(self, game, bx, by, t):
        s = self.surf
        p = game.piece
        kind = p.kind
        shape = SHAPES[kind][p.rot]
        n = BOX[kind]
        c = (n - 1) / 2

        gy = game.ghost_y()
        if gy > p.y:
            gh = self.sprites.ghost(kind)
            gh.set_alpha(int(170 + 70 * math.sin(t * 5)))
            for dx, dy in shape:
                s.blit(gh, (int(bx + (self.vis_x + dx) * CELL), by + (gy + dy - HIDDEN) * CELL))
            # light beam from the piece down to its ghost
            bottoms, tops = {}, {}
            for dx, dy in shape:
                bottoms[dx] = max(bottoms.get(dx, -9), dy)
                tops[dx] = min(tops.get(dx, 9), dy)
            color = PIECE_COLORS[kind]
            for dx in bottoms:
                y0 = (self.vis_y + bottoms[dx] + 1 - HIDDEN) * CELL
                y1 = (gy + tops[dx] - HIDDEN) * CELL
                h = int(y1 - y0)
                if h > 4:
                    strip = pygame.transform.scale(self.beam_strip, (CELL - 6, h))
                    strip.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
                    s.blit(strip, (int(bx + (self.vis_x + dx) * CELL + 3), int(by + y0)))

        flash_a = 0
        if game.lock_timer > 0 and game.on_ground():
            frac = game.lock_timer / LOCK_DELAY
            flash_a = int(120 * frac * (0.5 + 0.5 * math.sin(t * 30)))
        ang = self.vis_angle
        rot = ang != 0.0
        ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        fl = self.sprites.flash()
        for dx, dy in shape:
            rx, ry = dx - c, dy - c
            if rot:
                rx, ry = rx * ca - ry * sa, rx * sa + ry * ca
            cx = bx + (self.vis_x + c + rx + 0.5) * CELL
            cy = by + (self.vis_y + c + ry + 0.5 - HIDDEN) * CELL
            spr = self.sprites.rotated(kind, ang) if rot else self.sprites.block(kind)
            pos = (int(cx - spr.get_width() / 2), int(cy - spr.get_height() / 2))
            s.blit(spr, pos)
            if flash_a and not rot:
                fl.set_alpha(flash_a)
                s.blit(fl, pos)

    # --------------------------------------------------------- side panels
    def _draw_hold(self, game, t, accent):
        rect = pygame.Rect(LEFT_X, BOARD_Y, PANEL_W, 130)
        self.panel(rect, accent)
        self.text("HOLD", 24, shade(accent, 0.3), (rect.x + 16, rect.y + 12), spacing=4)
        if game.hold_kind:
            prev = self.sprites.preview(game.hold_kind, 26, gray=game.hold_used)
            scale = ease_out_back(1 - self.hold_anim) if self.hold_anim > 0 else 1.0
            if abs(scale - 1) > 0.01:
                w, h = prev.get_size()
                prev = pygame.transform.smoothscale(prev, (max(1, int(w * scale)), max(1, int(h * scale))))
            self.surf.blit(prev, prev.get_rect(center=(rect.centerx, rect.y + 78)))

    def _draw_stats(self, game, t, accent, info):
        s = self.surf
        rect = pygame.Rect(LEFT_X, BOARD_Y + 150, PANEL_W, 318)
        self.panel(rect, accent)
        lab = shade(accent, 0.3)
        x = rect.x + 16
        y = rect.y + 14
        self.text("SCORE", 22, lab, (x, y), spacing=3)
        self.text("{:,}".format(int(self.score_disp)), 42, (255, 255, 255), (x, y + 22))
        self.text("BEST  {:,}".format(max(info.get("high", 0), game.score)), 20, (150, 160, 200), (x, y + 60))
        y += 96
        self.text("LEVEL", 22, lab, (x, y), spacing=3)
        self.text("LINES", 22, lab, (x + 80, y), spacing=3)
        self.text(str(game.level), 44, (255, 255, 255), (x, y + 20))
        self.text(str(game.lines), 44, (255, 255, 255), (x + 80, y + 20))
        y += 66
        bar = pygame.Rect(x, y, PANEL_W - 32, 10)
        pygame.draw.rect(s, (25, 30, 55), bar, border_radius=5)
        fill = bar.copy()
        fill.w = max(0, int(bar.w * self.lines_bar))
        if fill.w > 2:
            pygame.draw.rect(s, accent, fill, border_radius=5)
            sx = fill.x + int((t * 90) % max(1, fill.w))
            pygame.draw.line(s, (255, 255, 255), (sx, fill.y + 2), (min(fill.right - 2, sx + 8), fill.y + 2), 2)
        y += 26
        self.text("TIME", 22, lab, (x, y), spacing=3)
        secs = int(game.time)
        self.text("%02d:%02d" % (secs // 60, secs % 60), 34, (255, 255, 255), (x, y + 20))
        y += 58
        badges = []
        if game.combo > 0:
            badges.append(("COMBO x%d" % game.combo, (255, 170, 40)))
        if game.b2b:
            badges.append(("B2B", (255, 95, 160)))
        bxp = x
        for label, col in badges:
            a = int(200 + 55 * math.sin(t * 6))
            r = self.text(label, 22, col, (bxp + 6, y + 4), alpha=a)
            pygame.draw.rect(s, col, r.inflate(12, 8), 2, border_radius=6)
            bxp = r.right + 16

    def _draw_next(self, game, t, accent):
        s = self.surf
        rect = pygame.Rect(RIGHT_X, BOARD_Y, PANEL_W, 420)
        self.panel(rect, accent)
        self.text("NEXT", 24, shade(accent, 0.3), (rect.x + 16, rect.y + 12), spacing=4)
        slots = [rect.y + 82, rect.y + 170, rect.y + 240, rect.y + 310, rect.y + 380]
        sizes = [26, 18, 18, 18, 18]
        a = ease_out_cubic(self.queue_anim)
        queue = list(game.queue)[:5]
        for i, kind in enumerate(queue):
            ny = slots[i + 1] if i + 1 < len(slots) else slots[i] + 70
            nsz = sizes[i + 1] if i + 1 < len(sizes) else sizes[i]
            y = lerp(slots[i], ny, a)
            size = int(round(lerp(sizes[i], nsz, a)))
            prev = self.sprites.preview(kind, size)
            alpha = 255
            if i == len(queue) - 1:
                alpha = int(255 * (1 - a))
            if i > 0:
                alpha = min(alpha, 200)
            prev.set_alpha(alpha)
            s.blit(prev, prev.get_rect(center=(rect.centerx, int(y))))
            prev.set_alpha(255)
        pygame.draw.line(s, (50, 60, 100), (rect.x + 20, rect.y + 128), (rect.right - 20, rect.y + 128))

    def _draw_help(self, accent):
        x, y = RIGHT_X + 4, BOARD_Y + 440
        lines = [("LEFT / RIGHT", "move"), ("UP / X", "rotate"), ("Z / CTRL", "rotate ccw"),
                 ("DOWN", "soft drop"), ("SPACE", "hard drop"), ("C / SHIFT", "hold"),
                 ("P / ESC", "pause"), ("M", "music"), ("F1 / F2", "crt / bloom")]
        for i, (k, v) in enumerate(lines):
            self.text(k, 18, (170, 180, 220), (x, y + i * 21))
            self.text(v, 18, (125, 135, 180), (x + 100, y + i * 21))

    # ------------------------------------------------------------ overlays
    def _draw_banner(self, t):
        if not self.banner:
            return
        lvl, t0 = self.banner
        age = t - t0
        if age > 2.0:
            self.banner = None
            return
        if age < 0.35:
            xoff = -SCREEN_W * (1 - ease_out_cubic(age / 0.35))
        elif age > 1.65:
            xoff = SCREEN_W * ease_in_cubic((age - 1.65) / 0.35)
        else:
            xoff = 0
        accent = self.fx.accent()
        band = pygame.Surface((SCREEN_W, 120), pygame.SRCALPHA)
        band.fill((5, 6, 18, 190))
        pygame.draw.line(band, accent, (0, 1), (SCREEN_W, 1), 3)
        pygame.draw.line(band, accent, (0, 118), (SCREEN_W, 118), 3)
        stripe_x = int((age * 900) % (SCREEN_W + 200)) - 100
        pygame.draw.polygon(band, (255, 255, 255, 40), [(stripe_x, 0), (stripe_x + 60, 0),
                                                        (stripe_x + 20, 120), (stripe_x - 40, 120)])
        title = self.fonts.render("LEVEL %d" % lvl, 96, shade(accent, 0.5), 6)
        title.set_alpha(255)
        band.blit(title, title.get_rect(center=(SCREEN_W / 2, 52)))
        sub = self.fonts.render("SPEED UP", 24, (220, 225, 255), 8)
        sub.set_alpha(255)
        band.blit(sub, sub.get_rect(center=(SCREEN_W / 2, 100)))
        self.surf.blit(band, (int(xoff), 250))

    def _draw_countdown(self, t):
        if not self.countdown:
            return
        label, t0 = self.countdown
        age = t - t0
        if age > COUNTDOWN_STEP:
            return
        p = age / COUNTDOWN_STEP
        scale = lerp(2.2, 1.0, ease_out_back(min(1.0, age / 0.25)))
        alpha = 1 - smoothstep(0.6, 1.0, p)
        color = (120, 255, 160) if label == "GO!" else shade(self.fx.accent(), 0.4)
        surf = self.fonts.fancy(label, 150, color, outline=5)
        w, h = surf.get_size()
        surf = pygame.transform.smoothscale(surf, (int(w * scale), int(h * scale)))
        surf.set_alpha(int(255 * alpha))
        self.surf.blit(surf, surf.get_rect(center=(BOARD_CX, SCREEN_H / 2 - 20)))

    def _draw_pause(self, bx, by, t):
        self.surf.blit(self.dim, (bx, by))
        a = int(200 + 55 * math.sin(t * 3))
        self.text("PAUSED", 84, (255, 255, 255), (BOARD_CX, SCREEN_H / 2 - 40), "center", a, spacing=4)
        self.text("P  resume", 26, (180, 190, 230), (BOARD_CX, SCREEN_H / 2 + 20), "center")
        self.text("Q  quit to title", 26, (180, 190, 230), (BOARD_CX, SCREEN_H / 2 + 50), "center")

    def _draw_game_over(self, game, t, info):
        if self.go_t0 is None:
            return
        age = t - self.go_t0
        if age < 1.5:
            return
        s = self.surf
        e = ease_out_back(min(1.0, (age - 1.5) / 0.6))
        w, h = 420, 360
        x = int(BOARD_CX - w / 2)
        y = int(lerp(-h - 20, 170, e))
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (8, 6, 20, 225), panel.get_rect(), border_radius=16)
        s.blit(panel, (x, y))
        pygame.draw.rect(s, (255, 60, 90), (x, y, w, h), 3, border_radius=16)

        cx = x + w // 2
        jitter = random.randint(-4, 4) if random.random() < 0.08 else 0
        title = self.fonts.render("GAME OVER", 80, (255, 255, 255), 4)
        for off, col in ((-3, (255, 40, 80)), (3, (40, 220, 255))):
            ghost = self.fonts.render("GAME OVER", 80, col, 4)
            ghost.set_alpha(160)
            s.blit(ghost, ghost.get_rect(center=(cx + off + jitter, y + 60)))
        title.set_alpha(255)
        s.blit(title, title.get_rect(center=(cx + jitter, y + 60)))

        rows = [("SCORE", "{:,}".format(game.score)), ("LINES", str(game.lines)),
                ("LEVEL", str(game.level)), ("TETRIS", str(game.stats["tetris"])),
                ("TIME", "%02d:%02d" % (int(game.time) // 60, int(game.time) % 60))]
        for i, (k, v) in enumerate(rows):
            yy = y + 112 + i * 34
            self.text(k, 26, (160, 170, 210), (x + 60, yy), spacing=2)
            self.text(v, 30, (255, 255, 255), (x + w - 60, yy - 2), "topright")
        if info.get("new_high"):
            col = hsv(t * 0.8, 0.7, 1.0)
            self.text("NEW HIGH SCORE!", 38, col, (cx, y + h - 62), "center",
                      int(200 + 55 * math.sin(t * 8)), spacing=2)
        else:
            self.text("BEST  {:,}".format(info.get("high", 0)), 26, (150, 160, 200), (cx, y + h - 62), "center")
        if age > 2.2:
            a = int(150 + 105 * math.sin(t * 4))
            self.text("ENTER  play again    ESC  menu", 24, (200, 210, 240), (cx, y + h - 26), "center", a)

    # -------------------------------------------------------------- title
    def _draw_title(self, info):
        s = self.surf
        t = self.fx.time
        age = t - self.title_t0
        for r in sorted(self.rain, key=lambda r: r["depth"]):
            prev = self.sprites.preview(r["kind"], 24)
            img = pygame.transform.rotozoom(prev, r["angle"], 0.5 + 0.9 * r["depth"])
            img.set_alpha(int(40 + 110 * r["depth"]))
            s.blit(img, img.get_rect(center=(int(r["x"]), int(r["y"]))))

        sweep = ((age - 2.0) * 0.6) % 1.6
        flash = self.sprites.flash(LOGO_CELL)
        for b in self.logo:
            p = clamp01((age - b["delay"]) / 0.9)
            e = ease_out_back(p, 1.3)
            x = lerp(b["sx"], b["tx"], e)
            y = lerp(b["sy"], b["ty"], e)
            if p >= 1:
                y += math.sin(t * 2.2 - b["col"] * 0.35) * 5
            blk = self.sprites.block(b["kind"], LOGO_CELL)
            if p < 1:
                blk = pygame.transform.rotate(blk, b["spin"] * (1 - e))
            s.blit(blk, blk.get_rect(center=(int(x + LOGO_CELL / 2), int(y + LOGO_CELL / 2))))
            if p >= 1:
                d = abs(sweep * 40 - b["col"])
                if d < 2.5:
                    flash.set_alpha(int(200 * (1 - d / 2.5)))
                    s.blit(flash, (int(x), int(y)))

        intro = clamp01((age - 1.2) / 0.6)
        if intro <= 0:
            return
        ia = int(255 * intro)
        accent = self.fx.accent()
        self.text("N E O N   E D I T I O N", 30, shade(accent, 0.35), (SCREEN_W / 2, 262), "center", ia)

        lvl = info.get("start_level", 1)
        self.text("START LEVEL", 24, (170, 180, 220), (SCREEN_W / 2, 350), "center", ia, spacing=3)
        wob = math.sin(t * 5) * 3
        self.text("<", 44, shade(accent, 0.3), (SCREEN_W / 2 - 70 - wob, 385), "center", ia)
        self.text(">", 44, shade(accent, 0.3), (SCREEN_W / 2 + 70 + wob, 385), "center", ia)
        self.text(str(lvl), 56, (255, 255, 255), (SCREEN_W / 2, 386), "center", ia)

        pa = int((0.55 + 0.45 * math.sin(t * 4)) * 255 * intro)
        self.text("PRESS ENTER", 48, (255, 255, 255), (SCREEN_W / 2, 465), "center", pa, spacing=6)
        self.text("HIGH SCORE  {:,}".format(info.get("high", 0)), 26, (255, 214, 90),
                  (SCREEN_W / 2, 515), "center", ia, spacing=2)

        help_rows = [("LEFT / RIGHT", "move"), ("UP / X", "rotate cw"), ("Z / CTRL", "rotate ccw"),
                     ("DOWN", "soft drop"), ("SPACE", "hard drop"), ("C / SHIFT", "hold"),
                     ("P / ESC", "pause"), ("M / N", "music / sfx"), ("F1 / F2", "CRT / bloom"),
                     ("F11 / F12", "fullscreen / shot")]
        for i, (k, v) in enumerate(help_rows):
            col, row = i % 2, i // 2
            x = SCREEN_W / 2 - 260 + col * 290
            y = 565 + row * 24
            self.text(k, 20, (150, 160, 200), (x, y), alpha=int(ia * 0.85))
            self.text(v, 20, (95, 105, 145), (x + 120, y), alpha=int(ia * 0.85))
