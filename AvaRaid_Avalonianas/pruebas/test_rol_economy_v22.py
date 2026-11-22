"""Control del rol Economy: almacenamiento, migración y autorización sin Discord."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sqlite3
import unittest
from raids import Almacen, Regla, autorizado_para_pagar


class PermisosEconomy(unittest.TestCase):
    def setUp(self):
        carpeta=TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.path=Path(carpeta.name)/"ava.sqlite3"
        self.db=Almacen(self.path)

    def test_sin_economy_nadie_puede_pagar(self):
        cfg=self.db.config(4)
        self.assertIsNone(cfg['economy'])
        self.assertFalse(autorizado_para_pagar(cfg,[SimpleNamespace(id=123)]))
        self.assertFalse(autorizado_para_pagar(cfg,[]))

    def test_rol_economy_es_el_unico_permitido(self):
        cfg=self.db.configurar(4,{'economy':333, 'caller':777})
        self.assertTrue(autorizado_para_pagar(cfg,[SimpleNamespace(id=333)]))
        self.assertTrue(autorizado_para_pagar(cfg,[SimpleNamespace(id=777),SimpleNamespace(id=333)]))
        self.assertFalse(autorizado_para_pagar(cfg,[SimpleNamespace(id=777)]))
        self.assertFalse(autorizado_para_pagar(cfg,[SimpleNamespace(id=999)]))
        self.assertFalse(autorizado_para_pagar(cfg,[]))
        self.assertFalse(autorizado_para_pagar(cfg,None))
        self.assertIsNone(self.db.config(5)['economy'])

    def test_persistir_reasignar_y_desconfigurar_role(self):
        self.db.configurar(4,{'economy':333,'caller':777,'silver_tab':444})
        self.assertEqual(Almacen(self.path).config(4)['economy'],333)
        cfg=self.db.configurar(4,{'economy':555})
        self.assertEqual(cfg['caller'],777)
        self.assertEqual(cfg['silver_tab'],444)
        self.assertFalse(autorizado_para_pagar(cfg,[SimpleNamespace(id=333)]))
        self.assertTrue(autorizado_para_pagar(cfg,[SimpleNamespace(id=555)]))
        cfg=self.db.configurar(4,{'economy':None})
        self.assertFalse(autorizado_para_pagar(cfg,[SimpleNamespace(id=555)]))
        self.assertEqual(cfg['caller'],777)

    def test_economy_no_acepta_otro_campo(self):
        with self.assertRaises(Regla):
            self.db.configurar(4,{'otro':222})

    def test_migracion_v21_sin_perdida_ni_borrado_saldos(self):
        # Simula la tabla servidor tal como estaba en V21.
        with sqlite3.connect(self.path) as c:
            c.execute('DROP TABLE servidor')
            c.execute('''CREATE TABLE servidor (
                guild INTEGER PRIMARY KEY, caller INTEGER, core INTEGER,
                avisos INTEGER, registro INTEGER, silver_tab INTEGER)''')
            c.execute('INSERT INTO servidor VALUES (4,777,888,999,111,222)')
            c.execute('INSERT INTO economia_movimiento(guild,usuario,delta,clase,evento,autor,fecha,detalle) VALUES (4,101,500,"manual",NULL,777,1234,"saldo previo")')
        anterior=Almacen(self.path)
        cfg=anterior.config(4)
        self.assertIsNone(cfg['economy'])
        self.assertEqual([cfg[k] for k in ('caller','core','avisos','registro','silver_tab')], [777,888,999,111,222])
        self.assertEqual(anterior.saldo(4,101),500)
        anterior.configurar(4,{'economy':333})
        self.assertEqual(Almacen(self.path).config(4)['economy'],333)
        self.assertEqual(Almacen(self.path).saldo(4,101),500)


if __name__=='__main__':
    unittest.main()
