"""Paneles, botones persistentes y avisos de inscripción en Discord."""
from __future__ import annotations
import asyncio
import logging
import time
from weakref import WeakValueDictionary
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import tasks
from raids import PLANTILLAS, Regla, asignacion
from avisos_economia import EntregasEconomia, estado_silver
from copias import ciclo_copias

log=logging.getLogger("avaraid")
NADIE=discord.AllowedMentions.none()
# Emojis personalizados entregados por el administrador del servidor.
# Se mantienen los roles, las plazas y las inscripciones sin modificaciones.
# Sintaxis de emoji de Discord: <:nombre:id> (Discord resuelve el icono por ID).
ICONOS={
    "Main Tank":"<:main_tank:1573894654338928721>",
    "Off Tank":"<:off_tank:1573894762749104208>",
    "Great Arcane":"<:great_arcane:1573895348462551050>",
    "Healer":"<:healer:1573894688513986611>",
    "ShadowCaller":"<:shadowcaller:1573895312626679818>",
    "Falce":"<:falce:1573894790687359118>",
    "Falce Daga":"<:falce_daga:1573895755012247713>",
    "Scout":"<:scout:1573895471443873902>",
    "Pierce":"🎯",  # Sin GA: pendiente de ID de emoji personalizado.
}
COLORES={"publicando":0x48567A,"abierta":0x00C8CC,"iniciada":0x5466D9,"cancelada":0xED4245,"pagada":0x23C45E}
ESTADOS={"publicando":"Preparando","abierta":"Inscripción abierta","iniciada":"Registro cerrado","cancelada":"Evento cancelado","pagada":"AVA pagada y finalizada"}


def seguro(s):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(s)))


def formato_plata(n):
    return f"{int(n):,}"


def mostrar_panel(r):
    p=r["plan"];jugadores=r["jugadores"]
    titulares=[x for x in jugadores if x["plaza"] is not None]
    banca=[x for x in jugadores if x["plaza"] is None]
    t=datetime.fromtimestamp(r["inicio"],timezone.utc)
    estado=r["estado"]
    if estado=="cancelada":
        return panel_cancelado_canal(r)
    prefijo="✅ " if estado=="pagada" else ""
    e=discord.Embed(title=f"{prefijo}⚔️ {seguro(r['nombre'])} ⚔️",color=COLORES[estado])
    e.description=(
       f"🔢 **Serie:** `N° {r['id']:04d}`\n"
       f"📌 **Tipo:** {p['nombre'].upper()}\n"
       f"⭐ **Prioridad:** {'🟡 ACTIVADA' if r['prioridad'] else '⚪ DESACTIVADA'}\n"
       f"📅 **Programación Hora Local:** <t:{r['inicio']}:F>\n"
       f"🌍 **Programación Hora UTC:** {t:%d/%m/%Y %H:%M} UTC\n"
    )
    if estado=="iniciada":e.description+="\n🔒 **Registro cerrado.**\n"
    if estado=="pagada":e.description+="\n💰 **AVA PAGADA Y FINALIZADA**\n"
    e.description+="\n"+("─"*32)+"\n"
    for rol,cfg in p["roles"].items():
        ocupados={x["plaza"]:x["usuario"] for x in titulares if x["rol"]==rol}
        filas=[f"• <@{ocupados[k]}>" for k in range(1,cfg["cupos"]+1) if k in ocupados]
        if not filas:filas=["-" if estado!="abierta" else "*Sin asignar*"]
        e.add_field(name=f"{ICONOS.get(rol, '⚔️')} {rol} ({len(ocupados)}/{cfg['cupos']})",value="\n".join(filas),inline=True)
    # Discord mantiene tres columnas y el bloque de banca al final.
    lista=[f"• <@{x['usuario']}> *(Espera: {seguro(x['rol'])})*" for x in banca[:14]]
    if len(banca)>14:lista.append(f"Y {len(banca)-14} reservas más")
    e.add_field(name="🪑 BENCH",value="\n".join(lista) or "-",inline=False)
    return e




def panel_cancelado_canal(r):
    actor=f" por <@{r['ultimo_actor']}>" if r.get('ultimo_actor') else ""
    e=discord.Embed(title="❌ RAID CANCELADA",description=f"La actividad **{seguro(r['nombre'])}** fue cancelada{actor}.",color=0xED4245)
    return e

