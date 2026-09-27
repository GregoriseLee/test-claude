"""Frame composition.

``GLPipeline``   - moderngl: shader background, game layer, GPU particles,
                   HDR bloom and a heavy post-processing pass.
``SoftPipeline`` - plain pygame fallback when OpenGL 3.3 is unavailable.
"""

import math
import random

import numpy as np
import pygame

from . import shaders
from .config import SCREEN_W, SCREEN_H
from .effects import K_GLOW, K_SPARK, K_SHARD, K_BEAM, K_TRAIL, K_RING, Particles


def surface_bytes(surf, flipped):
    if hasattr(pygame.image, "tobytes"):
        return pygame.image.tobytes(surf, "RGBA", flipped)
    return pygame.image.tostring(surf, "RGBA", flipped)


def letterbox(win_w, win_h):
    scale = min(win_w / SCREEN_W, win_h / SCREEN_H)
    w, h = int(SCREEN_W * scale), int(SCREEN_H * scale)
    return (win_w - w) // 2, (win_h - h) // 2, w, h


def create_gl_context():
    import moderngl
    try:
        return moderngl.create_context()
    except Exception:
        # moderngl looks for the unversioned "libGL.so", which only exists
        # when mesa dev packages are installed. Try the runtime soname.
        return moderngl.create_context(libgl="libGL.so.1")


