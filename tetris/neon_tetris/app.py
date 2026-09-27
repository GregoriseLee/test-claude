"""Window, main loop, input handling and the game state machine."""

import argparse
import json
import math
import os
import time
from pathlib import Path

import pygame

from .audio import Audio
from .config import (TITLE, SCREEN_W, SCREEN_H, FPS_CAP, DAS, ARR, COUNTDOWN_STEP,
                     BOARD_X, BOARD_PX_W, PIECE_COLORS)
from .effects import FX, K_RING
from .game import Game
from .pipeline import GLPipeline, SoftPipeline, create_gl_context
from .render import Renderer, fcol

SCORE_FILE = Path.home() / ".neon_tetris.json"

LEFT_KEYS = (pygame.K_LEFT, pygame.K_a)
RIGHT_KEYS = (pygame.K_RIGHT, pygame.K_d)
DOWN_KEYS = (pygame.K_DOWN, pygame.K_s)
CW_KEYS = (pygame.K_UP, pygame.K_x, pygame.K_w)
CCW_KEYS = (pygame.K_z, pygame.K_LCTRL, pygame.K_RCTRL)
HOLD_KEYS = (pygame.K_c, pygame.K_LSHIFT, pygame.K_RSHIFT)
CONFIRM_KEYS = (pygame.K_RETURN, pygame.K_KP_ENTER)


def load_high():
    try:
        return int(json.loads(SCORE_FILE.read_text()).get("high", 0))
    except Exception:
        return 0


def save_high(value):
    try:
        SCORE_FILE.write_text(json.dumps({"high": int(value)}))
    except Exception:
        pass


def make_icon():
    icon = pygame.Surface((32, 32), pygame.SRCALPHA)
    c = PIECE_COLORS["T"]
    for x, y in ((1, 0), (0, 1), (1, 1), (2, 1)):
        pygame.draw.rect(icon, c, (x * 10 + 1, y * 10 + 8, 9, 9), border_radius=2)
    return icon


