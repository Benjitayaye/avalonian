"""Presentación en Discord. Entrega los recibos sin volver a tocar los saldos."""
import asyncio
import logging
import time
from datetime import datetime, timezone

import discord

NADIE=discord.AllowedMentions.none()
log=logging.getLogger("avaraid")


def literal(s):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(s)))


def estado_silver(r):
    nombre=f"Ava N° {r['id']:04d} ({literal(r['nombre'])})"
    if r['estado']=='cancelada':
        estado="Cancelada"; fecha=r.get("cierre")
    elif r.get('ya_pagada') or r['estado']=='pagada':
        estado="Pagada"; fecha=r.get("fecha_pago")
    elif r['estado']=='iniciada':
        estado="En curso · Pendiente de pago"; fecha=r.get("cierre") or r['inicio']
    else:
        estado="Programada"; fecha=r['inicio']
    return f"{nombre} - {estado}"+(f" · <t:{fecha}:f>" if fecha else "")


def pendientes_embed(datos,guild):
    e=discord.Embed(title="🪙 AVA pendientes de pago",color=0xD6A636)
    if not datos["total"]:
        e.description="No hay AVA iniciadas pendientes de pago en este servidor."
    else:
        e.description="De la más antigua a la más reciente. Abre una AVA para revisar sus participantes."
        for r in datos["filas"]:
            url=f"https://discord.com/channels/{guild}/{r['canal']}/{r['mensaje']}"
            e.add_field(name=f"N.º {r['id']:04d} · {literal(r['nombre'])}",
                        value=f"<t:{r['cierre'] or r['inicio']}:f> · {r['titulares']} titular(es) · [Ver AVA]({url})"+
                              (f" · <#{r['silver_hilo']}>" if r['silver_hilo'] else ""),inline=False)
    e.set_footer(text=f"{datos['total']} pendiente(s) · Página {datos['pagina']}/{datos['paginas']}")
    return e


def firma(r):
    return f"Movimiento #{r['mov']} · AvaRaid"


def tarjeta_balance(r, nombre=None):
    """Recibo público compacto del movimiento, igual que la tarjeta de Discord.

    Los importes se extraen del recibo transaccional de SQLite; nunca se
    recalculan consultando el saldo actual, que podría haber cambiado.
    """
    retiro=r['delta']<0
    e=discord.Embed(title="💸 BALANCE ACTUALIZADO",
                    color=0xED4245 if retiro else 0x23C45E,
                    timestamp=datetime.fromtimestamp(r['fecha'],timezone.utc))
    jugador=f"<@{r['usuario']}>"
    if nombre:
        jugador+=f"\n**{literal(nombre)[:150]}**"
    e.add_field(name="Usuario",value=jugador,inline=False)
    e.add_field(name="Silver retirado" if retiro else "Silver agregado",
                value=f"🪙 {r['delta']:+,}",inline=True)
    e.add_field(name="Saldo anterior",value=f"🪙 {r['anterior']:,}",inline=True)
    e.add_field(name="Nuevo saldo",value=f"🪙 {r['posterior']:,}",inline=True)
    # El motivo y quién realizó la operación siguen guardados en SQLite.
    e.set_footer(text=firma(r))
    return e


class EntregasEconomia:
    async def destino_balance(self,guild,canal_origen=None):
        cfg=await self.datos("config",guild)
        # Un canal configurado expresamente para recibos tiene prioridad.
        if cfg.get("balance_logs"):return cfg["balance_logs"]
        # Sin configuración, el registro va al mismo canal del comando,
        # siempre como mensaje público (nunca ephemeral).
        if canal_origen:return canal_origen
        canales=getattr(self.get_guild(guild),"text_channels",())
        candidatos=[c.id for c in canales if "".join(x for x in c.name.casefold() if x.isalnum())=="balancelogs"]
        return candidatos[0] if len(candidatos)==1 else None

    async def entregar_balance(self,guild,mov,canal_origen=None,nombre=None):
        async with self.cerrojos.setdefault(("balance",guild,mov),asyncio.Lock()):
            r=await self.datos("recibo",guild,mov)
            if not r:return None
            def enlace(c,m):return f"https://discord.com/channels/{guild}/{c}/{m}"
            if r['mensaje']:return enlace(r['canal'],r['mensaje'])
            try:
                msg=None
                if r["marca"] and r["canal"]:
                    # Si Discord recibió el mensaje antes del reinicio, recuperar su ID incluso después de caducar el nonce.
                    try:
                        ch=await self.canal(r["canal"])
                        desde=datetime.fromtimestamp(r["marca"]-5,timezone.utc)
                        async for m in ch.history(limit=None,after=desde,oldest_first=True):
                            if m.author.id==self.user.id and any(getattr(e.footer,"text",None)==firma(r) for e in m.embeds):
                                msg=m;break
                    except discord.NotFound as exc:
                        if exc.code!=10003:raise
                if msg is None:
                    # Reintentos: conservar el canal original del recibo si ya
                    # se intentó su publicación, incluso tras un reinicio.
                    destino=r['canal'] or await self.destino_balance(guild,canal_origen)
                    if not destino:
                        await self.datos("resultado_recibo",guild,mov);return None
                    ch=await self.canal(destino)
                    if ch.guild.id!=guild:
                        await self.datos("resultado_recibo",guild,mov);return None
                    await self.datos("intento_recibo",guild,mov,ch.id,int(time.time()))
                    nonce="B"+(r["ref"] if r["ref"].isdigit() and len(r["ref"])<=23 else str(mov))
                    try:
                        if nombre is None:
                            servidor=self.get_guild(guild)
                            miembro=servidor.get_member(r['usuario']) if servidor and hasattr(servidor,'get_member') else None
                            nombre=getattr(miembro,'display_name',None)
                        msg=await ch.send(embed=tarjeta_balance(r,nombre),allowed_mentions=NADIE,nonce=nonce)
                    except (discord.Forbidden,discord.NotFound):
                        await self.datos("intento_recibo",guild,mov,None,None)
                        raise
                await self.datos("resultado_recibo",guild,mov,msg.id)
                return enlace(ch.id,msg.id)
            except (OSError,discord.HTTPException) as exc:
                log.warning("Recibo de balance #%s pendiente (%s).",mov,type(exc).__name__)
                await self.datos("resultado_recibo",guild,mov)
                return None

    async def actualizar_silver(self,r):
        guild,raid=r["guild"],r["id"]
        async with self.cerrojos.setdefault(("silver",raid),asyncio.Lock()):
            gen=await self.datos("generacion_silver",guild,raid)
            if gen is None:return False
            actual=await self.datos("ficha",guild,raid)
            if not actual.get("silver_canal") or not actual.get("silver_mensaje"):return False
            try:
                ch=await self.canal(actual['silver_canal'])
                await ch.get_partial_message(actual['silver_mensaje']).edit(content=estado_silver(actual),allowed_mentions=NADIE)
            except (OSError,discord.HTTPException) as exc:
                retirado=isinstance(exc,discord.NotFound) and exc.code in (10003,10008)
                await self.datos("edicion_silver",raid,gen,retirado)
                log.warning("No se editó Silver Tab de AVA #%s (%s).",raid,type(exc).__name__)
                return False
            await self.datos("edicion_silver",raid,gen,True)
            return True
