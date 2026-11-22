"""Comandos de Discord del bot de avalonianas."""
from __future__ import annotations
from typing import Literal, Optional
import logging
import discord
from discord import app_commands
from avisos_economia import pendientes_embed
from raids import Regla, PLANTILLAS, horario, autorizado_para_pagar
from panel import NADIE, Cliente, Panel, seguro, mostrar_panel, mensaje_inscrito, tarjeta_cancelado, tarjeta_reparto, tarjeta_pago_completado, formato_plata, panel_cancelado_canal

log=logging.getLogger("avaraid")
Tipo=Literal["Normal","Sin GA"]
Dia=Literal["Hoy","Mañana"]
I=discord.Interaction
A=("abierta",)
TODAS=("abierta","iniciada","cancelada","pagada")


def tipo_id(t):
    if t=="Normal":return "normal"
    if t=="Sin GA":return "sin_ga"
    raise Regla("Elige Normal o Sin GA.")


async def gestionar(i):
    if i.guild is None:return False
    if i.permissions.manage_guild or i.permissions.administrator:return True
    cfg=await i.client.datos("config",i.guild_id)
    return bool(cfg["caller"] and any(r.id==cfg["caller"] for r in getattr(i.user,"roles",())))


def solo_admin(i):
    return i.guild is not None and (i.permissions.manage_guild or i.permissions.administrator)


async def puede_pagar(i):
    """Comprobar rol de Economy en la ejecución y en la confirmación."""
    if i.guild is None or not i.guild_id:
        return False
    cfg = await i.client.datos("config", i.guild_id)
    return autorizado_para_pagar(cfg, getattr(i.user, "roles", ()))


def opciones_raid(estados):
    async def sugerir(i:I,actual:str):
        if not i.guild_id:return []
        rows=await i.client.datos("listar",i.guild_id,i.channel_id,estados,actual.strip().lstrip("#"))
        return [app_commands.Choice(name=f"#{r['id']} · {r['nombre']} · {r['estado']}"[:100],value=str(r["id"])) for r in rows]
    return sugerir


async def sugerir_roles(i:I,actual:str):
    tipo=getattr(i.namespace,"tipo",None)
    id_=getattr(i.namespace,"raid",None)
    planes=[]
    if tipo in ("Normal","Sin GA"):planes=[PLANTILLAS[tipo_id(tipo)]]
    elif i.guild_id and (id_ or getattr(i.namespace,"id_evento",None)):
        try:
            if id_:
                n=await i.client.datos("identificar",i.guild_id,i.channel_id,id_)
            else:
                n=await i.client.datos("identificar_global",i.guild_id,i.namespace.id_evento)
            planes=[(await i.client.datos("ficha",i.guild_id,n))["plan"]]
        except Regla:return []
    else:planes=PLANTILLAS.values()
    roles=dict.fromkeys(rol for plan in planes for rol in plan["roles"])
    return [app_commands.Choice(name=rol,value=rol) for rol in roles if actual.casefold() in rol.casefold()]


async def esperar(i,privado=True):
    await i.response.defer(ephemeral=privado,thinking=True)


def tarjeta_previa_pago(r,datos):
    """Tarjeta privada, no es un registro ni una transferencia de silver."""
    e=discord.Embed(title="🪙 CONFIRMAR PAGO DE AVA",
        description=f"**{seguro(r['nombre'])} · N.º {r['id']:04d}**\n⚠️ **Todavía no se ha acreditado plata.** Revisa los datos antes de confirmar.",
        color=0xE6B44C)
    e.add_field(name="💰 Bolsas",value=f"🪙 {formato_plata(datos['bolsas'])}",inline=True)
    e.add_field(name="📦 Loot",value=f"🪙 {formato_plata(datos['loot'])}",inline=True)
    e.add_field(name="🏛️ Tax gremio",value=f"{datos['tasa']}% · 🪙 {formato_plata(datos['impuesto'])}",inline=True)
    e.add_field(name="💵 Total bruto",value=f"🪙 {formato_plata(datos['total'])}",inline=True)
    e.add_field(name="✅ Neto a repartir",value=f"🪙 {formato_plata(datos['total']-datos['impuesto'])}",inline=True)
    e.add_field(name="👥 Titulares",value=str(len(datos['pagos'])),inline=True)
    e.add_field(name="🧾 Reparto previsto",value="\n".join(
        f"• <@{uid}> ➜ 🪙 {formato_plata(monto)}" for uid,monto in datos['pagos'])[:1024],inline=False)
    e.set_footer(text="Confirmar acredita silver y finaliza la AVA; Editar permite cambiar importes; Cancelar no paga.")
    return e