class GLPipeline:
    def __init__(self, ctx):
        import moderngl
        self.mgl = moderngl
        self.ctx = ctx
        W, H = SCREEN_W, SCREEN_H

        quad = np.array([-1, -1, 0, 0, 1, -1, 1, 0, -1, 1, 0, 1, 1, 1, 1, 1], "f4")
        self.quad = ctx.buffer(quad.tobytes())
        self.progs, self.vaos = {}, {}
        for name, fs in (("bg", shaders.BACKGROUND_FS), ("layer", shaders.LAYER_FS),
                         ("bright", shaders.BRIGHT_FS), ("down", shaders.DOWN_FS),
                         ("blur", shaders.BLUR_FS), ("final", shaders.FINAL_FS)):
            prog = ctx.program(vertex_shader=shaders.FULLSCREEN_VS, fragment_shader=fs)
            self.progs[name] = prog
            self.vaos[name] = ctx.vertex_array(
                prog, [(self.quad, "2f 2f", "in_pos", "in_uv")], skip_errors=True)

        self.p_part = ctx.program(vertex_shader=shaders.PARTICLE_VS,
                                  fragment_shader=shaders.PARTICLE_FS)
        corners = np.array([-1, -1, 1, -1, -1, 1, 1, 1], "f4")
        self.corner_vbo = ctx.buffer(corners.tobytes())
        self.inst_vbo = ctx.buffer(reserve=6000 * Particles.STRIDE * 4, dynamic=True)
        self.part_vao = ctx.vertex_array(self.p_part, [
            (self.corner_vbo, "2f", "in_corner"),
            (self.inst_vbo, "2f 2f 2f 4f 1f 1f 1f/i",
             "i_pos", "i_size", "i_vel", "i_color", "i_rot", "i_kind", "i_age"),
        ], skip_errors=True)

        self.layer = ctx.texture((W, H), 4)
        self.layer.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.scene = self._tex((W, H))
        self.scene_fbo = ctx.framebuffer(color_attachments=[self.scene])
        self.bloom = []
        for div in (2, 4, 8):
            size = (W // div, H // div)
            a, b = self._tex(size), self._tex(size)
            self.bloom.append((size, a, ctx.framebuffer([a]), b, ctx.framebuffer([b])))

        self._fast_upload = None
        self._dpi = None
        final = self.progs["final"]
        for i, name in enumerate(("u_scene", "u_bloom0", "u_bloom1", "u_bloom2")):
            self._set(final, name, i)

    def _tex(self, size):
        mgl = self.mgl
        t = self.ctx.texture(size, 4, dtype="f2")
        t.filter = (mgl.LINEAR, mgl.LINEAR)
        t.repeat_x = t.repeat_y = False
        return t

    @staticmethod
    def _set(prog, name, value):
        try:
            prog[name].value = value
        except KeyError:
            pass            # uniform optimised away by the driver

    def _upload_layer(self, surf):
        """Upload the game layer; returns 1.0 if the shader must flip it."""
        if self._fast_upload is None:
            orders = {(0xFF0000, 0xFF00, 0xFF, 0xFF000000): "BGRA",
                      (0xFF, 0xFF00, 0xFF0000, 0xFF000000): "RGBA"}
            order = orders.get(tuple(surf.get_masks()))
            self._fast_upload = bool(order) and surf.get_pitch() == surf.get_width() * 4
            if self._fast_upload:
                self.layer.swizzle = order
        if self._fast_upload:
            try:
                view = surf.get_view("0")
                self.layer.write(view)
                del view            # releases the surface lock
                return 1.0
            except Exception:
                self._fast_upload = False
                self.layer.swizzle = "RGBA"
        self.layer.write(surface_bytes(surf, True))
        return 0.0

    def _fullscreen(self, name, fbo, size, tex, **uniforms):
        fbo.use()
        self.ctx.viewport = (0, 0, size[0], size[1])
        tex.use(0)
        prog = self.progs[name]
        self._set(prog, "u_tex", 0)
        for k, v in uniforms.items():
            self._set(prog, k, v)
        self.vaos[name].render(self.mgl.TRIANGLE_STRIP)

    def _drawable(self, win_size):
        # On HiDPI setups the GL drawable can be larger than the window size.
        if self._dpi is None:
            try:
                self._dpi = max(1.0, self.ctx.screen.viewport[2] / float(win_size[0]))
            except Exception:
                self._dpi = 1.0
        return int(win_size[0] * self._dpi), int(win_size[1] * self._dpi)

    def render(self, surf, fx, win_size):
        mgl, ctx = self.mgl, self.ctx
        W, H = SCREEN_W, SCREEN_H

        # 1) scene: background shader -> game layer -> particles (HDR)
        self.scene_fbo.use()
        ctx.viewport = (0, 0, W, H)
        ctx.disable(mgl.BLEND)
        bg = self.progs["bg"]
        self._set(bg, "u_time", fx.time)
        self._set(bg, "u_res", (W, H))
        self._set(bg, "u_col_a", tuple(float(v) for v in fx.palette[0]))
        self._set(bg, "u_col_b", tuple(float(v) for v in fx.palette[1]))
        self._set(bg, "u_col_c", tuple(float(v) for v in fx.palette[2]))
        self._set(bg, "u_pulse", fx.pulse.v)
        self._set(bg, "u_speed", fx.star_speed)
        self._set(bg, "u_danger", fx.danger)
        self.vaos["bg"].render(mgl.TRIANGLE_STRIP)

        flip = self._upload_layer(surf)
        ctx.enable(mgl.BLEND)
        ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA
        self.layer.use(0)
        self._set(self.progs["layer"], "u_tex", 0)
        self._set(self.progs["layer"], "u_flip", flip)
        self.vaos["layer"].render(mgl.TRIANGLE_STRIP)

        data = fx.particles.instance_data()
        n = len(data)
        if n:
            self.inst_vbo.write(data.tobytes())
            self._set(self.p_part, "u_res", (W, H))
            self._set(self.p_part, "u_pass", 0)
            self.part_vao.render(mgl.TRIANGLE_STRIP, vertices=4, instances=n)
            ctx.blend_func = mgl.ONE, mgl.ONE
            self._set(self.p_part, "u_pass", 1)
            self.part_vao.render(mgl.TRIANGLE_STRIP, vertices=4, instances=n)
        ctx.disable(mgl.BLEND)

        # 2) bloom: bright pass then a blur pyramid
        prev = self.scene
        prev_size = (W, H)
        for level, (size, a, fa, b, fb) in enumerate(self.bloom):
            texel = (1.0 / prev_size[0], 1.0 / prev_size[1])
            if level == 0:
                self._fullscreen("bright", fa, size, prev, u_texel=texel,
                                 u_threshold=0.62, u_knee=0.25)
            else:
                self._fullscreen("down", fa, size, prev, u_texel=texel)
            self._fullscreen("blur", fb, size, a, u_dir=(1.0 / size[0], 0.0))
            self._fullscreen("blur", fa, size, b, u_dir=(0.0, 1.0 / size[1]))
            self._fullscreen("blur", fb, size, a, u_dir=(2.0 / size[0], 0.0))
            self._fullscreen("blur", fa, size, b, u_dir=(0.0, 2.0 / size[1]))
            prev, prev_size = a, size

        # 3) final post-process straight to the window
        ww, wh = self._drawable(win_size)
        ctx.screen.use()
        ctx.viewport = (0, 0, ww, wh)
        self.scene.use(0)
        for i, (_, a, _, _, _) in enumerate(self.bloom):
            a.use(i + 1)
        f = self.progs["final"]
        sx, sy, rot = fx.shake.offset(fx.time)
        self._set(f, "u_view", letterbox(ww, wh))
        self._set(f, "u_res", (W, H))
        self._set(f, "u_time", fx.time)
        self._set(f, "u_shake", (sx / W, -sy / H))
        self._set(f, "u_shake_rot", rot)
        try:
            f["u_waves"].write(np.array(fx.wave_uniforms(), "f4").tobytes())
        except KeyError:
            pass
        self._set(f, "u_chroma", fx.chroma.v)
        self._set(f, "u_glitch", min(1.0, fx.glitch.v))
        self._set(f, "u_flash", fx.flash.v)
        self._set(f, "u_flash_col", tuple(fx.flash_color))
        self._set(f, "u_danger", fx.danger)
        self._set(f, "u_crt", fx.crt)
        self._set(f, "u_bloom", fx.bloom)
        self._set(f, "u_pixelate", fx.pixelate)
        self._set(f, "u_desat", fx.desat)
        self.vaos["final"].render(mgl.TRIANGLE_STRIP)

    def screenshot(self, win_size):
        ww, wh = self._drawable(win_size)
        data = self.ctx.screen.read(viewport=(0, 0, ww, wh), components=3)
        img = pygame.image.frombuffer(data, (ww, wh), "RGB")
        return pygame.transform.flip(img, False, True)


class SoftPipeline:
    """Software fallback: gradient + stars, the game layer and simple particles."""

    def __init__(self, screen):
        self.screen = screen
        self.bg = pygame.Surface((SCREEN_W, SCREEN_H))
        self.stars = [(random.uniform(0, SCREEN_W), random.uniform(0, SCREEN_H),
                       random.uniform(0.3, 1.0)) for _ in range(140)]
        self.overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        self._palette_key = None

    def _rebuild_bg(self, fx):
        a = fx.palette[0]
        b = fx.palette[1]
        for y in range(SCREEN_H):
            t = y / SCREEN_H
            c = a * (1 - t) * 1.5 + b * t * 0.8
            self.bg.fill([int(max(0, min(255, v * 255))) for v in c], (0, y, SCREEN_W, 1))

    def render(self, surf, fx, win_size):
        key = tuple(int(v * 40) for c in fx.palette for v in c)
        if key != self._palette_key:
            self._palette_key = key
            self._rebuild_bg(fx)
        scr = self.screen
        sx, sy, _ = fx.shake.offset(fx.time)
        scr.blit(self.bg, (0, 0))
        speed = 20 * fx.star_speed
        for x, y, d in self.stars:
            yy = (y + fx.time * speed * d) % SCREEN_H
            tw = 0.6 + 0.4 * math.sin(fx.time * 3 + x)
            v = int(255 * d * tw)
            scr.fill((v, v, min(255, v + 30)), (int(x), int(yy), 2 if d > 0.8 else 1, 2 if d > 0.8 else 1))
        scr.blit(surf, (int(sx), int(sy)))

        p = fx.particles
        idx = p.alive_indices()
        if len(idx) > 900:
            idx = idx[-900:]
        for i in idx:
            kind = int(p.kind[i])
            age = 1.0 - p.life[i] / p.max_life[i]
            fade = max(0.0, 1.0 - age)
            r, g, b, a = p.color[i]
            col = (int(min(255, r * 255 * fade * a)), int(min(255, g * 255 * fade * a)),
                   int(min(255, b * 255 * fade * a)))
            x, y = p.pos[i] + (sx, sy)
            if kind == K_SHARD:
                s = max(1, int(p.size[i, 0]))
                scr.fill(tuple(int(min(255, c * 255)) for c in (r, g, b)), (int(x - s / 2), int(y - s / 2), s, s))
            elif kind == K_SPARK:
                vx, vy = p.vel[i] * 0.03
                pygame.draw.line(scr, col, (x, y), (x - vx, y - vy), 2)
            elif kind in (K_BEAM, K_TRAIL):
                w, h = p.size[i]
                if kind == K_BEAM:
                    h *= max(0.0, 1 - age)
                scr.fill(col, (int(x - w / 2), int(y - h / 2), int(w), max(1, int(h))),
                         special_flags=pygame.BLEND_ADD)
            elif kind == K_RING:
                rad = int(p.size[i, 0] * (0.25 + 0.9 * math.sqrt(age)) / 2)
                if rad > 1:
                    pygame.draw.circle(scr, col, (int(x), int(y)), rad, 2)
            elif kind == K_GLOW:
                rad = max(1, int(p.size[i, 0] * 0.18 * (1 - 0.5 * age)))
                scr.fill(col, (int(x - rad), int(y - rad), rad * 2, rad * 2),
                         special_flags=pygame.BLEND_ADD)

        if fx.flash.v > 0.01:
            fc = fx.flash_color
            self.overlay.fill((int(fc[0] * 255), int(fc[1] * 255), int(fc[2] * 255),
                               int(min(1.0, fx.flash.v) * 160)))
            scr.blit(self.overlay, (0, 0))
        if fx.desat > 0.01:
            self.overlay.fill((0, 0, 10, int(fx.desat * 110)))
            scr.blit(self.overlay, (0, 0))
        if fx.pixelate > 0.01:
            self.overlay.fill((0, 0, 0, int(min(1.0, fx.pixelate) * 255)))
            scr.blit(self.overlay, (0, 0))

    def screenshot(self, win_size):
        return self.screen.copy()
