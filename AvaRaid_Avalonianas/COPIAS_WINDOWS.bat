@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  set "AVA_PY=.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    set "AVA_PY=python"
  ) else (
    set "AVA_PY=py"
  )
)
echo 1. Elegir carpeta privada sincronizada o de red
echo 2. Ver la fecha del ultimo respaldo verificado
echo 3. Crear y comprobar la copia diaria ahora (cierra el bot antes)
choice /c 123 /n /m "Elige 1, 2 o 3: "
if errorlevel 3 goto ahora
if errorlevel 2 goto estado
"%AVA_PY%" -X utf8 copias.py elegir-carpeta
goto fin
:estado
"%AVA_PY%" -X utf8 copias.py estado
goto fin
:ahora
"%AVA_PY%" -X utf8 copias.py ahora
:fin
set "AVA_RC=%ERRORLEVEL%"
pause
exit /b %AVA_RC%