def tarjeta_cancelado(r,usuario):
    e=discord.Embed(title="❌ EVENTO CANCELADO",color=0xED4245)
    cierre=int(r.get('cierre') or time.time())
    e.add_field(name="📅 Fecha",value=datetime.fromtimestamp(cierre,timezone.utc).strftime('%d/%m/%Y'),inline=False)
    e.add_field(name="⚔️ Evento",value=seguro(r['nombre']),inline=False)
    e.add_field(name="🕒 Hora programada",value=f"<t:{r['inicio']}:F>",inline=False)
    actor=usuario or r.get("ultimo_actor")
    e.add_field(name="👤 Cancelado por",value=f"<@{actor}>" if actor else "Sin registrar",inline=False)
    return e


def tarjeta_reparto(r,datos):
    e=discord.Embed(title="💰 REPARTO REGISTRADO",color=0x22C55E)
    e.add_field(name="📅 Fecha",value=f"<t:{int(datos.get('fecha') or time.time())}:d>",inline=False)
    enlace=f"https://discord.com/channels/{r['guild']}/{r['canal']}/{r['mensaje']}" if r.get("mensaje") else None
    nombre=f"{seguro(r['nombre'])} (N° {r['id']:04d})"
    e.add_field(name="⚔️ Evento",value=f"[{nombre}]({enlace})" if enlace else nombre,inline=False)
    e.add_field(name="🕒 Hora programada",value=f"<t:{r['inicio']}:F>",inline=False)
    e.add_field(name="👤 Pagado por",value=f"<@{datos['pagador']}>",inline=False)
    e.add_field(name="💵 Total",value=f"🪙 {formato_plata(datos['total'])}",inline=True)
    e.add_field(name="🏛️ Tax",value=f"🪙 {formato_plata(datos['impuesto'])}",inline=True)
    participantes="\n".join(f"• <@{u}> ➜ 🪙 {formato_plata(v)}" for u,v in datos["pagos"])
    e.add_field(name="👥 Participantes",value=participantes[:1024],inline=False)
    return e


def tarjeta_pago_completado(r,datos):
    e=discord.Embed(title="✅ REPARTO COMPLETADO",color=0x22C55E)
    e.add_field(name="💰 Total Bruto",value=f"🪙 {formato_plata(datos['total'])}",inline=True)
    e.add_field(name="🏛️ Tax",value=f"🪙 {formato_plata(datos['impuesto'])}",inline=True)
    e.add_field(name="📊 Total Repartido",value=f"🪙 {formato_plata(sum(v for _,v in datos['pagos']))}",inline=True)
    e.add_field(name="👥 Participantes Pagados",value="\n".join(
        f"• <@{u}> ➜ 🪙 {formato_plata(v)}" for u,v in datos["pagos"])[:1024],inline=False)
    e.set_footer(text="Pagado por: registro de reparto virtual de la AVA")
    return e

def ver_loot(r):
    p=r["plan"];e=discord.Embed(title=f"Builds y botín · {p['nombre']} · #{r['id']}",color=0xAF8647)
    for rol,regla in p["roles"].items():
        lineas=[f"{i+1}. {seguro(asignacion(p,rol,i+1))}" for i in range(regla["cupos"])]
        if regla["build"]:
            # Mostrar el enlace directamente en el mensaje privado evita confundir build y arma.
            if len(regla["build"])<=520:
                lineas.append(f"[Ver build](<{regla['build']}>)")
            else:lineas.append("Build disponible al elegir el rol.")
        e.add_field(name=f"{ICONOS[rol]} {rol}",value="\n".join(lineas),inline=False)
    return e


def mensaje_inscrito(r,res,uid):
    if "bloqueo" in res:
        return discord.Embed(description=f"Alcanzaste el límite de cambios. Conservas tu plaza y podrás cambiarla <t:{res['bloqueo']}:R>.",color=0xAF8647)
    rol,plaza=res["rol"],res["plaza"]
    if plaza is None:
        texto=f"<@{uid}> está en **banca para {rol}**. Cuando se libere una plaza se actualizará el panel."
    else:
        texto=f"<@{uid}> · **{rol}, plaza {plaza}**.\nBotín asignado: **{seguro(asignacion(r['plan'],rol,plaza))}**."
    if res.get("igual"):texto+="\nLa inscripción ya estaba guardada."
    if res.get("promovido"):texto+=f"\nSube de banca: <@{res['promovido']}>."
    if res.get("desplazado"):texto+=f"\nPasa a banca por prioridad Core: <@{res['desplazado']}>."
    e=discord.Embed(title="Tu inscripción" if not res.get("igual") else "Inscripción actual",description=texto,color=0x25877C)
    build=r["plan"]["roles"][rol]["build"]
    if build:e.add_field(name="Build de este rol",value=f"[Abrir imagen](<{build}>)");e.set_image(url=build)
    return e


