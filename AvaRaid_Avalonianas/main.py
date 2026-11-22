"""Inicio local de AvaRaid. Las credenciales nunca se guardan junto al programa."""
import argparse
import asyncio
import getpass
import logging
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


def carpeta():
    if os.environ.get("AVARAID_DATOS"):
        return Path(os.environ["AVARAID_DATOS"]).expanduser().resolve()
    if os.name=="nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()/"AppData"/"Local")/"AvaRaidNuevaBase"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home()/".local"/"share")/"AvaRaidNuevaBase"


def _dpapi(data,proteger):
    import ctypes as C
    from ctypes import wintypes as W
    class Blob(C.Structure):
        _fields_=[("cbData",W.DWORD),("pbData",C.POINTER(C.c_ubyte))]
    crypt=C.WinDLL("Crypt32",use_last_error=True);kernel=C.WinDLL("Kernel32",use_last_error=True)
    entrada=(C.c_ubyte*len(data)).from_buffer_copy(data)
    origen,salida=Blob(len(data),entrada),Blob()
    metodo=crypt.CryptProtectData if proteger else crypt.CryptUnprotectData
    metodo.argtypes=[C.POINTER(Blob),C.c_void_p,C.POINTER(Blob),C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(Blob)]
    metodo.restype=W.BOOL
    if not metodo(C.byref(origen),None,None,None,None,1,C.byref(salida)):
        raise OSError("Windows no pudo acceder a la credencial de esta cuenta. Vuelve a configurarla.")
    kernel.LocalFree.argtypes=[C.c_void_p];kernel.LocalFree.restype=C.c_void_p
    try:return C.string_at(salida.pbData,salida.cbData)
    finally:kernel.LocalFree(salida.pbData)


def archivo_token():
    return carpeta()/("discord.token.dpapi" if os.name=="nt" else "discord.token")


def validar(token):
    token=token.strip()
    if not 30<=len(token)<=400 or not re.fullmatch(r"[A-Za-z0-9_.-]+",token):
        raise ValueError("No parece un token de bot. Usa el valor completo de Discord Developer Portal → Bot.")
    return token


def configurar_token():
    token=validar(getpass.getpass("Pega tu token NUEVO de Discord (no se verá en pantalla) y pulsa Enter: "))
    ruta=archivo_token();ruta.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    datos=token.encode("ascii")
    if os.name=="nt":datos=_dpapi(datos,True)
    nombre=None
    try:
        with tempfile.NamedTemporaryFile(dir=ruta.parent,prefix="ava-",delete=False) as f:
            nombre=f.name;f.write(datos)
        os.replace(nombre,ruta)
    finally:
        if nombre:Path(nombre).unlink(missing_ok=True)
    return token


def archivo_token_incluido():
    """Archivo local de credenciales adjunto para esta instalacion."""
    return Path(__file__).resolve().parent / ".env"


def leer_token_incluido():
    """Lee DISCORD_TOKEN sin evaluar codigo ni imprimir la credencial."""
    archivo = archivo_token_incluido()
    if not archivo.is_file():
        return None
    for linea in archivo.read_text(encoding="utf-8-sig").splitlines():
        clave, separador, valor = linea.partition("=")
        if separador and clave.strip() == "DISCORD_TOKEN":
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
                valor = valor[1:-1]
            return validar(valor)
    return None


def obtener_token():
    if os.environ.get("DISCORD_TOKEN"):return validar(os.environ["DISCORD_TOKEN"])
    incluido=leer_token_incluido()
    if incluido:return incluido
    ruta=archivo_token()
    if ruta.exists():
        dato=ruta.read_bytes()
        return validar((_dpapi(dato,False) if os.name=="nt" else dato).decode("ascii"))
    return configurar_token()


@contextmanager
def unica_instancia(dir_):
    dir_.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (dir_/"en_uso.lock").open("a+b") as f:
        f.seek(0,2)
        if not f.tell():f.write(b"0");f.flush()
        f.seek(0)
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError("AvaRaid ya está abierto para esta cuenta. Usa la otra ventana o ciérrala primero.") from None
        try:yield
        finally:
            if os.name=="nt":
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)


def main(args=None):
    p=argparse.ArgumentParser(description="AvaRaid · Avalonianas de Albion en Discord")
    modo=p.add_mutually_exclusive_group()
    modo.add_argument("--configurar",action="store_true",help="Cambiar el token local.")
    modo.add_argument("--comprobar",action="store_true",help="Comprobar los comandos y los paneles sin conectar con Discord.")
    args=p.parse_args(args)
    if sys.version_info<(3,10):
        print("Instala Python 3.10 o posterior.");return 2
    try:
        if args.configurar:
            configurar_token()
            # Si se configura un token manualmente, se desactiva el token del ZIP
            # para que la credencial nueva tome prioridad al reiniciar.
            incluido=archivo_token_incluido()
            if incluido.is_file():
                lineas=[l for l in incluido.read_text(encoding="utf-8-sig").splitlines()
                        if l.strip() and not l.lstrip().startswith("#")]
                if all(l.partition("=")[0].strip()=="DISCORD_TOKEN" for l in lineas):
                    incluido.unlink()
                else:
                    print("El archivo .env contiene otras opciones: revisalo antes de continuar.")
            print("Credencial local guardada. Abre INICIAR_WINDOWS.bat.")
            if os.environ.get("DISCORD_TOKEN"):
                print("La variable de entorno DISCORD_TOKEN tiene prioridad sobre la credencial local.")
            return 0
        import discord
        from raids import Almacen,PLANTILLAS
        from panel import Cliente,Panel
        from comandos import registrar
        if args.comprobar:
            async def probar():
                with tempfile.TemporaryDirectory() as tmp:
                    c=Cliente(Almacen(Path(tmp)/"prueba.sqlite3"));registrar(c)
                    for t,plan in PLANTILLAS.items():
                        v=Panel(t)
                        if not v.is_persistent():raise RuntimeError("No se pudieron preparar los botones.")
                        print(f"{plan['nombre']}: {sum(r['cupos'] for r in plan['roles'].values())} plazas.")
                    print(f"{len(c.tree.get_commands())} comandos preparados. Prueba local correcta, sin conectar con Discord.")
            asyncio.run(probar());return 0
        with unica_instancia(carpeta()):
            token=obtener_token()
            bd=Almacen(carpeta()/"avalonianas.sqlite3")
            c=Cliente(bd);registrar(c)
            logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",level=logging.INFO)
            print("Conectando AvaRaid 22.1. Deja esta ventana abierta mientras quieras recibir inscripciones.",flush=True)
            try:c.run(token,log_handler=None)
            except discord.LoginFailure:
                print("Discord rechazó el token. Ejecuta CONFIGURAR_TOKEN.bat con el token nuevo. Si usas DISCORD_TOKEN, actualízalo allí.");return 2
            except discord.Forbidden:
                print("Discord denegó el acceso. Revisa la instalación de esta aplicación en el servidor y los permisos del bot.");return 2
            except discord.HTTPException as e:
                print(f"Discord no completó la conexión (HTTP {e.status}). Vuelve a iniciar el bot en unos minutos.");return 2
    except ModuleNotFoundError as e:
        print(f"Falta una dependencia ({e.name}). Abre INICIAR_WINDOWS.bat para instalarla.");return 2
    except (ValueError,OSError,EOFError) as e:
        print(str(e) if not isinstance(e,UnicodeError) else "No se pudo leer la credencial. Ejecuta CONFIGURAR_TOKEN.bat.");return 2
    except KeyboardInterrupt:return 0
    return 0


if __name__=="__main__":
    raise SystemExit(main())
