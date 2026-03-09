# StroboSync v2

## License

[StroboSync](https://itechsolutions.es) © 2026 [techlogiixx](https://itechsolutions.es/tech_logix)

Is licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)


## Aviso

Este programa puede producir flashes y destellos rápidos. No recomendado para personas con fotosensibilidad o epilepsia fotosensible.

**Audio reactive light show** para Windows. Transforma tus pantallas en un visualizador sincronizado con cualquier música que suene en tu PC — Spotify, YouTube, SoundCloud, lo que sea.

---

## Licencia

**Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)**

Puedes usar, modificar y redistribuir este software libremente con la única condición de **no comercializarlo**.

- Usar para uso personal ✅
- Modificar y adaptar el código ✅
- Redistribuir con atribución ✅
- Proyectos no comerciales ✅
- Vender el software o incluirlo en productos de pago ❌
- Usar en servicios comerciales sin permiso ❌

Texto completo: https://creativecommons.org/licenses/by-nc/4.0/

---

## Instalación

**Requisitos:** Windows 10/11, Python 3.9–3.12 (recomendado 3.12)

1. Doble clic en `install.bat` — si no tienes Python, ofrece descargarlo automáticamente con el PATH ya configurado
2. Doble clic en `launch.bat` para iniciar

---

## Guía de usuario

### Panel de control

Al ejecutar `launch.bat` aparece el panel. En la parte superior verás:
- **PREVIEW** — muestra el color activo en tiempo real
- **VU meters** — niveles de BASS / MID / HIGH / TOTAL
- **BPM, ESTADO, BUILDUP** — información del análisis de audio

Si el dispositivo muestra "SIMULACIÓN", no se detectó loopback. Comprueba que hay música sonando y que `pyaudiowpatch` está instalado.

### Abrir pantallas

En la sección **PANTALLAS** hay un botón por monitor detectado (con resolución). Haz clic para activar/desactivar cada uno. El reparto de roles es automático:

| Pantallas | Reparto |
|---|---|
| 1 | Todo mezclado |
| 2 | Graves+Agudos / Medios+Combo |
| 3 | Graves / Medios / Agudos |
| 4+ | Un rol por pantalla: Graves, Medios, Agudos, Combo, Strobo... |

Con 2 o más pantallas **siempre se representan las 3 bandas**, ninguna se queda sin mostrar.

### Modos de visualización

Selecciona **LUCES**, **FORMAS** o **AMBOS**:

- **LUCES** — pantalla de color sólido reactivo, ideal para iluminación de habitación
- **FORMAS** — visualizador con efectos gráficos
- **AMBOS** — color de fondo + formas encima

Formas disponibles (cicla con `TAB` o selecciona con los botones):

| Forma | Descripción |
|---|---|
| BARS | Espectro clásico con barras, picos y reflejo |
| WAVE | Osciloscopio con 3 capas y bloom |
| TUNNEL | Anillos concéntricos distorsionados por los graves |
| PARTICLES | Partículas con trail + rayos laser |
| STARBURST | Explosión de rayos radiales reactivos al BPM |
| SPECTRAL | Espectro circular con trail histórico estilo SYQEL |

### Modos de transición de color

Controlan cómo evoluciona el color. El slider **vel. color** ajusta su velocidad (no afecta al strobo).

| Modo | Comportamiento |
|---|---|
| ESTÁTICO | Color fijo, brillo varía con el audio |
| ARCOÍRIS | Hue cicla al ritmo del BPM |
| PULSO | Color aparece en beat y decae |
| BREATHING | Fade in/out sinusoidal constante |
| ONDA | Hue ondula con los medios |
| GLITCH | Saltos bruscos de hue al beat |
| CHROMATIC | Cada banda cicla hue independiente |
| **ALEATORIO** | 🎲 Cambia modo y paleta automáticamente según la energía de la música |

El modo **ALEATORIO** es ideal para dejarlo correr sin tocar nada. Detecta buildup, energía y BPM para decidir cuándo y cómo cambiar. En los drops cambia la paleta de colores.

### Personalizar colores

- **Campo hex** — escribe `#ff0080` y pulsa Enter
- **Botón 🎨** — abre el selector de color de Windows
- **Flechas ◄ ‹ › ►** — rotan el hue en pasos de ±10° y ±30°
- **Paletas rápidas** — aplican los 3 colores a la vez (NEON, FUEGO, OCÉANO, PÚRPURA, MATRIX, CYBER, BLANCO)

### Stroboscopio

- **Manual** — activa con el botón o `Enter`. Flashes blancos puros al BPM.
- **Automático** — se activa solo en pre-drop y drop.

> ⚠️ Los flashes pueden afectar a personas con fotosensibilidad o epilepsia. Úsalo con responsabilidad.

### Cambio de dispositivo

Si cambias de altavoces a auriculares (o al revés), StroboSync detecta el cambio automáticamente en unos segundos sin necesidad de reiniciar.

---

## Shortcuts

Los shortcuts funcionan desde el panel. Si el cursor está en un campo hex, pulsa `ESC` primero.

| Tecla | Acción |
|---|---|
| `← →` | Rotar hue de la banda activa |
| `↑ ↓` | Intensidad global (±10%) |
| `F1` | Banda activa: GRAVES |
| `F2` | Banda activa: MEDIOS |
| `F3` | Banda activa: AGUDOS |
| `ESPACIO` | Ciclar modo de transición |
| `TAB` | Ciclar forma del visualizador |
| `ENTER` | Toggle stroboscopio |
| `1`–`9` | Toggle monitor individual |
| `F5` | Abrir todas las pantallas |
| `F6` | Cerrar todas las pantallas |
| `ESC` | Quitar foco del campo de texto |
| `ESC` / `F11` | Cerrar ventana fullscreen individual |

**Combinaciones útiles:**
- `F1` → `← →` : cambiar color de graves sobre la marcha
- `F2` → `← →` : cambiar color de medios
- `F3` → `← →` : cambiar color de agudos
- `ESPACIO` varias veces para encontrar el modo que mejor encaja con la canción
- `ENTER` para activar strobo en el drop y desactivarlo después

---

## Estructura del proyecto

```
StroboSync/
├── strobosync.py    Panel de control (tkinter)
├── audio.py         Captura WASAPI y análisis de audio
├── effects.py       Motor de colores y transiciones
├── visualizer.py    Formas pygame (ventanas fullscreen)
├── install.bat      Instalador
├── launch.bat       Lanzador
└── README.md        Este archivo
```

## Dependencias

| Paquete | Uso |
|---|---|
| numpy | FFT y análisis espectral |
| pyaudiowpatch | Captura WASAPI loopback |
| pygame-ce | Renderizado fullscreen |
| screeninfo | Detección de monitores |

## Solución de problemas

**Dispositivo muestra SIMULACIÓN** — verifica que hay música sonando y que `pyaudiowpatch` está instalado (`pip install pyaudiowpatch`).

**Pantalla negra / formas no aparecen** — cierra todo con `F6` y vuelve a abrir con `F5`. Asegúrate de seleccionar FORMAS o AMBOS.

**Shortcuts no responden** — haz clic en el panel de control o pulsa `ESC` para quitar el foco de los campos de texto.

**pygame no instala** — ejecuta `python -m pip install pygame-ce` directamente.

---