async def mensaje_error(i,exc):
    error=getattr(exc,"original",exc)
    if isinstance(error,Regla):texto=str(error)
    elif isinstance(error,(app_commands.CheckFailure,app_commands.MissingPermissions)):
        if getattr(getattr(i, "command", None), "name", None)=="pagar_evento":
            texto="Solo los miembros del rol Economy configurado pueden usar /pagar_evento. Un administrador debe asignar el rol desde /configurar-ava."
        else:
            texto="No tienes permiso para esa acción. Pídesela al Ava Caller o a quien gestiona el servidor."
    elif isinstance(error,discord.Forbidden):
        texto="Discord no permite esa acción. Revisa los permisos del bot en este canal."
    else:
        log.error("Error en una interacción",exc_info=(type(error),error,error.__traceback__))
        texto="No pude completar la acción. Consulta el panel antes de repetirla."
    if i.response.is_done():await i.followup.send(texto,ephemeral=True,allowed_mentions=NADIE)
    else:await i.response.send_message(texto,ephemeral=True,allowed_mentions=NADIE)


class Arbol(app_commands.CommandTree):
    async def on_error(self,interaction,error):
        await mensaje_error(interaction,error)


class Boton(discord.ui.Button):
    def __init__(self,tipo,clave,etiqueta,fila,estilo,emoji=None):
        super().__init__(label=etiqueta,row=fila,style=estilo,emoji=emoji,
                         custom_id="ava:v2:"+tipo+":"+clave.casefold().replace(" ","_"))
        self.clave=clave

    async def callback(self,i):
        await i.response.defer(ephemeral=True,thinking=True)
        if not i.guild_id or i.message is None:raise Regla("Usa el panel en el servidor de la raid.")
        bot=i.client; raid=await bot.datos("desde_boton",i.guild_id,i.channel_id,i.message.id)
        r=await bot.datos("ficha",i.guild_id,raid)
        if r["tipo"]!=self.view.tipo:raise Regla("El panel no corresponde a esta composición.")
        if self.clave=="salir":
            res=await bot.datos("salir",i.guild_id,raid,i.user.id)
            texto="Te retiraste de la avaloniana."
            if res["promovido"]:texto+=f" Sube de banca: <@{res['promovido']}>."
            await i.followup.send(texto,ephemeral=True,allowed_mentions=NADIE)
        else:
            res=await bot.datos("anotar",i.guild_id,raid,i.user.id,self.clave,await bot.es_core(i.guild_id,i.user))
            await i.followup.send(embed=mensaje_inscrito(r,res,i.user.id),ephemeral=True,allowed_mentions=NADIE)
            if res.get("igual") or "bloqueo" in res:return
        await bot.pintar_seguro(i.guild_id,raid)
        actual=await bot.datos("ficha",i.guild_id,raid)
        if self.clave=="salir":
            await bot.movimiento_hilo(actual,f"❌ <@{i.user.id}> se ha retirado." +
                (f"\n⬆️ <@{res['promovido']}> sube automáticamente a **{res['rol']}**." if res.get("promovido") else ""))
        else:
            await bot.movimiento_hilo(actual,
                f"✅ <@{i.user.id}> se anotó para **{res['rol']}**." if res.get("plaza") is not None else
                f"🪑 <@{i.user.id}> entró a BENCH para **{res['rol']}**.",res["rol"] if res.get("plaza") else None,i.user.id)
            if res.get("promovido"):
                await bot.movimiento_hilo(actual,f"⬆️ <@{res['promovido']}> sube automáticamente a **{res['rol']}**.",res["rol"],res["promovido"])
            if res.get("desplazado"):
                await bot.movimiento_hilo(actual,f"⚠️ <@{res['desplazado']}> pasó a BENCH por prioridad Core.")


