"""
effects.py — Motor de efectos de color
Modos de TRANSICIÓN: cómo evoluciona el color con el tiempo/audio.
La velocidad del slider sólo afecta a los cambios de color, nunca a los flashes.
"""

import math
import time
import random


def rgb_to_hsv(r, g, b):
    r, g, b = r/255, g/255, b/255
    mx = max(r,g,b); mn = min(r,g,b); df = mx-mn
    if mx == 0: return 0.0, 0.0, 0.0
    s = df/mx; v = mx
    if df == 0: return 0.0, s, v
    if mx == r:   h = (g-b)/df % 6
    elif mx == g: h = (b-r)/df + 2
    else:         h = (r-g)/df + 4
    return h/6, s, v


def hsv_to_rgb(h, s, v):
    if s == 0:
        c = int(v*255); return c, c, c
    i = int(h*6) % 6; f = h*6 - int(h*6)
    p = v*(1-s); q = v*(1-f*s); t = v*(1-(1-f)*s)
    rv,gv,bv = [(v,t,p),(q,v,p),(p,v,t),(p,q,v),(t,p,v),(v,p,q)][i]
    return int(rv*255), int(gv*255), int(bv*255)


def neon(rgb, level):
    h, s, v = rgb_to_hsv(*rgb)
    return hsv_to_rgb(h, min(1.0, 0.80+level*0.20), min(1.0, level**0.65))


def clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(v)))


PALETTES = {
    "NEON":    {"bass":(255,0,80),    "mid":(0,255,100),   "high":(0,180,255)},
    "FUEGO":   {"bass":(255,50,0),    "mid":(255,160,0),   "high":(255,240,0)},
    "OCÉANO":  {"bass":(0,20,255),    "mid":(0,160,255),   "high":(0,255,220)},
    "PÚRPURA": {"bass":(180,0,255),   "mid":(255,0,180),   "high":(80,0,255)},
    "MATRIX":  {"bass":(0,180,0),     "mid":(0,255,100),   "high":(0,255,200)},
    "CYBER":   {"bass":(255,0,120),   "mid":(255,200,0),   "high":(0,255,255)},
    "BLANCO":  {"bass":(255,255,255), "mid":(200,200,255), "high":(180,220,255)},
}

# Modos de TRANSICIÓN — cómo evoluciona el color con el tiempo
# La velocidad del slider controla solo estos modos, nunca los flashes/strobo
TRANSITION_MODES = {
    "ESTÁTICO":   "Color fijo reactivo al audio",
    "ARCOÍRIS":   "Ciclo de hue continuo al BPM",
    "PULSO":      "Flash en cada beat, decae suavemente",
    "BREATHING":  "Respiración sinusoidal constante",
    "ONDA":       "Hue ondula suavemente con los medios",
    "GLITCH":     "Saltos de hue al beat",
    "CHROMATIC":  "Cada banda cicla hue independiente",
    "ALEATORIO":  "Cambia de modo y color dinámicamente con la música",
}

_RANDOM_POOL = ["ESTÁTICO","ARCOÍRIS","PULSO","BREATHING","ONDA","GLITCH","CHROMATIC"]


