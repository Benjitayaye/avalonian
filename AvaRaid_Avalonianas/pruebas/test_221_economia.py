"""Flujos completos del gremio con su SQLite real y un transporte local de Discord."""
import asyncio
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from avisos_economia import estado_silver
from comandos import registrar, puede_pagar
from panel import Cliente
from raids import Almacen,Regla


class Economia(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.now=1800000000;self.ruta=Path(t.name)/"datos.db"
        self.db=Almacen(self.ruta,lambda:self.now)

    def raid(self,g=1):
        n=self.db.crear(g,12,25,"Salida del gremio","normal",self.now+600)
        self.db.publicar(n,n+1000);self.db.anotar(g,n,11,"Main Tank");return n

    def test_todo_el_ciclo_y_listado_solo_de_este_servidor(self):
        n=self.raid();self.assertIn("Programada",estado_silver(self.db.ficha(1,n)))
        self.db.cerrar(1,n,"iniciada",25);self.assertIn("En curso",estado_silver(self.db.ficha(1,n)))
        otro=self.raid(2);self.db.cerrar(2,otro,"iniciada",25)
        can=self.raid();self.db.cerrar(1,can,"cancelada",25)
        self.assertIn("Cancelada",estado_silver(self.db.ficha(1,can)))
        self.assertEqual([r["id"] for r in self.db.pendientes_economy(1)["filas"]],[n])
        self.db.previsualizar_pago(1,n,1000,0,10)
        self.assertEqual(self.db.pendientes_economy(1)["total"],1)
        self.db.pagar_evento(1,n,1000,0,10,25)
        self.assertIn("Pagada",estado_silver(self.db.ficha(1,n)))
        self.assertEqual(self.db.pendientes_economy(1)["total"],0)
        self.assertEqual(self.db.pendientes_economy(2)["total"],1)

    def test_paginacion_e_inicio_automatico_y_reparto_antiguo(self):
        ids=[self.raid() for _ in range(12)]
        self.now+=610;self.db.vencimientos()
        self.db.repartir(1,ids[-1],100,0,25)  # Forma de reparto histórica: no cambia estado='iniciada'.
        a=self.db.pendientes_economy(1);b=self.db.pendientes_economy(1,9000)
        self.assertEqual((a["total"],a["paginas"],b["pagina"]),(11,2,2))
        self.assertEqual([r["id"] for r in a["filas"]+b["filas"]],ids[:-1])
        self.assertIn("Pagada",estado_silver(self.db.ficha(1,ids[-1])))

    def test_no_se_acredita_dos_veces_y_motivo_es_inmutable(self):
        def sumar(_):return self.db.ajustar_balance(1,55,250,99,"Devolución de mapa","9182")
        with ThreadPoolExecutor(4) as ex:
            ids={r["mov"] for r in ex.map(sumar,range(8))}
        self.assertEqual(len(ids),1);self.assertEqual(self.db.saldo(1,55),250)
        with self.assertRaises(Regla):self.db.ajustar_balance(1,55,251,99,"Devolución de mapa","9182")
        with self.assertRaises(Regla):self.db.ajustar_balance(1,55,300,99,"Demasiado","9183",True)
        with self.assertRaises(Regla):self.db.ajustar_balance(1,55,5,99,"  ","9183")
        r=self.db.ajustar_balance(1,55,100,99,"Reparación","9183",True)
        self.assertEqual((r["anterior"],r["posterior"],r["delta"]),(250,150,-100))
        self.assertIsNone(self.db.recibo(2,r["mov"]))
        nueva=Almacen(self.ruta,lambda:self.now)
        self.assertEqual(len(nueva.recibos_pendientes()),2);self.assertEqual(nueva.saldo(1,55),150)


class Canal:
    def __init__(self,id_,guild=1):
        self.id=id_;self.guild=NS(id=guild);self.name="🪙「balance-logs」";self.mensajes=[]
        self.corte=False
    async def send(self,**kw):
        m=NS(id=1+len(self.mensajes),author=NS(id=99),embeds=[kw["embed"]],menciones=kw["allowed_mentions"])
        self.mensajes.append(m)
        if self.corte:
            self.corte=False
            raise OSError("Corte después de aceptar el mensaje")
        return m
    async def history(self,**_):
        for m in self.mensajes:yield m


class Discord(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.ruta=Path(t.name)/"a.db";self.t=1800000000
        self.canal=Canal(171);self.config_bot()
    def config_bot(self):
        self.db=Almacen(self.ruta,lambda:self.t);self.bot=Cliente(self.db);registrar(self.bot)
        self.bot.get_guild=lambda _:NS(text_channels=[self.canal])
        self.bot._connection.user=NS(id=99)
        self.bot.canal=AsyncMock(return_value=self.canal)
    def interaccion(self,ref):
        return NS(id=ref,client=self.bot,guild_id=1,guild=NS(id=1),user=NS(id=61,roles=[]),
                  response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))
    async def test_pendientes_exige_economy_y_muestra_enlace(self):
        i=self.interaccion(55);i.permissions=NS(administrator=True);self.db.configurar(1,{"economy":15})
        cmd=self.bot.tree.get_command("avas-pendientes")
        self.assertIn(puede_pagar,cmd.checks);self.assertFalse(await puede_pagar(i))
        i.user.roles=[NS(id=15)];self.assertTrue(await puede_pagar(i))
        n=self.db.crear(1,22,61,"Prueba","normal",self.t+60);self.db.publicar(n,47)
        self.db.cerrar(1,n,"iniciada",61)
        await cmd.callback(i)
        self.assertIn("https://discord.com/channels/1/22/47",i.followup.send.await_args.kwargs["embed"].fields[0].value)
        self.assertTrue(i.followup.send.await_args.kwargs["ephemeral"])
    async def test_dos_comandos_publican_el_recibo_correcto_y_sin_ping(self):
        u=NS(id=51,bot=False,mention="<@51>")
        for cmd,ref,cantidad in (("add-bal",555,1200),("retirar",556,200)):
            i=self.interaccion(ref)
            await self.bot.tree.get_command(cmd).callback(i,u,cantidad,"Reembolso @everyone")
            self.assertIn("Ver recibo público",i.followup.send.await_args.args[0])
        self.assertEqual(len(self.canal.mensajes),2)
        e=self.canal.mensajes[1].embeds[0];campos=[x.value for x in e.fields]
        self.assertIn("<@51>",campos);self.assertIn("<@61>",campos)
        self.assertIn("1,200 silver",campos);self.assertIn("1,000 silver",campos);self.assertIn("-200 silver",campos)
        self.assertFalse(self.canal.mensajes[1].menciones.everyone)
        self.assertEqual(self.db.saldo(1,51),1000)
    async def test_corte_despues_de_enviar_y_reinicio_recupera_el_recibo(self):
        r=self.db.ajustar_balance(1,51,140,61,"Mapa","83245")
        self.canal.corte=True
        self.assertIsNone(await self.bot.entregar_balance(1,r["mov"]))
        self.config_bot();href=await self.bot.entregar_balance(1,r["mov"])
        self.assertTrue(href.endswith("/171/1"));self.assertEqual(self.db.saldo(1,51),140)
        self.assertEqual(self.db.recibos_pendientes(),[]);self.assertEqual(len(self.canal.mensajes),1)
    async def test_sin_canal_guarda_y_reintenta_al_configurar(self):
        self.bot.get_guild=lambda _:NS(text_channels=[])
        r=self.db.ajustar_balance(1,51,140,61,"Mapa","83245")
        self.assertIsNone(await self.bot.entregar_balance(1,r["mov"]))
        self.assertEqual(self.db.recibos_pendientes(),[])
        self.db.configurar(1,{"balance_logs":171})
        self.assertEqual(self.db.recibos_pendientes(),[(1,r["mov"])])
        await self.bot.entregar_balance(1,r["mov"]);self.assertEqual(len(self.canal.mensajes),1)
    async def test_silver_recupera_edicion_fallida_y_respeta_cambio_durante_la_edicion(self):
        n=self.db.crear(1,30,2,"AVA","normal",self.t+60);self.db.publicar(n,700)
        self.db.vincular_silver_tab(1,n,171,701);self.db.anotar(1,n,51,"Healer")
        self.db.cerrar(1,n,"iniciada",2)
        ed=AsyncMock(side_effect=OSError("simulado"));self.canal.get_partial_message=lambda _:NS(edit=ed)
        r=self.db.ficha(1,n)
        self.assertFalse(await self.bot.actualizar_silver(r));self.assertEqual(self.db.silver_por_editar(),[])
        self.t+=60;self.assertEqual(self.db.silver_por_editar(),[(1,n)])
        async def pagar_mientras_edita(**kw):self.db.pagar_evento(1,n,1000,0,0,2)
        ed.side_effect=pagar_mientras_edita;await self.bot.actualizar_silver(r)
        self.assertIn("En curso",ed.await_args.kwargs["content"])
        self.assertEqual(self.db.silver_por_editar(),[(1,n)])
        ed.side_effect=None;await self.bot.actualizar_silver(r)
        self.assertIn("Pagada",ed.await_args.kwargs["content"]);self.assertEqual(self.db.silver_por_editar(),[])
