"""
audio.py — Motor de captura y análisis de audio
Reconecta automáticamente cuando cambia el dispositivo predeterminado de Windows.
"""

import numpy as np
import threading
import time
import math
from collections import deque

try:
    import pyaudiowpatch as pyaudio
    WASAPI = True
except ImportError:
    try:
        import pyaudio
    except ImportError:
        pyaudio = None
    WASAPI = False

CHUNK       = 1024
SAMPLE_RATE = 44100
FFT_SIZE    = 2048
BASS_RANGE  = (20,   250)
MID_RANGE   = (250,  4000)
HIGH_RANGE  = (4000, 20000)


def _find_default_loopback(pa):
    """
    Devuelve (idx, info) del loopback de la salida predeterminada actual.
    Prueba tres métodos en cascada.
    """
    if not WASAPI:
        return None

    # Método 1: API directa pyaudiowpatch ≥0.2.12
    try:
        lb = pa.get_default_wasapi_loopback()
        if lb and lb.get("maxInputChannels", 0) > 0:
            idx = lb.get("index", -1)
            print(f"[Audio] Loopback: [{idx}] {lb.get('name','?')}")
            return (idx, lb)
    except AttributeError:
        pass
    except Exception as e:
        print(f"[Audio] Método 1: {e}")

    # Método 2: WASAPI host API (type=13) → defaultOutputDevice
    try:
        default_name = ""
        for hi in range(pa.get_host_api_count()):
            hinfo = pa.get_host_api_info_by_index(hi)
            if hinfo.get("type", -1) == 13:   # paWASAPI
                doi = hinfo.get("defaultOutputDevice", -1)
                if doi >= 0:
                    try:
                        dinfo = pa.get_device_info_by_index(doi)
                        default_name = dinfo.get("name", "").lower()
                        print(f"[Audio] Salida Windows: {dinfo.get('name','?')}")
                    except Exception:
                        pass
                break

        best_idx, best_info, best_score = None, None, -999
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
            except Exception:
                continue
            if not info.get("isLoopbackDevice", False):
                continue
            if info.get("maxInputChannels", 0) < 1:
                continue
            nlow  = info.get("name", "").lower()
            score = 0
            for bad in ("digital", "spdif", "s/pdif"):
                if bad in nlow:
                    score -= 10
            if default_name:
                clean = nlow.replace("[loopback]", "").strip()
                dw = set(w for w in default_name.split() if len(w) > 2)
                cw = set(w for w in clean.split()         if len(w) > 2)
                score += len(dw & cw) * 5
            if score > best_score:
                best_score, best_idx, best_info = score, i, info

        if best_idx is not None and best_score > -5:
            print(f"[Audio] Loopback match: [{best_idx}] {best_info.get('name','?')}")
            return (best_idx, best_info)
    except Exception as e:
        print(f"[Audio] Método 2: {e}")

    # Método 3: primer loopback no-digital
    try:
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
            except Exception:
                continue
            if not info.get("isLoopbackDevice", False):
                continue
            if info.get("maxInputChannels", 0) < 1:
                continue
            if any(bad in info.get("name","").lower() for bad in ("digital","spdif","s/pdif")):
                continue
            print(f"[Audio] Fallback: [{i}] {info.get('name','?')}")
            return (i, info)
    except Exception as e:
        print(f"[Audio] Fallback: {e}")

    print("[Audio] Sin loopback. Dispositivos detectados:")
    try:
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
                print(f"  [{i}] in={info.get('maxInputChannels',0)}"
                      f" lb={info.get('isLoopbackDevice',False)} — {info.get('name','?')}")
            except Exception:
                pass
    except Exception:
        pass
    return None


def _loopback_name(pa):
    """Devuelve el nombre del loopback actual (para detectar cambios)."""
    try:
        result = _find_default_loopback(pa)
        return result[1].get("name","") if result else ""
    except Exception:
        return ""


