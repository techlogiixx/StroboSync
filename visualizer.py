"""
visualizer.py — Motor de visualización pygame

Arquitectura de sincronización:
  - VisualEngine corre en el proceso PRINCIPAL (strobosync.py)
    y calcula todo el estado de las formas cada frame.
  - run_window corre en procesos HIJOS (uno por pantalla)
    y solo recibe el estado serializado y lo renderiza.
  - Resultado: todas las pantallas muestran exactamente el mismo efecto.
"""

import pygame
import numpy as np
import math
import random
import time
import sys
import multiprocessing as mp

# Compatibilidad pygame / pygame-ce
try:
    import pygame
except ImportError:
    try:
        import pygame_ce as pygame
        import sys as _sys
        _sys.modules["pygame"] = pygame
    except ImportError:
        raise ImportError("Instala pygame: pip install pygame-ce")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def hsv_to_rgb(h, s, v):
    if s == 0:
        c = int(v * 255); return c, c, c
    i = int(h * 6) % 6; f = h * 6 - int(h * 6)
    p = v*(1-s); q = v*(1-f*s); t = v*(1-(1-f)*s)
    rv,gv,bv = [(v,t,p),(q,v,p),(p,v,t),(p,q,v),(t,p,v),(v,p,q)][i]
    return int(rv*255), int(gv*255), int(bv*255)


