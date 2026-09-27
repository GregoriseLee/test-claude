"""Animation helpers: easing, springs, screen shake, GPU-friendly particles.

Everything here is renderer-agnostic. The ``FX`` object is the shared
"juice" state: the shader pipeline reads it to drive post-processing
uniforms and the particle instance buffer.
"""

import math
import random

import numpy as np

from .config import PALETTES, SCREEN_W, SCREEN_H

TAU = math.pi * 2

# Particle kinds (must match the particle shader)
K_GLOW, K_SPARK, K_SHARD, K_BEAM, K_TRAIL, K_RING = range(6)


# --------------------------------------------------------------------- easing
def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(e0, e1, x):
    t = clamp01((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


def ease_out_cubic(t):
    t = clamp01(t)
    return 1 - (1 - t) ** 3


def ease_in_cubic(t):
    t = clamp01(t)
    return t * t * t


def ease_out_back(t, s=1.70158):
    t = clamp01(t) - 1
    return t * t * ((s + 1) * t + s) + 1


def ease_out_elastic(t):
    t = clamp01(t)
    if t in (0.0, 1.0):
        return t
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * (TAU / 3)) + 1


def smooth_noise(t):
    """Cheap 1D pseudo noise in [-1, 1] made from incommensurate sines."""
    return (math.sin(t) * 0.5 + math.sin(t * 2.31 + 1.7) * 0.3 +
            math.sin(t * 4.13 + 0.3) * 0.2)


# ------------------------------------------------------------------- physics
class Spring:
    """Damped spring pulled back to 0. ``kick`` adds velocity."""

    def __init__(self, stiffness=520.0, damping=20.0):
        self.k, self.c = stiffness, damping
        self.x = 0.0
        self.v = 0.0

    def kick(self, velocity):
        self.v += velocity

    def update(self, dt):
        steps = max(1, int(dt / (1 / 240)) + 1)
        h = dt / steps
        for _ in range(steps):
            a = -self.k * self.x - self.c * self.v
            self.v += a * h
            self.x += self.v * h


class Pulse:
    """A value that jumps up on ``hit`` and decays exponentially."""

    def __init__(self, rate):
        self.v = 0.0
        self.rate = rate

    def hit(self, amount):
        self.v = max(self.v, amount)

    def update(self, dt):
        self.v *= math.exp(-self.rate * dt)
        if self.v < 1e-4:
            self.v = 0.0


class Shake:
    """Trauma based screen shake (offset grows with trauma squared)."""

    def __init__(self):
        self.trauma = 0.0
        self.seed = random.random() * 100

    def add(self, amount):
        self.trauma = min(1.0, self.trauma + amount)

    def update(self, dt):
        self.trauma = max(0.0, self.trauma - dt * 1.5)

    def offset(self, t):
        s = self.trauma ** 2
        return (s * 18 * smooth_noise(t * 31 + self.seed),
                s * 18 * smooth_noise(t * 29 + self.seed + 37),
                s * 0.035 * smooth_noise(t * 23 + self.seed + 71))


# ----------------------------------------------------------------- particles
class Particles:
    """Structure-of-arrays particle system in a ring buffer.

    Simulated on the CPU with numpy, rendered on the GPU as instanced quads
    (or with plain pygame drawing in the fallback renderer).
    """

    STRIDE = 13  # floats per instance: pos2 size2 vel2 color4 rot kind age

    def __init__(self, cap=6000):
        self.cap = cap
        self.pos = np.zeros((cap, 2), np.float32)
        self.vel = np.zeros((cap, 2), np.float32)
        self.size = np.zeros((cap, 2), np.float32)
        self.color = np.zeros((cap, 4), np.float32)
        self.life = np.zeros(cap, np.float32)
        self.max_life = np.ones(cap, np.float32)
        self.rot = np.zeros(cap, np.float32)
        self.spin = np.zeros(cap, np.float32)
        self.kind = np.zeros(cap, np.float32)
        self.grav = np.zeros(cap, np.float32)
        self.drag = np.zeros(cap, np.float32)
        self.head = 0

    def clear(self):
        self.life[:] = 0

    def emit(self, n, x, y, color, kind=K_GLOW, speed=(50, 200), angle=(0.0, TAU),
             life=(0.4, 1.0), size=(3, 8), size_y=None, grav=0.0, drag=1.5,
             spin=(-6, 6), spread=(0, 0), vel=(0, 0), rot=None):
        if n <= 0:
            return
        idx = (self.head + np.arange(n)) % self.cap
        self.head = (self.head + n) % self.cap
        rnd = np.random.uniform
        a = rnd(angle[0], angle[1], n)
        s = rnd(speed[0], speed[1], n)
        self.pos[idx, 0] = x + rnd(-spread[0], spread[0], n) if spread[0] else x
        self.pos[idx, 1] = y + rnd(-spread[1], spread[1], n) if spread[1] else y
        self.vel[idx, 0] = np.cos(a) * s + vel[0]
        self.vel[idx, 1] = np.sin(a) * s + vel[1]
        sz = rnd(size[0], size[1], n)
        self.size[idx, 0] = sz
        self.size[idx, 1] = sz if size_y is None else rnd(size_y[0], size_y[1], n)
        col = np.asarray(color, np.float32)
        if col.ndim == 1:
            if col.shape[0] == 3:
                col = np.append(col, 1.0)
            self.color[idx] = col
        else:
            if col.shape[1] == 3:
                col = np.hstack([col, np.ones((len(col), 1), np.float32)])
            self.color[idx] = col[np.arange(n) % len(col)]
        lf = rnd(life[0], life[1], n)
        self.life[idx] = lf
        self.max_life[idx] = lf
        self.rot[idx] = rnd(0, TAU, n) if rot is None else rot
        self.spin[idx] = rnd(spin[0], spin[1], n)
        self.kind[idx] = kind
        self.grav[idx] = grav
        self.drag[idx] = drag

    def update(self, dt):
        # Updating the whole buffer is cheaper than masking for these sizes.
        self.vel[:, 1] += self.grav * dt
        self.vel *= np.exp(-self.drag * dt)[:, None]
        self.pos += self.vel * dt
        self.rot += self.spin * dt
        self.life -= dt

    def alive_indices(self):
        return np.nonzero(self.life > 0)[0]

    def instance_data(self):
        idx = self.alive_indices()
        n = len(idx)
        data = np.empty((n, self.STRIDE), np.float32)
        if n:
            data[:, 0:2] = self.pos[idx]
            data[:, 2:4] = self.size[idx]
            data[:, 4:6] = self.vel[idx]
            data[:, 6:10] = self.color[idx]
            data[:, 10] = self.rot[idx]
            data[:, 11] = self.kind[idx]
            data[:, 12] = 1.0 - self.life[idx] / self.max_life[idx]
        return data


# ------------------------------------------------------------------- FX hub
class FX:
    def __init__(self):
        self.time = 0.0
        self.particles = Particles()
        self.shake = Shake()
        self.waves = []                    # shockwaves: dicts in pixel space
        self.chroma = Pulse(3.5)           # chromatic aberration kick
        self.glitch = Pulse(2.5)
        self.flash = Pulse(5.0)
        self.flash_color = (1.0, 1.0, 1.0)
        self.pulse = Pulse(2.2)            # background beat
        self.danger = 0.0
        self.danger_target = 0.0
        self.desat = 0.0
        self.desat_target = 0.0
        self.pixelate = 0.0
        self.star_speed = 1.0
        self.star_speed_target = 1.0
        self.crt_on = True
        self.bloom_on = True
        self.crt = 1.0
        self.bloom = 1.0
        self.palette = [np.array(c, np.float32) for c in PALETTES[0]]
        self.palette_target = [np.array(c, np.float32) for c in PALETTES[0]]

    # palette / level theme
    def set_palette(self, index):
        self.palette_target = [np.array(c, np.float32) for c in PALETTES[index % len(PALETTES)]]
        self.star_speed_target = 1.0 + index * 0.35

    def accent(self, boost=1.0):
        c = self.palette[2] * boost
        return tuple(int(max(0, min(255, v * 255))) for v in c)

    def wave(self, x, y, strength=1.0, duration=0.9, radius=0.7):
        self.waves.append({"x": x, "y": y, "t": 0.0, "dur": duration,
                           "strength": strength, "radius": radius})
        if len(self.waves) > 4:
            self.waves.pop(0)

    def wave_uniforms(self):
        """Up to 4 vec4: (u, v, radius in screen heights, strength)."""
        out = []
        for w in self.waves[-4:]:
            p = clamp01(w["t"] / w["dur"])
            radius = w["radius"] * ease_out_cubic(p)
            strength = w["strength"] * (1 - p) ** 1.5
            out.append((w["x"] / SCREEN_W, 1.0 - w["y"] / SCREEN_H, radius, strength))
        while len(out) < 4:
            out.append((0.0, 0.0, 0.0, 0.0))
        return out

    def update(self, dt):
        self.time += dt
        self.particles.update(dt)
        self.shake.update(dt)
        for p in (self.chroma, self.glitch, self.flash, self.pulse):
            p.update(dt)
        for w in self.waves:
            w["t"] += dt
        self.waves = [w for w in self.waves if w["t"] < w["dur"]]
        k = 1 - math.exp(-dt * 3.0)
        self.danger += (self.danger_target - self.danger) * k
        self.desat += (self.desat_target - self.desat) * (1 - math.exp(-dt * 8.0))
        self.star_speed += (self.star_speed_target - self.star_speed) * k
        kp = 1 - math.exp(-dt * 1.5)
        for i in range(3):
            self.palette[i] += (self.palette_target[i] - self.palette[i]) * kp
        ka = 1 - math.exp(-dt * 6.0)
        self.crt += ((1.0 if self.crt_on else 0.0) - self.crt) * ka
        self.bloom += ((1.0 if self.bloom_on else 0.0) - self.bloom) * ka