class EditarPago(discord.ui.Modal, title="Editar pago de la AVA"):
    bolsas=discord.ui.Label(text="Bolsas (silver)",component=discord.ui.TextInput(max_length=13,required=True))
    loot=discord.ui.Label(text="Loot (silver)",component=discord.ui.TextInput(max_length=13,required=True))
    tax_gremio=discord.ui.Label(text="Tax del gremio (%)",component=discord.ui.TextInput(max_length=3,required=True))

    def __init__(self,view):
        super().__init__(timeout=300)
        self.pago=view
        self.bolsas.component.default=str(view.bolsas)
        self.loot.component.default=str(view.loot)
        self.tax_gremio.component.default=str(view.tax)

    async def on_submit(self,i:discord.Interaction):
        if i.user.id!=self.pago.usuario or i.guild_id!=self.pago.guild:
            return await i.response.send_message("Esta edición pertenece a otro organizador.",ephemeral=True)
        if not await puede_pagar(i):
            return await i.response.send_message("Solo los miembros del rol Economy pueden editar pagos. Pide que configuren el rol con /configurar-ava.",ephemeral=True)
        try:
            bolsas,loot,tax=(int(str(x.component.value).strip()) for x in (self.bolsas,self.loot,self.tax_gremio))
            datos=await i.client.datos("previsualizar_pago",self.pago.guild,self.pago.raid,bolsas,loot,tax)
            r=await i.client.datos("ficha",self.pago.guild,self.pago.raid)
        except (Regla,ValueError) as exc:
            return await i.response.send_message(f"❌ No se pudo editar: {exc}",ephemeral=True)
        nuevo=ConfirmacionPago(self.pago.guild,self.pago.raid,self.pago.usuario,bolsas,loot,tax,datos["revision"])
        await i.response.send_message(embed=tarjeta_previa_pago(r,datos),view=nuevo,ephemeral=True,allowed_mentions=NADIE)


