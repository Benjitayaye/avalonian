"""Composiciones y reglas de inscripción de las avalonianas. No depende de Discord."""
from __future__ import annotations
import json
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from seguimiento import Seguimiento, ESQUEMA_SEGUIMIENTO, renovar_silver

BASE = Path(__file__).resolve().parent
PLANTILLAS = json.loads((BASE / "composiciones.json").read_text(encoding="utf-8"))
assert set(PLANTILLAS) == {"normal", "sin_ga"}


class Regla(ValueError):
    """Un mensaje breve que se puede mostrar al jugador."""


def autorizado_para_pagar(config, roles):
    """Solo autoriza a quien tenga el rol economy configurado para su servidor.

    Administrador, dueño, caller y otros roles NO son excepciones. Sin rol
    configurado, se niega el pago por defecto.
    """
    seleccionado = config.get("economy")
    return seleccionado is not None and any(getattr(rol, "id", rol) == seleccionado for rol in (roles or ()))


def horario(hora, fecha, ahora=None):
    ahora = ahora or datetime.now(timezone.utc)
    if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", hora.strip()) or fecha not in ("hoy", "manana"):
        raise Regla("Usa una hora UTC válida, por ejemplo 22:15, y elige Hoy o Mañana.")
    h, m = map(int, hora.split(":"))
    inicio = (ahora.astimezone(timezone.utc) + timedelta(days=int(fecha == "manana"))).replace(hour=h, minute=m, second=0, microsecond=0)
    if inicio <= ahora:
        raise Regla("Esa hora UTC ya pasó. Elige Mañana u otra hora de hoy.")
    return int(inicio.timestamp())


def nombre_rol(plan, texto):
    for rol in plan["roles"]:
        if rol.casefold() == " ".join(texto.split()).casefold():
            return rol
    raise Regla("Ese rol no pertenece a esta composición. Elige uno de la lista.")


def plantilla(tipo):
    if tipo not in PLANTILLAS:
        raise Regla("Elige Normal o Sin GA.")
    return json.loads(json.dumps(PLANTILLAS[tipo]))


def asignacion(plan, rol, plaza):
    opciones = plan["roles"][rol]["loot"]
    return opciones[min(plaza-1, len(opciones)-1)] if plaza and opciones else "Sin definir"


ESQUEMA = """
CREATE TABLE IF NOT EXISTS servidor(
 guild INTEGER PRIMARY KEY, caller INTEGER, core INTEGER, avisos INTEGER, registro INTEGER, silver_tab INTEGER, economy INTEGER);
CREATE TABLE IF NOT EXISTS ajustes(
 guild INTEGER NOT NULL, tipo TEXT NOT NULL, rol TEXT NOT NULL, campo TEXT NOT NULL, valor TEXT NOT NULL,
 PRIMARY KEY(guild,tipo,rol,campo));
CREATE TABLE IF NOT EXISTS raid(
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, canal INTEGER NOT NULL, autor INTEGER NOT NULL,
 tipo TEXT NOT NULL, nombre TEXT NOT NULL, inicio INTEGER NOT NULL, prioridad INTEGER NOT NULL, plan TEXT NOT NULL,
 estado TEXT NOT NULL DEFAULT 'publicando', cierre INTEGER, mensaje INTEGER UNIQUE, hilo INTEGER UNIQUE, espejo INTEGER UNIQUE,
 ultimo_actor INTEGER, silver_canal INTEGER, silver_mensaje INTEGER, silver_hilo INTEGER, revision INTEGER NOT NULL DEFAULT 0, pintada INTEGER NOT NULL DEFAULT -1);
CREATE INDEX IF NOT EXISTS ix_fecha ON raid(estado,inicio);
CREATE INDEX IF NOT EXISTS ix_canal ON raid(guild,canal,id);
CREATE TABLE IF NOT EXISTS puesto(
 raid INTEGER NOT NULL REFERENCES raid(id), usuario INTEGER NOT NULL, rol TEXT NOT NULL, plaza INTEGER,
 core INTEGER NOT NULL, orden INTEGER NOT NULL, PRIMARY KEY(raid,usuario), UNIQUE(raid,rol,plaza));
CREATE TABLE IF NOT EXISTS cambios(
 raid INTEGER NOT NULL REFERENCES raid(id), usuario INTEGER NOT NULL, cambios INTEGER NOT NULL DEFAULT 0,
 bajas INTEGER NOT NULL DEFAULT 0, hasta INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(raid,usuario));
CREATE TABLE IF NOT EXISTS aviso(
 raid INTEGER NOT NULL REFERENCES raid(id), tipo TEXT NOT NULL, situacion TEXT NOT NULL DEFAULT 'pendiente',
 proximo INTEGER NOT NULL DEFAULT 0, intentos INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(raid,tipo));
CREATE TABLE IF NOT EXISTS reparto(
 raid INTEGER PRIMARY KEY REFERENCES raid(id), datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS economia_movimiento(
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, usuario INTEGER NOT NULL,
 delta INTEGER NOT NULL, clase TEXT NOT NULL, evento INTEGER, autor INTEGER NOT NULL,
 fecha INTEGER NOT NULL, detalle TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_movimientos_usuario ON economia_movimiento(guild,usuario,id);
CREATE TABLE IF NOT EXISTS economia_corte(
 guild INTEGER PRIMARY KEY, ultimo_id INTEGER NOT NULL DEFAULT 0, fecha INTEGER NOT NULL, autor INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cuentas_evento(
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, evento INTEGER NOT NULL,
 carniceria INTEGER NOT NULL, autor INTEGER NOT NULL, fecha INTEGER NOT NULL,
 fotos TEXT NOT NULL, enlace TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS publicacion(
 raid INTEGER NOT NULL, tipo TEXT NOT NULL, mensaje INTEGER,
 PRIMARY KEY(raid,tipo));
"""