class Panel(discord.ui.View):
    def __init__(self,tipo,activo=True):
        super().__init__(timeout=None)
        self.tipo=tipo
        for n,rol in enumerate(PLANTILLAS[tipo]["roles"]):
            item=Boton(tipo,rol,{"Main Tank":"MainTank","Off Tank":"OffTank","Great Arcane":"GA","Falce Daga":"Falce Daga","ShadowCaller":"Shadow","Healer":"Healer"}.get(rol,rol),n//4,
                discord.ButtonStyle.success if rol=="Scout" else discord.ButtonStyle.primary, None)
            item.disabled=not activo
            self.add_item(item)
        retiro=Boton(tipo,"salir","SignOut",2,discord.ButtonStyle.danger)
        retiro.disabled=not activo
        self.add_item(retiro)

    async def on_error(self,interaction,error,item):
        await mensaje_error(interaction,error)


class Cliente(EntregasEconomia, discord.Client):
    def __init__(self,almacen):
        # Los comandos y los botones ya incluyen los roles del miembro que interactúa.
        super().__init__(intents=discord.Intents(guilds=True),allowed_mentions=NADIE)
        self.bd=almacen;self.tree=Arbol(self)
        self.cerrojos=WeakValueDictionary()
        self.esperas={}
        self.cerrojos_log={}

    async def datos(self,operacion,*args):
        return await asyncio.to_thread(getattr(self.bd,operacion),*args)

    async def setup_hook(self):
        for tipo in PLANTILLAS:self.add_view(Panel(tipo))
        comandos=await self.tree.sync()
        log.info("Registrados %s comandos de avalonianas.",len(comandos))
        # Los paneles creados con versiones anteriores también mostrarán los iconos.
        await self.datos("refrescar_iconos")
        self.reloj.start()
        self.copias_del_bot.start()

    async def on_ready(self):
        log.info("AvaRaid 22.1 conectado como %s. Usa /crear-ava en tu servidor.",self.user)

    async def close(self):
        self.reloj.cancel()
        self.copias_del_bot.cancel()
        await super().close()

    async def es_core(self,guild,miembro):
        cfg=await self.datos("config",guild)
        return cfg["core"] is not None and any(r.id==cfg["core"] for r in getattr(miembro,"roles",()))

    async def canal(self,id):
        return self.get_channel(id) or await self.fetch_channel(id)

    async def pintar(self,guild,id):
        async with self.cerrojos.setdefault(id,asyncio.Lock()):
            r=await self.datos("ficha",guild,id)
            if r["pintada"]==r["revision"] or not r["mensaje"]:return
            embed=mostrar_panel(r)
            for canal_id,msg_id in ((r["canal"],r["mensaje"]),(r["hilo"],r["espejo"])):
                if not canal_id or not msg_id:continue
                try:
                    canal=await self.canal(canal_id)
                    await canal.get_partial_message(msg_id).edit(embed=embed,
                        view=None if r["estado"]=="cancelada" else Panel(r["tipo"],activo=r["estado"]=="abierta"),allowed_mentions=NADIE)
                except discord.NotFound:
                    log.info("Panel retirado de Discord (raid %s); los datos siguen guardados.",id)
            await self.datos("pintada",id,r["revision"])

    async def pintar_seguro(self,guild,id):
        if self.esperas.get(id,0)>time.monotonic():return
        try:
            await self.pintar(guild,id)
            self.esperas.pop(id,None)
        except (discord.HTTPException,OSError) as ex:
            self.esperas[id]=time.monotonic()+60
            log.warning("Discord no ha actualizado el panel #%s (%s); se reintentará.",id,type(ex).__name__)

    async def registro(self,guild,texto):
        cfg=await self.datos("config",guild)
        if not cfg["registro"]:return
        try:
            ch=await self.canal(cfg["registro"])
            await ch.send(texto,allowed_mentions=NADIE)
        except (discord.HTTPException,OSError) as e:
            log.warning("Registro de avaloniana no enviado (%s).",type(e).__name__)

    async def canal_silver(self,guild):
        cfg=await self.datos('config',guild)
        if cfg.get('silver_tab'):
            try:
                channel=await self.canal(cfg['silver_tab'])
                if isinstance(channel,discord.TextChannel):
                    return channel
            except (discord.HTTPException,OSError):
                pass
        servidor=self.get_guild(guild)
        if servidor:
            return next((c for c in servidor.text_channels if
                         'silver-tabs' in c.name.casefold() or
                         ('silver' in c.name.casefold() and 'carnicer' in c.name.casefold())),None)
        return None

    texto_silver=staticmethod(estado_silver)

    async def crear_silver(self,r):
        """Crea un único mensaje por AVA y su hilo independiente de inscripción."""
        async with self.cerrojos.setdefault(('silver',r['id']),asyncio.Lock()):
            actual=await self.datos('ficha',r['guild'],r['id'])
            canal=await self.canal_silver(actual['guild'])
            if canal is None:
                return False,'No hay canal de silver-tabs. Configúralo con /configurar-ava silver_tab.'
            try:
                if actual.get('silver_mensaje'):
                    canal=await self.canal(actual['silver_canal'])
                    msg=await canal.fetch_message(actual['silver_mensaje'])
                else:
                    msg=await canal.send(self.texto_silver(actual),allowed_mentions=NADIE)
                    if not await self.datos('vincular_silver_tab',actual['guild'],actual['id'],canal.id,msg.id):
                        await msg.delete()
                        return False,'La AVA ya tenía un registro en silver-tabs.'
                    actual=await self.datos('ficha',actual['guild'],actual['id'])
                if not actual.get('silver_hilo'):
                    hilo=await msg.create_thread(
                        name=f"Carnicería - N° {actual['id']:04d} ({actual['nombre']})"[:100],
                        auto_archive_duration=1440)
                    await self.datos('vincular_silver_hilo',actual['guild'],actual['id'],hilo.id)
                    enlace=f"https://discord.com/channels/{actual['guild']}/{actual['canal']}/{actual['mensaje']}"
                    e=discord.Embed(title=f"📑 Comprobantes - Evento N° {actual['id']:04d}",
                        description=f"**AVA:** [{seguro(actual['nombre'])}]({enlace})\n"
                                    "Subí las capturas de cuentas mediante `/cuentas`.",
                        color=0x3498DB)
                    await hilo.send(embed=e,allowed_mentions=NADIE)
                return True,None
            except (discord.HTTPException,OSError) as exc:
                log.warning('No se pudo crear la silver tab de AVA #%s: %s',r['id'],exc)
                return False,'Revisá los permisos para enviar mensajes y crear hilos públicos en silver-tabs.'

    async def canal_logs(self,guild):
        cfg=await self.datos("config",guild)
        if cfg.get("registro"):
            try:return await self.canal(cfg["registro"])
            except (discord.HTTPException,OSError):pass
        servidor=self.get_guild(guild)
        if servidor:
            return next((c for c in servidor.text_channels if "ava-logs" in c.name.casefold()),None)
        return None

    async def publicar_log(self,guild,raid,tipo,embed):
        # Un cerrojo por evento y tipo protege de clics/órdenes concurrentes.
        async with self.cerrojos_log.setdefault((raid,tipo),asyncio.Lock()):
            if await self.datos("publicacion_hecha",raid,tipo):return True
            canal=await self.canal_logs(guild)
            if not canal:return False
            try:
                msg=await canal.send(embed=embed,allowed_mentions=NADIE)
                await self.datos("guardar_publicacion",raid,tipo,msg.id)
                return True
            except (discord.HTTPException,OSError) as ex:
                log.warning("No se pudo publicar %s de AVA #%s: %s",tipo,raid,ex)
                return False

    async def movimiento_hilo(self,r,texto,rol=None,usuario=None):
        # Los movimientos se publican SOLAMENTE en el hilo de la AVA.
        if not r.get("hilo"):return
        try:
            canal=await self.canal(r["hilo"])
            await canal.send(texto,allowed_mentions=NADIE)
            if rol and usuario:
                url=r["plan"]["roles"].get(rol,{}).get("build","")
                if url:
                    e=discord.Embed(title=f"🧰 BUILD · {seguro(rol)}",description=f"<@{usuario}> · Equipamiento y swaps de referencia",color=0x3979D6)
                    e.set_image(url=url)
                    await canal.send(embed=e,allowed_mentions=NADIE)
        except (discord.HTTPException,OSError) as ex:
            log.warning("No se pudo registrar movimiento en hilo de AVA #%s: %s",r["id"],ex)

    async def finalizar_hilo(self,r,datos):
        if not r.get("hilo"):return False
        try:
            canal=await self.canal(r["hilo"])
            await canal.send("✅ Pago realizado correctamente.",embed=tarjeta_pago_completado(r,datos),allowed_mentions=NADIE)
            await canal.send("💰 Reparto completado. El hilo ha sido cerrado automáticamente.",allowed_mentions=NADIE)
            await canal.edit(archived=True,locked=True)
            return True
        except (discord.HTTPException,OSError) as ex:
            log.warning("No se pudo cerrar el hilo de AVA #%s: %s",r["id"],ex)
            return False

    async def enviar_aviso(self,a):
        r=await self.datos("ficha",a["guild"],a["raid"])
        tipo=a["tipo"];ahora=int(self.bd.reloj())
        if ((tipo=="30m" and (r["estado"]!="abierta" or r["inicio"]<=ahora)) or
            (tipo!="30m" and r["estado"]!=tipo)):return
        if tipo=='iniciada':
            await self.actualizar_silver(r)
        uid=[x["usuario"] for x in r["jugadores"] if x["plaza"] is not None]
        titulo={"30m":"Empieza en menos de 30 minutos","iniciada":"La avaloniana ha comenzado","cancelada":"Avaloniana cancelada"}[tipo]
        # Nunca despertar al grupo por una raid que empezó hace horas con el bot apagado.
        if tipo=="cancelada" or (tipo=="iniciada" and ahora-r["inicio"]>3600):uid=[]
        texto=" ".join(f"<@{u}>" for u in uid)
        embed=discord.Embed(title=f"⚔️ ¡HORA DE INICIO: {seguro(r['nombre'])}! ⚔️" if tipo=="iniciada" else
            f"⏰ {titulo}" if tipo=="30m" else "❌ EVENTO CANCELADO",color=0xED4245 if tipo=="iniciada" else COLORES[r["estado"]])
        maximo=sum(v["cupos"] for v in r["plan"]["roles"].values())
        completos=len(uid)==maximo
        embed.description=("La expedición comienza ya.\n\n" if tipo=="iniciada" else "")+texto+(
           "\n\n✅ La composición está completa." if completos else f"\n\n⚠️ Composición incompleta ({len(uid)}/{maximo}).")
        menciones=discord.AllowedMentions(everyone=False,roles=False,replied_user=False,users=[discord.Object(id=u) for u in uid])
        destinos=list(dict.fromkeys(v for v in (r["hilo"],r["canal"]) if v))
        for pos,id in enumerate(destinos):
            try:
                ch=await self.canal(id);await ch.send(content=texto or None,embed=embed,allowed_mentions=menciones)
                return
            except (discord.NotFound,discord.Forbidden):
                if pos+1==len(destinos):raise

    @tasks.loop(minutes=15)
    async def copias_del_bot(self):
        try:
            info=await asyncio.to_thread(ciclo_copias,self.bd.ruta)
            if info.get("aviso"):
                if getattr(self,"_ultimo_aviso_copia",None)!=info["aviso"]:
                    log.warning("%s",info["aviso"])
            elif info["creada"] or info["enviadas"]:
                log.info("Copias verificadas: local %s; %s nueva(s) en el destino externo.",info["dia"],info["enviadas"])
            self._ultimo_aviso_copia=info.get("aviso")
        except Exception as exc:
            # No se registran credenciales ni direcciones privadas del proveedor.
            log.error("Copia de seguridad pendiente (%s); se reintentará en 15 minutos.",type(exc).__name__)

    @tasks.loop(seconds=20)
    async def reloj(self):
        try:
            await self.datos("vencimientos")
            for g,id in await self.datos("sucias"):await self.pintar_seguro(g,id)
            for a in await self.datos("tomar_avisos"):
                try:
                    await self.enviar_aviso(a)
                except (discord.HTTPException,OSError) as e:
                    log.warning("Aviso pendiente #%s (%s); se reintentará.",a["raid"],type(e).__name__)
                    await self.datos("aviso_listo",a["raid"],a["tipo"],False,a["intentos"])
                else:await self.datos("aviso_listo",a["raid"],a["tipo"],True)
            for g,n in await self.datos("silver_por_editar"):
                await self.actualizar_silver({"guild":g,"id":n})
            for g,m in await self.datos("recibos_pendientes"):
                await self.entregar_balance(g,m)
        except Exception:
            log.exception("No se pudo completar esta comprobación de avalonianas; se reintentará.")

    @reloj.before_loop
    async def esperar_conexion(self):
        await self.wait_until_ready()
