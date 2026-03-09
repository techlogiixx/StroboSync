"""
strobosync.py — Panel de control principal
Gestiona audio, efectos y ventanas de visualización.
"""

import tkinter as tk
from tkinter import colorchooser
import threading
import time
import multiprocessing as mp
import sys
import os

from audio   import AudioEngine
from effects import EffectsEngine, PALETTES, TRANSITION_MODES, hsv_to_rgb, rgb_to_hsv
from visualizer import VIS_MODES, VisualEngine, run_window

try:
    import screeninfo
    SCREENINFO = True
except ImportError:
    SCREENINFO = False


# ══════════════════════════════════════════════════════════════════════════════
#  REPARTO DE ROLES EQUITATIVO
# ══════════════════════════════════════════════════════════════════════════════

def assign_roles(n: int) -> list:
    """
    Asigna roles a n pantallas de forma que SIEMPRE se representen
    graves, medios y agudos, independientemente de cuántas pantallas haya.
    
    Con 2 pantallas: cada una mezcla todo pero con énfasis distinto.
    Con 3+: reparto directo más roles adicionales.
    """
    base = ["bass", "mid", "high", "combo", "strobe"]
    if n == 1:
        return ["combo"]
    if n == 2:
        # Ambas pantallas muestran todo, pero con énfasis diferente
        # La diferencia la harán los colores dominantes
        return ["bass_high", "mid_combo"]
    if n == 3:
        return ["bass", "mid", "high"]
    if n == 4:
        return ["bass", "mid", "high", "combo"]
    if n == 5:
        return ["bass", "mid", "high", "combo", "strobe"]
    # 6+: ciclar
    return [base[i % len(base)] for i in range(n)]