class Almacen(Seguimiento):
    def __init__(self, ruta, reloj=time.time):
        self.ruta, self.reloj = Path(ruta), reloj
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._con()) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(ESQUEMA + ESQUEMA_SEGUIMIENTO)
            cols={x[1] for x in c.execute("PRAGMA table_info(raid)").fetchall()}
            if 'ultimo_actor' not in cols:
                c.execute("ALTER TABLE raid ADD COLUMN ultimo_actor INTEGER")
            for campo in ('silver_canal','silver_mensaje','silver_hilo'):
                if campo not in cols:
                    c.execute(f"ALTER TABLE raid ADD COLUMN {campo} INTEGER")
            serv={x[1] for x in c.execute("PRAGMA table_info(servidor)").fetchall()}
            if 'silver_tab' not in serv:
                c.execute("ALTER TABLE servidor ADD COLUMN silver_tab INTEGER")
            if 'economy' not in serv:
                c.execute("ALTER TABLE servidor ADD COLUMN economy INTEGER")
            if "balance_logs" not in serv:
                c.execute("ALTER TABLE servidor ADD COLUMN balance_logs INTEGER")
            if c.execute("INSERT OR IGNORE INTO cambios_version(clave) VALUES('silver-estados-22.1')").rowcount:
                # Editar solo mensajes existentes, sin recrear convocatorias ni reenviar ajustes antiguos.
                c.execute("INSERT OR IGNORE INTO silver_repintar(raid) SELECT id FROM raid WHERE silver_mensaje IS NOT NULL")
            c.commit()

    def _con(self):
        c = sqlite3.connect(self.ruta, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def _tx(self, escribir=False):
        with closing(self._con()) as c, c:
            c.execute("BEGIN IMMEDIATE" if escribir else "BEGIN")
            yield c

    def config(self, guild):
        with self._tx() as c:
            r = c.execute("SELECT * FROM servidor WHERE guild=?", (guild,)).fetchone()
        return dict(r) if r else dict(guild=guild, caller=None, core=None, avisos=None, registro=None, silver_tab=None, economy=None, balance_logs=None)

    def configurar(self, guild, cambios):
        if not cambios or any(k not in ("caller","core","avisos","registro","silver_tab","economy","balance_logs") for k in cambios):
            raise Regla("Elige el rol que quieras configurar.")
        with self._tx(True) as c:
            c.execute("INSERT OR IGNORE INTO servidor(guild) VALUES(?)", (guild,))
            for k,v in cambios.items():
                c.execute(f"UPDATE servidor SET {k}=? WHERE guild=?", (v,guild))
            if "balance_logs" in cambios:
                c.execute("UPDATE recibo_balance SET proximo=0,intentos=0 WHERE guild=? AND mensaje IS NULL",(guild,))
        return self.config(guild)

    def editar(self, guild, tipo, rol, campo, valor):
        rol = nombre_rol(plantilla(tipo), rol)
        valor = valor.strip()
        if campo == "build":
            try:
                parsed = urlsplit(valor)
                valido = (parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
                          and not parsed.password and len(valor) <= 1000 and not re.search(r"[\s<>]", valor))
            except ValueError:
                valido = False
            if valor and not valido: raise Regla("Usa un enlace HTTPS válido o un guion para quitar la imagen.")
        elif campo == "loot":
            partes = [p.strip() for p in valor.split(",") if p.strip()]
            if not partes or any(len(p)>90 for p in partes) or len(partes)>plantilla(tipo)["roles"][rol]["cupos"]:
                raise Regla("Escribe una asignación por plaza, separada por comas (hasta 90 caracteres por plaza).")
            valor = partes
        else:
            raise Regla("Ajuste desconocido.")
        with self._tx(True) as c:
            c.execute("INSERT OR REPLACE INTO ajustes VALUES(?,?,?,?,?)",(guild,tipo,rol,campo,json.dumps(valor)))
        return rol

    def crear(self, guild, canal, autor, nombre, tipo, inicio, prioridad=False):
        plan=plantilla(tipo); nombre=" ".join(nombre.split())
        if not 1<=len(nombre)<=80: raise Regla("El nombre debe tener entre 1 y 80 caracteres.")
        if inicio<=int(self.reloj()): raise Regla("La hora de la avaloniana ya pasó.")
        with self._tx(True) as c:
            if prioridad and not c.execute("SELECT core FROM servidor WHERE guild=? AND core IS NOT NULL",(guild,)).fetchone():
                raise Regla("Configura el rol Core con /configurar-ava o crea la raid sin prioridad Core.")
            for x in c.execute("SELECT rol,campo,valor FROM ajustes WHERE guild=? AND tipo=?",(guild,tipo)):
                plan["roles"][x["rol"]][x["campo"]]=json.loads(x["valor"])
            return c.execute("INSERT INTO raid(guild,canal,autor,nombre,tipo,inicio,prioridad,plan) VALUES(?,?,?,?,?,?,?,?)",
                          (guild,canal,autor,nombre,tipo,inicio,int(prioridad),json.dumps(plan))).lastrowid

    def publicar(self, raid, mensaje):
        with self._tx(True) as c:
            if not c.execute("UPDATE raid SET mensaje=?,estado='abierta',revision=revision+1 WHERE id=? AND estado='publicando'",
                             (mensaje,raid)).rowcount: raise Regla("La convocatoria ya fue publicada o retirada.")

    def descartar_borrador(self, raid):
        with self._tx(True) as c: c.execute("DELETE FROM raid WHERE id=? AND estado='publicando'",(raid,))

    def vincular_hilo(self,raid,hilo,espejo):
        with self._tx(True) as c:
            c.execute("UPDATE raid SET hilo=?,espejo=?,revision=revision+1 WHERE id=?",(hilo,espejo,raid))

    def vincular_silver_tab(self,guild,raid,canal,mensaje):
        """Guarda el mensaje antes de abrir el hilo para evitar duplicarlo al reintentar."""
        with self._tx(True) as c:
            r=self._leer(c,guild,raid)
            if r['silver_mensaje'] is not None:
                return False
            c.execute("UPDATE raid SET silver_canal=?,silver_mensaje=? WHERE id=?",(canal,mensaje,raid))
            renovar_silver(c,raid)
            return True

    def vincular_silver_hilo(self,guild,raid,hilo):
        with self._tx(True) as c:
            r=self._leer(c,guild,raid)
            if r['silver_hilo'] is not None:
                return False
            c.execute("UPDATE raid SET silver_hilo=? WHERE id=?",(hilo,raid))
            return True

    @staticmethod
    def _leer(c, guild, raid):
        r = c.execute("SELECT * FROM raid WHERE id=? AND guild=?",(raid,guild)).fetchone()
        if not r: raise Regla("No se encontró esa avaloniana en este servidor.")
        return r

    def ficha(self, guild, raid):
        with self._tx() as c:
            r=dict(self._leer(c,guild,raid));r["plan"]=json.loads(r["plan"])
            pago=c.execute("SELECT datos FROM reparto WHERE raid=?",(raid,)).fetchone() if r["estado"] in ("iniciada","pagada") else None
            r["ya_pagada"]=bool(pago) or r["estado"]=="pagada"
            r["fecha_pago"]=json.loads(pago[0]).get("fecha") if pago else None
            r["jugadores"]=[dict(x) for x in c.execute("SELECT usuario,rol,plaza,core,orden FROM puesto WHERE raid=? ORDER BY orden,usuario",(raid,))]
            return r

    def desde_boton(self,guild,canal,mensaje):
        with self._tx() as c:
            fila=c.execute("SELECT id FROM raid WHERE guild=? AND ((canal=? AND mensaje=?) OR (hilo=? AND espejo=?))",
                           (guild,canal,mensaje,canal,mensaje)).fetchone()
        if not fila: raise Regla("Este panel no pertenece a una avaloniana activa de esta versión.")
        return fila[0]

    def identificar(self,guild,canal,texto,estados=()):
        try: raid=int(str(texto).strip().lstrip("#"))
        except ValueError: raise Regla("Elige la avaloniana de la lista o escribe su número.") from None
        if not 0<raid<2**63: raise Regla("Número de avaloniana inválido.")
        with self._tx() as c:
            r=self._leer(c,guild,raid)
            if canal not in (r["canal"],r["hilo"]): raise Regla("Usa este comando en el canal o el hilo de esa avaloniana.")
            if r["estado"]=="publicando" or (estados and r["estado"] not in estados):
                raise Regla("La avaloniana ya no está en el estado necesario para este comando.")
        return raid

    def listar(self,guild,canal,estados=("abierta",),buscar=""):
        if not estados:return []
        qs=",".join("?" for _ in estados)
        with self._tx() as c:
            rows=c.execute(f"""SELECT id,nombre,tipo,inicio,estado,canal,mensaje FROM raid WHERE guild=? AND (canal=? OR hilo=?)
                AND estado IN ({qs}) AND (instr(lower(nombre),lower(?)) OR instr(CAST(id AS TEXT),?))
                ORDER BY CASE estado WHEN 'abierta' THEN 0 ELSE 1 END,CASE WHEN estado='abierta' THEN inicio ELSE -inicio END,id DESC LIMIT 25""",
                (guild,canal,canal,*estados,buscar,buscar))
            return [dict(x) for x in rows]

    def _abierta(self,c,guild,raid):
        r=self._leer(c,guild,raid)
        if r["estado"]!="abierta" or r["inicio"]<=int(self.reloj()):
            raise Regla("La inscripción está cerrada.")
        return r

    @staticmethod
    def _promover(c,r,rol,plaza):
        if plaza is None:return None
        x=c.execute("""SELECT usuario FROM puesto WHERE raid=? AND rol=? AND plaza IS NULL
            ORDER BY CASE WHEN ? THEN core ELSE 0 END DESC,orden,usuario LIMIT 1""",(r["id"],rol,r["prioridad"])).fetchone()
        if x: c.execute("UPDATE puesto SET plaza=? WHERE raid=? AND usuario=?",(plaza,r["id"],x[0]))
        return x[0] if x else None

    def _limites(self,c,raid,uid):
        ahora=int(self.reloj())
        c.execute("INSERT OR IGNORE INTO cambios(raid,usuario) VALUES(?,?)",(raid,uid))
        c.execute("UPDATE cambios SET hasta=0,cambios=0,bajas=0 WHERE raid=? AND usuario=? AND hasta>0 AND hasta<=?",(raid,uid,ahora))
        return c.execute("SELECT * FROM cambios WHERE raid=? AND usuario=?",(raid,uid)).fetchone()

    def anotar(self,guild,raid,uid,rol,core=False,forzado=False):
        ahora=int(self.reloj())
        with self._tx(True) as c:
            if forzado:
                # Solo los comandos de organización pueden añadir asistentes tras el inicio.
                # No se puede alterar la lista si ya se registró algún pago.
                r=self._leer(c,guild,raid)
                if r["estado"] not in ("abierta", "iniciada") or c.execute(
                    "SELECT 1 FROM reparto WHERE raid=?", (raid,)
                ).fetchone():
                    raise Regla("Solo puedes agregar gente a una AVA abierta o iniciada y aún no pagada.")
            else:
                r=self._abierta(c,guild,raid)
            plan=json.loads(r["plan"]);rol=nombre_rol(plan,rol)
            viejo=c.execute("SELECT rol,plaza FROM puesto WHERE raid=? AND usuario=?",(raid,uid)).fetchone()
            if viejo and viejo["rol"]==rol:
                return dict(rol=rol,plaza=viejo["plaza"],igual=True)
            limites=self._limites(c,raid,uid)
            if not forzado:
                if limites["hasta"]>ahora:
                    raise Regla(f"Podrás volver a anotarte en esta raid <t:{limites['hasta']}:R>.")
                if viejo and limites["cambios"]>=4:
                    c.execute("UPDATE cambios SET hasta=? WHERE raid=? AND usuario=?",(ahora+1800,raid,uid))
                    return dict(bloqueo=ahora+1800)
            turno=c.execute("SELECT coalesce(max(orden),0)+1 FROM puesto WHERE raid=?",(raid,)).fetchone()[0]
            promovido=desplazado=None
            if viejo:
                c.execute("DELETE FROM puesto WHERE raid=? AND usuario=?",(raid,uid))
                promovido=self._promover(c,r,viejo["rol"],viejo["plaza"])
                if not forzado:c.execute("UPDATE cambios SET cambios=cambios+1 WHERE raid=? AND usuario=?",(raid,uid))
            titulares=list(c.execute("SELECT usuario,plaza,core FROM puesto WHERE raid=? AND rol=? AND plaza IS NOT NULL ORDER BY orden DESC",(raid,rol)))
            ocupadas={x["plaza"] for x in titulares}
            plaza=next((i for i in range(1,plan["roles"][rol]["cupos"]+1) if i not in ocupadas),None)
            if plaza is None and core and r["prioridad"]:
                sale=next((x for x in titulares if not x["core"]),None)
                if sale:
                    plaza,desplazado=sale["plaza"],sale["usuario"]
                    c.execute("UPDATE puesto SET plaza=NULL,orden=? WHERE raid=? AND usuario=?",(turno,raid,desplazado))
                    turno+=1
            c.execute("INSERT INTO puesto(raid,usuario,rol,plaza,core,orden) VALUES(?,?,?,?,?,?)",(raid,uid,rol,plaza,int(core),turno))
            c.execute("UPDATE raid SET revision=revision+1 WHERE id=?",(raid,))
            return dict(rol=rol,plaza=plaza,promovido=promovido,desplazado=desplazado,igual=False)

    def salir(self,guild,raid,uid,forzado=False):
        with self._tx(True) as c:
            if forzado:
                r=self._leer(c,guild,raid)
                if r["estado"] not in ("abierta","iniciada") or c.execute(
                    "SELECT 1 FROM reparto WHERE raid=?",(raid,)
                ).fetchone():
                    raise Regla("Solo puedes retirar gente de una AVA abierta o iniciada y aún no pagada.")
            else:
                r=self._abierta(c,guild,raid)
            x=c.execute("SELECT rol,plaza FROM puesto WHERE raid=? AND usuario=?",(raid,uid)).fetchone()
            if not x:raise Regla("Este jugador no está inscrito en esta avaloniana.")
            c.execute("DELETE FROM puesto WHERE raid=? AND usuario=?",(raid,uid))
            promovido=self._promover(c,r,x["rol"],x["plaza"])
            if not forzado:
                l=self._limites(c,raid,uid)
                c.execute("UPDATE cambios SET bajas=bajas+1,hasta=? WHERE raid=? AND usuario=?",
                          (max(l["hasta"],int(self.reloj())+1800 if l["bajas"]>=4 else 0),raid,uid))
            c.execute("UPDATE raid SET revision=revision+1 WHERE id=?",(raid,))
            return dict(rol=x["rol"],promovido=promovido)

    def _fin(self,c,raid,estado,actor=None):
        c.execute("UPDATE raid SET estado=?,cierre=?,ultimo_actor=?,revision=revision+1 WHERE id=?",(estado,int(self.reloj()),actor,raid))
        renovar_silver(c,raid)
        c.execute("UPDATE aviso SET situacion='omitido' WHERE raid=? AND tipo='30m' AND situacion='pendiente'",(raid,))
        c.execute("INSERT OR IGNORE INTO aviso(raid,tipo) VALUES(?,?)",(raid,estado))

    def cerrar(self,guild,raid,estado,actor=None):
        if estado not in ("iniciada","cancelada"):raise Regla("Estado inválido.")
        with self._tx(True) as c:
            r=self._leer(c,guild,raid)
            if r["estado"]!="abierta":raise Regla("Esta avaloniana ya está cerrada.")
            self._fin(c,raid,estado,actor)
            if estado=="cancelada":
                c.execute("UPDATE aviso SET situacion='omitido' WHERE raid=? AND tipo='cancelada'",(raid,))

    def vencimientos(self):
        ahora=int(self.reloj())
        with self._tx(True) as c:
            for r in c.execute("SELECT id,inicio FROM raid r WHERE estado='abierta' AND inicio<=? AND (inicio<=? OR NOT EXISTS(SELECT 1 FROM aviso a WHERE a.raid=r.id AND a.tipo='30m')) ORDER BY inicio LIMIT 200",(ahora+1800,ahora)).fetchall():
                if r["inicio"]<=ahora:self._fin(c,r["id"],"iniciada")
                else:c.execute("INSERT OR IGNORE INTO aviso(raid,tipo) VALUES(?,'30m')",(r["id"],))

    def tomar_avisos(self):
        ahora=int(self.reloj())
        with self._tx(True) as c:
            rows=list(c.execute("""SELECT a.raid,a.tipo,a.intentos,r.guild FROM aviso a JOIN raid r ON r.id=a.raid
                WHERE a.situacion='pendiente' AND a.proximo<=? ORDER BY a.proximo,a.raid LIMIT 12""",(ahora,)))
            for x in rows:c.execute("UPDATE aviso SET proximo=?,intentos=intentos+1 WHERE raid=? AND tipo=?",(ahora+120,x["raid"],x["tipo"]))
            return [dict(x) for x in rows]

    def aviso_listo(self,raid,tipo,ok,intentos=0):
        with self._tx(True) as c:
            if ok:c.execute("UPDATE aviso SET situacion='enviado' WHERE raid=? AND tipo=? AND situacion='pendiente'",(raid,tipo))
            else:c.execute("UPDATE aviso SET proximo=? WHERE raid=? AND tipo=?",(int(self.reloj())+min(1800,30*2**min(intentos,6)),raid,tipo))

    def refrescar_iconos(self):
        """Solicita repintar paneles abiertos al iniciar una versión con iconos nuevos."""
        with self._tx(True) as c:
            return c.execute("UPDATE raid SET pintada=-1 WHERE mensaje IS NOT NULL AND estado IN ('abierta','iniciada')").rowcount

    def sucias(self):
        with self._tx() as c:
            return [(x[0],x[1]) for x in c.execute("SELECT guild,id FROM raid WHERE mensaje IS NOT NULL AND revision<>pintada ORDER BY id DESC")]

    def pintada(self,raid,revision):
        with self._tx(True) as c:c.execute("UPDATE raid SET pintada=? WHERE id=? AND revision=?",(revision,raid,revision))

    def repartir(self,guild,raid,total=None,tasa=10,pagador=None):
        with self._tx(True) as c:
            r=self._leer(c,guild,raid)
            previo=c.execute("SELECT datos FROM reparto WHERE raid=?",(raid,)).fetchone()
            if previo:
                res=json.loads(previo[0])
                if total is not None and (total!=res["total"] or tasa!=res["tasa"]):
                    raise Regla("Esta raid ya tiene un reparto registrado. Omite el total para consultarlo.")
                return res
            if total is None:raise Regla("Esta raid aún no tiene un reparto registrado.")
            if r["estado"]!="iniciada":raise Regla("El reparto se registra después de iniciar la avaloniana.")
            if type(total) is not int or not 0<total<=10**12 or type(tasa) is not int or not 0<=tasa<=100:
                raise Regla("Usa un total positivo y un impuesto de 0 a 100.")
            ids=[x[0] for x in c.execute("SELECT usuario FROM puesto WHERE raid=? AND plaza IS NOT NULL ORDER BY orden,usuario",(raid,))]
            if not ids:raise Regla("Esta avaloniana no tiene titulares para repartir el botín.")
            impuesto=total*tasa//100; base,extra=divmod(total-impuesto,len(ids))
            res=dict(total=total,tasa=tasa,impuesto=impuesto,pagador=pagador,pagos=[[u,base+int(i<extra)] for i,u in enumerate(ids)])
            c.execute("INSERT INTO reparto VALUES(?,?)",(raid,json.dumps(res)))
            renovar_silver(c,raid)
            return res


    # ECONOMÍA: los repartos y ajustes son registros de plata virtual de Albion,
    # no realizan transferencias dentro del juego.
    @staticmethod
    def _corte(c, guild):
        x=c.execute("SELECT ultimo_id FROM economia_corte WHERE guild=?",(guild,)).fetchone()
        return x[0] if x else 0

    @classmethod
    def _saldo_en(cls,c,guild,usuario):
        x=c.execute("SELECT coalesce(sum(delta),0) FROM economia_movimiento WHERE guild=? AND usuario=? AND id>?",
                    (guild,usuario,cls._corte(c,guild))).fetchone()
        return x[0]

    def saldo(self,guild,usuario):
        with self._tx() as c:
            return self._saldo_en(c,guild,usuario)

    def ranking(self,guild,limite=25):
        with self._tx() as c:
            return [dict(x) for x in c.execute("""SELECT usuario, sum(delta) AS saldo
                FROM economia_movimiento WHERE guild=? AND id>? GROUP BY usuario
                HAVING sum(delta)>0 ORDER BY saldo DESC,usuario ASC LIMIT ?""",
                (guild,self._corte(c,guild),min(25,max(1,int(limite)))))]

    def depositos(self,guild,usuario,limite=20):
        with self._tx() as c:
            return [dict(x) for x in c.execute("""SELECT id,delta,clase,evento,autor,fecha,detalle
                FROM economia_movimiento WHERE guild=? AND usuario=? AND id>? ORDER BY id DESC LIMIT ?""",
                (guild,usuario,self._corte(c,guild),min(20,max(1,int(limite)))))]

    def _movimiento(self,c,guild,usuario,delta,clase,evento,autor,detalle=""):
        return c.execute("INSERT INTO economia_movimiento(guild,usuario,delta,clase,evento,autor,fecha,detalle) VALUES(?,?,?,?,?,?,?,?)",
                  (guild,usuario,delta,clase,evento,autor,int(self.reloj()),detalle[:140])).lastrowid

    @staticmethod
    def _importes_pago(bolsas,loot,tax_gremio):
        if any(type(x) is not int for x in (bolsas,loot,tax_gremio)) or bolsas<0 or loot<0 or bolsas+loot<=0 or bolsas+loot>10**12 or not 0<=tax_gremio<=100:
            raise Regla("Las bolsas y el loot deben ser cantidades positivas o cero; el tax debe ser un porcentaje de 0 a 100.")

    @staticmethod
    def _calcular_pago(c,raid,bolsas,loot,tax_gremio):
        ids=[x[0] for x in c.execute("SELECT usuario FROM puesto WHERE raid=? AND plaza IS NOT NULL ORDER BY orden,usuario",(raid,))]
        if not ids:raise Regla("No hay titulares para repartir. BENCH no recibe plata.")
        total=bolsas+loot
        impuesto=total*tax_gremio//100
        base,resto=divmod(total-impuesto,len(ids))
        return dict(total=total,bolsas=bolsas,loot=loot,tasa=tax_gremio,impuesto=impuesto,
                    pagos=[[u,base+int(i<resto)] for i,u in enumerate(ids)])

    def previsualizar_pago(self,guild,raid,bolsas,loot,tax_gremio):
        """Solo lee datos: jamás modifica saldos ni estado de la AVA."""
        self._importes_pago(bolsas,loot,tax_gremio)
        with self._tx() as c:
            r=self._leer(c,guild,raid)
            if c.execute("SELECT 1 FROM reparto WHERE raid=?",(raid,)).fetchone():
                raise Regla("Esta AVA ya fue pagada. No se puede registrar de nuevo.")
            if r["estado"]!="iniciada":raise Regla("La AVA debe estar iniciada para preparar su pago.")
            vista=self._calcular_pago(c,raid,bolsas,loot,tax_gremio)
            vista["revision"]=r["revision"]
            return vista

    def pagar_evento(self,guild,raid,bolsas,loot,tax_gremio,pagador,esperada_revision=None):
        self._importes_pago(bolsas,loot,tax_gremio)
        with self._tx(True) as c:
            r=self._leer(c,guild,raid)
            if c.execute("SELECT 1 FROM reparto WHERE raid=?",(raid,)).fetchone():
                raise Regla("Esta AVA ya tiene un reparto registrado. No se puede pagar dos veces.")
            if r["estado"]!="iniciada":raise Regla("Primero debe iniciarse la AVA antes de pagarla.")
            if esperada_revision is not None and r["revision"]!=esperada_revision:
                raise Regla("La lista de participantes cambió. Vuelve a revisar el reparto antes de pagarlo.")
            res=self._calcular_pago(c,raid,bolsas,loot,tax_gremio)
            res.update(pagador=pagador,fecha=int(self.reloj()))
            c.execute("INSERT INTO reparto(raid,datos) VALUES(?,?)",(raid,json.dumps(res)))
            for usuario,monto in res["pagos"]:
                self._movimiento(c,guild,usuario,monto,"pago",raid,pagador)
            c.execute("UPDATE raid SET estado='pagada',revision=revision+1 WHERE id=?",(raid,))
            renovar_silver(c,raid)
            return res

    def ver_reparto(self,guild,raid):
        with self._tx() as c:
            self._leer(c,guild,raid)
            x=c.execute("SELECT datos FROM reparto WHERE raid=?",(raid,)).fetchone()
            return json.loads(x[0]) if x else None

    def agregar_silver(self,guild,usuario,cantidad,autor):
        return self.ajustar_balance(guild,usuario,cantidad,autor,"Ajuste manual",None)["posterior"]

    def retirar_silver(self,guild,usuario,cantidad,autor):
        return self.ajustar_balance(guild,usuario,cantidad,autor,"Ajuste manual",None,retirar=True)["posterior"]

    def transferir(self,guild,emisor,receptor,cantidad):
        if emisor==receptor:raise Regla("No puedes enviarte plata a ti mismo.")
        if type(cantidad) is not int or not 0<cantidad<=10**12:raise Regla("La cantidad debe ser positiva.")
        with self._tx(True) as c:
            actual=self._saldo_en(c,guild,emisor)
            if actual<cantidad:raise Regla("Saldo insuficiente.")
            self._movimiento(c,guild,emisor,-cantidad,"transferencia",None,emisor,f"A {receptor}")
            self._movimiento(c,guild,receptor,cantidad,"transferencia",None,emisor,f"De {emisor}")
            return actual-cantidad

    def resetear_economia(self,guild,autor):
        with self._tx(True) as c:
            ultimo=c.execute("SELECT coalesce(max(id),0) FROM economia_movimiento WHERE guild=?",(guild,)).fetchone()[0]
            c.execute("INSERT INTO economia_corte(guild,ultimo_id,fecha,autor) VALUES(?,?,?,?) ON CONFLICT(guild) DO UPDATE SET ultimo_id=excluded.ultimo_id,fecha=excluded.fecha,autor=excluded.autor",
                      (guild,ultimo,int(self.reloj()),autor))
            return ultimo

    def guardar_cuentas(self,guild,raid,carniceria,autor,fotos,enlace):
        if not 1<=carniceria<=10:raise Regla("La carnicería debe estar entre 1 y 10.")
        if not fotos:raise Regla("Adjunta al menos una captura de cuentas.")
        with self._tx(True) as c:
            self._leer(c,guild,raid)
            return c.execute("INSERT INTO cuentas_evento(guild,evento,carniceria,autor,fecha,fotos,enlace) VALUES(?,?,?,?,?,?,?)",
                (guild,raid,carniceria,autor,int(self.reloj()),json.dumps(fotos),enlace)).lastrowid

    def obtener_cuentas(self,guild,raid):
        with self._tx() as c:
            self._leer(c,guild,raid)
            return [dict(x) for x in c.execute("SELECT * FROM cuentas_evento WHERE guild=? AND evento=? ORDER BY id DESC",(guild,raid))]

    def publicacion_hecha(self,raid,tipo):
        with self._tx() as c:
            return bool(c.execute("SELECT 1 FROM publicacion WHERE raid=? AND tipo=?",(raid,tipo)).fetchone())

    def guardar_publicacion(self,raid,tipo,mensaje):
        with self._tx(True) as c:
            c.execute("INSERT OR IGNORE INTO publicacion(raid,tipo,mensaje) VALUES(?,?,?)",(raid,tipo,mensaje))

    def identificar_global(self,guild,texto,estados=()):
        try: raid=int(str(texto).strip().lstrip('#'))
        except (ValueError,TypeError):raise Regla("Indica un ID de AVA numérico.") from None
        with self._tx() as c:
            r=self._leer(c,guild,raid)
            if r["estado"]=="publicando" or estados and r["estado"] not in estados:
                raise Regla("La AVA no está en el estado necesario.")
            return raid
