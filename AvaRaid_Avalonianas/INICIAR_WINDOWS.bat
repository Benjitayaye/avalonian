@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto comprobar
where py >nul 2>nul
if errorlevel 1 goto conpython
py -3 -m venv ".venv"
if errorlevel 1 goto fallo
goto comprobar
:conpython
where python >nul 2>nul
if errorlevel 1 goto sinpython
python -m venv ".venv"
if errorlevel 1 goto fallo
:comprobar
set "AVA_PY=.venv\Scripts\python.exe"
"%AVA_PY%" -c "import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto sinpython
"%AVA_PY%" -c "import discord,sys;sys.exit(0 if discord.__version__=='2.7.1' else 1)" >nul 2>nul
if errorlevel 1 goto dependencias
if not defined AVARAID_BACKUP_BUCKET goto iniciar
"%AVA_PY%" -c "import boto3" >nul 2>nul
if not errorlevel 1 goto iniciar
:dependencias
echo Preparando las dependencias de AvaRaid.
"%AVA_PY%" -m pip install --disable-pip-version-check --requirement requirements.txt
if errorlevel 1 goto fallo
:iniciar
"%AVA_PY%" -X utf8 main.py %*
set "AVA_RC=%ERRORLEVEL%"
pause
exit /b %AVA_RC%
:sinpython
echo Instala Python 3.10 o posterior para Windows y vuelve a abrir este archivo.
echo Si .venv fue creada con un Python antiguo, elimina solo .venv antes de reintentar.
pause
exit /b 2
:fallo
echo No se completo la preparacion. Revisa la conexion y la instalacion de Python.
pause
exit /b 2
