"""Pruebas sin conexión ni dependencia externa de Discord para la presentación de balances."""
import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import AsyncMock

from raids import Almacen, Regla

ROOT=Path(__file__).resolve().parents[1]


class Embed:
    def __init__(self, title, color, timestamp):
        self.title,self.color,self.timestamp=title,color,timestamp
        self.fields=[];self.footer=None
    def add_field(self, name, value, inline=True):
        self.fields.append(NS(name=name,value=value,inline=inline))
    def set_footer(self,text):
        self.footer=NS(text=text)


def load_parts():
    tree=ast.parse((ROOT/'avisos_economia.py').read_text(encoding='utf-8'))
    selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ('tarjeta_balance','EntregasEconomia')]
    # El método de envío usa globals del módulo real pero no requiere red al ser simulado.
    env={
        'discord':NS(Embed=Embed,HTTPException=Exception,Forbidden=Exception,NotFound=Exception),
        'datetime':datetime,'timezone':timezone,'asyncio':asyncio,
        'NADIE':NS(everyone=False),
        'time':NS(time=lambda:1800000001),
        'firma':lambda r:f"Movimiento #{r['mov']} · AvaRaid",
        'literal':lambda s:str(s).replace('@','@\\u200b'),
        'log':NS(warning=lambda *a:None),
        'estado_silver':lambda r:'',
    }
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(ROOT/'avisos_economia.py'),'exec'),env)
    return env


class Visual(unittest.TestCase):
    def test_tres_columnas_tarjeta_deposito(self):
        env=load_parts();r={'mov':4,'delta':500_000,'anterior':0,'posterior':500_000,'fecha':1800000000,'usuario':77}
        e=env['tarjeta_balance'](r,'[OUT] KFemto')
        self.assertEqual(e.title,'💸 BALANCE ACTUALIZADO')
        self.assertEqual(e.color,0x23C45E)
        self.assertEqual([f.name for f in e.fields],['Usuario','Silver agregado','Saldo anterior','Nuevo saldo'])
        self.assertEqual([f.inline for f in e.fields],[False,True,True,True])
        self.assertEqual([f.value for f in e.fields[1:]],['🪙 +500,000','🪙 0','🪙 500,000'])
        self.assertIn('KFemto',e.fields[0].value)
    def test_tres_columnas_tarjeta_retiro(self):
        env=load_parts();r={'mov':8,'delta':-5_763_181,'anterior':5_763_181,'posterior':0,'fecha':1800000000,'usuario':77}
        e=env['tarjeta_balance'](r,'[OUT] KFemto')
        self.assertEqual(e.color,0xED4245)
        self.assertEqual(e.fields[1].name,'Silver retirado')
        self.assertEqual([f.value for f in e.fields[1:]],['🪙 -5,763,181','🪙 5,763,181','🪙 0'])
        self.assertEqual(e.footer.text,'Movimiento #8 · AvaRaid')
    def test_registro_real_no_altera_repartos_y_evade_duplicados(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Almacen(Path(tmp)/'datos.sqlite3',lambda:1800000000)
            r=db.ajustar_balance(1,51,1200,99,'Ajuste','manual-101')
            rr=db.ajustar_balance(1,51,1200,99,'Ajuste','manual-101')
            self.assertEqual(r['mov'],rr['mov'])
            self.assertEqual(db.saldo(1,51),1200)
            a=db.ajustar_balance(1,51,300,99,'Retiro','manual-102',True)
            self.assertEqual((a['anterior'],a['posterior'],a['delta']),(1200,900,-300))
            self.assertEqual(db.saldo(1,51),900)
            self.assertEqual(len(db.depositos(1,51,20)),2)


class Publicacion(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env=load_parts();self.obj=self.env['EntregasEconomia']()
        self.cfg={'balance_logs':None}
        async def datos(oper,*args):
            if oper=='config':return self.cfg
            raise AssertionError(oper)
        self.obj.datos=datos
        self.obj.get_guild=lambda guild:NS(text_channels=[])
    async def test_sin_configuracion_publica_en_canal_del_comando(self):
        self.assertEqual(await self.obj.destino_balance(1,4321),4321)
    async def test_canal_balance_logs_tiene_prioridad(self):
        self.cfg['balance_logs']=2345
        self.assertEqual(await self.obj.destino_balance(1,4321),2345)
    async def test_canal_reintento_conserva_destino(self):
        self.obj.cerrojos={}
        recibo={'mov':7,'guild':1,'mensaje':None,'marca':None,'canal':None,
            'usuario':60,'autor':70,'delta':100,'anterior':50,'posterior':150,'fecha':1800000000,
            'ref':'7','motivo':'test'}
        rows=[]
        async def datos(op,*args):
            if op=='recibo':return recibo
            if op=='config':return {'balance_logs':None}
            if op=='intento_recibo':
                recibo['canal']=args[2];recibo['marca']=args[3];rows.append(('intento',args))
            if op=='resultado_recibo':
                recibo['mensaje']=args[2] if len(args)>2 else None;rows.append(('final',args))
        self.obj.datos=datos
        msg=NS(id=111)
        canal=NS(id=4321,guild=NS(id=1),send=AsyncMock(return_value=msg))
        self.obj.canal=AsyncMock(return_value=canal)
        self.obj.get_guild=lambda guild:NS(text_channels=[])
        url=await self.obj.entregar_balance(1,7,canal_origen=4321,nombre='KFemto')
        self.assertEqual(url,'https://discord.com/channels/1/4321/111')
        self.assertEqual(canal.send.await_count,1)
        self.assertEqual(canal.send.await_args.kwargs['embed'].title,'💸 BALANCE ACTUALIZADO')
        self.assertFalse(canal.send.await_args.kwargs['allowed_mentions'].everyone)
        await self.obj.entregar_balance(1,7,canal_origen=4321,nombre='KFemto')
        self.assertEqual(canal.send.await_count,1)
