@echo off
title StroboSync — Instalador
color 0A
cd /d "%~dp0"

echo.
echo  ==========================================
echo   STROBOSYNC  v2  -  Instalador
echo  ==========================================
echo.

:: ── Comprobar si Python existe ────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% equ 0 goto :python_ok

:: Python no encontrado — ofrecer descarga automática
echo  [!] Python no encontrado en el sistema.
echo.
echo  Opciones:
echo    1. Descargar e instalar Python 3.12 automaticamente (recomendado)
echo    2. Salir e instalarlo manualmente desde python.org
echo.
set /p CHOICE="  Elige (1 o 2): "

if "%CHOICE%"=="2" (
    echo.
    echo  Instala Python 3.12 desde: https://www.python.org/downloads/
    echo  IMPORTANTE: marca "Add Python to PATH" al instalar.
    pause & exit /b 0
)

:: Descargar Python 3.12 con PowerShell
echo.
echo  Descargando Python 3.12.10 ...
set PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
set PY_INSTALLER=%TEMP%\python-3.12.10-amd64.exe

powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_INSTALLER%' -UseBasicParsing}" 2>nul
if not exist "%PY_INSTALLER%" (
    echo  [ERROR] No se pudo descargar. Comprueba tu conexion a internet.
    echo  Instala manualmente: https://www.python.org/downloads/
    pause & exit /b 1
)

echo  Instalando Python 3.12 (puede tardar 1-2 minutos)...
echo  [IMPORTANTE] Se marca automaticamente "Add to PATH"
:: /quiet = silencioso, PrependPath=1 = añadir al PATH, Include_test=0 = sin tests
"%PY_INSTALLER%" /quiet PrependPath=1 Include_test=0 Include_doc=0

:: Refrescar PATH en esta sesión
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
set "PATH=%APPDATA%\Python\Python312\Scripts;%PATH%"

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] La instalacion silenciosa fallo.
    echo  Por favor instala manualmente: https://www.python.org/downloads/
    echo  Marca "Add Python to PATH"
    del "%PY_INSTALLER%" >nul 2>&1
    pause & exit /b 1
)
del "%PY_INSTALLER%" >nul 2>&1
echo  [OK] Python instalado correctamente.

:python_ok
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] Python %PYVER%

:: ── Actualizar pip ────────────────────────────────────────────────────────
echo  Actualizando pip...
python -m pip install --upgrade pip --quiet 2>nul

:: ── pygame-ce (Community Edition) ────────────────────────────────────────
echo  Instalando pygame-ce (compatible Python 3.9-3.14)...
pip install pygame-ce --quiet
if %errorlevel% neq 0 (
    echo  [ERROR] No se pudo instalar pygame-ce.
    echo  Prueba manualmente: python -m pip install pygame-ce
    pause & exit /b 1
)
echo  [OK] pygame-ce

:: ── Resto de dependencias ─────────────────────────────────────────────────
echo  Instalando numpy, pyaudiowpatch, screeninfo...
pip install numpy pyaudiowpatch screeninfo --quiet
if %errorlevel% neq 0 (
    echo  [AVISO] Reintentando sin --quiet para ver errores...
    pip install numpy pyaudiowpatch screeninfo
)

:: ── Verificacion ─────────────────────────────────────────────────────────
echo.
echo  Verificando instalacion...
python -c "import pygame;        print('  [OK] pygame       ', pygame.__version__)" 2>nul
python -c "import numpy;         print('  [OK] numpy        ', numpy.__version__)" 2>nul
python -c "import pyaudiowpatch; print('  [OK] pyaudiowpatch (WASAPI loopback)')" 2>nul || (
    echo  [AVISO] pyaudiowpatch no disponible
    echo          Sin este paquete StroboSync funcionara en modo simulacion
    echo          Prueba: pip install pyaudiowpatch
)
python -c "import screeninfo;    print('  [OK] screeninfo')" 2>nul || (
    echo  [AVISO] screeninfo no disponible - se usara el monitor principal
)

echo.
echo  ==========================================
echo   Listo. Ejecuta launch.bat para iniciar.
echo  ==========================================
pause