def role_color(role: str, fx: EffectsEngine) -> tuple:
    """Obtiene el color RGB para un rol dado."""
    if role == "bass":        return fx.get_color("bass")
    if role == "mid":         return fx.get_color("mid")
    if role == "high":        return fx.get_color("high")
    if role == "strobe":      return fx.get_color("strobe")
    if role == "bass_high":
        b = fx.get_color("bass")
        h = fx.get_color("high")
        return (
            min(255, (b[0]+h[0])//2 + 30),
            min(255, (b[1]+h[1])//2),
            min(255, (b[2]+h[2])//2 + 30),
        )
    if role == "mid_combo":
        m = fx.get_color("mid")
        c = fx.get_color("combo")
        return (
            min(255, (m[0]+c[0])//2),
            min(255, (m[1]+c[1])//2 + 20),
            min(255, (m[2]+c[2])//2),
        )
    return fx.get_color("combo")


# ══════════════════════════════════════════════════════════════════════════════
#  PANEL DE CONTROL
# ══════════════════════════════════════════════════════════════════════════════

class ControlPanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("StroboSync v2")
        self.root.configure(bg="#07070f")
        self.root.resizable(False, False)

        self.audio    = AudioEngine()
        self.fx       = EffectsEngine(self.audio)
        self._monitors= self._get_monitors()
        self._windows : dict[int, mp.Process] = {}   # idx → proceso
        self._queues  : dict[int, mp.Queue]   = {}   # idx → queue
        self._roles   : dict[int, str]        = {}   # idx → rol
        self._vis_mode= "SHAPES"   # LIGHTS | SHAPES | BOTH
        self._vis_name= "STARBURST"
        self._selected_band = "bass"
        self._intensity = 1.0
        self._key_queue = mp.Queue(maxsize=64)  # teclas reenviadas desde ventanas pygame
        self._palette_idx = 0                   # índice de paleta actual para P
        self._manual_mode = False               # L: modo color manual (sin audio)
        self._blackout    = False               # B: blackout total
        # VisualEngine calcula el estado de las formas en este proceso
        # y lo envía serializado a todas las ventanas → efecto idéntico en todas
        self.vis_engine = VisualEngine()
        self.vis_engine.set_mode(self._vis_name)

        self._build_ui()
        self._bind_keys()
        self.audio.start()
        self._push_loop()   # hilo que envía estado a las ventanas
        self._update_ui()
        self._poll_key_queue()  # procesa teclas reenviadas desde ventanas pygame
        self._focus_loop()  # mantiene shortcuts activos aunque las pantallas estén abiertas
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Dar foco a la ventana principal para que los shortcuts funcionen
        # desde el primer momento sin necesidad de hacer clic
        self.root.focus_force()
        # Cuando un Button recibe el foco (tras un clic), devolvérselo al root
        # para que los shortcuts sigan activos
        self.root.bind_all("<FocusIn>", self._on_widget_focus)

    # ── Monitores ─────────────────────────────────────────────────────────────
    def _get_monitors(self):
        if SCREENINFO:
            try:
                return screeninfo.get_monitors()
            except Exception:
                pass
        return []

    # ── Hilo de envío de estado ───────────────────────────────────────────────
    def _poll_key_queue(self):
        """
        Procesa las teclas reenviadas desde los procesos pygame (ventanas de visualización).
        Cuando una ventana pygame tiene el foco del SO y el usuario pulsa una tecla,
        pygame la captura y la envía aquí para que el panel ejecute el shortcut correspondiente.
        Se llama cada 30ms desde el loop de tkinter.
        """
        try:
            while True:
                try:
                    key = self._key_queue.get_nowait()
                except Exception:
                    break
                if   key == "left":   self._shift_hue(-15)
                elif key == "right":  self._shift_hue(+15)
                elif key == "up":     self._change_intensity(+0.1)
                elif key == "down":   self._change_intensity(-0.1)
                elif key == "space":  self._cycle_transition()
                elif key == "return": self._toggle_strobe()
                elif key == "tab":    self._cycle_vis_mode()
                elif key == "f1":     self._select_band("bass")
                elif key == "f2":     self._select_band("mid")
                elif key == "f3":     self._select_band("high")
                elif key == "f4":     self._cycle_palette(-1)
                elif key == "f5":     self._open_all()
                elif key == "f6":     self._close_all()
                elif key == "f7":     self._cycle_palette(+1)
                elif key == "f8":     self._cycle_display_mode()
                elif key == "p":      self._cycle_palette(+1)
                elif key == "m":      self._cycle_display_mode()
                elif key == "b":      self._toggle_blackout()
                elif key == "l":      self._toggle_manual_mode()
                elif key == "plus":   self._shift_saturation(+0.05)
                elif key == "minus":  self._shift_saturation(-0.05)
                elif key.isdigit():
                    idx = int(key) - 1
                    if 0 <= idx <= 8:
                        self._toggle_monitor(idx)
        except Exception:
            pass
        self.root.after(30, self._poll_key_queue)

    def _focus_loop(self):
        """
        Mantiene el foco en el panel cuando NO hay ventanas pygame activas.
        Cuando hay pantallas abiertas, los shortcuts llegan vía _poll_key_queue
        (reenvío desde pygame) — no hace falta robar el foco del SO.
        """
        def loop():
            if not self._windows:
                try:
                    self.root.focus_set()
                except Exception:
                    pass
            self.root.after(500, loop)
        self.root.after(500, loop)

    def _push_loop(self):
        def loop():
            while True:
                try:
                    self._push_state()
                except Exception:
                    pass
                time.sleep(0.016)   # ~60fps
        t = threading.Thread(target=loop, daemon=True)
        t.start()

    def _push_state(self):
        if not self._queues:
            return
        a = self.audio.snapshot()

        # Calcular colores
        combo = self.fx.get_color("combo")

        # ── Estado visual calculado AQUÍ, una sola vez ──────────────────────
        # VisualEngine avanza la simulación (partículas, ángulos, trails…)
        # y devuelve un dict serializable con el resultado.
        # Todas las ventanas reciben exactamente el mismo snapshot → efecto idéntico.
        vis_state = self.vis_engine.update(a)
        vis_state["vis_display"] = self._vis_mode   # LIGHTS / SHAPES / BOTH

        base_state = {
            **a,
            "col_bass":  self.fx.color_bass,
            "col_mid":   self.fx.color_mid,
            "col_high":  self.fx.color_high,
            "col_combo": combo,
            **vis_state,
        }

        for idx, q in list(self._queues.items()):
            role = self._roles.get(idx, "combo")
            bg   = role_color(role, self.fx)
            msg  = {**base_state, "bg_color": bg}
            try:
                while not q.empty():
                    try: q.get_nowait()
                    except Exception: break
                q.put_nowait(msg)
            except Exception:
                pass

    # ── Abrir / cerrar ventanas ───────────────────────────────────────────────
    def _open_monitor(self, idx):
        if idx in self._windows and self._windows[idx].is_alive():
            return

        if self._monitors and idx < len(self._monitors):
            mon = self._monitors[idx]
            mx, my, mw, mh = mon.x, mon.y, mon.width, mon.height
        else:
            mx, my, mw, mh = 0, 0, 1920, 1080

        q = mp.Queue(maxsize=4)
        self._queues[idx] = q

        # Actualizar roles con el nuevo número de pantallas
        all_idx = sorted(self._windows.keys()) + [idx]
        all_idx = sorted(set(all_idx))
        roles   = assign_roles(len(all_idx))
        for i, aidx in enumerate(all_idx):
            self._roles[aidx] = roles[i]

        p = mp.Process(
            target=run_window,
            args=(q, mx, my, mw, mh, idx, self._key_queue),
            daemon=True
        )
        p.start()
        self._windows[idx] = p

        self._refresh_mon_btns()

    def _close_monitor(self, idx):
        if idx in self._queues:
            try:
                self._queues[idx].put_nowait({"cmd": "quit"})
            except Exception:
                pass
            del self._queues[idx]
        if idx in self._windows:
            p = self._windows[idx]
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()
            del self._windows[idx]
        if idx in self._roles:
            del self._roles[idx]

        # Reasignar roles
        remaining = sorted(self._windows.keys())
        if remaining:
            roles = assign_roles(len(remaining))
            for i, ridx in enumerate(remaining):
                self._roles[ridx] = roles[i]

        self._refresh_mon_btns()

    def _toggle_monitor(self, idx):
        if idx in self._windows and self._windows[idx].is_alive():
            self._close_monitor(idx)
        else:
            self._open_monitor(idx)

    def _open_all(self):
        n = len(self._monitors) if self._monitors else 1
        for i in range(n):
            self._open_monitor(i)

    def _close_all(self):
        for idx in list(self._windows.keys()):
            self._close_monitor(idx)

    def _broadcast(self, msg: dict):
        for q in self._queues.values():
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    # ── Shortcuts ─────────────────────────────────────────────────────────────
    def _on_widget_focus(self, event):
        """
        Cuando un widget hijo (Button, Checkbutton, Scale…) recibe el foco
        por un clic, se lo devolvemos inmediatamente al root para que los
        shortcuts de teclado globales sigan activos sin necesidad de hacer
        clic en un área vacía.
        Excepción: los Entry necesitan el foco para editar texto.
        """
        if not isinstance(event.widget, tk.Entry):
            self.root.focus_set()

    def _bind_keys(self):
        """
        Registra todos los shortcuts a nivel de la ventana raíz.
        - bind_all captura la tecla aunque el foco esté en un widget hijo.
        - Devolver "break" impide que el evento se propague al widget activo
          (evita que Tab cambie el foco, Space active botones, etc.)
        - En campos Entry las flechas y Enter siguen funcionando con normalidad
          porque el guard devuelve None (sin "break") en ese caso.
        """

        def on_key(event, fn, allow_in_entry=False):
            w = self.root.focus_get()
            in_entry = isinstance(w, tk.Entry)
            if in_entry and not allow_in_entry:
                return          # no interferir con edición de texto
            fn()
            return "break"      # evitar propagación nativa (Tab focus, Space button…)

        # Flechas — solo fuera de Entry
        self.root.bind_all("<Left>",
            lambda e: on_key(e, lambda: self._shift_hue(-15)))
        self.root.bind_all("<Right>",
            lambda e: on_key(e, lambda: self._shift_hue(+15)))
        self.root.bind_all("<Up>",
            lambda e: on_key(e, lambda: self._change_intensity(+0.1)))
        self.root.bind_all("<Down>",
            lambda e: on_key(e, lambda: self._change_intensity(-0.1)))

        # Espacio / Tab — solo fuera de Entry
        self.root.bind_all("<space>",
            lambda e: on_key(e, self._cycle_transition))
        self.root.bind_all("<Tab>",
            lambda e: on_key(e, self._cycle_vis_mode))

        # Enter — fuera de Entry activa strobo; dentro de Entry aplica hex
        self.root.bind_all("<Return>",
            lambda e: on_key(e, self._toggle_strobe, allow_in_entry=False))

        # Escape — siempre quita el foco del Entry activo
        self.root.bind_all("<Escape>",
            lambda e: (self.root.focus_set(), "break"))

        # Fx — siempre activos
        self.root.bind_all("<F1>", lambda e: (self._select_band("bass"),  "break")[1])
        self.root.bind_all("<F2>", lambda e: (self._select_band("mid"),   "break")[1])
        self.root.bind_all("<F3>", lambda e: (self._select_band("high"),  "break")[1])
        self.root.bind_all("<F4>", lambda e: (self._cycle_palette(-1),     "break")[1])
        self.root.bind_all("<F5>", lambda e: (self._open_all(),           "break")[1])
        self.root.bind_all("<F6>", lambda e: (self._close_all(),          "break")[1])
        self.root.bind_all("<F7>", lambda e: (self._cycle_palette(+1),    "break")[1])
        self.root.bind_all("<F8>", lambda e: (self._cycle_display_mode(), "break")[1])
        self.root.bind_all("p",    lambda e: on_key(e, lambda: self._cycle_palette(+1)))
        self.root.bind_all("m",    lambda e: on_key(e, lambda: self._cycle_display_mode()))
        self.root.bind_all("b",    lambda e: on_key(e, lambda: self._toggle_blackout()))
        self.root.bind_all("l",    lambda e: on_key(e, lambda: self._toggle_manual_mode()))
        self.root.bind_all("equal",  lambda e: on_key(e, lambda: self._shift_saturation(+0.05)))
        self.root.bind_all("minus",  lambda e: on_key(e, lambda: self._shift_saturation(-0.05)))

        # Números 1-9 para monitores (solo cuando el foco NO es Entry)
        for i in range(9):
            self.root.bind_all(
                str(i + 1),
                lambda e, idx=i: on_key(e, lambda: self._toggle_monitor(idx))
            )

    def _shift_hue(self, delta):
        attr = f"color_{self._selected_band}"
        rgb  = getattr(self.fx, attr)
        h, s, v = rgb_to_hsv(*rgb)
        h = (h + delta / 360.0) % 1.0
        s = max(0.75, s); v = max(0.85, v)
        setattr(self.fx, attr, hsv_to_rgb(h, s, v))
        self._refresh_swatches()
        self._hint(f"← → hue {self._selected_band}")

    def _change_intensity(self, delta):
        self._intensity = max(0.05, min(1.0, self._intensity + delta))
        self.fx.intensity = self._intensity
        pct = int(self._intensity * 100)
        self._int_label.configure(text=f"intensidad: {pct}%")
        self._int_bar.place(relwidth=self._intensity)
        self._hint(f"intensidad: {pct}%")

    def _select_band(self, band):
        self._selected_band = band
        cols = {"bass": "#ff1444", "mid": "#00ff88", "high": "#00d4ff"}
        self._band_lbl.configure(
            text=f"banda activa: {band.upper()}   ← → hue",
            fg=cols[band])

    def _cycle_transition(self):
        modes = list(TRANSITION_MODES.keys())
        idx   = modes.index(self.fx.transition) if self.fx.transition in modes else 0
        self.fx.transition = modes[(idx + 1) % len(modes)]
        self._update_trans_btns()
        self._hint(f"transición: {self.fx.transition}")

    def _cycle_vis_mode(self):
        modes = VIS_MODES
        idx   = modes.index(self._vis_name) if self._vis_name in modes else 0
        self._vis_name = modes[(idx + 1) % len(modes)]
        self.vis_engine.set_mode(self._vis_name)
        self._vis_lbl.configure(text=f"forma: {self._vis_name}")
        self._hint(f"forma: {self._vis_name}")

    def _toggle_strobe(self):
        self.fx.strobe_on = not self.fx.strobe_on
        if self.fx.strobe_on:
            self._strobe_btn.configure(bg="#ffffff", fg="#000", text="⚡ STROBO ON")
        else:
            self._strobe_btn.configure(bg="#111122", fg="#666", text="⚡ STROBO")

    def _cycle_palette(self, direction):
        names = list(PALETTES.keys())
        self._palette_idx = (self._palette_idx + direction) % len(names)
        name = names[self._palette_idx]
        self._apply_palette(name)
        self._hint(f"paleta: {name}")
        # Actualizar botón visual si existe
        if hasattr(self, "_pal_lbl"):
            self._pal_lbl.configure(text=f"paleta: {name}")

    def _cycle_display_mode(self):
        modes = ["LIGHTS", "SHAPES", "BOTH"]
        labels = {"LIGHTS": "💡 LUCES", "SHAPES": "✦ FORMAS", "BOTH": "🔀 AMBOS"}
        idx = modes.index(self._vis_mode) if self._vis_mode in modes else 0
        self._vis_mode = modes[(idx + 1) % len(modes)]
        self._set_display_mode(self._vis_mode)
        self._hint(f"modo: {labels[self._vis_mode]}")

    def _toggle_blackout(self):
        self._blackout = not self._blackout
        self.fx.blackout = self._blackout
        if self._blackout:
            self._hint("⬛ BLACKOUT")
            if hasattr(self, "_blackout_btn"):
                self._blackout_btn.configure(bg="#222222", fg="#ffffff", text="⬛ BLACKOUT ON")
        else:
            self._hint("blackout OFF")
            if hasattr(self, "_blackout_btn"):
                self._blackout_btn.configure(bg="#111122", fg="#666666", text="⬛ BLACKOUT OFF")

    def _toggle_manual_mode(self):
        self._manual_mode = not self._manual_mode
        if self._manual_mode:
            # Captura el color actual como color fijo
            current = self.fx.get_color("combo")
            self.fx.manual_color = current
            hx = "#{:02x}{:02x}{:02x}".format(*current)
            self._hint(f"🔒 MANUAL {hx}")
            if hasattr(self, "_manual_btn"):
                self._manual_btn.configure(bg="#ffcc00", fg="#000", text="🔒 MANUAL ON")
        else:
            self.fx.manual_color = None
            self._hint("🔓 manual OFF → audio reactivo")
            if hasattr(self, "_manual_btn"):
                self._manual_btn.configure(bg="#111122", fg="#666666", text="🔓 MANUAL OFF")

    def _shift_saturation(self, delta):
        attr = f"color_{self._selected_band}"
        rgb  = getattr(self.fx, attr)
        h, s, v = rgb_to_hsv(*rgb)
        s = max(0.0, min(1.0, s + delta))
        v = max(0.5, v)
        setattr(self.fx, attr, hsv_to_rgb(h, s, v))
        self._refresh_swatches()
        self._hint(f"+/- saturación {self._selected_band}: {int(s*100)}%")

    def _hint(self, text):
        self._hint_lbl.configure(text=text)
        self.root.after(2000, lambda: self._hint_lbl.configure(text=""))

    # ── Colores ───────────────────────────────────────────────────────────────
    def _apply_hex(self, attr, hex_var, swatch):
        raw = hex_var.get().strip()
        if not raw.startswith("#"): raw = "#" + raw
        try:
            r = int(raw[1:3], 16); g = int(raw[3:5], 16); b = int(raw[5:7], 16)
            setattr(self.fx, attr, (r, g, b))
            swatch.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
            hex_var.set(f"#{r:02x}{g:02x}{b:02x}")
        except Exception:
            pass

    def _hue_step(self, attr, delta, hex_var, swatch):
        rgb = getattr(self.fx, attr)
        h, s, v = rgb_to_hsv(*rgb)
        h = (h + delta / 360.0) % 1.0
        s = max(0.75, s); v = max(0.85, v)
        r, g, b = hsv_to_rgb(h, s, v)
        setattr(self.fx, attr, (r, g, b))
        hx = f"#{r:02x}{g:02x}{b:02x}"
        hex_var.set(hx); swatch.configure(bg=hx)

    def _pick_color(self, attr, hex_var, swatch):
        current = getattr(self.fx, attr)
        init    = "#{:02x}{:02x}{:02x}".format(*current)
        result  = colorchooser.askcolor(color=init, title=f"Color — {attr}")
        if result and result[0]:
            r, g, b = (int(x) for x in result[0])
            setattr(self.fx, attr, (r, g, b))
            hx = f"#{r:02x}{g:02x}{b:02x}"
            hex_var.set(hx); swatch.configure(bg=hx)

    def _apply_palette(self, name):
        self.fx.apply_palette(name)
        self._refresh_swatches()

    def _refresh_swatches(self):
        for attr, (swatch, hex_var) in self._color_widgets.items():
            r, g, b = getattr(self.fx, attr)
            hx = f"#{r:02x}{g:02x}{b:02x}"
            swatch.configure(bg=hx); hex_var.set(hx)

    # ── Refresh button states ─────────────────────────────────────────────────
    def _update_trans_btns(self):
        for name, btn in self._trans_btns.items():
            if name == self.fx.transition:
                btn.configure(bg="#00ff88", fg="#000")
            else:
                btn.configure(bg="#111122", fg="#e0e0f0")

    def _refresh_mon_btns(self):
        active = set(k for k, p in self._windows.items() if p.is_alive())
        for i, btn in enumerate(self._mon_btns):
            if i in active:
                btn.configure(bg="#00ff88", fg="#000")
            else:
                btn.configure(bg="#111122", fg="#e0e0f0")

    def _set_display_mode(self, mode):
        self._vis_mode = mode
        for m, btn in self._disp_btns.items():
            btn.configure(bg="#00ff88" if m == mode else "#111122",
                          fg="#000" if m == mode else "#e0e0f0")

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        BG   = "#07070f"; CARD = "#0f0f1c"; ACC = "#00ff88"
        RED  = "#ff1444"; CYN  = "#00d4ff"; GRY = "#44445a"; WHT = "#e0e0f0"
        PRP  = "#cc00ff"
        FMN  = ("Consolas", 9); FBD = ("Consolas", 10, "bold")
        FBG  = ("Consolas", 18, "bold"); FSML = ("Consolas", 8)

        # Scrollable container
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        cv = tk.Canvas(outer, bg=BG, highlightthickness=0, width=600)
        sb = tk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG)
        cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        # MouseWheel para scroll — las flechas las manejamos nosotros, no el canvas
        cv.bind("<MouseWheel>", lambda e: cv.yview_scroll(-1*(e.delta//120), "units"))
        # Cuando el canvas tiene el foco, redirigir flechas a nuestros handlers
        cv.bind("<Left>",  lambda e: (self._shift_hue(-15),         "break")[1])
        cv.bind("<Right>", lambda e: (self._shift_hue(+15),         "break")[1])
        cv.bind("<Up>",    lambda e: (self._change_intensity(+0.1), "break")[1])
        cv.bind("<Down>",  lambda e: (self._change_intensity(-0.1), "break")[1])

        def section(title):
            f = tk.Frame(inner, bg=BG, padx=16, pady=5)
            f.pack(fill="x")
            tk.Label(f, text=title, bg=BG, fg=GRY, font=FSML).pack(anchor="w", pady=(0,3))
            return f

        # Header
        hdr = tk.Frame(inner, bg="#050510", pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="S T R O B O S Y N C", bg="#050510",
                 fg=ACC, font=("Consolas", 15, "bold")).pack()
        tk.Label(hdr, text="v2  •  audio reactive light engine",
                 bg="#050510", fg=GRY, font=FMN).pack()
        self._dev_lbl = tk.Label(hdr, text="dispositivo: —",
                                  bg="#050510", fg=CYN, font=FMN)
        self._dev_lbl.pack(pady=(2,0))
        self._hint_lbl = tk.Label(hdr, text="", bg="#050510", fg="#ffcc00", font=FMN)
        self._hint_lbl.pack()

        # Preview + VU
        pv = section("PREVIEW")
        self._preview = tk.Canvas(pv, height=48, bg="black",
                                   highlightthickness=1, highlightbackground=GRY)
        self._preview.pack(fill="x", pady=2)
        self._meters = {}
        for name, col in [("BASS","#ff1444"),("MID","#00ff88"),("HIGH","#00d4ff"),("TOTAL","#ffffff")]:
            row = tk.Frame(pv, bg=BG); row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{name:<5}", bg=BG, fg=col, font=FMN, width=5, anchor="w").pack(side="left")
            bg_b = tk.Frame(row, bg="#111122", height=11)
            bg_b.pack(side="left", fill="x", expand=True, padx=4)
            bg_b.pack_propagate(False)
            bar = tk.Frame(bg_b, bg=col, height=11)
            bar.place(x=0, y=0, relheight=1, width=0)
            self._meters[name] = (bar, bg_b)

        # Stats
        sf = tk.Frame(inner, bg=CARD, pady=8)
        sf.pack(fill="x", padx=16, pady=4)
        for i, (lbl, col, key) in enumerate([("BPM",ACC,"bpm"),("ESTADO",WHT,"state"),("BUILDUP",CYN,"buildup")]):
            c = tk.Frame(sf, bg=CARD, padx=10); c.grid(row=0, column=i, sticky="nsew")
            sf.grid_columnconfigure(i, weight=1)
            tk.Label(c, text=lbl, bg=CARD, fg=GRY, font=FMN).pack()
            w = tk.Label(c, text="—", bg=CARD, fg=col, font=FBG if key != "state" else FBD)
            w.pack()
            setattr(self, f"_{key}_lbl", w)

        # Modo de visualización
        vf = section("MODO DE VISUALIZACIÓN  (TAB para ciclar)")
        vr = tk.Frame(vf, bg=BG); vr.pack(fill="x")

        self._disp_btns = {}
        for mode, label in [("LIGHTS","💡 LUCES"),("SHAPES","✦ FORMAS"),("BOTH","🔀 AMBOS")]:
            btn = tk.Button(vr, text=label,
                bg=ACC if mode=="SHAPES" else "#111122",
                fg="#000" if mode=="SHAPES" else WHT,
                font=FBD, relief="flat", padx=10, pady=7, cursor="hand2",
                command=lambda m=mode: self._set_display_mode(m))
            btn.pack(side="left", padx=3)
            self._disp_btns[mode] = btn

        # Formas disponibles
        shapes_row = tk.Frame(vf, bg=BG); shapes_row.pack(fill="x", pady=(4,0))
        tk.Label(shapes_row, text="forma:", bg=BG, fg=GRY, font=FSML).pack(side="left", padx=(0,4))
        for vname in VIS_MODES:
            tk.Button(shapes_row, text=vname, bg="#111122", fg=WHT,
                font=FSML, relief="flat", padx=7, pady=4, cursor="hand2",
                command=lambda m=vname: self._set_vis_shape(m)
            ).pack(side="left", padx=2)
        self._vis_lbl = tk.Label(vf, text=f"forma: {self._vis_name}", bg=BG, fg=PRP, font=FSML)
        self._vis_lbl.pack(anchor="w")

        # Modos de transición
        tf = section("TRANSICIÓN DE COLOR  (ESPACIO para ciclar)")
        tr_grid = tk.Frame(tf, bg=BG); tr_grid.pack(fill="x")
        self._trans_btns = {}
        for i, (tname, tdesc) in enumerate(TRANSITION_MODES.items()):
            btn = tk.Button(tr_grid, text=tname,
                bg=ACC if tname == self.fx.transition else "#111122",
                fg="#000" if tname == self.fx.transition else WHT,
                font=FSML, relief="flat", padx=8, pady=6, cursor="hand2",
                command=lambda m=tname: self._set_transition(m))
            btn.grid(row=i//4, column=i%4, padx=2, pady=2, sticky="ew")
            tr_grid.grid_columnconfigure(i%4, weight=1)
            self._trans_btns[tname] = btn

        # Velocidad de transición de COLOR (no afecta a flashes ni strobo)
        speed_row = tk.Frame(tf, bg=BG); speed_row.pack(fill="x", pady=(6,0))
        tk.Label(speed_row, text="vel. color:", bg=BG, fg=GRY, font=FSML).pack(side="left")
        self._speed_var = tk.DoubleVar(value=1.0)
        speed_scale = tk.Scale(speed_row, from_=0.2, to=4.0, resolution=0.1,
            orient="horizontal", variable=self._speed_var,
            bg=BG, fg=WHT, troughcolor="#111122", highlightthickness=0,
            font=FSML, length=160, showvalue=True,
            command=lambda v: setattr(self.fx, "speed", float(v)))
        speed_scale.pack(side="left", padx=8)
        # Redirigir flechas del Scale a nuestros handlers globales
        speed_scale.bind("<Left>",  lambda e: (self._shift_hue(-15),          "break")[1])
        speed_scale.bind("<Right>", lambda e: (self._shift_hue(+15),          "break")[1])
        speed_scale.bind("<Up>",    lambda e: (self._change_intensity(+0.1),  "break")[1])
        speed_scale.bind("<Down>",  lambda e: (self._change_intensity(-0.1),  "break")[1])
        self._sync_var = tk.BooleanVar(value=True)
        tk.Checkbutton(speed_row, text="sync BPM", variable=self._sync_var,
            bg=BG, fg=ACC, selectcolor=CARD, font=FSML,
            command=lambda: setattr(self.fx, "sync_bpm", self._sync_var.get())
        ).pack(side="left", padx=4)
        tk.Label(speed_row, text="(solo color, no flashes)",
                 bg=BG, fg=GRY, font=("Consolas",7)).pack(side="left", padx=4)

        # Intensidad
        intf = section("INTENSIDAD  (↑↓)")
        int_row = tk.Frame(intf, bg=BG); int_row.pack(fill="x")
        self._int_label = tk.Label(int_row, text="intensidad: 100%", bg=BG, fg=ACC, font=FBD)
        self._int_label.pack(side="left")
        int_bg = tk.Frame(int_row, bg="#111122", height=14)
        int_bg.pack(side="left", fill="x", expand=True, padx=(8,0))
        int_bg.pack_propagate(False)
        self._int_bar = tk.Frame(int_bg, bg=ACC, height=14)
        self._int_bar.place(x=0, y=0, relheight=1, relwidth=1.0)

        # Banda activa
        bandf = section("BANDA ACTIVA  (F1=GRAVES · F2=MEDIOS · F3=AGUDOS)")
        self._band_lbl = tk.Label(bandf, text="banda activa: BASS   ← → hue",
                                   bg=BG, fg=RED, font=FBD)
        self._band_lbl.pack(anchor="w")

        # Colores por banda
        cf = section("COLORES POR BANDA")
        # Paletas
        pal_row = tk.Frame(cf, bg=BG); pal_row.pack(fill="x", pady=(0,6))
        tk.Label(pal_row, text="paleta:", bg=BG, fg=GRY, font=FSML).pack(side="left", padx=(0,4))
        for pname in PALETTES:
            r,g,b = PALETTES[pname]["bass"]
            btn = tk.Button(pal_row, text=pname,
                bg=f"#{r//4:02x}{g//4:02x}{b//4:02x}",
                fg=f"#{min(255,r):02x}{min(255,g):02x}{min(255,b):02x}",
                font=FSML, relief="flat", padx=6, pady=3, cursor="hand2",
                command=lambda p=pname: self._apply_palette(p))
            btn.pack(side="left", padx=2)

        # Editores
        self._color_widgets = {}
        for band_name, attr, default, label_col in [
            ("GRAVES", "color_bass",  (255,0,80),   RED),
            ("MEDIOS", "color_mid",   (0,255,100),  ACC),
            ("AGUDOS", "color_high",  (0,180,255),  CYN),
        ]:
            row = tk.Frame(cf, bg=CARD, padx=8, pady=5)
            row.pack(fill="x", pady=2)
            r,g,b = default
            swatch = tk.Label(row, bg=f"#{r:02x}{g:02x}{b:02x}", width=3, relief="flat")
            swatch.pack(side="left", padx=(0,6))
            tk.Label(row, text=band_name, bg=CARD, fg=label_col,
                     font=FBD, width=8, anchor="w").pack(side="left")
            hex_var = tk.StringVar(value=f"#{r:02x}{g:02x}{b:02x}")
            entry = tk.Entry(row, textvariable=hex_var, bg="#1a1a2e", fg=WHT,
                font=("Consolas",10), width=9, relief="flat", insertbackground=WHT)
            entry.pack(side="left", padx=(0,5))
            entry.bind("<Return>",   lambda e,a=attr,v=hex_var,s=swatch: self._apply_hex(a,v,s))
            entry.bind("<FocusOut>", lambda e,a=attr,v=hex_var,s=swatch: self._apply_hex(a,v,s))
            tk.Button(row, text="🎨", bg="#1a1a2e", fg=WHT, font=FSML, relief="flat",
                padx=5, cursor="hand2",
                command=lambda a=attr,v=hex_var,s=swatch: self._pick_color(a,v,s)
            ).pack(side="left", padx=(0,3))
            for delta, arrow in [(-30,"◄"),(-10,"‹"),(+10,"›"),(+30,"►")]:
                tk.Button(row, text=arrow, bg="#111122", fg=GRY, font=FSML,
                    relief="flat", padx=3, cursor="hand2",
                    command=lambda a=attr,d=delta,v=hex_var,s=swatch: self._hue_step(a,d,v,s)
                ).pack(side="left")
            self._color_widgets[attr] = (swatch, hex_var)

        # Strobo + Blackout + Manual
        stf = section("CONTROL DE LUCES  (ENTER strobo · B blackout · L manual)")
        st_row = tk.Frame(stf, bg=BG); st_row.pack(fill="x")
        self._strobe_btn = tk.Button(st_row, text="⚡ STROBO",
            bg="#111122", fg="#666", font=FBD,
            relief="flat", padx=12, pady=8, cursor="hand2",
            command=self._toggle_strobe)
        self._strobe_btn.pack(side="left", padx=(0,6))
        self._blackout_btn = tk.Button(st_row, text="⬛ BLACKOUT",
            bg="#111122", fg="#666666", font=FBD,
            relief="flat", padx=12, pady=8, cursor="hand2",
            command=self._toggle_blackout)
        self._blackout_btn.pack(side="left", padx=(0,6))
        self._manual_btn = tk.Button(st_row, text="🔓 MANUAL",
            bg="#111122", fg="#666666", font=FBD,
            relief="flat", padx=12, pady=8, cursor="hand2",
            command=self._toggle_manual_mode)
        self._manual_btn.pack(side="left", padx=(0,6))
        # Paleta activa label
        pal_names = list(PALETTES.keys())
        self._pal_lbl = tk.Label(stf, text=f"paleta: {pal_names[0]}  (F4/F7 o P)",
            bg=BG, fg=GRY, font=FSML)
        self._pal_lbl.pack(anchor="w", pady=(3,0))

        # Pantallas
        monf = section("PANTALLAS  (1-9 toggle · F5 todas · F6 cerrar)")
        self._mon_btns = []
        mon_row = tk.Frame(monf, bg=BG); mon_row.pack(fill="x")
        for i, mon in enumerate(self._monitors if self._monitors else [None]):
            if mon:
                lbl = f"Monitor {i+1}\n{mon.width}x{mon.height}"
                if getattr(mon, "is_primary", False):
                    lbl += "\n★"
            else:
                lbl = "Pantalla\nprincipal"
            btn = tk.Button(mon_row, text=lbl, bg="#111122", fg=WHT,
                font=FSML, relief="flat", padx=12, pady=8, cursor="hand2",
                command=lambda idx=i: self._toggle_monitor(idx))
            btn.pack(side="left", padx=4)
            self._mon_btns.append(btn)

        # Acciones
        af = tk.Frame(inner, bg=BG, padx=16, pady=8); af.pack(fill="x")
        tk.Button(af, text="▶  TODAS", bg=ACC, fg="#000", font=FBD,
            relief="flat", padx=14, pady=9, cursor="hand2",
            command=self._open_all).pack(side="left", padx=(0,8))
        tk.Button(af, text="■  CERRAR TODO", bg=RED, fg="white", font=FBD,
            relief="flat", padx=14, pady=9, cursor="hand2",
            command=self._close_all).pack(side="left")

        # Shortcuts ref
        sf2 = section("SHORTCUTS")
        sc_text = (
            "← →  hue   +/-  saturación   ↑ ↓  intensidad\n"
            "F1/F2/F3  banda activa   ESPACIO  transición   TAB  forma\n"
            "ENTER  strobo   B  blackout   L  manual (congela color)\n"
            "P / F4 / F7  paleta anterior/siguiente\n"
            "M / F8  ciclar modo LUCES→FORMAS→AMBOS\n"
            "1-9  monitor   F5  todas   F6  cerrar"
        )
        tk.Label(sf2, text=sc_text, bg=BG, fg=GRY, font=FSML, justify="left").pack(anchor="w")

        self.root.update_idletasks()
        self.root.geometry("610x820")

    def _set_transition(self, mode):
        self.fx.transition = mode
        self._update_trans_btns()

    def _set_vis_shape(self, name):
        self._vis_name = name
        self.vis_engine.set_mode(name)
        self._vis_lbl.configure(text=f"forma: {name}")

    # ── UI update loop ────────────────────────────────────────────────────────
    def _update_ui(self):
        a = self.audio
        self._dev_lbl.configure(text=f"dispositivo: {a.device_name}")

        # Preview color
        r, g, b = self.fx.get_color("combo")
        self._preview.configure(bg=f"#{r:02x}{g:02x}{b:02x}")

        # VU
        lv = {"BASS": a.bass_level, "MID": a.mid_level,
              "HIGH": a.high_level, "TOTAL": a.overall}
        for name, (bar, bgb) in self._meters.items():
            bgb.update_idletasks()
            bar.place(width=int(bgb.winfo_width() * lv[name]))

        # Stats
        self._bpm_lbl.configure(text=f"{int(a.bpm)}" if a.bpm > 0 else "—")
        self._buildup_lbl.configure(text=f"{int(a.buildup_score*100)}%")
        if a.drop_active:
            self._state_lbl.configure(text="DROP!", fg="#ff1444")
        elif a.pre_drop:
            self._state_lbl.configure(text="PRE-DROP", fg="#ff8800")
        elif a.buildup_score > 0.4:
            self._state_lbl.configure(text="BUILDUP", fg="#ffcc00")
        else:
            self._state_lbl.configure(text="ESCUCHANDO", fg="#e0e0f0")

        # Limpiar procesos muertos
        dead = [i for i, p in self._windows.items() if not p.is_alive()]
        for i in dead:
            self._close_monitor(i)
        if dead:
            self._refresh_mon_btns()

        self.root.after(50, self._update_ui)

    def _on_close(self):
        self._close_all()
        self.audio.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mp.freeze_support()   # necesario para PyInstaller
    app = ControlPanel()
    app.run()
