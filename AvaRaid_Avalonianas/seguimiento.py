"""Estado de Silver Tab y registro de ajustes manuales, aislados por servidor."""
from uuid import uuid4

ESQUEMA_SEGUIMIENTO = """
CREATE TABLE IF NOT EXISTS cambios_version(clave TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS silver_repintar(
 raid INTEGER PRIMARY KEY REFERENCES raid(id), generacion INTEGER NOT NULL DEFAULT 1,
 proximo INTEGER NOT NULL DEFAULT 0, intentos INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_silver_repintar ON silver_repintar(proximo);
CREATE TABLE IF NOT EXISTS recibo_balance(
 mov INTEGER PRIMARY KEY REFERENCES economia_movimiento(id),guild INTEGER NOT NULL,ref TEXT NOT NULL,
 anterior INTEGER NOT NULL,posterior INTEGER NOT NULL,motivo TEXT NOT NULL,
 canal INTEGER,mensaje INTEGER,marca INTEGER,proximo INTEGER NOT NULL DEFAULT 0,intentos INTEGER NOT NULL DEFAULT 0,
 UNIQUE(guild,ref));
CREATE INDEX IF NOT EXISTS ix_recibo_pendiente ON recibo_balance(proximo,mov) WHERE mensaje IS NULL;
CREATE INDEX IF NOT EXISTS ix_ava_pago ON raid(guild,estado,inicio,id);
"""


def renovar_silver(c, raid):
    c.execute("""INSERT INTO silver_repintar(raid)
        SELECT id FROM raid WHERE id=? AND silver_mensaje IS NOT NULL
        ON CONFLICT(raid) DO UPDATE SET generacion=generacion+1,proximo=0,intentos=0""", (raid,))


class Seguimiento:
    def pendientes_economy(self,guild,pagina=1):
        por_pagina=10
        filtros=""" FROM raid r WHERE r.guild=? AND r.estado='iniciada' AND r.mensaje IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM reparto p WHERE p.raid=r.id)"""
        with self._tx() as c:
            total=c.execute("SELECT count(*)"+filtros,(guild,)).fetchone()[0]
            paginas=max(1,(total+por_pagina-1)//por_pagina)
            pagina=max(1,min(int(pagina),paginas))
            rows=c.execute("""SELECT r.id,r.nombre,r.inicio,r.cierre,r.canal,r.mensaje,r.silver_hilo,
                (SELECT count(*) FROM puesto u WHERE u.raid=r.id AND plaza IS NOT NULL) titulares"""+filtros+
                " ORDER BY coalesce(r.cierre,r.inicio),r.id LIMIT ? OFFSET ?",
                (guild,por_pagina,(pagina-1)*por_pagina)).fetchall()
        return dict(total=total,pagina=pagina,paginas=paginas,filas=[dict(r) for r in rows])

    def silver_por_editar(self):
        with self._tx() as c:
            return [tuple(row) for row in c.execute("""SELECT r.guild,r.id FROM silver_repintar s JOIN raid r
                ON r.id=s.raid WHERE s.proximo<=? ORDER BY s.proximo,r.id LIMIT 10""",(int(self.reloj()),))]

    def generacion_silver(self,guild,raid):
        with self._tx() as c:
            r=c.execute("""SELECT s.generacion FROM silver_repintar s JOIN raid r ON r.id=s.raid
                WHERE r.guild=? AND r.id=?""",(guild,raid)).fetchone()
            return r[0] if r else None

    def edicion_silver(self,raid,generacion,ok):
        with self._tx(True) as c:
            if ok:
                c.execute("DELETE FROM silver_repintar WHERE raid=? AND generacion=?",(raid,generacion))
            else:
                c.execute("""UPDATE silver_repintar SET intentos=intentos+1,proximo=?+min(1800,30*(1 << min(intentos,6)))
                    WHERE raid=? AND generacion=?""",(int(self.reloj()),raid,generacion))

    def ajustar_balance(self,guild,usuario,cantidad,autor,motivo,ref,retirar=False):
        from raids import Regla
        if type(cantidad) is not int or not 0<cantidad<=10**12:
            raise Regla("La cantidad debe ser positiva y no superar 1.000.000.000.000 de silver.")
        motivo=" ".join(str(motivo or "").split())
        if not 1<=len(motivo)<=140:raise Regla("Escribe un motivo de 1 a 140 caracteres.")
        ref=str(ref) if ref is not None else uuid4().hex
        delta=-cantidad if retirar else cantidad
        clase="retiro" if retirar else "deposito_manual"
        with self._tx(True) as c:
            previo=self._recibo(c,guild,"a.ref",ref)
            if previo:
                if (previo['usuario'],previo['delta'],previo['autor'],previo['motivo']) != (usuario,delta,autor,motivo):
                    raise Regla("La referencia ya pertenece a otro movimiento.")
                return previo
            anterior=self._saldo_en(c,guild,usuario);posterior=anterior+delta
            if posterior<0:raise Regla("No hay suficiente plata para retirar esa cantidad.")
            if posterior>2**63-1:raise Regla("El saldo total excede el límite admitido.")
            mov=self._movimiento(c,guild,usuario,delta,clase,None,autor,motivo)
            c.execute("INSERT INTO recibo_balance(mov,guild,ref,anterior,posterior,motivo) VALUES(?,?,?,?,?,?)",
                      (mov,guild,ref,anterior,posterior,motivo))
            return self._recibo(c,guild,"a.mov",mov)

    @staticmethod
    def _recibo(c,guild,campo,valor):
        if campo not in ("a.ref","a.mov"):raise ValueError("Campo de recibo inválido.")
        row=c.execute(f"""SELECT a.*,m.usuario,m.autor,m.delta,m.fecha FROM recibo_balance a
            JOIN economia_movimiento m ON m.id=a.mov AND m.guild=a.guild
            WHERE a.guild=? AND {campo}=?""",(guild,valor)).fetchone()
        return dict(row) if row else None

    def recibo(self,guild,mov):
        with self._tx() as c:return self._recibo(c,guild,"a.mov",mov)

    def recibos_pendientes(self):
        with self._tx() as c:
            return [tuple(x) for x in c.execute("""SELECT guild,mov FROM recibo_balance WHERE mensaje IS NULL AND proximo<=?
                ORDER BY proximo,mov LIMIT 10""",(int(self.reloj()),))]

    def intento_recibo(self,guild,mov,canal,marca):
        with self._tx(True) as c:
            c.execute("UPDATE recibo_balance SET canal=?,marca=? WHERE guild=? AND mov=? AND mensaje IS NULL",
                      (canal,marca,guild,mov))

    def resultado_recibo(self,guild,mov,mensaje=None):
        with self._tx(True) as c:
            if mensaje is not None:
                c.execute("UPDATE recibo_balance SET mensaje=?,intentos=0 WHERE guild=? AND mov=? AND mensaje IS NULL",
                          (mensaje,guild,mov))
            else:
                c.execute("""UPDATE recibo_balance SET proximo=?+min(1800,30*(1 << min(intentos,6))),intentos=intentos+1
                    WHERE guild=? AND mov=? AND mensaje IS NULL""",(int(self.reloj()),guild,mov))
