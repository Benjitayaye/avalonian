"""Reglas sin conexión Discord: economía, registro y datos persistentes."""
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from raids import Almacen, Regla

class EconomiaNueva(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        self.path=Path(d.name)/'ava.db'; self.t=1_800_000_000
        self.bd=Almacen(self.path,lambda:self.t)
        self.raid=self.bd.crear(1,10,55,'Prueba 8.2','normal',self.t+3600)
        self.bd.publicar(self.raid,100)
        for uid,rol in ((10,'Main Tank'),(11,'Off Tank'),(12,'Healer'),(13,'Healer')):
            self.bd.anotar(1,self.raid,uid,rol)
        self.bd.cerrar(1,self.raid,'iniciada')

    def test_reparto_contabiliza_una_vez_y_no_paga_a_banca(self):
        res=self.bd.pagar_evento(1,self.raid,10_000_000,17_000_000,0,55)
        self.assertEqual(res['total'],27_000_000)
        self.assertEqual(sum(v for _,v in res['pagos']),27_000_000)
        self.assertEqual([uid for uid,_ in res['pagos']],[10,11,12])
        self.assertEqual(self.bd.saldo(1,13),0)
        self.assertEqual(self.bd.ficha(1,self.raid)['estado'],'pagada')
        self.assertEqual(sum(x['saldo'] for x in self.bd.ranking(1)),27_000_000)
        with self.assertRaisesRegex(Regla,'ya tiene un reparto'):
            self.bd.pagar_evento(1,self.raid,10_000_000,17_000_000,0,55)
        self.assertEqual(sum(x['saldo'] for x in self.bd.ranking(1)),27_000_000)
        self.assertEqual(self.bd.ver_reparto(1,self.raid),res)
        # Reiniciar el proceso no borra los saldos ni vuelve a acreditar el reparto.
        nueva=Almacen(self.path,lambda:self.t)
        self.assertEqual(nueva.saldo(1,10),res['pagos'][0][1])

    def test_tax_redondeo_y_orden(self):
        res=self.bd.pagar_evento(1,self.raid,15,88,10,55)
        self.assertEqual(res['impuesto'],10)
        self.assertEqual(sum(v for _,v in res['pagos']),93)
        self.assertEqual([v for _,v in res['pagos']],[31,31,31])

    def test_transferencia_retiro_y_reset_aislados(self):
        self.bd.pagar_evento(1,self.raid,105,0,0,55)
        self.assertEqual(self.bd.saldo(1,10),35)
        self.assertEqual(self.bd.transferir(1,10,50,20),15)
        self.assertEqual(self.bd.saldo(1,50),20)
        self.assertEqual(self.bd.retirar_silver(1,50,7,55),13)
        self.assertEqual([x['saldo'] for x in self.bd.ranking(1)],[35,35,15,13])
        with self.assertRaises(Regla):self.bd.transferir(1,10,50,16)
        with self.assertRaises(Regla):self.bd.retirar_silver(1,50,1000,55)
        self.assertEqual(self.bd.saldo(2,50),0)
        self.assertEqual(len(self.bd.depositos(1,50)),2)
        self.bd.resetear_economia(1,55)
        self.assertEqual(self.bd.ranking(1),[])
        self.assertEqual(self.bd.saldo(1,11),0)
        self.assertEqual(self.bd.depositos(1,50),[])
        self.assertEqual(self.bd.ver_reparto(1,self.raid)['total'],105)

    def test_add_bal_acredita_registra_y_separa_servidores(self):
        self.assertEqual(self.bd.saldo(1,77),0)
        self.assertEqual(self.bd.agregar_silver(1,77,2_500_000,55),2_500_000)
        self.assertEqual(self.bd.agregar_silver(1,77,500_000,55),3_000_000)
        self.assertEqual(self.bd.saldo(1,77),3_000_000)
        self.assertEqual(self.bd.saldo(2,77),0)
        self.assertEqual(self.bd.ranking(1)[0]['usuario'],77)
        movimientos=self.bd.depositos(1,77)
        self.assertEqual([m['delta'] for m in movimientos],[500_000,2_500_000])
        self.assertTrue(all(m['clase']=='deposito_manual' and m['autor']==55 for m in movimientos))
        self.assertEqual(self.bd.transferir(1,77,88,100_000),2_900_000)
        self.assertEqual(self.bd.saldo(1,88),100_000)
        nueva=Almacen(self.path,lambda:self.t)
        self.assertEqual(nueva.saldo(1,77),2_900_000)
        self.assertEqual(nueva.saldo(1,88),100_000)
        self.assertIsNone(self.bd.ver_reparto(1,self.raid))

    def test_add_bal_rechaza_cantidades_invalidas_y_respeta_reset(self):
        for cantidad in (0,-1,10**12+1,1.5,True,None):
            with self.subTest(cantidad=cantidad),self.assertRaises(Regla):
                self.bd.agregar_silver(1,77,cantidad,55)
        self.assertEqual(self.bd.saldo(1,77),0)
        self.bd.agregar_silver(1,77,10**12,55)
        self.assertEqual(self.bd.saldo(1,77),10**12)
        self.bd.resetear_economia(1,55)
        self.assertEqual(self.bd.saldo(1,77),0)
        self.assertEqual(self.bd.depositos(1,77),[])
        self.assertEqual(self.bd.agregar_silver(1,77,234,55),234)
        self.assertEqual(len(self.bd.depositos(1,77)),1)

    def test_add_bal_simultaneo_no_pierde_movimientos(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            saldos=list(pool.map(lambda _:self.bd.agregar_silver(1,77,100,55),range(10)))
        self.assertEqual(sorted(saldos),[x*100 for x in range(1,11)])
        self.assertEqual(self.bd.saldo(1,77),1_000)
        self.assertEqual(len(self.bd.depositos(1,77)),10)

    def test_cuentas_estado_y_marcador_publicacion(self):
        self.assertEqual(self.bd.obtener_cuentas(1,self.raid),[])
        cod=self.bd.guardar_cuentas(1,self.raid,4,55,['https://example.test/imagen.png'],'https://discord.com/x')
        self.assertEqual(self.bd.obtener_cuentas(1,self.raid)[0]['id'],cod)
        with self.assertRaises(Regla):self.bd.guardar_cuentas(2,self.raid,4,55,['u'],'')
        with self.assertRaises(Regla):self.bd.guardar_cuentas(1,self.raid,11,55,['u'],'')
        self.assertFalse(self.bd.publicacion_hecha(self.raid,'reparto'))
        self.bd.guardar_publicacion(self.raid,'reparto',123)
        self.bd.guardar_publicacion(self.raid,'reparto',124)
        self.assertTrue(self.bd.publicacion_hecha(self.raid,'reparto'))
        self.assertEqual(self.bd.identificar_global(1,str(self.raid),('iniciada',)),self.raid)
        with self.assertRaises(Regla):self.bd.identificar_global(2,str(self.raid))

    def test_concurrencia_no_duplicar_saldos(self):
        def intentar(_):
            try:return self.bd.pagar_evento(1,self.raid,111,0,0,55)
            except Regla:return None
        with ThreadPoolExecutor(max_workers=5) as pool:
            resultados=list(pool.map(intentar,range(5)))
        self.assertEqual(sum(r is not None for r in resultados),1)
        self.assertEqual(sum(x['saldo'] for x in self.bd.ranking(1)),111)

if __name__=='__main__': unittest.main()