def draw_glow(surf, x, y, radius, color, alpha=180):
    if radius <= 0: return
    for i in range(4, 0, -1):
        r = radius * i
        a = max(0, min(255, alpha // (i * 2)))
        gs = pygame.Surface((r*2+2, r*2+2), pygame.SRCALPHA)
        pygame.draw.circle(gs, (*color, a), (r+1, r+1), r)
        surf.blit(gs, (x-r-1, y-r-1), special_flags=pygame.BLEND_ADD)


# ══════════════════════════════════════════════════════════════════════════════
#  PARTÍCULAS — se calculan en el proceso principal y se serializan
# ══════════════════════════════════════════════════════════════════════════════

class Particle:
    __slots__ = ['x','y','vx','vy','life','max_life','size','color','trail']

    def __init__(self, x, y, color, speed_mult=1.0, on_beat=False):
        angle = random.uniform(0, math.tau)
        speed = random.uniform(0.5, 3.0) * speed_mult * (2.0 if on_beat else 1.0)
        self.x        = float(x)
        self.y        = float(y)
        self.vx       = math.cos(angle) * speed
        self.vy       = math.sin(angle) * speed
        self.max_life = random.uniform(0.8, 2.5)
        self.life     = self.max_life
        self.size     = random.uniform(2, 6) * (1.5 if on_beat else 1.0)
        self.color    = color
        self.trail    = []

    def update(self, dt, audio):
        self.trail.append((self.x, self.y))
        if len(self.trail) > 8: self.trail.pop(0)
        boost = 1.0 + audio["bass"] * 2.5
        self.x  += self.vx * boost
        self.y  += self.vy * boost
        self.vy += 0.02
        self.vx *= 0.99
        self.life -= dt
        return self.life > 0

    def to_dict(self):
        return {"x":self.x,"y":self.y,"vx":self.vx,"vy":self.vy,
                "life":self.life,"max_life":self.max_life,
                "size":self.size,"color":self.color,"trail":list(self.trail)}

    @classmethod
    def from_dict(cls, d):
        p = cls.__new__(cls)
        for k,v in d.items(): setattr(p, k, v)
        return p

    def draw(self, surf):
        alpha = max(0.0, self.life / self.max_life)
        r,g,b = self.color
        col = (min(255,int(r*alpha)), min(255,int(g*alpha)), min(255,int(b*alpha)))
        for k,(tx,ty) in enumerate(self.trail):
            ta = alpha * (k/max(len(self.trail),1)) * 0.35
            tc = (min(255,int(r*ta)), min(255,int(g*ta)), min(255,int(b*ta)))
            sz = max(1, int(self.size*0.4*k/max(len(self.trail),1)))
            if sum(tc) > 0:
                pygame.draw.circle(surf, tc, (int(tx),int(ty)), sz)
        sz = max(1, int(self.size * alpha))
        if sum(col) > 0:
            pygame.draw.circle(surf, col, (int(self.x),int(self.y)), sz)
            if alpha > 0.4:
                gs = pygame.Surface((sz*4+2,sz*4+2), pygame.SRCALPHA)
                pygame.draw.circle(gs, (*col,50), (sz*2+1,sz*2+1), sz*2)
                surf.blit(gs, (int(self.x)-sz*2-1, int(self.y)-sz*2-1),
                          special_flags=pygame.BLEND_ADD)


# ══════════════════════════════════════════════════════════════════════════════
#  VISUAL ENGINE — corre en el proceso PRINCIPAL, calcula estado compartido
# ══════════════════════════════════════════════════════════════════════════════

VIS_MODES = ["BARS", "WAVE", "TUNNEL", "PARTICLES", "STARBURST", "SPECTRAL"]


class VisualEngine:
    """
    Calcula el estado completo del visualizador cada frame en el proceso principal.
    Lo serializa a un dict plano que se envía por queue a todas las ventanas.
    """

    def __init__(self):
        self.mode    = "STARBURST"
        self._t      = 0.0
        self._last_t = time.time()

        # BARS
        self._bars_peaks  = None
        self._bars_pvels  = None
        self._bars_smooth = None

        # TUNNEL
        self._tunnel_angle = 0.0

        # PARTICLES / STARBURST / SPECTRAL
        self._particles   : list = []
        self._rays        : list = []   # lista de dicts
        self._spawn_t     = 0.0
        self._ray_t       = 0.0

        # STARBURST
        self._star_angles  = [i * math.tau / 16 for i in range(16)]
        self._star_lengths = [random.uniform(0.3, 1.0) for _ in range(16)]

        # SPECTRAL
        self._spec_history = []
        self._spec_spawn_t = 0.0

        # WAVE
        # (sin estado, solo usa _t)

    def next_mode(self):
        idx = VIS_MODES.index(self.mode) if self.mode in VIS_MODES else 0
        self.mode = VIS_MODES[(idx + 1) % len(VIS_MODES)]
        # Reset estado específico del nuevo modo
        self._particles.clear()
        self._rays.clear()
        self._spec_history.clear()
        self._bars_peaks = None
        self._tunnel_angle = 0.0
        self._star_angles  = [i * math.tau / 16 for i in range(16)]
        self._star_lengths = [random.uniform(0.3, 1.0) for _ in range(16)]

    def set_mode(self, name):
        if name != self.mode:
            self.mode = name
            self._particles.clear()
            self._rays.clear()
            self._spec_history.clear()
            self._bars_peaks = None

    def update(self, audio: dict):
        """Actualiza el estado interno y devuelve un dict serializable para las ventanas."""
        now = time.time()
        dt  = min(now - self._last_t, 0.05)
        self._last_t = now
        self._t += dt

        mode = self.mode
        state = {"vis_t": self._t, "vis_mode": mode}

        if mode == "BARS":
            state.update(self._update_bars(dt, audio))
        elif mode == "WAVE":
            pass   # solo necesita _t
        elif mode == "TUNNEL":
            self._tunnel_angle += dt * (0.4 + audio["bass"] * 2.5)
            state["tunnel_angle"] = self._tunnel_angle
        elif mode == "PARTICLES":
            state.update(self._update_particles(dt, audio, now))
        elif mode == "STARBURST":
            state.update(self._update_starburst(dt, audio, now))
        elif mode == "SPECTRAL":
            state.update(self._update_spectral(dt, audio, now))

        return state

    # ── Updaters por modo ─────────────────────────────────────────────────────

    def _update_bars(self, dt, audio):
        n = 48
        bass_end = int(n * 0.20); mid_end = int(n * 0.60)
        raw = np.zeros(n)
        for i in range(n):
            if i < bass_end:
                t = i / max(bass_end, 1)
                raw[i] = audio["bass"] * (0.7 + 0.3*math.sin(t*math.pi))
            elif i < mid_end:
                t = (i-bass_end) / max(mid_end-bass_end, 1)
                raw[i] = audio["mid"] * (0.6 + 0.4*math.sin(t*math.pi*0.8))
            else:
                t = (i-mid_end) / max(n-mid_end, 1)
                raw[i] = audio["high"] * (0.8 - t*0.5)
        raw[:bass_end] *= (1 + audio["beat"] * 0.5)

        if self._bars_smooth is None or len(self._bars_smooth) != n:
            self._bars_smooth = raw.copy()
        else:
            self._bars_smooth = self._bars_smooth*0.5 + raw*0.5

        if self._bars_peaks is None or len(self._bars_peaks) != n:
            self._bars_peaks = self._bars_smooth.copy()
            self._bars_pvels = np.zeros(n)
        else:
            for i in range(n):
                if self._bars_smooth[i] > self._bars_peaks[i]:
                    self._bars_peaks[i] = self._bars_smooth[i]
                    self._bars_pvels[i] = 0.0
                else:
                    self._bars_pvels[i] = min(0.015, self._bars_pvels[i]+0.0008)
                    self._bars_peaks[i] = max(self._bars_smooth[i], self._bars_peaks[i]-self._bars_pvels[i])

        return {"bars_vals": self._bars_smooth.tolist(),
                "bars_peaks": self._bars_peaks.tolist()}

    def _update_particles(self, dt, audio, now):
        rate = max(0.03, 0.20 - audio["overall"]*0.16)
        if audio["drop"]: rate *= 0.2
        on_beat = audio["beat"] > 0.7

        if now - self._spawn_t > rate and len(self._particles) < 300:
            if on_beat:
                for _ in range(4):
                    self._particles.append(Particle(0, 0, (255,255,255), 2.0, True))
            cols = [(255,0,80),(0,255,100),(0,180,255)]
            edge = random.choice(["t","b","l","r"])
            px,py = (random.randint(0,1920), 0 if edge=="t" else
                     random.randint(0,1920) if edge=="b" else
                     (0 if edge=="l" else 1920)), random.randint(0,1080)
            self._particles.append(Particle(px, py, random.choice(cols), 0.8))
            self._spawn_t = now

        # Rays
        ray_rate = max(0.15, 0.6 - audio["high"]*0.4)
        if now - self._ray_t > ray_rate and len(self._rays) < 20:
            col = random.choice([(255,0,80),(0,255,100),(0,180,255)])
            self._rays.append({
                "angle": random.uniform(0, math.tau),
                "speed": random.uniform(0.3, 1.2),
                "length": random.uniform(0.3, 1.0),
                "life": random.uniform(0.3, 1.2),
                "max_life": 0.0,
                "width": random.randint(1,3),
                "color": col,
            })
            self._rays[-1]["max_life"] = self._rays[-1]["life"]
            self._ray_t = now

        self._particles = [p for p in self._particles if p.update(dt, audio)]
        for r in self._rays:
            r["angle"] = (r["angle"] + r["speed"] * dt * (1 + audio["mid"]*3)) % math.tau
            r["life"]  = r["life"] - dt
        self._rays = [r for r in self._rays if r["life"] > 0]

        return {"particles": [p.to_dict() for p in self._particles],
                "rays": list(self._rays)}

    def _update_starburst(self, dt, audio, now):
        rot = dt * (0.2 + audio["mid"]*1.5)
        self._star_angles = [(a+rot) % math.tau for a in self._star_angles]
        for i in range(len(self._star_lengths)):
            target = 0.4 + audio["bass"]*0.6
            self._star_lengths[i] = self._star_lengths[i]*0.85 + target*0.15

        rate = max(0.05, 0.3 - audio["overall"]*0.25)
        if now - self._spawn_t > rate and len(self._particles) < 200:
            for i,(angle,length) in enumerate(zip(self._star_angles, self._star_lengths)):
                if random.random() < 0.3:
                    ex = math.cos(angle)*2000*length   # relativo al centro, se escala en render
                    ey = math.sin(angle)*2000*length
                    col = (0,255,100) if i%3==0 else (255,200,0) if i%3==1 else (0,200,255)
                    self._particles.append(Particle(ex, ey, col, 0.5, audio["beat"]>0.6))
            self._spawn_t = now

        self._particles = [p for p in self._particles if p.update(dt, audio)]

        return {"star_angles":  list(self._star_angles),
                "star_lengths": list(self._star_lengths),
                "particles":    [p.to_dict() for p in self._particles]}

    def _update_spectral(self, dt, audio, now):
        snap = (audio["bass"], audio["mid"], audio["high"])
        self._spec_history.append(snap)
        if len(self._spec_history) > 60:
            self._spec_history.pop(0)

        rate = max(0.08, 0.4 - audio["bass"]*0.3)
        if now - self._spec_spawn_t > rate and len(self._particles) < 200:
            for k in range(8):
                angle  = k * math.tau/8 + self._t*0.5
                r_mod  = 0.25 * (0.8 + audio["bass"]*0.5)  # fracción del min(W,H)
                px = math.cos(angle) * r_mod   # relativo, se escala en render
                py = math.sin(angle) * r_mod * 0.65
                cols = [(255,0,80),(0,255,100),(0,180,255)]
                self._particles.append(Particle(px, py, cols[k%3], 0.6, audio["beat"]>0.6))
            self._spec_spawn_t = now

        self._particles = [p for p in self._particles if p.update(dt, audio)]

        return {"spec_history": list(self._spec_history),
                "particles":    [p.to_dict() for p in self._particles]}


# ══════════════════════════════════════════════════════════════════════════════
#  RENDERER — corre en el proceso HIJO, solo dibuja lo que recibe
# ══════════════════════════════════════════════════════════════════════════════

class Renderer:
    """Recibe el estado serializado y lo renderiza en la surface dada."""

    def draw(self, surf, vis_state: dict, audio: dict, colors: dict):
        mode = vis_state.get("vis_mode", "BARS")
        W, H = surf.get_size()

        if mode == "BARS":      self._draw_bars(surf, vis_state, audio, colors, W, H)
        elif mode == "WAVE":    self._draw_wave(surf, vis_state, audio, colors, W, H)
        elif mode == "TUNNEL":  self._draw_tunnel(surf, vis_state, audio, colors, W, H)
        elif mode == "PARTICLES": self._draw_particles(surf, vis_state, audio, colors, W, H)
        elif mode == "STARBURST": self._draw_starburst(surf, vis_state, audio, colors, W, H)
        elif mode == "SPECTRAL":  self._draw_spectral(surf, vis_state, audio, colors, W, H)

    def _draw_bars(self, surf, vs, audio, colors, W, H):
        vals  = vs.get("bars_vals", [])
        peaks = vs.get("bars_peaks", [])
        n = len(vals)
        if n == 0: return
        bw = W / n; pad = max(1, bw*0.1)
        bass_end = int(n*0.20); mid_end = int(n*0.60)

        for i in range(n):
            lv = vals[i]
            h_px = max(2, int(lv * H * 0.88))
            x0 = int(i*bw+pad); x1 = int((i+1)*bw-pad)
            w = max(1, x1-x0)

            col = colors["bass"] if i<bass_end else colors["mid"] if i<mid_end else colors["high"]
            bright = 0.3 + lv*0.7
            draw_col = tuple(min(255,int(c*bright)) for c in col)
            if sum(draw_col) < 5: continue

            pygame.draw.rect(surf, draw_col, (x0, H-h_px, w, h_px))
            # Reflejo
            ref_col = tuple(c//5 for c in draw_col)
            if sum(ref_col) > 0:
                pygame.draw.rect(surf, ref_col, (x0, H, w, max(1,h_px//5)))
            # Pico
            if i < len(peaks):
                py = H - int(peaks[i]*H*0.88)
                pygame.draw.rect(surf, draw_col, (x0, py-2, w, 3))
            # Bloom
            if lv > 0.6:
                cx = (x0+x1)//2
                bl = pygame.Surface((20, h_px), pygame.SRCALPHA)
                bl.fill((*tuple(min(255,c//2) for c in draw_col), 40))
                surf.blit(bl, (cx-10, H-h_px), special_flags=pygame.BLEND_ADD)

    def _draw_wave(self, surf, vs, audio, colors, W, H):
        t  = vs.get("vis_t", 0.0)
        cx = W//2; cy = H//2; n = 300
        layers = [
            (colors["bass"],  audio["bass"],  2.0,  1.5, 0.0),
            (colors["mid"],   audio["mid"],   5.0,  2.8, 0.5),
            (colors["high"],  audio["high"],  11.0, 5.0, 1.0),
        ]
        for color, level, freq, speed, phase_off in layers:
            if level < 0.02: continue
            amp = H * 0.35 * (0.15 + level*0.85)
            pts = []
            for i in range(n):
                px = int(i*W/(n-1))
                p  = i/(n-1)*math.tau + phase_off
                v  = (level*math.sin(p*freq + t*speed)*0.6 +
                      audio["overall"]*math.sin(p*(freq*0.5)+t*(speed*1.3))*0.25 +
                      audio["beat"]*math.sin(p*2+t*0.8)*0.15)
                pts.append((px, int(cy + v*amp)))
            if len(pts) >= 2:
                bright = 0.3 + level*0.7
                col = tuple(min(255,int(c*bright)) for c in color)
                if sum(col) < 5: continue
                lw = max(1, int(1+level*3))
                pygame.draw.lines(surf, col, False, pts, lw)
                glow = tuple(c//3 for c in col)
                if sum(glow) > 0:
                    pygame.draw.lines(surf, glow, False, pts, lw+2)
        r = int(6 + audio["beat"]*50)
        if r > 0 and audio["beat"] > 0.05:
            draw_glow(surf, cx, cy, r, colors["bass"], int(120*audio["beat"]))

    def _draw_tunnel(self, surf, vs, audio, colors, W, H):
        cx, cy = W//2, H//2
        angle_base = vs.get("tunnel_angle", 0.0)
        t  = vs.get("vis_t", 0.0)
        n_rings = 20; n_pts = 80
        max_r = min(W,H)*0.50

        for ring in range(n_rings, 0, -1):
            frac    = ring/n_rings
            r_rad   = max_r*frac
            distort = (audio["bass"]*0.35 + audio["mid"]*0.15)*r_rad*frac
            pts = []
            for k in range(n_pts+1):
                angle = k/n_pts*math.tau + angle_base*(1-frac*0.7)
                noise = distort*math.sin(angle*3 + t*2.0 + ring*0.5)
                pts.append((int(cx+(r_rad+noise)*math.cos(angle)),
                            int(cy+(r_rad+noise)*math.sin(angle)*0.65)))

            col = colors["bass"] if frac<0.35 else colors["mid"] if frac<0.65 else colors["high"]
            lv  = audio["bass"] if frac<0.35 else audio["mid"] if frac<0.65 else audio["high"]
            bright = max(0.05, lv*(1-frac*0.5))
            draw_col = tuple(min(255,int(c*bright*1.4)) for c in col)
            if sum(draw_col) < 3: continue
            lw = max(1, int(2-frac*1.5))
            pygame.draw.lines(surf, draw_col, False, pts, lw)

        sz = int(8 + audio["beat"]*35)
        col = colors["mid"]
        bright = 0.3 + audio["beat"]*0.7
        col = tuple(min(255,int(c*bright)) for c in col)
        if sum(col) > 5 and sz > 0:
            pygame.draw.line(surf, col, (cx-sz,cy),(cx+sz,cy), 2)
            pygame.draw.line(surf, col, (cx,cy-sz),(cx,cy+sz), 2)
            draw_glow(surf, cx, cy, sz//2, col, 80)

    def _draw_particles(self, surf, vs, audio, colors, W, H):
        cx, cy = W//2, H//2
        diag   = math.sqrt(W**2+H**2)*0.5

        # Rays desde el centro
        for rd in vs.get("rays", []):
            alpha  = max(0.0, rd["life"]/max(rd["max_life"],1e-9))
            bright = 0.3 + audio["high"]*0.7
            col    = rd["color"]
            final  = tuple(min(255,int(c*alpha*bright)) for c in col)
            if sum(final) < 5: continue
            length = diag * rd["length"] * (0.5+audio["bass"]*0.5)
            ex = cx + math.cos(rd["angle"])*length
            ey = cy + math.sin(rd["angle"])*length
            for k in range(3,0,-1):
                dim = tuple(c//k for c in final)
                if sum(dim) > 0:
                    pygame.draw.line(surf, dim, (cx,cy),(int(ex),int(ey)), rd["width"]*k)

        # Partículas — posiciones ya son absolutas
        for pd in vs.get("particles", []):
            Particle.from_dict(pd).draw(surf)

        r = int(4 + audio["beat"]*40 + audio["bass"]*20)
        if r > 0:
            draw_glow(surf, cx, cy, r, colors["bass"], int(100+audio["beat"]*100))

    def _draw_starburst(self, surf, vs, audio, colors, W, H):
        cx, cy = W//2, H//2
        diag   = math.sqrt(W**2+H**2)*0.5
        angles  = vs.get("star_angles",  [])
        lengths = vs.get("star_lengths", [])

        for i,(angle,length) in enumerate(zip(angles,lengths)):
            ex = cx + math.cos(angle)*diag*length
            ey = cy + math.sin(angle)*diag*length
            col = colors["bass"] if i%3==0 else colors["mid"] if i%3==1 else colors["high"]
            bright = 0.4 + audio["overall"]*0.6
            col = tuple(min(255,int(c*bright)) for c in col)
            if sum(col) < 5: continue
            for k in range(3,0,-1):
                dim = tuple(c//k for c in col)
                if sum(dim) > 0:
                    pygame.draw.line(surf, dim, (cx,cy),(int(ex),int(ey)), k*2)
            for sub in range(3):
                sa = angle+(sub-1)*0.08
                sx = cx + math.cos(sa)*diag*length*0.6
                sy = cy + math.sin(sa)*diag*length*0.6
                dim2 = tuple(c//4 for c in col)
                if sum(dim2) > 0:
                    pygame.draw.line(surf, dim2, (cx,cy),(int(sx),int(sy)), 1)

        # Partículas — posiciones relativas al centro
        for pd in vs.get("particles", []):
            p = Particle.from_dict(pd)
            p.x += cx; p.y += cy   # convertir a coordenadas de pantalla
            p.draw(surf)

        r = int(10 + audio["beat"]*30 + audio["bass"]*20)
        if r > 0:
            draw_glow(surf, cx, cy, r, colors["mid"],   int(150+audio["beat"]*105))
            draw_glow(surf, cx, cy, r//2, (255,255,255), int(100+audio["beat"]*100))

    def _draw_spectral(self, surf, vs, audio, colors, W, H):
        cx, cy   = W//2, H//2
        n_pts    = 128
        base_r   = min(W,H)*0.28
        t        = vs.get("vis_t", 0.0)
        history  = vs.get("spec_history", [])

        # Trail histórico
        for hist_i,(hb,hm,hh) in enumerate(history):
            age   = hist_i/max(len(history),1)
            alpha = age*0.15
            scale = 1.0+(1.0-age)*0.3
            pts = []
            for k in range(n_pts+1):
                angle = k/n_pts*math.tau
                lv = hb if k%3==0 else hm if k%3==1 else hh
                r  = base_r*scale*(0.7+lv*0.6)
                pts.append((int(cx+math.cos(angle)*r),
                            int(cy+math.sin(angle)*r*0.65)))
            if len(pts) >= 2:
                c = tuple(min(255,int(cv*alpha*1.5)) for cv in colors["mid"])
                if sum(c) > 2:
                    pygame.draw.lines(surf, c, True, pts, 1)

        # Espectro actual
        pts_main = []
        for k in range(n_pts+1):
            angle = k/n_pts*math.tau + t*0.2
            lv    = audio["bass"] if k%3==0 else audio["mid"] if k%3==1 else audio["high"]
            r     = base_r*(0.6+lv*0.8)
            pts_main.append((int(cx+math.cos(angle)*r),
                             int(cy+math.sin(angle)*r*0.65)))
        if len(pts_main) >= 2:
            col = colors["combo"]
            lw  = max(1, int(1+audio["overall"]*3))
            if sum(col) > 5:
                pygame.draw.lines(surf, col, True, pts_main, lw)
                dim = tuple(c//3 for c in col)
                if sum(dim) > 0:
                    pygame.draw.lines(surf, dim, True, pts_main, lw+3)

        # Partículas — posiciones relativas al min(W,H)
        scale_f = min(W,H)
        for pd in vs.get("particles", []):
            p = Particle.from_dict(pd)
            p.x = cx + p.x*scale_f
            p.y = cy + p.y*scale_f
            p.draw(surf)

        r = int(8+audio["beat"]*30)
        if r > 0:
            draw_glow(surf, cx, cy, r, colors["bass"], int(120+audio["beat"]*100))


# ══════════════════════════════════════════════════════════════════════════════
#  VENTANA FULLSCREEN — proceso hijo, solo renderiza
# ══════════════════════════════════════════════════════════════════════════════

def run_window(state_queue, monitor_x, monitor_y, monitor_w, monitor_h, window_id, key_queue=None):
    import os
    os.environ["SDL_VIDEO_WINDOW_POS"] = f"{monitor_x},{monitor_y}"

    pygame.init()
    pygame.display.set_caption(f"StroboSync {window_id+1}")
    screen   = pygame.display.set_mode((monitor_w, monitor_h), pygame.NOFRAME)
    clock    = pygame.time.Clock()
    renderer = Renderer()

    state = {}

    work_surf = pygame.Surface((monitor_w, monitor_h))
    work_surf.fill((0,0,0))
    fade_surf = pygame.Surface((monitor_w, monitor_h))
    fade_surf.fill((0,0,0))
    fade_surf.set_alpha(35)

    # Teclas que se reenvian al proceso principal para que funcionen los shortcuts
    _KEY_ACTIONS = {
        pygame.K_LEFT:   "left",   pygame.K_RIGHT: "right",
        pygame.K_UP:     "up",     pygame.K_DOWN:  "down",
        pygame.K_SPACE:  "space",  pygame.K_RETURN: "return",
        pygame.K_TAB:    "tab",    pygame.K_F1: "f1",
        pygame.K_F2:     "f2",     pygame.K_F3: "f3",
        pygame.K_F4:     "f4",     pygame.K_F5: "f5",
        pygame.K_F6:     "f6",     pygame.K_F7: "f7",
        pygame.K_F8:     "f8",
        pygame.K_p:      "p",      pygame.K_m: "m",
        pygame.K_b:      "b",      pygame.K_l: "l",
        pygame.K_PLUS:   "plus",   pygame.K_MINUS: "minus",
        pygame.K_KP_PLUS: "plus",  pygame.K_KP_MINUS: "minus",
        pygame.K_EQUALS: "plus",   pygame.K_UNDERSCORE: "minus",
        pygame.K_1: "1", pygame.K_2: "2", pygame.K_3: "3",
        pygame.K_4: "4", pygame.K_5: "5", pygame.K_6: "6",
        pygame.K_7: "7", pygame.K_8: "8", pygame.K_9: "9",
    }

    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_F11):
                    running = False
                elif key_queue is not None and event.key in _KEY_ACTIONS:
                    try:
                        key_queue.put_nowait(_KEY_ACTIONS[event.key])
                    except Exception:
                        pass

        # Vaciar queue — quedarse siempre con el frame más reciente
        while True:
            try:
                msg = state_queue.get_nowait()
            except Exception:
                break
            if msg.get("cmd") == "quit":
                running = False; break
            else:
                state.update(msg)

        if not running:
            break

        audio = {
            "bass":     state.get("bass",     0.0),
            "mid":      state.get("mid",      0.0),
            "high":     state.get("high",     0.0),
            "overall":  state.get("overall",  0.0),
            "bpm":      state.get("bpm",      0.0),
            "beat":     state.get("beat",     0.0),
            "drop":     state.get("drop",     False),
            "pre_drop": state.get("pre_drop", False),
        }
        colors = {
            "bass":  state.get("col_bass",  (255,0,80)),
            "mid":   state.get("col_mid",   (0,255,100)),
            "high":  state.get("col_high",  (0,180,255)),
            "combo": state.get("col_combo", (100,100,255)),
        }
        bg_color  = state.get("bg_color", (0,0,0))
        # vis_display viene de strobosync.py en cada frame — nunca por comando separado
        vis_mode  = state.get("vis_display", "SHAPES")
        vis_state = {k:v for k,v in state.items()
                     if k.startswith("vis_") or k in
                     ("bars_vals","bars_peaks","tunnel_angle",
                      "particles","rays","star_angles","star_lengths","spec_history")}

        if vis_mode == "LIGHTS":
            screen.fill(bg_color)

        elif vis_mode == "SHAPES":
            work_surf.blit(fade_surf, (0,0))
            renderer.draw(work_surf, vis_state, audio, colors)
            screen.blit(work_surf, (0,0))

        elif vis_mode == "BOTH":
            screen.fill(bg_color)
            renderer.draw(screen, vis_state, audio, colors)

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
