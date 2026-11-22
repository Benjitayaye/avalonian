"""Regresiones para la composición, las plazas y la continuidad entre reinicios."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from raids import Almacen, PLANTILLAS, Regla, asignacion, horario


class Avalonianas(unittest.TestCase):
    def setUp(self):
        d=TemporaryDirectory();self.addCleanup(d.cleanup);self.path=Path(d.name)/"raid.sqlite3"
        self.ahora=1_800_000_000;self.db=Almacen(self.path,lambda:self.ahora)
        self.msg=100_000

    def raid(self,t="normal",g=1,canal=10,core=False,inicio=None):
        n=self.db.crear(g,canal,9,"Ruta del sábado",t,inicio or self.ahora+3600,core)
        self.msg+=1;self.db.publicar(n,self.msg);return n

    def poses(self,n):
        return {x["usuario"]:(x["rol"],x["plaza"]) for x in self.db.ficha(1,n)["jugadores"]}

    def test_compos_tipado_utc_y_dos_modalidades(self):
        self.assertEqual(set(PLANTILLAS),{"normal","sin_ga"})
        for plan in PLANTILLAS.values():self.assertEqual(sum(r["cupos"] for r in plan["roles"].values()),11)
        d=datetime(2026,11,21,23,58,tzinfo=timezone.utc)
        self.assertEqual(horario("00:01","manana",d),int((d+timedelta(minutes=3)).timestamp()))
        for h,fecha in (("19:00","hoy"),("24:00","manana"),("12:70","hoy"),("12:00","xxxx")):
            with self.assertRaises(Regla):horario(h,fecha,d)
        with self.assertRaises(Regla):self.raid("desconocida")

    def test_no_confunde_falce_y_falce_daga_y_respeta_loot_por_plaza(self):
        n=self.raid()
        for uid in (21,22,23,24):self.db.anotar(1,n,uid,"Falce")
        for uid in (31,32,33):self.db.anotar(1,n,uid,"Falce Daga")
        self.assertEqual(self.db.salir(1,n,32)["promovido"],33)
        self.assertEqual(self.poses(n)[24],("Falce",None))
        self.assertEqual(self.poses(n)[33],("Falce Daga",2))
        self.assertEqual(self.db.anotar(1,n,21,"scout")["promovido"],24)
        self.assertEqual(self.poses(n)[24],("Falce",1))
        self.assertEqual(self.poses(n)[23],("Falce",3))
        self.assertEqual(asignacion(self.db.ficha(1,n)["plan"],"Falce",3),"Capas")
        with self.assertRaises(Regla):self.db.anotar(1,n,21,"Fal")
        self.assertEqual(self.poses(n)[21],("Scout",1))

    def test_concurrencia_ultimas_plazas_y_sin_inscripcion_duplicada(self):
        n=self.raid()
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda uid:self.db.anotar(1,n,uid,"Falce"),[51,51,52,53,54,55,56,57,58,59]))
        pos=self.poses(n);self.assertEqual(len(pos),9)
        self.assertEqual(sorted(v[1] for v in pos.values() if v[1]),[1,2,3])
        viejo=self.db.ficha(1,n)["jugadores"]
        self.assertEqual(Almacen(self.path).ficha(1,n)["jugadores"],viejo)
        primer=next(x["usuario"] for x in viejo if x["plaza"] is None)
        titular=next(x["usuario"] for x in viejo if x["plaza"])
        self.assertEqual(self.db.salir(1,n,titular)["promovido"],primer)

    def test_core_solo_desplaza_no_core_y_promueve_por_rol(self):
        with self.assertRaises(Regla):self.raid(core=True)
        self.db.configurar(1,{"core":12});n=self.raid(core=True)
        self.db.anotar(1,n,1,"Healer")
        self.assertEqual(self.db.anotar(1,n,2,"Healer",True)["desplazado"],1)
        self.assertIsNone(self.db.anotar(1,n,3,"Healer",True)["plaza"])
        self.assertEqual(self.db.salir(1,n,2)["promovido"],3)
        self.assertEqual(self.db.salir(1,n,3)["promovido"],1)
        n2=self.raid();self.db.anotar(1,n2,1,"Healer")
        self.assertIsNone(self.db.anotar(1,n2,2,"Healer",True)["plaza"])

    def test_aislamiento_identidad_mensaje_y_ajustes_de_composicion(self):
        a=self.raid();ma=self.msg;b=self.raid();otro=self.raid(g=2);self.db.vincular_hilo(a,77,78)
        self.assertEqual(self.db.desde_boton(1,10,ma),a);self.assertEqual(self.db.desde_boton(1,77,78),a)
        for g,ch,msg in ((2,10,ma),(1,77,ma),(1,10,78)):
            with self.assertRaises(Regla):self.db.desde_boton(g,ch,msg)
        with self.assertRaises(Regla):self.db.identificar(1,77,str(b))
        with self.assertRaises(Regla):self.db.identificar(1,10,str(otro))
        self.db.cerrar(1,a,"cancelada")
        self.assertEqual([r["id"] for r in self.db.listar(1,10)], [b])
        self.db.editar(1,"normal","Healer","loot","Cristales")
        self.assertNotEqual(self.db.ficha(1,b)["plan"]["roles"]["Healer"]["loot"],["Cristales"])
        nuevo=self.raid();self.assertEqual(self.db.ficha(1,nuevo)["plan"]["roles"]["Healer"]["loot"],["Cristales"])
        self.assertNotEqual(self.db.ficha(2,otro)["plan"]["roles"]["Healer"]["loot"],["Cristales"])
        with self.assertRaises(Regla):self.db.editar(1,"sin_ga","Great Arcane","loot","X")
        with self.assertRaises(Regla):self.db.editar(1,"normal","Healer","build","http://ejemplo.test/a")

    def test_reloj_persistente_reintento_y_cierre_automatico(self):
        n=self.raid();self.ahora+=1801;self.db.vencimientos()
        aviso=self.db.tomar_avisos();self.assertEqual([a["tipo"] for a in aviso],["30m"])
        self.db.aviso_listo(n,"30m",False)
        self.db=Almacen(self.path,lambda:self.ahora)
        self.db.vencimientos();self.assertFalse(self.db.tomar_avisos())
        self.ahora+=31;self.assertEqual([a["tipo"] for a in self.db.tomar_avisos()],["30m"])
        self.db.aviso_listo(n,"30m",True)
        self.db.vencimientos();self.assertFalse(self.db.tomar_avisos())
        self.ahora+=1800;self.db.vencimientos();self.db.vencimientos()
        self.assertEqual(self.db.ficha(1,n)["estado"],"iniciada")
        self.assertEqual([a["tipo"] for a in self.db.tomar_avisos()],["iniciada"])
        with self.assertRaises(Regla):self.db.anotar(1,n,77,"Healer")
        with self.assertRaises(Regla):self.db.cerrar(1,n,"iniciada")

    def test_cancelar_no_avisa_del_inicio_ni_repite_recordatorio(self):
        n=self.raid(inicio=self.ahora+10);self.db.vencimientos()
        self.db.cerrar(1,n,"cancelada");self.ahora+=3000;self.db.vencimientos()
        # La cancelación se comunica con la tarjeta de #ava-logs, no por un segundo aviso.
        self.assertEqual(self.db.tomar_avisos(),[])

    def test_reparto_conserva_plata_no_duplica_y_no_incluye_banca(self):
        n=self.raid()
        for uid,rol in ((30,"Healer"),(31,"Healer"),(32,"Scout"),(33,"Off Tank")):
            self.db.anotar(1,n,uid,rol)
        with self.assertRaises(Regla):self.db.repartir(1,n,100,10,60)
        self.db.cerrar(1,n,"iniciada");r=self.db.repartir(1,n,103,10,60)
        self.assertEqual(r["pagos"],[[30,31],[32,31],[33,31]])
        self.assertEqual(sum(p for _,p in r["pagos"])+r["impuesto"],103)
        self.assertEqual(self.db.repartir(1,n),r)
        with self.assertRaises(Regla):self.db.repartir(1,n,105,10)
        n=self.raid()
        for i in range(3):self.db.anotar(1,n,100+i,"Falce")
        self.db.cerrar(1,n,"iniciada")
        r=self.db.repartir(1,n,101,10,42)
        self.assertEqual([p for _,p in r["pagos"]],[31,30,30])

    def test_limite_de_cambios_es_local_y_permite_retirarse(self):
        n=self.raid();u=180
        for rol in ("Healer","Scout","Healer","Scout","Healer"):self.db.anotar(1,n,u,rol)
        self.assertIn("bloqueo",self.db.anotar(1,n,u,"Scout"))
        self.assertEqual(self.poses(n)[u],("Healer",1))
        self.db.salir(1,n,u)
        with self.assertRaises(Regla):self.db.anotar(1,n,u,"Healer")
        otro=self.raid();self.assertEqual(self.db.anotar(1,otro,u,"Healer")["plaza"],1)
        self.ahora+=1801
        self.assertEqual(self.db.anotar(1,n,u,"Healer")["plaza"],1)
