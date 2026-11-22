"""Persistencia de la integración de carnicerías en silver-tabs (sin Discord)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from raids import Almacen

class SilverTabs(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'data.sqlite3'
        self.db=Almacen(self.path,lambda:1_800_000_000)
        self.raid=self.db.crear(100,200,300,'prueba','normal',1_800_003_600)
        self.db.publicar(self.raid,400)

    def test_configuracion_silver_tab_es_independiente_de_logs(self):
        self.assertIsNone(self.db.config(100)['silver_tab'])
        self.db.configurar(100,{'registro':555,'silver_tab':777})
        self.assertEqual(self.db.config(100)['registro'],555)
        self.assertEqual(self.db.config(100)['silver_tab'],777)
        self.assertIsNone(self.db.config(101)['silver_tab'])
        self.db.configurar(100,{'silver_tab':None})
        self.assertIsNone(self.db.config(100)['silver_tab'])
        self.assertEqual(self.db.config(100)['registro'],555)

    def test_se_guarda_un_solo_mensaje_e_hilo_por_ava(self):
        self.assertTrue(self.db.vincular_silver_tab(100,self.raid,777,888))
        self.assertFalse(self.db.vincular_silver_tab(100,self.raid,777,889))
        self.assertTrue(self.db.vincular_silver_hilo(100,self.raid,999))
        self.assertFalse(self.db.vincular_silver_hilo(100,self.raid,1000))
        r=self.db.ficha(100,self.raid)
        self.assertEqual((r['silver_canal'],r['silver_mensaje'],r['silver_hilo']),(777,888,999))
        self.assertEqual((Almacen(self.path).ficha(100,self.raid)['silver_hilo']),999)
        self.assertEqual(r['mensaje'],400)

    def test_migracion_sqlite_v15_no_pierde_eventos(self):
        # Simula un archivo previo al agregado de silver_tab en la tabla servidor.
        with sqlite3.connect(self.path) as c:
            c.execute('ALTER TABLE servidor RENAME TO servidor_backup')
            c.execute('CREATE TABLE servidor(guild INTEGER PRIMARY KEY, caller INTEGER, core INTEGER, avisos INTEGER, registro INTEGER)')
            c.execute('INSERT INTO servidor(guild,registro) VALUES (100,1234)')
            c.execute('DROP TABLE servidor_backup')
            for field in ('silver_canal','silver_mensaje','silver_hilo'):
                # SQLite no permite DROP COLUMN cuando afecta a restricciones; estas columnas no tienen ninguna.
                c.execute(f'ALTER TABLE raid DROP COLUMN {field}')
        migrated=Almacen(self.path,lambda:1_800_000_000)
        self.assertEqual(migrated.ficha(100,self.raid)['mensaje'],400)
        self.assertEqual(migrated.config(100)['registro'],1234)
        self.assertIsNone(migrated.config(100)['silver_tab'])
        self.assertTrue(migrated.vincular_silver_tab(100,self.raid,777,888))

if __name__ == '__main__': unittest.main()
