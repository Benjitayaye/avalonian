"""Copias diarias de AvaRaid: SQLite Online Backup, restauración de prueba y copia externa."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

PATRON=re.compile(r"ava-(\d{4}-\d{2}-\d{2})\.sqlite3\.gz\Z")
TABLAS={"servidor":{"guild"},"raid":{"id","estado"},"reparto":{"raid","datos"},
        "puesto":{"raid","usuario"},"economia_movimiento":{"guild","usuario","delta"}}


class ErrorCopia(ValueError):
    pass


def huella(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for trozo in iter(lambda:f.read(1024*1024),b""):h.update(trozo)
    return h.hexdigest()


def verificar_base(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro&immutable=1",uri=True)) as c:
        if c.execute("PRAGMA integrity_check").fetchall()!=[("ok",)]:
            raise ErrorCopia("La integridad de la copia SQLite no es válida.")
        if c.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ErrorCopia("La copia tiene referencias incompletas.")
        for tabla,columnas in TABLAS.items():
            if not columnas.issubset(x[1] for x in c.execute(f"PRAGMA table_info({tabla})")):
                raise ErrorCopia("La copia no es una base de AvaRaid compatible.")


def descomprimir(archivo,salida):
    with gzip.open(archivo,"rb") as entrada, Path(salida).open("xb") as out:
        shutil.copyfileobj(entrada,out,1024*1024)
    verificar_base(salida)


def verificar_copia(archivo,tempdir=None):
    with tempfile.TemporaryDirectory(dir=tempdir) as t:
        descomprimir(archivo,Path(t)/"restaurada.sqlite3")


def crear_copia(base,destino):
    # La API de SQLite incluye los datos confirmados que aún estén en el WAL.
    with tempfile.TemporaryDirectory(dir=destino.parent) as t:
        t=Path(t); bruto=t/"snapshot.sqlite3"; archivo=t/"snapshot.gz"
        with closing(sqlite3.connect(base.resolve().as_uri()+"?mode=ro",uri=True,timeout=10)) as src, \
             closing(sqlite3.connect(bruto)) as dst:
            src.backup(dst,pages=128,sleep=0.1)
            dst.execute("PRAGMA journal_mode=DELETE")
        with bruto.open("rb") as entrada,gzip.open(archivo,"wb",compresslevel=6) as out:
            shutil.copyfileobj(entrada,out,1024*1024)
        verificar_copia(archivo,t)
        resumen=huella(archivo)
        archivo.chmod(0o600);os.replace(archivo,destino)
        st=destino.stat()
        return {"sha":resumen,"tam":st.st_size,"mtime":st.st_mtime_ns}


def restaurar(archivo,salida):
    """Publica una base de recuperación nueva. Nunca reemplaza una base existente."""
    archivo=Path(archivo);salida=Path(salida)
    if salida.exists():raise ErrorCopia("Elige un archivo de salida NUEVO; no se sobrescriben bases.")
    salida.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=salida.parent) as t:
        nueva=Path(t)/"probada.sqlite3"
        descomprimir(archivo,nueva);nueva.chmod(0o600)
        # Mismo volumen: la base aparece completa, sin sustituir un archivo creado entre tanto.
        try:os.link(nueva,salida)
        except FileExistsError:raise ErrorCopia("El archivo de salida ya existe.") from None
        except OSError:
            try:
                with salida.open("xb") as out:
                    try:
                        if hasattr(os,"fchmod"):os.fchmod(out.fileno(),0o600)
                        with nueva.open("rb") as f:shutil.copyfileobj(f,out,1024*1024)
                    except BaseException:
                        out.close();salida.unlink(missing_ok=True);raise
            except FileExistsError:raise ErrorCopia("El archivo de salida ya existe.") from None


def leer_estado(d):
    try:
        x=json.loads((d/"estado.json").read_text(encoding="utf-8"))
        return x if isinstance(x,dict) and isinstance(x.get("copias"),dict) else {"copias":{}}
    except (FileNotFoundError,ValueError):
        return {"copias":{}}


def guardar_estado(d,estado):
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=d,delete=False) as f:
        tmp=Path(f.name)
        try:
            json.dump(estado,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        except BaseException:
            f.close();tmp.unlink(missing_ok=True);raise
    try:os.replace(tmp,d/"estado.json")
    finally:tmp.unlink(missing_ok=True)


class DestinoCarpeta:
    tipo="carpeta externa"
    def __init__(self,ruta,base):
        seleccion=Path(ruta).expanduser()
        if not seleccion.is_absolute():raise ErrorCopia("Elige una ruta absoluta a la carpeta externa.")
        self.ruta=seleccion.resolve()
        if not self.ruta.is_dir() or self.ruta==base.parent or base.parent in self.ruta.parents:
            raise ErrorCopia("La carpeta externa debe existir y estar fuera de la carpeta de datos.")
        self.firma="carpeta:"+str(self.ruta)
    def enviar(self,archivo,clave):
        with tempfile.NamedTemporaryFile(dir=self.ruta,delete=False) as out:
            temp=Path(out.name)
        try:
            shutil.copyfile(archivo,temp);os.replace(temp,self.ruta/clave)
        finally:temp.unlink(missing_ok=True)
    def recibir(self,clave,salida):
        shutil.copyfile(self.ruta/clave,salida)


class DestinoS3:
    tipo="S3"
    def __init__(self,bucket,prefijo,cliente=None):
        self.bucket=bucket;self.prefijo=prefijo.strip("/")
        self.firma=f"s3:{bucket}/{self.prefijo}"
        self.cliente=cliente
    def api(self):
        if self.cliente is None:
            import boto3
            from botocore.config import Config
            self.cliente=boto3.client("s3",config=Config(connect_timeout=10,read_timeout=60,
                retries={"mode":"standard","max_attempts":3}))
        return self.cliente
    def clave(self,nombre):return f"{self.prefijo}/{nombre}" if self.prefijo else nombre
    def enviar(self,archivo,nombre):
        self.api().upload_file(str(archivo),self.bucket,self.clave(nombre),
            ExtraArgs={"ContentType":"application/gzip","ServerSideEncryption":"AES256"})
    def recibir(self,nombre,salida):
        self.api().download_file(self.bucket,self.clave(nombre),str(salida))


def destino_configurado(base,entorno):
    ruta=entorno.get("AVARAID_COPIAS_CARPETA","").strip()
    bucket=entorno.get("AVARAID_BACKUP_BUCKET","").strip()
    if ruta and bucket:raise ErrorCopia("Elige un solo destino externo: carpeta o S3.")
    if bucket:return DestinoS3(bucket,entorno.get("AVARAID_BACKUP_PREFIX","avaraid"))
    if not ruta:
        config=base.parent/"ajustes-copias.json"
        if config.is_file():
            ruta=json.loads(config.read_text(encoding="utf-8"))["carpeta_externa"]
    return DestinoCarpeta(ruta,base) if ruta else None


def configurar_carpeta(base,ruta=None):
    if ruta is None:
        from tkinter import Tk,filedialog
        raiz=Tk();raiz.withdraw()
        try:ruta=filedialog.askdirectory(parent=raiz,title="Selecciona una carpeta privada sincronizada o una unidad de red",mustexist=True)
        finally:raiz.destroy()
        if not ruta:return False
    dest=DestinoCarpeta(ruta,base.resolve())
    base.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=base.parent,delete=False) as f:
        tmp=Path(f.name);json.dump({"carpeta_externa":str(dest.ruta)},f,ensure_ascii=False)
    try:os.replace(tmp,base.parent/"ajustes-copias.json")
    finally:tmp.unlink(missing_ok=True)
    return True


def _anotar_archivo(p,estado):
    f=p.stat()
    actual=estado["copias"].get(p.name,{})
    if actual.get("tam")==f.st_size and actual.get("mtime")==f.st_mtime_ns and actual.get("sha"):
        return actual
    verificar_copia(p,p.parent)
    datos={"tam":f.st_size,"mtime":f.st_mtime_ns,"sha":huella(p)}
    estado["copias"][p.name]=datos
    return datos


def ciclo_copias(base,*,entorno=None,ahora=None):
    base=Path(base).resolve()
    if not base.is_file():raise ErrorCopia("No se encontró la base de AvaRaid; no se creó una vacía.")
    dia=(ahora or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    d=base.parent/"copias";d.mkdir(mode=0o700,exist_ok=True)
    estado=leer_estado(d);local=d/f"ava-{dia}.sqlite3.gz";creada=False
    if not local.exists():
        estado["copias"][local.name]=crear_copia(base,local);creada=True
    try:_anotar_archivo(local,estado)
    except (OSError,EOFError,ValueError,sqlite3.Error):
        estado["copias"][local.name]=crear_copia(base,local);creada=True;_anotar_archivo(local,estado)
    archivos=sorted(p for p in d.glob("ava-*.sqlite3.gz") if PATRON.fullmatch(p.name))
    estado["local"]=dia
    salida={"dia":dia,"creada":creada,"enviadas":0,"aviso":None}
    estado["externo_actual"]=None
    try:
        dest=destino_configurado(base,os.environ if entorno is None else entorno)
        if dest:
            firma=hashlib.sha256(dest.firma.encode("utf-8")).hexdigest()
            estado["externo_actual"]={"tipo":dest.tipo,"id":firma}
            # Primero hoy, después otros días pendientes, incluso tras reiniciar el bot.
            for p in [local]+[p for p in archivos if p!=local]:
                if salida["enviadas"]>=4:break
                dato=_anotar_archivo(p,estado)
                if dato.get("destino")==firma:continue
                clave=f"{p.name[:-len('.sqlite3.gz')]}-{dato['sha']}.sqlite3.gz"
                with tempfile.TemporaryDirectory(dir=d) as temp:
                    recibido=Path(temp)/"descargada.gz"
                    dest.enviar(p,clave);dest.recibir(clave,recibido)
                    if huella(recibido)!=dato["sha"]:raise ErrorCopia("Los bytes recuperados no coinciden.")
                    verificar_copia(recibido,temp)
                dato.update(destino=firma,objeto=clave);salida["enviadas"]+=1
                guardar_estado(d,estado)
        else:
            salida["aviso"]="Copia local activa; todavía no has configurado un destino externo para los respaldos."
        # Con un destino externo, nunca purgar una copia pendiente.
        for p in archivos[:-14]:
            if dest and estado["copias"].get(p.name,{}).get("destino")!=firma:continue
            p.unlink(missing_ok=True);estado["copias"].pop(p.name,None)
    except Exception as exc:
        # No guardar excepciones del proveedor: pueden contener direcciones privadas.
        salida["aviso"]=f"Copia externa pendiente ({type(exc).__name__}); comprueba el destino, sus permisos y la conexión."
    estado["aviso"]=salida["aviso"]
    guardar_estado(d,estado);return salida


def main():
    from main import carpeta
    parser=argparse.ArgumentParser(description="Copias verificadas de AvaRaid")
    sub=parser.add_subparsers(dest="orden",required=True)
    sub.add_parser("estado",help="Última copia local y externa confirmada")
    sel=sub.add_parser("elegir-carpeta",help="Elegir una carpeta sincronizada o de red (Windows)")
    sel.add_argument("ruta",nargs="?")
    sub.add_parser("ahora",help="Crear/verificar la copia diaria; cierra el bot antes de usar este modo manual")
    v=sub.add_parser("verificar");v.add_argument("archivo",type=Path)
    r=sub.add_parser("restaurar");r.add_argument("archivo",type=Path);r.add_argument("--salida",required=True,type=Path)
    args=parser.parse_args();base=carpeta()/"avalonianas.sqlite3"
    try:
        if args.orden=="elegir-carpeta":
            if os.environ.get("AVARAID_BACKUP_BUCKET") or os.environ.get("AVARAID_COPIAS_CARPETA"):
                raise ErrorCopia("Ya se eligió un destino mediante variables de entorno. Cámbialas antes de elegir carpeta.")
            elegido=configurar_carpeta(base,args.ruta)
            print("Carpeta guardada. El bot la comprobará en los próximos 15 minutos." if elegido else "Selección cancelada.")
        elif args.orden=="verificar":verificar_copia(args.archivo);print("Restauración de prueba correcta.")
        elif args.orden=="restaurar":
            restaurar(args.archivo,args.salida);print(f"Base recuperada en {args.salida}.")
        else:
            if args.orden=="ahora":
                from main import unica_instancia
                with unica_instancia(base.parent):
                    res=ciclo_copias(base)
                print(res["aviso"] or "Copia diaria verificada.")
            e=leer_estado(base.parent/"copias");dest=e.get("externo_actual") or {}
            verificadas=sorted((n for n,v in e["copias"].items() if dest.get("id") and v.get("destino")==dest["id"]),reverse=True)
            print("Última copia local (UTC):",e.get("local") or "Ninguna")
            print("Última copia verificada en destino:",verificadas[0] if verificadas else "Ninguna")
            if e.get("aviso"):print(e["aviso"])
            if args.orden=="ahora" and (os.environ.get("AVARAID_BACKUP_BUCKET") or os.environ.get("AVARAID_COPIAS_CARPETA") or (base.parent/"ajustes-copias.json").is_file()) and e.get("aviso"):return 2
        return 0
    except (OSError,ValueError,EOFError,sqlite3.Error) as exc:
        print("No se pudo completar la operación:",exc);return 2


if __name__=="__main__":
    raise SystemExit(main())