class EffectsEngine:
    def __init__(self, audio):
        self.audio       = audio
        self.color_bass  = (255, 0, 80)
        self.color_mid   = (0, 255, 100)
        self.color_high  = (0, 180, 255)
        self.transition  = "ESTÁTICO"
        # speed: multiplica la velocidad de cambio de COLOR (no de flashes)
        self.speed       = 1.0
        self.sync_bpm    = True
        self.intensity   = 1.0
        self.strobe_on   = False
        self.blackout    = False   # pantalla negra total (ignora todo)
        self.manual_color: tuple | None = None  # color fijo manual (ignora audio)

        # Estado interno
        self._t              = 0.0
        self._last_t         = time.time()
        self._hue_bass       = 0.0
        self._hue_mid        = 0.33
        self._hue_high       = 0.66
        self._rainbow_hue    = 0.0
        self._glitch_hue     = 0.0
        self._glitch_timer   = 0.0
        self._onda_phase     = 0.0
        self._strobe_on      = False
        self._strobe_t       = 0.0
        self._auto_strobe_on = False
        self._auto_strobe_t  = 0.0

        # Modo ALEATORIO
        self._rand_mode        = "ESTÁTICO"
        self._rand_next_t      = time.time() + 8.0
        self._rand_palette_t   = time.time() + 20.0
        self._rand_energy_buf  = []

    # ── Tick de tiempo interno ────────────────────────────────────────────────

    def _tick(self, a):
        now = time.time()
        dt  = min(now - self._last_t, 0.05)
        self._last_t = now
        bpm = max(a["bpm"], 60.0)

        # Velocidad de color: speed afecta al hue, no a los beats/flashes
        if self.sync_bpm and bpm > 0:
            hue_hz = (bpm / 60.0) / (8.0 / self.speed)
        else:
            hue_hz = 0.08 * self.speed

        self._rainbow_hue = (self._rainbow_hue + dt * hue_hz) % 1.0
        self._onda_phase  = (self._onda_phase  + dt * hue_hz * 0.5) % 1.0
        self._t          += dt * self.speed   # tiempo de color (afectado por speed)

        self._hue_bass  = (self._hue_bass  + dt * hue_hz * 1.0) % 1.0
        self._hue_mid   = (self._hue_mid   + dt * hue_hz * 1.3) % 1.0
        self._hue_high  = (self._hue_high  + dt * hue_hz * 1.7) % 1.0

        if a["beat"] > 0.8 and now - self._glitch_timer > 0.15:
            self._glitch_hue   = (self._glitch_hue + 0.15 + a["overall"]*0.3) % 1.0
            self._glitch_timer = now

        return dt

    # ── Modo ALEATORIO ────────────────────────────────────────────────────────

    def _update_random(self, a, now):
        """
        Cambia de modo y colores en función de la energía de la música.
        - Más energía → cambios más frecuentes
        - En drop → paleta aleatoria también
        - Cada X segundos (adaptado al BPM) cambia el modo de transición
        """
        self._rand_energy_buf.append(a["overall"])
        if len(self._rand_energy_buf) > 60:
            self._rand_energy_buf.pop(0)

        avg_energy = sum(self._rand_energy_buf) / max(1, len(self._rand_energy_buf))
        # Intervalo de cambio: entre 4s (mucha energía) y 15s (poca)
        interval = max(4.0, 15.0 - avg_energy * 11.0)

        if now >= self._rand_next_t:
            # Cambiar modo de transición, ponderado por lo que encaja con la música
            weights = {
                "ARCOÍRIS":  0.5 + a["overall"] * 0.5,
                "PULSO":     0.3 + a["beat"] * 0.7,
                "BREATHING": 0.5 + (1 - a["overall"]) * 0.4,
                "ONDA":      0.4 + a["mid"] * 0.5,
                "GLITCH":    0.2 + a["high"] * 0.6,
                "CHROMATIC": 0.3 + a["overall"] * 0.4,
                "ESTÁTICO":  0.3,
            }
            modes = list(weights.keys())
            ws    = [weights[m] for m in modes]
            total = sum(ws)
            r     = random.random() * total
            cumul = 0
            for mode, w in zip(modes, ws):
                cumul += w
                if r <= cumul:
                    self._rand_mode = mode
                    break

            self._rand_next_t = now + interval

        # Cambio de paleta de colores cada ~20s o en drop
        if now >= self._rand_palette_t or (a["drop"] and random.random() < 0.03):
            palette = random.choice(list(PALETTES.values()))
            self.color_bass  = palette["bass"]
            self.color_mid   = palette["mid"]
            self.color_high  = palette["high"]
            self._rand_palette_t = now + random.uniform(15.0, 30.0)

    # ── Aplicar transición ────────────────────────────────────────────────────

    def _apply_transition(self, base_rgb, level, mode, band_hue=None):
        if mode == "ARCOÍRIS":
            h = self._rainbow_hue
            r, g, b = hsv_to_rgb(h, 1.0, min(1.0, 0.2 + level*0.8))

        elif mode == "PULSO":
            pulse = self.audio.snapshot()["beat"] ** 1.3
            r, g, b = neon(base_rgb, max(0.04, pulse))

        elif mode == "BREATHING":
            breath = (math.sin(self._t * 1.5) + 1) / 2
            r, g, b = neon(base_rgb, 0.08 + breath*0.92)

        elif mode == "ONDA":
            wave = (math.sin(self._onda_phase * math.tau + level*2) + 1) / 2
            h, s, v = rgb_to_hsv(*base_rgb)
            h2 = (h + wave*0.25) % 1.0
            r, g, b = hsv_to_rgb(h2, min(1.0, s+0.1), min(1.0, 0.15+level*0.85))

        elif mode == "GLITCH":
            h = (self._glitch_hue + (band_hue or 0)) % 1.0
            r, g, b = hsv_to_rgb(h, 1.0, min(1.0, 0.1+level*0.9))

        elif mode == "CHROMATIC":
            h = band_hue if band_hue is not None else self._rainbow_hue
            r, g, b = hsv_to_rgb(h, 1.0, min(1.0, 0.15+level*0.85))

        else:  # ESTÁTICO
            r, g, b = neon(base_rgb, level)

        return r, g, b

    def _apply_intensity(self, rgb):
        i = self.intensity
        return (clamp(rgb[0]*i), clamp(rgb[1]*i), clamp(rgb[2]*i))

    # ── API pública ───────────────────────────────────────────────────────────

    def get_color(self, role: str) -> tuple:
        # Blackout total — prioridad absoluta
        if self.blackout:
            return (0, 0, 0)

        a   = self.audio.snapshot()
        now = time.time()
        self._tick(a)

        # Color manual fijo — ignora el audio, solo aplica intensidad
        if self.manual_color is not None:
            return self._apply_intensity(self.manual_color)

        # Actualizar modo aleatorio si está activo
        if self.transition == "ALEATORIO":
            self._update_random(a, now)
            active_mode = self._rand_mode
        else:
            active_mode = self.transition

        # Strobo manual (velocidad de flash NO afectada por speed)
        if self.strobe_on:
            bpm  = max(a["bpm"], 80.0)
            rate = max(0.04, 60.0 / bpm * 0.5)
            if now - self._strobe_t > rate:
                self._strobe_t  = now
                self._strobe_on = not self._strobe_on
            v = clamp(160 + a["bass"]*95) if self._strobe_on else 0
            return self._apply_intensity((v, v, v))

        # Strobo auto en drop/pre-drop (flash speed NO afectada por speed)
        if a["drop"] or a["pre_drop"]:
            bpm  = max(a["bpm"], 60.0)
            rate = (60.0 / bpm) * (0.5 if a["drop"] else 1.0)
            rate = max(0.04, rate)
            if now - self._auto_strobe_t > rate:
                self._auto_strobe_t  = now
                self._auto_strobe_on = not self._auto_strobe_on
            v = clamp(180 + a["bass"]*75) if self._auto_strobe_on else 0
            return self._apply_intensity((v, v, v))

        if role == "bass":
            rgb = self._apply_transition(self.color_bass,  a["bass"],  active_mode, self._hue_bass)
        elif role == "mid":
            rgb = self._apply_transition(self.color_mid,   a["mid"],   active_mode, self._hue_mid)
        elif role == "high":
            rgb = self._apply_transition(self.color_high,  a["high"],  active_mode, self._hue_high)
        elif role == "strobe":
            v = clamp(a["beat"]**1.5 * 255)
            return self._apply_intensity((v, v, v))
        else:  # combo / bass_high / mid_combo
            bl = a["bass"]**1.2; ml = a["mid"]**1.2; hl = a["high"]**1.2
            tot = bl + ml + hl + 1e-9
            br,bg_,bb = self.color_bass
            mr,mg,mb  = self.color_mid
            hr,hg,hb  = self.color_high
            r = (br*bl + mr*ml + hr*hl) / tot
            g = (bg_*bl + mg*ml + hg*hl) / tot
            b = (bb*bl + mb*ml + hb*hl) / tot
            scale = 0.1 + a["overall"]*0.9
            base  = (clamp(r*scale*1.6), clamp(g*scale*1.6), clamp(b*scale*1.6))
            rgb   = self._apply_transition(base, a["overall"], active_mode, self._rainbow_hue)

        return self._apply_intensity(rgb)

    def apply_palette(self, name: str):
        p = PALETTES.get(name, PALETTES["NEON"])
        self.color_bass  = p["bass"]
        self.color_mid   = p["mid"]
        self.color_high  = p["high"]
