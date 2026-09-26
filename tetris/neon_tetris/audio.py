"""Procedurally synthesised sound effects and music (no asset files).

Everything is generated with numpy when the game starts. If the mixer can
not be opened (no audio device) the game simply runs silently.
"""

import numpy as np
import pygame

SR = 44100
_rng = np.random.default_rng(7)

NOTE_INDEX = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
              "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def midi(name):
    return 12 * (int(name[-1]) + 1) + NOTE_INDEX[name[:-1]]


def mfreq(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def nfreq(name):
    return mfreq(midi(name))


# ------------------------------------------------------------------ synthesis
def _t(dur):
    return np.arange(max(1, int(SR * dur))) / SR


def osc(freq, dur, wave="sine", freq_end=None, duty=0.5, vib=0.0, vib_rate=5.5):
    t = _t(dur)
    if freq_end is not None:
        f = freq * (freq_end / freq) ** (t / dur)
    else:
        f = np.full(t.shape, float(freq))
    if vib:
        f = f * (1.0 + vib * np.sin(2 * np.pi * vib_rate * t))
    ph = np.cumsum(f) / SR
    frac = ph % 1.0
    if wave == "sine":
        return np.sin(2 * np.pi * ph)
    if wave == "square":
        return np.where(frac < duty, 1.0, -1.0)
    if wave == "saw":
        return 2.0 * frac - 1.0
    if wave == "tri":
        return 4.0 * np.abs(frac - 0.5) - 1.0
    raise ValueError(wave)


def noise(dur):
    return _rng.uniform(-1.0, 1.0, max(1, int(SR * dur)))


def env(n, attack=0.004, decay=0.1, hold=0.0):
    t = np.arange(n) / SR
    a = np.minimum(1.0, t / max(attack, 1e-5))
    return a * np.exp(-np.maximum(0.0, t - attack - hold) / decay)


def lowpass(y, k):
    if k <= 1:
        return y
    return np.convolve(y, np.ones(k) / k, mode="same")


def highpass(y, k):
    return y - lowpass(y, k)


def echo(y, delay=0.11, fb=0.4, taps=4):
    d = int(SR * delay)
    out = np.zeros(len(y) + d * taps)
    for i in range(taps + 1):
        out[i * d:i * d + len(y)] += y * fb ** i
    return out


def mix(parts):
    end = max(int(s * SR) + len(a) for s, a in parts)
    out = np.zeros(end)
    for s, a in parts:
        i = int(s * SR)
        out[i:i + len(a)] += a
    return out


def blip(freq, dur, wave="square", decay=0.08, duty=0.5, vol=1.0, freq_end=None):
    y = osc(freq, dur, wave, freq_end=freq_end, duty=duty)
    return y * env(len(y), 0.003, decay) * vol


def arpeggio(notes, step, dur, wave="square", decay=0.12, vol=0.5, duty=0.5):
    return mix([(i * step, blip(nfreq(n), dur, wave, decay, duty, vol)) for i, n in enumerate(notes)])


# ---------------------------------------------------------------------- sfx
def build_sfx():
    s = {}
    s["move"] = blip(1300, 0.03, "square", 0.01, 0.25, 0.18)
    s["rotate"] = mix([(0, blip(520, 0.07, "tri", 0.03, vol=0.45, freq_end=1100)),
                       (0, blip(1560, 0.05, "sine", 0.02, vol=0.12))])
    s["soft"] = highpass(noise(0.02), 3) * env(int(SR * 0.02), 0.001, 0.004) * 0.12
    thump = osc(170, 0.28, "sine", freq_end=38)
    s["hard"] = (thump * env(len(thump), 0.002, 0.09) * 0.95
                 + np.pad(lowpass(noise(0.12), 6) * env(int(SR * 0.12), 0.001, 0.03) * 0.55,
                          (0, len(thump) - int(SR * 0.12)))
                 + np.pad(blip(2400, 0.04, "square", 0.01, 0.5, 0.08), (0, len(thump) - int(SR * 0.04))))
    click = blip(300, 0.07, "square", 0.02, 0.3, 0.25, freq_end=170)
    s["lock"] = click + np.pad(highpass(noise(0.02), 2) * 0.1, (0, len(click) - int(SR * 0.02)))
    whoosh = lowpass(noise(0.22), 18) * np.sin(np.linspace(0, np.pi, int(SR * 0.22))) * 0.7
    s["hold"] = mix([(0, whoosh), (0.0, blip(420, 0.16, "tri", 0.08, vol=0.35, freq_end=840))])
    s["bump"] = blip(95, 0.07, "sine", 0.03, vol=0.45)
    s["select"] = blip(880, 0.06, "square", 0.03, 0.5, 0.18)
    s["count"] = blip(660, 0.18, "square", 0.08, 0.5, 0.3) + blip(1320, 0.18, "sine", 0.06, vol=0.15)
    s["go"] = echo(mix([(0, blip(880, 0.45, "square", 0.2, 0.5, 0.25)),
                        (0, blip(1318.5, 0.45, "square", 0.2, 0.25, 0.2)),
                        (0, blip(1760, 0.45, "sine", 0.2, vol=0.2))]), 0.09, 0.3, 3)

    s["clear1"] = echo(arpeggio(["C5", "G5"], 0.05, 0.18, "square", 0.08, 0.35), 0.08, 0.3, 2)
    s["clear2"] = echo(arpeggio(["C5", "E5", "G5"], 0.045, 0.2, "square", 0.09, 0.35), 0.08, 0.3, 3)
    s["clear3"] = echo(arpeggio(["C5", "E5", "G5", "C6"], 0.04, 0.22, "square", 0.1, 0.35), 0.08, 0.32, 3)
    sub = osc(110, 0.7, "sine", freq_end=45)
    tetris = mix([
        (0.0, arpeggio(["C5", "E5", "G5", "C6", "E6", "G6"], 0.035, 0.25, "square", 0.1, 0.32, 0.25)),
        (0.2, blip(nfreq("C5"), 0.8, "saw", 0.35, vol=0.18)),
        (0.2, blip(nfreq("E5"), 0.8, "saw", 0.35, vol=0.16)),
        (0.2, blip(nfreq("G5"), 0.8, "saw", 0.35, vol=0.16)),
        (0.2, blip(nfreq("C6"), 0.8, "square", 0.3, 0.25, 0.14)),
        (0.0, sub * env(len(sub), 0.005, 0.25) * 0.7),
    ])
    s["clear4"] = echo(lowpass(tetris, 2), 0.1, 0.35, 4)
    s["tspin"] = echo(mix([
        (0.0, arpeggio(["D5", "G5", "A5", "D6"], 0.05, 0.25, "square", 0.1, 0.32, 0.25)),
        (0.2, blip(nfreq("F#6"), 0.5, "sine", 0.25, vol=0.25)),
    ]), 0.09, 0.35, 4)
    s["allclear"] = echo(arpeggio(["C6", "E6", "G6", "B6", "C7", "E7", "G7"], 0.05, 0.3, "sine", 0.18, 0.3),
                         0.12, 0.4, 5)
    for c in range(1, 11):
        m = 72 + min(c, 10) * 2
        s["combo%d" % c] = blip(mfreq(m), 0.12, "square", 0.06, 0.25, 0.22) + blip(mfreq(m + 12), 0.12, "sine", 0.05, vol=0.12)
    s["levelup"] = echo(mix([
        (0.0, arpeggio(["G4", "C5", "E5", "G5", "C6"], 0.07, 0.3, "square", 0.14, 0.3, 0.25)),
        (0.35, blip(nfreq("E6"), 0.6, "sine", 0.3, vol=0.25)),
        (0.35, blip(nfreq("C6"), 0.6, "tri", 0.3, vol=0.25)),
    ]), 0.1, 0.35, 4)
    rumble = lowpass(noise(0.7), 40)
    boom = osc(90, 0.7, "sine", freq_end=30)
    s["crumble"] = (rumble / (np.max(np.abs(rumble)) + 1e-6) * env(len(rumble), 0.01, 0.25) * 0.6
                    + boom * env(len(boom), 0.003, 0.2) * 0.6)
    s["thud"] = blip(120, 0.12, "sine", 0.05, vol=0.4, freq_end=55)
    over = mix([(i * 0.22, blip(nfreq(n), 0.4, "saw", 0.2, vol=0.3, freq_end=nfreq(n) * 0.94))
                for i, n in enumerate(["C5", "G4", "D#4", "C4"])])
    s["gameover"] = echo(lowpass(over, 5), 0.13, 0.35, 3)
    return s


# -------------------------------------------------------------------- music
# Korobeiniki (Russian folk song, public domain) - the classic Tetris theme.
MELODY_A = [("E5", 1), ("B4", .5), ("C5", .5), ("D5", 1), ("C5", .5), ("B4", .5),
            ("A4", 1), ("A4", .5), ("C5", .5), ("E5", 1), ("D5", .5), ("C5", .5),
            ("B4", 1.5), ("C5", .5), ("D5", 1), ("E5", 1),
            ("C5", 1), ("A4", 1), ("A4", 1), (None, 1),
            (None, .5), ("D5", 1), ("F5", .5), ("A5", 1), ("G5", .5), ("F5", .5),
            ("E5", 1.5), ("C5", .5), ("E5", 1), ("D5", .5), ("C5", .5),
            ("B4", 1), ("B4", .5), ("C5", .5), ("D5", 1), ("E5", 1),
            ("C5", 1), ("A4", 1), ("A4", 1), (None, 1)]
MELODY_B = [("E5", 2), ("C5", 2), ("D5", 2), ("B4", 2), ("C5", 2), ("A4", 2), ("G#4", 2), ("B4", 2),
            ("E5", 2), ("C5", 2), ("D5", 2), ("B4", 2), ("C5", 1), ("E5", 1), ("A5", 2), ("G#5", 4)]
BASS_A = [40, 45, 40, 45, 38, 36, 40, 45]
BASS_B = [45, 40, 45, 40, 45, 40, 45, 40]


def _lead(freq, dur):
    y = (0.6 * osc(freq, dur, "square", duty=0.25, vib=0.004 if dur > 0.5 else 0.0)
         + 0.3 * osc(freq * 1.004, dur, "square", duty=0.5)
         + 0.25 * osc(freq * 2, dur, "tri"))
    t = np.arange(len(y)) / SR
    e = np.minimum(1.0, t / 0.006) * (0.55 + 0.45 * np.exp(-t / 0.09))
    e *= np.clip((dur * 0.92 - t) / 0.03, 0.0, 1.0)
    return lowpass(y * e, 3)


def build_music(bpm=144):
    beat = 60.0 / bpm
    sections = [(MELODY_A, BASS_A), (MELODY_A, BASS_A), (MELODY_B, BASS_B), (MELODY_A, BASS_A)]
    total_beats = 32 * len(sections)
    n = int(total_beats * beat * SR) + SR
    lead = np.zeros(n)
    bass = np.zeros(n)
    drums = np.zeros(n)

    def put(buf, start, y):
        i = int(start * SR)
        j = min(n, i + len(y))
        buf[i:j] += y[:j - i]

    kick = osc(115, 0.2, "sine", freq_end=40)
    kick *= env(len(kick), 0.002, 0.07)
    snare = lowpass(noise(0.14), 2) * 0.7 + osc(190, 0.14, "tri") * 0.35
    snare *= env(len(snare), 0.001, 0.045)
    hat = highpass(noise(0.04), 3) * env(int(SR * 0.04), 0.001, 0.012)

    pos = 0.0
    for sec_i, (melody, roots) in enumerate(sections):
        b = pos
        for name, beats in melody:
            if name:
                put(lead, b * beat, _lead(nfreq(name), beats * beat))
            b += beats
        for bar, root in enumerate(roots):
            for e8 in range(8):
                start = (pos + bar * 4 + e8 * 0.5) * beat
                m = root + (12 if e8 % 2 else 0)
                y = osc(mfreq(m), beat * 0.48, "tri") * 0.8 + osc(mfreq(m), beat * 0.48, "square", duty=0.5) * 0.12
                put(bass, start, y * env(len(y), 0.003, 0.12))
                if e8 in (0, 4) or (sec_i != 2 and e8 == 5 and bar % 2):
                    put(drums, start, kick)
                if e8 in (2, 6):
                    put(drums, start, snare * 0.6)
                put(drums, start, hat * (0.25 if e8 % 2 else 0.15))
        pos += 32

    out = lead * 0.30 + bass * 0.28 + drums * 0.35
    out = out[:int(total_beats * beat * SR)]
    return out / (np.max(np.abs(out)) + 1e-6) * 0.9


# ----------------------------------------------------------------- playback
class Audio:
    def __init__(self, enabled=True):
        self.ok = False
        self.sfx_on = enabled
        self.music_on = enabled
        self.sounds = {}
        self.music = None
        self.music_channel = None
        self._soft_t = 0.0
        if not enabled:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(44100, -16, 2, 512)
            global SR
            SR, _fmt, self.channels = pygame.mixer.get_init()
            pygame.mixer.set_num_channels(32)
            pygame.mixer.set_reserved(1)
            self.sounds = {k: self._make(v) for k, v in build_sfx().items()}
            self.music = self._make(build_music(), 1.0)
            self.music_channel = pygame.mixer.Channel(0)
            self.ok = True
        except Exception as exc:        # no audio device etc.
            print("[audio] disabled:", exc)

    def _make(self, y, vol=1.0):
        a = (np.clip(y * vol, -1, 1) * 32767).astype(np.int16)
        if self.channels > 1:
            a = np.repeat(a[:, None], self.channels, axis=1)
        return pygame.sndarray.make_sound(np.ascontiguousarray(a))

    def play(self, name, vol=1.0, pan=0.0):
        if not (self.ok and self.sfx_on):
            return
        snd = self.sounds.get(name)
        if snd is None:
            return
        ch = snd.play()
        if ch is not None:
            left = vol * min(1.0, 1.0 - pan)
            right = vol * min(1.0, 1.0 + pan)
            ch.set_volume(left, right)

    # music ---------------------------------------------------------------
    def start_music(self):
        if self.ok and self.music_on and self.music_channel:
            self.music_channel.play(self.music, loops=-1, fade_ms=400)
            self.music_channel.set_volume(0.42)

    def stop_music(self, fade_ms=300):
        if self.ok and self.music_channel:
            self.music_channel.fadeout(fade_ms)

    def pause_music(self, paused):
        if self.ok and self.music_channel:
            if paused:
                self.music_channel.pause()
            else:
                self.music_channel.unpause()

    def toggle_music(self, playing_now):
        self.music_on = not self.music_on
        if not self.music_on:
            self.stop_music(150)
        elif playing_now:
            self.start_music()

    def toggle_sfx(self):
        self.sfx_on = not self.sfx_on

    # game event hooks -------------------------------------------------------
    def on_event(self, ev, now):
        t = ev["type"]
        if t == "move":
            self.play("move", 0.8)
        elif t == "rotate":
            self.play("rotate", 0.8)
        elif t == "soft_drop":
            if now - self._soft_t > 0.045:
                self._soft_t = now
                self.play("soft", 0.7)
        elif t == "hard_drop":
            xs = [x for x, _ in ev["cells"]]
            self.play("hard", 1.0, pan=(sum(xs) / len(xs) - 4.5) / 12)
        elif t == "lock":
            if not ev.get("hard"):
                self.play("lock", 0.9)
        elif t == "clear":
            n = ev["lines"]
            if ev["perfect"]:
                self.play("allclear")
            if ev["tspin"]:
                self.play("tspin")
            elif n:
                self.play("clear%d" % n)
            if ev["combo"] > 0:
                self.play("combo%d" % min(10, ev["combo"]), 0.8)
        elif t == "level_up":
            self.play("levelup")
        elif t == "hold":
            self.play("hold", 0.8)
        elif t == "hold_fail":
            self.play("bump", 0.8)
        elif t == "game_over":
            self.stop_music(600)
            self.play("gameover")
