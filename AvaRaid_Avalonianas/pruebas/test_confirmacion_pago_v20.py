"""Verificaciones que no requieren una conexión a Discord."""
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from raids import Almacen,Regla

class ConfirmacionEconomiaV20(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        self.now=1_800_000_000
        self.bd=Almacen(Path(d.name)/'base.db',lambda:self.now)
        self.raid=self.bd.crear(1,22,55,'Avaloniana prueba','normal',self.now+600)
        self.bd.publicar(self.raid,222)
        self.bd.anotar(1,self.raid,101,'Main Tank')
        self.bd.anotar(1,self.raid,102,'Healer')
        self.bd.cerrar(1,self.raid,'iniciada')

    def test_force_join_despues_de_inicio_y_antes_de_pago(self):
        with self.assertRaises(Regla):self.bd.anotar(1,self.raid,103,'Scout')
        r=self.bd.anotar(1,self.raid,103,'Scout',False,True)
        self.assertEqual(r['plaza'],1)
        self.assertEqual(self.bd.ficha(1,self.raid)['estado'],'iniciada')
        pago=self.bd.pagar_evento(1,self.raid,100,0,0,55)
        self.assertEqual(len(pago['pagos']),3)
        with self.assertRaises(Regla):self.bd.anotar(1,self.raid,104,'Scout',False,True)
        self.assertEqual(self.bd.saldo(1,104),0)

    def test_force_leave_corrige_la_lista_antes_de_pagar(self):
        self.bd.anotar(1,self.raid,103,'Falce',False,True)
        with self.assertRaises(Regla):self.bd.salir(1,self.raid,103)
        self.bd.salir(1,self.raid,103,True)
        vista=self.bd.previsualizar_pago(1,self.raid,100,0,0)
        self.assertEqual(len(vista['pagos']),2)
        self.bd.pagar_evento(1,self.raid,100,0,0,55,vista['revision'])
        with self.assertRaises(Regla):self.bd.salir(1,self.raid,101,True)

    def test_vista_previa_no_paga_y_edicion_recalcula(self):
        vista=self.bd.previsualizar_pago(1,self.raid,1_000_000,2_000_000,10)
        self.assertEqual(vista['total'],3_000_000)
        self.assertEqual(vista['impuesto'],300_000)
        self.assertEqual([amount for _,amount in vista['pagos']],[1_350_000,1_350_000])
        self.assertIsNone(self.bd.ver_reparto(1,self.raid))
        self.assertEqual(self.bd.saldo(1,101),0)
        otra=self.bd.previsualizar_pago(1,self.raid,2_000_000,2_000_000,5)
        self.assertEqual(otra['total']-otra['impuesto'],3_800_000)
        self.assertEqual(self.bd.saldo(1,101),0)
        self.assertEqual(self.bd.ficha(1,self.raid)['estado'],'iniciada')

    def test_revision_cambia_con_force_join_e_invalida_confirmacion(self):
        vieja=self.bd.previsualizar_pago(1,self.raid,100,200,0)
        self.bd.anotar(1,self.raid,103,'Off Tank',False,True)
        with self.assertRaisesRegex(Regla,'cambió'):
            self.bd.pagar_evento(1,self.raid,100,200,0,55,vieja['revision'])
        self.assertIsNone(self.bd.ver_reparto(1,self.raid))
        self.assertEqual(self.bd.saldo(1,101),0)
        actual=self.bd.previsualizar_pago(1,self.raid,100,200,0)
        self.assertNotEqual(actual['revision'],vieja['revision'])
        pagado=self.bd.pagar_evento(1,self.raid,100,200,0,55,actual['revision'])
        self.assertEqual(len(pagado['pagos']),3)
        self.assertEqual(sum(self.bd.saldo(1,u) for u in [101,102,103]),300)

    def test_pago_una_vez_y_bloqueo_de_agregar_posterior(self):
        vista=self.bd.previsualizar_pago(1,self.raid,10,9,0)
        def confirmar(_):
            try:return self.bd.pagar_evento(1,self.raid,10,9,0,55,vista['revision'])
            except Regla:return None
        with ThreadPoolExecutor(max_workers=3) as pool:
            resultados=list(pool.map(confirmar,range(3)))
        self.assertEqual(sum(x is not None for x in resultados),1)
        self.assertEqual(sum(self.bd.saldo(1,u) for u in [101,102]),19)
        with self.assertRaises(Regla):self.bd.anotar(1,self.raid,103,'Healer',False,True)

    def test_cancelar_previa_no_hace_mutaciones(self):
        vista=self.bd.previsualizar_pago(1,self.raid,100,200,5)
        self.assertTrue(vista['pagos'])
        # El botón Cancelar descarta esta vista sin invocar pagar_evento.
        self.assertEqual(self.bd.ficha(1,self.raid)['estado'],'iniciada')
        self.assertIsNone(self.bd.ver_reparto(1,self.raid))
        self.assertEqual(self.bd.ranking(1),[])

if __name__=='__main__':unittest.main()