class ConfirmacionPago(discord.ui.View):
    def __init__(self,guild,raid,usuario,bolsas,loot,tax,revision):
        super().__init__(timeout=300)
        self.guild,self.raid,self.usuario=guild,raid,usuario
        self.bolsas,self.loot,self.tax=bolsas,loot,tax
        self.revision=revision
        self.usada=False

    async def interaction_check(self,i:discord.Interaction):
        if i.guild_id!=self.guild or i.user.id!=self.usuario:
            await i.response.send_message("Solo quien solicitó el reparto puede usar estos botones.",ephemeral=True)
            return False
        if not await puede_pagar(i):
            await i.response.send_message("Solo los miembros del rol Economy pueden confirmar o editar pagos. Pide que configuren el rol con /configurar-ava.",ephemeral=True)
            return False
        if self.usada:
            await i.response.send_message("Este formulario ya se utilizó. Vuelve a ejecutar /pagar_evento.",ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmar pago",style=discord.ButtonStyle.success,emoji="✅")
    async def confirmar(self,i:discord.Interaction,button:discord.ui.Button):
        self.usada=True
        await i.response.defer(thinking=False)
        try:
            datos=await i.client.datos("pagar_evento",self.guild,self.raid,self.bolsas,self.loot,self.tax,self.usuario,self.revision)
        except Regla as exc:
            await i.edit_original_response(content=f"❌ **No se pagó:** {exc}\nEjecuta `/pagar_evento` para generar una vista previa actualizada.",embed=None,view=None)
            return
        r=await i.client.datos("ficha",self.guild,self.raid)
        await i.client.pintar_seguro(self.guild,self.raid)
        try:
            await i.client.actualizar_silver(r)
        except (discord.HTTPException,OSError) as exc:
            log.warning("Silver tab no actualizada para AVA #%s: %s",self.raid,exc)
        try:
            log_ok=await i.client.publicar_log(self.guild,self.raid,"reparto",tarjeta_reparto(r,datos))
        except (discord.HTTPException,OSError) as exc:
            log.warning("Log de pago no publicado para AVA #%s: %s",self.raid,exc)
            log_ok=False
        try:
            await i.client.finalizar_hilo(r,datos)
        except (discord.HTTPException,OSError) as exc:
            log.warning("Hilo de pago no finalizado para AVA #%s: %s",self.raid,exc)
        texto="✅ **Pago confirmado.** La AVA quedó pagada y finalizada."
        if not log_ok:
            texto+="\n⚠️ No pude publicar en #ava-logs. El pago SÍ quedó registrado; reintenta `/pagar_evento` con los mismos importes para publicar el registro, sin acreditar dos veces."
        await i.edit_original_response(content=texto,embed=tarjeta_pago_completado(r,datos),view=None)

    @discord.ui.button(label="Editar",style=discord.ButtonStyle.primary,emoji="✏️")
    async def editar(self,i:discord.Interaction,button:discord.ui.Button):
        self.usada=True
        await i.response.send_modal(EditarPago(self))

    @discord.ui.button(label="Cancelar",style=discord.ButtonStyle.danger,emoji="❌")
    async def cancelar(self,i:discord.Interaction,button:discord.ui.Button):
        self.usada=True
        await i.response.edit_message(content="❌ **Pago cancelado.** No se modificaron los saldos de nadie.",embed=None,view=None)

    async def on_error(self,i,error,item):
        log.exception("Error al confirmar pago AVA %s",self.raid,exc_info=error)
        texto="Se produjo un error procesando la confirmación. Comprueba `/money` o `/pagar_evento` antes de reintentar."
        if i.response.is_done():
            await i.followup.send(texto,ephemeral=True)
        else:
            await i.response.send_message(texto,ephemeral=True)


def registrar(bot: Cliente):
    def cmd(nombre,descripcion):
        def decorar(fn):
            return bot.tree.command(name=nombre,description=descripcion)(app_commands.guild_only()(fn))
        return decorar

    async def id_raid(i,raid,estados):
        return await bot.datos("identificar",i.guild_id,i.channel_id,raid,estados)

    async def id_global(i,raid,estados=()):
        return await bot.datos("identificar_global",i.guild_id,raid,estados)

    @cmd("configurar-ava","Elige los roles y el canal de registro para las avalonianas de este servidor.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.check(solo_admin)
    @app_commands.describe(caller="Rol autorizado para organizar raids",core="Rol con prioridad cuando la raid la activa",
        avisos="Rol al que se avisa al abrir la raid; debe ser mencionable",registro="Canal para #ava-logs",
        silver_tab="Canal para crear una Carnicería por AVA",
        balance_logs="Canal público para los recibos de /add-bal y /retirar",
        economy="ÚNICO rol autorizado a pagar AVA y confirmar repartos",
        quitar="Desvincula únicamente este ajuste")
    async def configurar(i:I,caller:Optional[discord.Role]=None,core:Optional[discord.Role]=None,
               avisos:Optional[discord.Role]=None,registro:Optional[discord.TextChannel]=None,
               silver_tab:Optional[discord.TextChannel]=None,economy:Optional[discord.Role]=None,
               quitar:Optional[Literal["caller","core","avisos","registro","silver_tab","economy","balance_logs"]]=None,
               balance_logs:Optional[discord.TextChannel]=None):
        await esperar(i)
        campos={k:v.id for k,v in dict(caller=caller,core=core,avisos=avisos,registro=registro,silver_tab=silver_tab,economy=economy,balance_logs=balance_logs).items() if v is not None}
        if any(rol is not None and rol.is_default() for rol in (caller,core,avisos,economy)):
            raise Regla("Selecciona un rol específico, distinto de @everyone.")
        if quitar:
            if quitar in campos:raise Regla("No puedes configurar y quitar el mismo ajuste a la vez.")
            campos[quitar]=None
        cfg=await bot.datos("configurar",i.guild_id,campos) if campos else await bot.datos("config",i.guild_id)
        def valor(k):
            if not cfg[k]:return "Sin configurar"
            return f"<#{cfg[k]}>" if k in ("registro","silver_tab","balance_logs") else f"<@&{cfg[k]}>"
        await i.followup.send("**Avalonianas de este servidor**\n"+"\n".join(f"{k.capitalize()}: {valor(k)}" for k in ("caller","core","avisos","registro","silver_tab","economy","balance_logs")),
                              ephemeral=True,allowed_mentions=NADIE)

    @cmd("crear-ava","Publica una avaloniana Normal o Sin GA y abre las inscripciones.")
    @app_commands.check(gestionar)
    @app_commands.describe(nombre="Por ejemplo: T8.2 · Benjayaye",hora="Hora de inicio en UTC, por ejemplo 22:00",
        fecha="Día en UTC",tipo="Composición de la avaloniana",prioridad_core="Prioridad para el rol Core en esta raid")
    async def crear(i:I,nombre:app_commands.Range[str,1,80],hora:str,tipo:Tipo="Normal",fecha:Dia="Hoy",prioridad_core:bool=False):
        await esperar(i)
        if not isinstance(i.channel,discord.TextChannel):raise Regla("Crea la avaloniana en un canal de texto del servidor, no dentro de un hilo.")
        t=horario(hora,"hoy" if fecha=="Hoy" else "manana")
        id=await bot.datos("crear",i.guild_id,i.channel_id,i.user.id,nombre,tipo_id(tipo),t,prioridad_core)
        r=await bot.datos("ficha",i.guild_id,id)
        cfg=await bot.datos("config",i.guild_id)
        rol=i.guild.get_role(cfg["avisos"]) if cfg["avisos"] else None
        ping=bool(rol and rol.mentionable and not rol.is_default())
        try:
            msg=await i.channel.send(content=rol.mention if ping else None,embed=mostrar_panel(r),
                    allowed_mentions=discord.AllowedMentions(roles=[rol],users=False,everyone=False,replied_user=False) if ping else NADIE)
        except (discord.HTTPException,OSError):
            await bot.datos("descartar_borrador",id);raise
        await bot.datos("publicar",id,msg.id)
        await bot.pintar_seguro(i.guild_id,id)
        nota=""
        if cfg["avisos"] and not ping:nota+=" No se mencionó el rol de aviso: verifica que exista y sea mencionable."
        if i.app_permissions.create_public_threads and i.app_permissions.send_messages_in_threads:
            try:
                hilo=await msg.create_thread(name=f"Ava {id} · {r['nombre']}"[:100],auto_archive_duration=1440)
                r=await bot.datos("ficha",i.guild_id,id)
                espejo=await hilo.send("Inscripción de la avaloniana:",embed=mostrar_panel(r))
                await bot.datos("vincular_hilo",id,hilo.id,espejo.id)
                await bot.pintar_seguro(i.guild_id,id)
            except (discord.HTTPException,OSError):
                nota+=" No se pudo abrir el hilo; usa los botones del canal."
        else:nota+=" Las inscripciones funcionan en el canal principal sin hilo."
        # Registro visual en #silver-tabs-carnicerias con hilo propio de comprobantes.
        r=await bot.datos('ficha',i.guild_id,id)
        creado,error=await bot.crear_silver(r)
        if not creado and error:
            nota+=' ⚠️ '+error
        await i.followup.send(f"Avaloniana **#{id}** publicada: {msg.jump_url}."+nota,ephemeral=True,allowed_mentions=NADIE)


    @cmd("editar-build","Configura la imagen de un rol para las próximas avalonianas de este servidor.")
    @app_commands.check(gestionar)
    @app_commands.autocomplete(rol=sugerir_roles)
    async def build(i:I,tipo:Tipo,rol:str,url:str):
        await esperar(i)
        role=await bot.datos("editar",i.guild_id,tipo_id(tipo),rol,"build","" if url.strip()=="-" else url)
        await i.followup.send(f"Build guardada para **{role}**. Se usará en las próximas raids **{tipo}**.",ephemeral=True)

    @cmd("editar-loot","Configura el botín de un rol para futuras raids; separa las plazas con comas.")
    @app_commands.check(gestionar)
    @app_commands.autocomplete(rol=sugerir_roles)
    async def loot(i:I,tipo:Tipo,rol:str,asignacion:str):
        await esperar(i)
        role=await bot.datos("editar",i.guild_id,tipo_id(tipo),rol,"loot",asignacion)
        await i.followup.send(f"Botín guardado para **{role}** en las próximas avalonianas **{tipo}**.",ephemeral=True)

    async def finalizar(i,raid,estado):
        await esperar(i);n=await id_raid(i,raid,A)
        await bot.datos("cerrar",i.guild_id,n,estado,i.user.id)
        await i.followup.send(f"Avaloniana **#{n} {estado}**. Inscripción cerrada.",ephemeral=True)
        await bot.pintar_seguro(i.guild_id,n)
        r=await bot.datos("ficha",i.guild_id,n)
        await bot.actualizar_silver(r)
        if estado=="cancelada":
            # Recuadro corto en el call to PVE y en el espejo del hilo.
            try:
                canal=await bot.canal(r["canal"])
                msg=await canal.fetch_message(r["mensaje"])
                await msg.edit(embed=panel_cancelado_canal(r),view=None)
            except (discord.HTTPException,OSError,KeyError):
                pass
            if r.get("hilo") and r.get("espejo"):
                try:
                    hilo=await bot.canal(r["hilo"])
                    espejo=await hilo.fetch_message(r["espejo"])
                    await espejo.edit(embed=panel_cancelado_canal(r),view=None)
                except (discord.HTTPException,OSError,KeyError):
                    pass
            ok=await bot.publicar_log(i.guild_id,n,"cancelada",tarjeta_cancelado(r,i.user.id))
            if not ok:await i.followup.send("⚠️ No pude registrar la cancelación en #ava-logs. Comprueba /configurar-ava.",ephemeral=True)
            if r.get("hilo"):
                await bot.movimiento_hilo(r,"❌ Esta AVA ha sido cancelada por " + i.user.mention + ".")

    @cmd("iniciar-ava","Comienza una avaloniana ahora y cierra su inscripción.")
    @app_commands.check(gestionar)
    @app_commands.autocomplete(raid=opciones_raid(A))
    async def iniciar(i:I,raid:str):await finalizar(i,raid,"iniciada")

    @cmd("cancelar-ava","Cancela la avaloniana que seleccionaste y cierra sus botones.")
    @app_commands.check(gestionar)
    @app_commands.autocomplete(raid=opciones_raid(A))
    async def cancelar(i:I,raid:str):await finalizar(i,raid,"cancelada")

    @cmd("pagar_evento","Previsualiza bolsas, loot y tax; confirma antes de acreditar silver.")
    @app_commands.check(puede_pagar)
    @app_commands.describe(id_evento="N° de AVA (ej. 0008)",bolsas="Plata de bolsas",loot="Plata del loot",tax_gremio="Impuesto del gremio en porcentaje (0 a 100)")
    async def pagar(i:I,id_evento:str,bolsas:app_commands.Range[int,0,10**12],
                    loot:app_commands.Range[int,0,10**12],tax_gremio:app_commands.Range[int,0,100]=0):
        await esperar(i)
        n=await id_global(i,id_evento,("iniciada","pagada"))
        r=await bot.datos("ficha",i.guild_id,n)
        previo=await bot.datos("ver_reparto",i.guild_id,n)
        if previo:
            if (previo.get("bolsas",0),previo.get("loot",0),previo["tasa"])!=(bolsas,loot,tax_gremio):
                raise Regla("La AVA ya fue pagada. No se puede volver a repartir ni cambiar importes.")
            ok=await bot.publicar_log(i.guild_id,n,"reparto",tarjeta_reparto(r,previo))
            texto="✅ Esta AVA ya estaba pagada. No se abonó dos veces."
            if not ok: texto+=" ⚠️ No pude publicar el registro en #ava-logs."
            return await i.followup.send(texto,embed=tarjeta_pago_completado(r,previo),ephemeral=True,allowed_mentions=NADIE)
        datos=await bot.datos("previsualizar_pago",i.guild_id,n,bolsas,loot,tax_gremio)
        vista=ConfirmacionPago(i.guild_id,n,i.user.id,bolsas,loot,tax_gremio,datos["revision"])
        await i.followup.send(embed=tarjeta_previa_pago(r,datos),view=vista,ephemeral=True,allowed_mentions=NADIE)

    @cmd("money","Ver el saldo de plata virtual acumulado de tu billetera.")
    async def money(i:I):
        await esperar(i,privado=False)
        saldo=await bot.datos("saldo",i.guild_id,i.user.id)
        nombre=seguro(getattr(i.user,"display_name",i.user.name)).upper()[:100]
        e=discord.Embed(title=f"💰 BILLETERA DE {nombre}",color=0x23C45E)
        e.add_field(name="Saldo",value=f"🪙 **{formato_plata(saldo)} Silver**",inline=False)
        await i.followup.send(embed=e,allowed_mentions=NADIE)

    @cmd("money-top","Ranking de las cuentas con más plata acumulada en este servidor.")
    async def money_top(i:I):
        await esperar(i,privado=False)
        filas=await bot.datos("ranking",i.guild_id,25)
        e=discord.Embed(title="📊 Gente con derechos",color=0x258DD3,
            description="\n".join(f"**{j}.** <@{r['usuario']}> ➜ 🪙 {formato_plata(r['saldo'])}" for j,r in enumerate(filas,1)) or "Todavía no hay saldos registrados.")
        await i.followup.send(embed=e,allowed_mentions=NADIE)

    @cmd("force-join","Agregar un jugador incluso después del inicio, antes de pagar la AVA.")
    @app_commands.check(gestionar)
    @app_commands.autocomplete(rol=sugerir_roles)
    async def force_join(i:I,id_evento:str,usuario:discord.Member,rol:str):
        await esperar(i)
        n=await id_global(i,id_evento,("abierta","iniciada"))
        if usuario.bot:raise Regla("No se pueden anotar bots.")
        r=await bot.datos("ficha",i.guild_id,n)
        res=await bot.datos("anotar",i.guild_id,n,usuario.id,rol,await bot.es_core(i.guild_id,usuario),True)
        await bot.pintar_seguro(i.guild_id,n)
        if not res.get("igual") and "bloqueo" not in res:
            actual=await bot.datos("ficha",i.guild_id,n)
            await bot.movimiento_hilo(actual,f"⚠️ <@{usuario.id}> fue agregado forzosamente como **{res['rol']}** por <@{i.user.id}>.",res["rol"] if res.get("plaza") else None,usuario.id)
            if res.get("promovido"):await bot.movimiento_hilo(actual,f"⬆️ <@{res['promovido']}> sube automáticamente a **{res['rol']}**.")
        await i.followup.send(embed=mensaje_inscrito(r,res,usuario.id),ephemeral=True,allowed_mentions=NADIE)

    @cmd("force-leave","Retirar un jugador antes del pago, incluso si la AVA ya inició.")
    @app_commands.check(gestionar)
    async def force_leave(i:I,id_evento:str,usuario:discord.Member):
        await esperar(i)
        n=await id_global(i,id_evento,("abierta","iniciada"))
        res=await bot.datos("salir",i.guild_id,n,usuario.id,True)
        await bot.pintar_seguro(i.guild_id,n)
        r=await bot.datos("ficha",i.guild_id,n)
        await bot.movimiento_hilo(r,f"⚠️ <@{usuario.id}> fue retirado forzosamente por <@{i.user.id}>."+
                (f"\n⬆️ <@{res['promovido']}> sube automáticamente a **{res['rol']}**." if res.get("promovido") else ""))
        await i.followup.send(f"✅ Retirado <@{usuario.id}> de la AVA #{n}.",ephemeral=True,allowed_mentions=NADIE)

    @cmd("give-bal","Transferir plata de tu billetera a otro jugador.")
    async def give_bal(i:I,usuario:discord.Member,cantidad:app_commands.Range[int,1,10**12]):
        await esperar(i)
        if usuario.bot:raise Regla("No puedes enviarle plata a un bot.")
        saldo=await bot.datos("transferir",i.guild_id,i.user.id,usuario.id,cantidad)
        await i.followup.send(f"✅ Transferiste 🪙 **{formato_plata(cantidad)}** a {usuario.mention}. Saldo: {formato_plata(saldo)} Silver.",ephemeral=True,allowed_mentions=NADIE)

    @cmd("historial-de-depositos","Muestra tus últimos 20 movimientos de plata y sus eventos.")
    async def historial(i:I):
        await esperar(i)
        rows=await bot.datos("depositos",i.guild_id,i.user.id,20)
        e=discord.Embed(title="📜 HISTORIAL DE DEPÓSITOS",color=0x258DD3)
        lineas=[]
        for row in rows:
            ref=""
            if row["evento"]:
                r=await bot.datos("ficha",i.guild_id,row["evento"])
                destino=r.get("hilo") or r["canal"]
                mensaje=r.get("espejo") or r.get("mensaje")
                if mensaje:ref=f" · [AVA N° {r['id']:04d}](https://discord.com/channels/{i.guild_id}/{destino}/{mensaje})"
            lineas.append(f"<t:{row['fecha']}:d> · {'+' if row['delta']>=0 else ''}{formato_plata(row['delta'])} 🪙{ref}")
        e.description="\n".join(lineas) or "Todavía no tienes movimientos registrados."
        await i.followup.send(embed=e,ephemeral=True,allowed_mentions=NADIE)

    @cmd("avas-pendientes","Economy: muestra las AVA iniciadas que todavía no tienen reparto.")
    @app_commands.check(puede_pagar)
    @app_commands.describe(pagina="Página de resultados; se muestran hasta 10 AVA por página")
    async def avas_pendientes(i:I,pagina:app_commands.Range[int,1,1000000]=1):
        await esperar(i)
        datos=await bot.datos("pendientes_economy",i.guild_id,pagina)
        await i.followup.send(embed=pendientes_embed(datos,i.guild_id),ephemeral=True,allowed_mentions=NADIE)

    async def ajuste(i,usuario,cantidad,motivo,retirar):
        await esperar(i)
        if usuario.bot:raise Regla("Elige la cuenta de un jugador.")
        recibo=await bot.datos("ajustar_balance",i.guild_id,usuario.id,cantidad,i.user.id,motivo,str(i.id),retirar)
        url=await bot.entregar_balance(i.guild_id,recibo["mov"],
                                       canal_origen=getattr(i,'channel_id',None),
                                       nombre=getattr(usuario,'display_name',getattr(usuario,'name',None)))
        texto=(f"✅ Ajuste #{recibo['mov']} registrado. Saldo de {usuario.mention}: "
               f"{formato_plata(recibo['posterior'])} silver.")
        if url:texto+=f" [Ver recibo público]({url})"
        else:texto+=" El aviso público está pendiente. Revisa los permisos del canal o /configurar-ava balance_logs. No repitas el ajuste: el saldo ya quedó registrado."
        await i.followup.send(texto,ephemeral=True,allowed_mentions=NADIE)

    @cmd("add-bal","Agrega silver y publica BALANCE ACTUALIZADO para todos.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.check(solo_admin)
    @app_commands.describe(motivo="Explicación que aparecerá en el recibo público")
    async def add_bal(i:I,usuario:discord.Member,cantidad:app_commands.Range[int,1,10**12],motivo:app_commands.Range[str,1,140]):
        await ajuste(i,usuario,cantidad,motivo,False)

    @cmd("retirar","Retira silver y publica BALANCE ACTUALIZADO para todos.")
    @app_commands.check(gestionar)
    @app_commands.describe(motivo="Explicación que aparecerá en el recibo público")
    async def retirar_silver(i:I,usuario:discord.Member,cantidad:app_commands.Range[int,1,10**12],motivo:app_commands.Range[str,1,140]):
        await ajuste(i,usuario,cantidad,motivo,True)

    @cmd("reset-economia","⚠️ Reiniciar los saldos de todo el servidor (irreversible).")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.check(solo_admin)
    async def reset_economia(i:I,confirmar:Literal["CONFIRMAR"]):
        await esperar(i)
        await bot.datos("resetear_economia",i.guild_id,i.user.id)
        await i.followup.send("⚠️ Se reiniciaron todos los saldos del servidor. Los registros de AVA permanecen en el historial.",ephemeral=True)

    @cmd("cuentas","Sube fotos de las cuentas y vincúlalas al evento y carnicería (1 a 10).")
    @app_commands.check(gestionar)
    async def cuentas(i:I,id_evento:str,carniceria:app_commands.Range[int,1,10],
                      foto:discord.Attachment,foto_2:Optional[discord.Attachment]=None):
        await esperar(i)
        n=await id_global(i,id_evento)
        adjuntos=[foto]+([foto_2] if foto_2 else [])
        for adj in adjuntos:
            if not (adj.content_type or "").startswith("image/"):
                raise Regla("Las cuentas deben ser capturas de imagen.")
        r=await bot.datos("ficha",i.guild_id,n)
        hilo=r.get("hilo") or r["canal"]
        enlace=f"https://discord.com/channels/{i.guild_id}/{hilo}/{r.get('espejo') or r.get('mensaje')}"
        nuevo=await bot.datos("guardar_cuentas",i.guild_id,n,carniceria,i.user.id,[a.url for a in adjuntos],enlace)
        e=discord.Embed(title=f"📑 CUENTAS · CARNICERÍA {carniceria}",description=f"Evento [N.º {n:04d}]({enlace})\nRegistrado por <@{i.user.id}>",color=0x258DD3)
        e.set_image(url=foto.url)
        e.add_field(name="📸 Capturas",value="\n".join(f"[Ver captura {pos}]({adj.url})" for pos,adj in enumerate(adjuntos,1)),inline=False)
        # Publicamos los comprobantes en la carnicería de silver-tabs, si existe.
        if not r.get('silver_hilo'):
            await bot.crear_silver(r)
            r=await bot.datos('ficha',i.guild_id,n)
        canal=await bot.canal(r['silver_hilo']) if r.get('silver_hilo') else await bot.canal_logs(i.guild_id)
        if canal:
            try:
                await canal.send(embed=e,allowed_mentions=NADIE)
            except discord.HTTPException:
                log.warning("No se pudo publicar el comprobante %s de AVA #%s",nuevo,n)
        await i.followup.send(f"✅ Cuentas #{nuevo} asociadas a AVA #{n}, carnicería {carniceria}. Fotos: {len(adjuntos)}.",ephemeral=True)

    @cmd("ayuda-ava","Guía rápida para crear, pagar y administrar avalonianas.")
    async def ayuda(i:I):
        e=discord.Embed(title="⚔️ AvaRaid · Guía",color=0x25877C,
            description="/crear-ava publica el panel con hora UTC y un hilo con inscripciones. Los botones dejan entrar, cambiar de rol y salir.")
        e.add_field(name="Organización",value="/configurar-ava · roles, #ava-logs y #silver-tabs\n/iniciar-ava · comenzar\n/cancelar-ava · cancelar\n/force-join · inscribir a alguien\n/force-leave · retirar a alguien",inline=False)
        e.add_field(name="Economía",value="/avas-pendientes · salidas iniciadas por pagar (Economy)\n/pagar_evento · exclusivo rol Economy: repartir y cerrar la AVA\n/money · billetera personal\n/money-top · ranking\n/give-bal · transferir\n/add-bal · sumar plata (admin)\n/retirar · descontar\n/historial-de-depositos · historial\n/reset-economia · reiniciar (admin)\n/cuentas · imágenes de cuentas por carnicería",inline=False)
        await i.response.send_message(embed=e,ephemeral=True,allowed_mentions=NADIE)
