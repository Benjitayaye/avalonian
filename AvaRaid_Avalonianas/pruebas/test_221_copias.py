"""Respaldos y restauraciones reales con SQLite en modo WAL; ningún acceso a la nube."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
import gzip
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from copias import ciclo_copias, leer_estado, restaurar, verificar_copia, configurar_carpeta, DestinoS3, ErrorCopia
from raids import Almacen


class Copias(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        self.tmp=Path(d.name);self.base=self.tmp/"local"/"avalonianas.sqlite3"
        self.externo=self.tmp/"remoto";self.externo.mkdir()
        self.entorno={"AVARAID_COPIAS_CARPETA":str(self.externo)}
        self.dia=datetime(2026,11,19,10,tzinfo=timezone.utc)
        self.bd=Almacen(self.base,lambda:1800000000)
        self.n=self.bd.crear(1,20,2,"Mapa","normal",1800001000);self.bd.publicar(self.n,50)
        self.bd.anotar(1,self.n,50,"Healer");self.bd.cerrar(1,self.n,"iniciada",2)
        self.bd.pagar_evento(1,self.n,12300,700,10,2)
        self.bd.ajustar_balance(1,50,80,2,"Repuesto","600")
    def ciclo(self,dia=None,entorno=None):
        return ciclo_copias(self.base,entorno=self.entorno if entorno is None else entorno,ahora=dia or self.dia)
    def test_copia_captura_wal_y_la_copia_externa_restaura_raid_saldos_y_roles(self):
        with closing(sqlite3.connect(self.base)) as c:
            c.execute("PRAGMA wal_autocheckpoint=0")
            c.execute("INSERT INTO servidor(guild,economy) VALUES(?,?)",(1,771));c.commit()
            self.assertTrue(Path(str(self.base)+"-wal").is_file())
            info=self.ciclo()
        self.assertEqual((info["creada"],info["enviadas"],info["aviso"]),(True,1,None))
        remoto,=self.externo.glob("*.gz");salida=self.tmp/"recuperada.db"
        restaurar(remoto,salida);reabierta=Almacen(salida)
        self.assertEqual(reabierta.saldo(1,50),11780)
        self.assertEqual(reabierta.config(1)["economy"],771)
        self.assertEqual(reabierta.ver_reparto(1,self.n)["total"],13000)
        self.assertEqual(reabierta.ficha(1,self.n)["estado"],"pagada")
        segundo=self.ciclo();self.assertFalse(segundo["creada"]);self.assertEqual(segundo["enviadas"],0)
        self.assertEqual(len(list(self.externo.glob("*.gz"))),1)
    def test_selector_persistente_y_carpeta_dentro_de_datos_rechazada(self):
        with self.assertRaises(ErrorCopia):configurar_carpeta(self.base,self.base.parent)
        self.assertTrue(configurar_carpeta(self.base,self.externo))
        info=self.ciclo(entorno={})
        self.assertEqual(info["enviadas"],1);self.assertIsNone(info["aviso"])
        self.assertEqual(len(list(self.externo.glob("*.gz"))),1)
    def test_no_hay_destino_no_confunde_local_con_fuera_del_servidor(self):
        info=self.ciclo(entorno={});self.assertIn("no has configurado",info["aviso"])
        estado=leer_estado(self.base.parent/"copias");self.assertIsNone(estado["externo_actual"])
        self.assertFalse(any(v.get("destino") for v in estado["copias"].values()))
        rec=self.ciclo();self.assertEqual(rec["enviadas"],1)
    def test_transporte_s3_remoto_corrompido_no_se_confirma_y_reintenta_al_dia_siguiente(self):
        class S3:
            def __init__(self):self.store={};self.corrupto=True;self.metadata=None
            def upload_file(self,archivo,bucket,clave,ExtraArgs):
                self.store[bucket,clave]=Path(archivo).read_bytes();self.metadata=ExtraArgs
            def download_file(self,bucket,clave,archivo):
                Path(archivo).write_bytes(b"copia alterada" if self.corrupto else self.store[bucket,clave])
        s=S3();remoto=DestinoS3("prueba","gremio",s)
        with patch("copias.destino_configurado",return_value=remoto):
            mala=self.ciclo();self.assertEqual(mala["enviadas"],0);self.assertIn("pendiente",mala["aviso"])
            local,=(self.base.parent/"copias").glob("*.gz");verificar_copia(local)
            s.corrupto=False;buena=self.ciclo(self.dia+timedelta(days=1));self.assertEqual(buena["enviadas"],2)
            self.assertFalse(buena["aviso"]);self.assertEqual(len(s.store),2)
            self.assertEqual(s.metadata["ServerSideEncryption"],"AES256")
            self.assertTrue(all(k.startswith("gremio/ava-") for b,k in s.store))
    def test_no_sobrescribe_base_ni_restaura_datos_ajenos_o_truncados(self):
        self.ciclo();copia,=self.externo.glob("*.gz")
        with self.assertRaises(ErrorCopia):restaurar(copia,self.base)
        distinta=self.tmp/"ajena.db";c=sqlite3.connect(distinta);c.execute("CREATE TABLE prueba(k)");c.close()
        otro=self.tmp/"ajena.gz"
        with gzip.open(otro,"wb") as f:f.write(distinta.read_bytes())
        salida=self.tmp/"no_creada.sqlite3"
        with self.assertRaises(ErrorCopia):restaurar(otro,salida)
        otro.write_bytes(copia.read_bytes()[:20])
        with self.assertRaises((EOFError,OSError)):restaurar(otro,salida)
        self.assertFalse(salida.exists());self.assertEqual(self.bd.saldo(1,50),11780)
    def test_retencion_local_y_conservacion_cuando_externo_esta_caido(self):
        mal={"AVARAID_COPIAS_CARPETA":str(self.tmp/"falta")}
        for i in range(15):self.ciclo(self.dia+timedelta(days=i),mal)
        d=self.base.parent/"copias";self.assertEqual(len(list(d.glob("*.gz"))),15)
        for _ in range(4):self.ciclo(self.dia+timedelta(days=14))
        self.assertEqual(len(list(d.glob("*.gz"))),14);self.assertEqual(len(list(self.externo.glob("*.gz"))),15)
        self.assertTrue(all(x.get("destino") for x in leer_estado(d)["copias"].values()))
