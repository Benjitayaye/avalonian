"""Comprobación de paneles y comandos con discord.py instalado; sin conectar al servidor."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

if importlib.util.find_spec('discord') is not None:
    import discord
    from comandos import registrar
    from panel import Cliente, Panel, mostrar_panel, tarjeta_cancelado, tarjeta_reparto, tarjeta_pago_completado
    from raids import Almacen

@unittest.skipUnless(importlib.util.find_spec('discord') is not None, 'requiere discord.py')
class DiscordLocal(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.t=1800000000
        self.bd=Almacen(Path(d.name)/'ava.db',lambda:self.t)
        self.bot=Cliente(self.bd); registrar(self.bot)
        self.n=self.bd.crear(1,10,45,'Prueba','normal',self.t+1000)
        self.bd.publicar(self.n,99)

    def test_comandos_y_paneles(self):
        esperado={'crear-ava','configurar-ava','editar-build','editar-loot',
          'iniciar-ava','cancelar-ava','pagar_evento','money','money-top',
          'force-join','force-leave','give-bal','historial-de-depositos',
          'add-bal','retirar','reset-economia','cuentas','ayuda-ava','avas-pendientes'}
        self.assertEqual({x.name for x in self.bot.tree.get_commands()},esperado)
        for tipo in ('normal','sin_ga'):
            panel=Panel(tipo)
            self.assertTrue(panel.is_persistent())
            self.assertTrue(any(x.label=='SignOut' for x in panel.children))
            self.assertFalse(any(x.label=='Builds y botín' for x in panel.children))
            self.assertTrue(all(x.disabled for x in Panel(tipo,False).children))

    def test_tarjetas_cancelar_y_pago(self):
        r=self.bd.ficha(1,self.n)
        self.assertIn('Programación Hora Local:',mostrar_panel(r).description)
        self.bd.cerrar(1,self.n,'cancelada')
        r=self.bd.ficha(1,self.n)
        panel=mostrar_panel(r)
        self.assertEqual(panel.title,'❌ RAID CANCELADA')
        self.assertEqual(panel.description,'La actividad **Prueba** fue cancelada.')
        log=tarjeta_cancelado(r,None)
        self.assertEqual(log.title,'❌ EVENTO CANCELADO')
        self.assertEqual(log.fields[-1].value,'Sin registrar')
        self.assertEqual(tarjeta_cancelado(r,45).fields[-1].value,'<@45>')
        self.assertIn('por <@45>',mostrar_panel(dict(r,ultimo_actor=45)).description)

    def test_pago_y_finalizacion(self):
        self.bd.anotar(1,self.n,10,'Main Tank')
        self.bd.cerrar(1,self.n,'iniciada')
        datos=self.bd.pagar_evento(1,self.n,14_000,11_000,10,45)
        r=self.bd.ficha(1,self.n)
        self.assertEqual(r['estado'],'pagada')
        self.assertIn('AVA PAGADA Y FINALIZADA',mostrar_panel(r).description)
        self.assertEqual(tarjeta_reparto(r,datos).title,'💰 REPARTO REGISTRADO')
        self.assertEqual(tarjeta_pago_completado(r,datos).title,'✅ REPARTO COMPLETADO')

if __name__=='__main__':unittest.main()