class App:
    def __init__(self, use_shaders=True, mute=False, fullscreen=False, window=None):
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        pygame.display.set_caption(TITLE)
        try:
            pygame.display.set_icon(make_icon())
        except Exception:
            pass
        self.fullscreen = fullscreen
        self.window_size = window or self._default_window_size()
        self.fx = FX()
        self.pipeline = None
        if use_shaders:
            try:
                self._init_gl()
            except Exception as exc:
                print("[gfx] OpenGL pipeline unavailable, using software renderer:", exc)
                self.pipeline = None
                pygame.display.quit()
                pygame.display.init()
                pygame.display.set_caption(TITLE)
        if self.pipeline is None:
            self._init_soft()

        pygame.key.set_repeat()
        self.renderer = Renderer(self.fx)
        self.audio = Audio(enabled=not mute)
        self.clock = pygame.time.Clock()
        self.high = load_high()
        self.new_high = False
        self.start_level = 1
        self.game = Game(1)
        self.state = "title"
        self.state_t = 0.0
        self.transition = None
        self.count_t = 0.0
        self.count_step = -1
        self.show_fps = False
        self.running = True
        self.held = {"left": False, "right": False}
        self.das_dir = 0
        self.das_t = 0.0
        self.arr_t = 0.0
        self.renderer.enter_title()

    # ------------------------------------------------------------ display
    def _default_window_size(self):
        try:
            dw, dh = pygame.display.get_desktop_sizes()[0]
            scale = min(1.0, dw * 0.9 / SCREEN_W, dh * 0.85 / SCREEN_H)
            return int(SCREEN_W * scale), int(SCREEN_H * scale)
        except Exception:
            return SCREEN_W, SCREEN_H

    def _init_gl(self):
        gl = pygame.display.gl_set_attribute
        gl(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        gl(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        gl(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        gl(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, True)
        flags = pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE
        size = self.window_size
        if self.fullscreen:
            flags |= pygame.FULLSCREEN
            size = (0, 0)
        try:
            pygame.display.set_mode(size, flags, vsync=1)
        except Exception:
            pygame.display.set_mode(size, flags)
        self.pipeline = GLPipeline(create_gl_context())
        self.gl = True

    def _init_soft(self):
        flags = pygame.SCALED | pygame.RESIZABLE
        if self.fullscreen:
            flags |= pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), flags)
        self.pipeline = SoftPipeline(self.screen)
        self.gl = False

    def win_size(self):
        if self.gl:
            try:
                return pygame.display.get_window_size()
            except AttributeError:
                return pygame.display.get_surface().get_size()
        return SCREEN_W, SCREEN_H

    # ------------------------------------------------------------ states
    def set_state(self, state):
        self.state = state
        self.state_t = 0.0

    def start_transition(self, action, duration=0.7):
        if self.transition is None:
            self.transition = {"t": 0.0, "dur": duration, "action": action, "fired": False}

    def begin_game(self):
        self.game = Game(self.start_level)
        self.renderer.reset()
        self.fx.set_palette(self.start_level - 1)
        self.fx.danger_target = 0.0
        self.new_high = False
        self.count_t = 0.0
        self.count_step = -1
        self.held = {"left": False, "right": False}
        self.das_dir = 0
        self.set_state("countdown")

    def go_title(self):
        self.audio.pause_music(False)
        self.audio.stop_music()
        self.fx.set_palette(0)
        self.fx.danger_target = 0.0
        self.fx.desat_target = 0.0
        self.game = Game(self.start_level)
        self.renderer.reset()
        self.renderer.enter_title()
        self.set_state("title")

    def set_pause(self, paused):
        if paused and self.state == "play":
            self.set_state("pause")
            self.fx.desat_target = 1.0
            self.audio.pause_music(True)
            self.game.soft_drop = False
        elif not paused and self.state == "pause":
            self.set_state("play")
            self.fx.desat_target = 0.0
            self.audio.pause_music(False)

    # ------------------------------------------------------------- input
    def on_key(self, key, down):
        if key in LEFT_KEYS:
            self._dir_key("left", down)
        elif key in RIGHT_KEYS:
            self._dir_key("right", down)
        if key in DOWN_KEYS:
            self.game.soft_drop = down and self.state == "play"
        if not down:
            return

        if key == pygame.K_F1:
            self.fx.crt_on = not self.fx.crt_on
        elif key == pygame.K_F2:
            self.fx.bloom_on = not self.fx.bloom_on
        elif key == pygame.K_F3:
            self.show_fps = not self.show_fps
        elif key == pygame.K_F11:
            try:
                pygame.display.toggle_fullscreen()
            except Exception:
                pass
        elif key == pygame.K_F12:
            self.screenshot()
        elif key == pygame.K_m:
            self.audio.toggle_music(self.state in ("play", "countdown"))
        elif key == pygame.K_n:
            self.audio.toggle_sfx()

        if self.transition is not None:
            return
        st = self.state
        if st == "title":
            if key in CONFIRM_KEYS or key == pygame.K_SPACE:
                self.audio.play("select")
                self.start_transition(self.begin_game)
            elif key in LEFT_KEYS or key in RIGHT_KEYS:
                d = -1 if key in LEFT_KEYS else 1
                self.start_level = max(1, min(15, self.start_level + d))
                self.fx.set_palette(self.start_level - 1)
                self.audio.play("move")
            elif key == pygame.K_ESCAPE:
                self.running = False
        elif st == "play":
            g = self.game
            if key in (pygame.K_p, pygame.K_ESCAPE):
                self.set_pause(True)
            elif key in CW_KEYS:
                g.rotate(1)
            elif key in CCW_KEYS:
                g.rotate(-1)
            elif key == pygame.K_SPACE:
                g.hard_drop()
            elif key in HOLD_KEYS:
                g.hold()
        elif st == "pause":
            if key in (pygame.K_p, pygame.K_ESCAPE) or key in CONFIRM_KEYS:
                self.set_pause(False)
            elif key == pygame.K_q:
                self.fx.desat_target = 0.0
                self.start_transition(self.go_title)
        elif st == "over" and self.state_t > 1.6:
            if key in CONFIRM_KEYS or key == pygame.K_SPACE:
                self.audio.play("select")
                self.start_transition(self.begin_game)
            elif key == pygame.K_ESCAPE:
                self.start_transition(self.go_title)

    def _dir_key(self, name, down):
        other = "right" if name == "left" else "left"
        d = -1 if name == "left" else 1
        self.held[name] = down
        if down:
            self.das_dir = d
            self.das_t = 0.0
            self.arr_t = 0.0
            if self.state == "play":
                if not self.game.move(d) and self.game.piece is not None:
                    self.renderer.kick_x.kick(d * 110)
                    self.audio.play("bump", 0.5)
        elif self.das_dir == d:
            self.das_dir = (1 if other == "right" else -1) if self.held[other] else 0
            self.das_t = 0.0
            self.arr_t = 0.0

    def _update_das(self, dt):
        if self.das_dir == 0 or self.state != "play":
            return
        self.das_t += dt
        if self.das_t < DAS:
            return
        self.arr_t += dt
        while self.arr_t >= ARR:
            self.arr_t -= ARR
            if not self.game.move(self.das_dir):
                self.arr_t = 0.0
                break

    # ------------------------------------------------------------ update
    def update(self, dt):
        fx = self.fx
        self.state_t += dt
        tr = self.transition
        if tr:
            tr["t"] += dt
            p = tr["t"] / tr["dur"]
            if p >= 0.5 and not tr["fired"]:
                tr["fired"] = True
                tr["action"]()
            fx.pixelate = math.sin(min(1.0, p) * math.pi)
            if p >= 1:
                self.transition = None
                fx.pixelate = 0.0

        if self.state == "countdown":
            self.count_t += dt
            step = int(self.count_t / COUNTDOWN_STEP)
            if step != self.count_step and step < 4:
                self.count_step = step
                label = ["3", "2", "1", "GO!"][step]
                self.renderer.countdown = (label, fx.time)
                cx = BOARD_X + BOARD_PX_W / 2
                fx.particles.emit(1, cx, SCREEN_H / 2 - 20, fcol(fx.accent(), 1.5), K_RING,
                                  speed=(0, 0), life=(0.5, 0.5), size=(320, 320))
                if label == "GO!":
                    self.audio.play("go")
                    fx.wave(cx, SCREEN_H / 2, strength=0.8, duration=0.8, radius=0.8)
                    fx.shake.add(0.25)
                    self.set_state("play")
                    self.game.start()
                    self.audio.start_music()
                    if self.held["left"] or self.held["right"]:
                        self.das_t = DAS
                else:
                    self.audio.play("count")
                    fx.shake.add(0.1)

        if self.state == "play":
            self._update_das(dt)
            self.game.update(dt)

        for ev in self.game.pop_events():
            self.renderer.on_event(ev, self.game)
            self.audio.on_event(ev, fx.time)
            if ev["type"] == "game_over":
                self.set_state("over")
                self.fx.desat_target = 0.0
                if self.game.score > self.high:
                    self.high = self.game.score
                    self.new_high = True
                    save_high(self.high)

        if self.state in ("play", "pause", "over"):
            fx.danger_target = max(0.0, min(1.0, (self.game.stack_height() - 13) / 5.0))
            if self.state == "over":
                fx.danger_target = 0.0

        self.renderer.update(dt, self.state, self.game)
        for name in self.renderer.sounds:
            self.audio.play(name)
        self.renderer.sounds.clear()
        fx.update(dt)

    def draw(self):
        info = {"high": self.high, "new_high": self.new_high, "start_level": self.start_level,
                "fps": self.clock.get_fps() if self.show_fps else None}
        surf = self.renderer.draw(self.state, self.game, info)
        self.pipeline.render(surf, self.fx, self.win_size())

    def screenshot(self, path=None):
        img = self.pipeline.screenshot(self.win_size())
        path = path or "tetris_%s.png" % time.strftime("%Y%m%d_%H%M%S")
        pygame.image.save(img, path)
        print("[screenshot]", os.path.abspath(path))
        return path

    # -------------------------------------------------------------- loop
    def handle_events(self):
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                self.on_key(e.key, True)
            elif e.type == pygame.KEYUP:
                self.on_key(e.key, False)
            elif e.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                self.set_pause(True)

    def frame(self, dt):
        self.handle_events()
        self.update(dt)
        self.draw()
        pygame.display.flip()

    def run(self):
        while self.running:
            dt = min(self.clock.tick(FPS_CAP) / 1000.0, 1 / 20)
            self.frame(dt)
        pygame.quit()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Neon Tetris - shader-powered Tetris in Python")
    ap.add_argument("--no-shaders", action="store_true", help="use the software renderer")
    ap.add_argument("--mute", action="store_true", help="disable all audio")
    ap.add_argument("--fullscreen", action="store_true", help="start in fullscreen")
    args = ap.parse_args(argv)
    App(use_shaders=not args.no_shaders, mute=args.mute, fullscreen=args.fullscreen).run()