class AudioEngine:
    def __init__(self):
        self.running       = False
        self.bass_level    = 0.0
        self.mid_level     = 0.0
        self.high_level    = 0.0
        self.overall       = 0.0
        self.bpm           = 0.0
        self.beat_pulse    = 0.0
        self.device_name   = "—"
        self.is_simulation = False

        self._beat_times    = deque(maxlen=32)
        self._last_beat     = 0.0
        self._beat_thr      = 0.0
        self._energy_hist   = deque(maxlen=300)
        self.buildup_score  = 0.0
        self.drop_active    = False
        self.pre_drop       = False
        self._drop_cooldown = 0.0
        self._bass_max      = 1e-6
        self._mid_max       = 1e-6
        self._high_max      = 1e-6
        self._lock          = threading.Lock()

    def _fidx(self, hz):
        return max(1, int(hz * FFT_SIZE / SAMPLE_RATE))

    def _band(self, fft, lo, hi):
        s = fft[self._fidx(lo):self._fidx(hi)]
        return float(np.mean(s**2)) if len(s) else 0.0

    def _norm(self, v, attr):
        mx = max(getattr(self, attr) * 0.9997, v, 1e-9)
        setattr(self, attr, mx)
        return min(1.0, v / mx)

    def _detect_beat(self, bass_raw):
        now = time.time()
        self._beat_thr = self._beat_thr * 0.92 + bass_raw * 0.08
        if bass_raw > self._beat_thr * 1.55 and (now - self._last_beat) > 0.20:
            self._beat_times.append(now)
            self._last_beat = now
            self.beat_pulse = 1.0
            if len(self._beat_times) >= 4:
                ivs = np.diff(list(self._beat_times)[-12:])
                med = float(np.median(ivs))
                if 0.25 < med < 2.0:
                    self.bpm = round(60.0 / med, 1)

    def _detect_drop(self, energy):
        self._energy_hist.append(energy)
        self.beat_pulse = max(0.0, self.beat_pulse - 0.06)
        now = time.time()
        if len(self._energy_hist) < 50:
            return
        hist   = np.array(self._energy_hist)
        recent = float(np.mean(hist[-8:]))
        medium = float(np.mean(hist[-50:-8]))
        old    = float(np.mean(hist[-150:-50])) if len(hist) >= 150 else medium
        if recent > medium * 1.07 and medium > old * 1.03 and self.high_level > 0.5:
            self.buildup_score = min(1.0, self.buildup_score + 0.03)
        else:
            self.buildup_score = max(0.0, self.buildup_score - 0.012)
        self.pre_drop = self.buildup_score > 0.65 and not self.drop_active
        if now > self._drop_cooldown:
            if self.buildup_score > 0.70 and recent > medium * 1.4 and self.bass_level > 0.65:
                self.drop_active    = True
                self._drop_cooldown = now + 5.0
                self.buildup_score  = 0.0
            elif self.drop_active and self.bass_level < 0.22 and recent < medium * 0.75:
                self.drop_active = False

    def _process(self, raw, ch, rate):
        try:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if ch == 2:
                samples = (samples[0::2] + samples[1::2]) * 0.5
            if rate != SAMPLE_RATE:
                n2 = int(len(samples) * SAMPLE_RATE / rate)
                if n2 > 0:
                    idx = np.linspace(0, len(samples)-1, n2)
                    samples = np.interp(idx, np.arange(len(samples)), samples)
            n = min(len(samples), FFT_SIZE)
            if n < 64:
                return
            buf = np.zeros(FFT_SIZE)
            buf[:n] = samples[:n] * np.hanning(n)
            fft = np.abs(np.fft.rfft(buf))
            b = self._band(fft, *BASS_RANGE)
            m = self._band(fft, *MID_RANGE)
            h = self._band(fft, *HIGH_RANGE)
            with self._lock:
                self.bass_level = self._norm(b, "_bass_max")
                self.mid_level  = self._norm(m, "_mid_max")
                self.high_level = self._norm(h, "_high_max")
                self.overall    = (self.bass_level + self.mid_level + self.high_level) / 3.0
                self._detect_beat(b)
                self._detect_drop((b + m + h) / 3.0)
        except Exception:
            pass

    def _capture_thread(self):
        """
        Bucle principal de captura.
        En cada iteración obtiene el dispositivo predeterminado ACTUAL de Windows,
        así si el usuario cambia de altavoces/auriculares reconecta solo.
        """
        while self.running:
            pa     = pyaudio.PyAudio()
            result = _find_default_loopback(pa)

            if result is None:
                pa.terminate()
                self.is_simulation = True
                self.device_name   = "SIMULACION"
                # Reintentar cada 3s por si el dispositivo aparece después
                time.sleep(3.0)
                continue

            dev_idx, info = result
            rate = int(info.get("defaultSampleRate", SAMPLE_RATE))
            ch   = min(2, max(1, int(info.get("maxInputChannels", 2))))
            current_name = info.get("name", "")

            try:
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=ch,
                    rate=rate,
                    input=True,
                    input_device_index=dev_idx,
                    frames_per_buffer=CHUNK,
                )
                self.device_name   = current_name.replace(" [Loopback]", "")
                self.is_simulation = False
                print(f"[Audio] Capturando: {self.device_name} @ {rate}Hz")

                errors        = 0
                check_counter = 0   # cada N chunks comprueba si cambió el dispositivo

                while self.running:
                    try:
                        data = stream.read(CHUNK, exception_on_overflow=False)
                        self._process(data, ch, rate)
                        errors = 0

                        # Cada ~2 segundos comprueba si Windows cambió el dispositivo
                        check_counter += 1
                        if check_counter >= 120:
                            check_counter = 0
                            new_name = _loopback_name(pa)
                            if new_name and new_name != current_name:
                                print(f"[Audio] Dispositivo cambió → {new_name}")
                                break   # salir para reconectar con el nuevo

                    except Exception:
                        errors += 1
                        if errors > 20:
                            print("[Audio] Stream perdido, reconectando...")
                            break

                stream.stop_stream()
                stream.close()

            except Exception as e:
                print(f"[Audio] Error stream [{dev_idx}]: {e}")

            pa.terminate()
            if self.running:
                time.sleep(1.5)

    def _simulation_mode(self):
        t = 0.0
        while self.running:
            t += 0.033
            self.bass_level    = abs(math.sin(t * 2.1)) ** 1.3
            self.mid_level     = abs(math.sin(t * 3.3 + 1.0)) ** 1.3
            self.high_level    = abs(math.sin(t * 5.7 + 2.0)) ** 1.3
            self.overall       = (self.bass_level + self.mid_level + self.high_level) / 3
            self.bpm           = 128.0
            self.beat_pulse    = max(0, self.beat_pulse - 0.07)
            if math.sin(t * (128/60) * math.pi) > 0.93:
                self.beat_pulse = 1.0
            self.buildup_score = (math.sin(t * 0.22) + 1) / 2
            self.pre_drop      = self.buildup_score > 0.65
            self.drop_active   = self.buildup_score > 0.88
            time.sleep(0.025)

    def start(self):
        self.running = True
        if pyaudio:
            t = threading.Thread(target=self._capture_thread, daemon=True)
        else:
            t = threading.Thread(target=self._simulation_mode, daemon=True)
        t.start()

    def stop(self):
        self.running = False

    def snapshot(self):
        with self._lock:
            return {
                "bass":     self.bass_level,
                "mid":      self.mid_level,
                "high":     self.high_level,
                "overall":  self.overall,
                "bpm":      self.bpm,
                "beat":     self.beat_pulse,
                "buildup":  self.buildup_score,
                "drop":     self.drop_active,
                "pre_drop": self.pre_drop,
            }
