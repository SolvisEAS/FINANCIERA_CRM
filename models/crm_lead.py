from datetime import timedelta
from urllib.parse import quote

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import ORIGENES, PRODUCTOS, PRODUCTOS_CON_SIN_DEFINIR, Horario, normalizar_telefono

SITUACION_LABORAL = [
    ('dependiente', 'Dependiente'),
    ('independiente', 'Independiente'),
    ('jubilado', 'Jubilado'),
    ('otro', 'Otro'),
]
NOMBRE_PRODUCTO = dict(PRODUCTOS_CON_SIN_DEFINIR)
NOMBRE_ORIGEN = dict(ORIGENES)


def fin_ahora(env):
    """Momento actual; los tests y la demo pueden fijarlo con el contexto `fin_ahora`."""
    return env.context.get('fin_ahora') or fields.Datetime.now()


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    fin_producto = fields.Selection(PRODUCTOS_CON_SIN_DEFINIR, string='Producto', tracking=True, index=True)
    fin_importe_solicitado = fields.Monetary('Importe solicitado', currency_field='company_currency', tracking=True,
                                             help='Préstamo/tarjeta: monto o línea. Seguro: prima.')
    fin_importe_final = fields.Monetary('Importe final', currency_field='company_currency', tracking=True,
                                        help='Obligatorio al pasar a Cerrado ganado. Es el que suma en reportes y objetivos.')
    fin_origen = fields.Selection(ORIGENES, string='Origen', default='manual', tracking=True, index=True)
    fin_campana = fields.Char('Campaña / formulario Meta')
    fin_plazo_meses = fields.Integer('Plazo (meses)')
    fin_vehiculo = fields.Char('Vehículo (marca, modelo, año)')
    fin_ingreso_mensual = fields.Monetary('Ingreso mensual declarado', currency_field='company_currency')
    fin_situacion_laboral = fields.Selection(SITUACION_LABORAL, string='Situación laboral')
    fin_ci = fields.Char(related='partner_id.fin_ci', readonly=False, string='Cédula (CI)')
    fin_ciudad = fields.Char(related='partner_id.city', readonly=False, string='Ciudad')
    fin_fecha_nacimiento = fields.Date(related='partner_id.fin_fecha_nacimiento', readonly=False)

    fin_asignado_por = fields.Char('Asignada por', readonly=True, copy=False)
    fin_fecha_primer_contacto = fields.Datetime('Fecha de primer contacto', readonly=True, copy=False)
    fin_horas_primer_contacto = fields.Float('Tiempo a primer contacto (h hábiles)', readonly=True, copy=False,
                                             aggregator='avg')
    fin_dias_primer_contacto = fields.Float('Tiempo a primer contacto (días hábiles)', readonly=True, copy=False,
                                            aggregator='avg')
    fin_intentos = fields.Integer('Intentos sin respuesta', readonly=True, copy=False)
    fin_cliente_escribio = fields.Boolean('El cliente escribió', copy=False)
    fin_vendedora_respondio_wa = fields.Boolean('Respondió por WhatsApp', copy=False)
    fin_meta_payload = fields.Text('Payload de Meta (técnico)', readonly=True, copy=False)
    fin_demo = fields.Boolean('Dato de demo', copy=False, index='btree_not_null')

    fin_codigo_etapa = fields.Char(related='stage_id.fin_codigo', string='Código de etapa')
    fin_cerrada = fields.Boolean(related='stage_id.fin_cerrada', store=True, string='Cerrada')
    fin_es_ganada = fields.Boolean(related='stage_id.is_won', store=True, string='Ganada')
    fin_es_perdida = fields.Boolean(related='stage_id.fin_es_perdido', store=True, string='Perdida')

    fin_proxima_tarea_fecha = fields.Datetime('Próxima tarea', compute='_compute_fin_proxima_tarea', store=True)
    fin_proxima_tarea_asunto = fields.Char('Asunto próxima tarea', compute='_compute_fin_proxima_tarea', store=True)
    fin_proxima_tarea_estado = fields.Selection([
        ('vencida', 'Vencida'), ('hoy', 'Hoy'), ('futura', 'Próxima'), ('sin', 'Sin tarea'),
    ], string='Estado de la próxima tarea', compute='_compute_fin_proxima_tarea_estado')
    fin_horas_sin_contactar = fields.Float('Sin contactar (h hábiles)', compute='_compute_fin_semaforo')
    fin_semaforo = fields.Selection([
        ('verde', 'En tiempo'), ('amarillo', 'Atrasada'), ('rojo', 'Muy atrasada'),
    ], string='Semáforo de contacto', compute='_compute_fin_semaforo')

    # ------------------------------------------------------------------ computes
    @api.depends('activity_ids.fin_fecha_hora', 'activity_ids.summary', 'activity_ids.date_deadline')
    def _compute_fin_proxima_tarea(self):
        for lead in self:
            actividades = lead.activity_ids.sorted(lambda a: a.fin_fecha_hora or fields.Datetime.to_datetime(a.date_deadline))
            proxima = actividades[:1]
            lead.fin_proxima_tarea_fecha = proxima.fin_fecha_hora or (
                proxima and fields.Datetime.to_datetime(proxima.date_deadline)) or False
            lead.fin_proxima_tarea_asunto = proxima.summary or proxima.activity_type_id.name or False

    def _compute_fin_proxima_tarea_estado(self):
        ahora = fields.Datetime.now()
        horario = Horario(self.env)
        hoy = horario.dia_local(ahora)
        for lead in self:
            fecha = lead.fin_proxima_tarea_fecha
            if not fecha:
                lead.fin_proxima_tarea_estado = 'sin'
            elif fecha < ahora:
                lead.fin_proxima_tarea_estado = 'vencida'
            elif horario.dia_local(fecha) == hoy:
                lead.fin_proxima_tarea_estado = 'hoy'
            else:
                lead.fin_proxima_tarea_estado = 'futura'

    def _compute_fin_semaforo(self):
        pendientes = self.filtered(lambda l: l.fin_codigo_etapa == '00' and not l.fin_fecha_primer_contacto)
        (self - pendientes).update({'fin_horas_sin_contactar': 0.0, 'fin_semaforo': False})
        if not pendientes:
            return
        horario = Horario(self.env)
        sla = horario.config.sla_horas or 1.0
        ahora = fields.Datetime.now()
        for lead in pendientes:
            horas = horario.horas_entre(lead.date_open or lead.create_date, ahora)
            lead.fin_horas_sin_contactar = horas
            lead.fin_semaforo = 'verde' if horas < sla else 'amarillo' if horas < 2 * sla else 'rojo'

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        automatico = not self.env.context.get('fin_sin_automatismos')
        etapa_inicial = self.env.ref('financiera_crm.stage_00')
        config = self.env['financiera.config']._get()
        Partner = self.env['res.partner']
        for vals in vals_list:
            vals['type'] = 'opportunity'
            if automatico or not vals.get('stage_id'):
                vals['stage_id'] = etapa_inicial.id
            if config.equipo_id and not vals.get('team_id'):
                vals['team_id'] = config.equipo_id.id
            if not vals.get('partner_id') and (vals.get('contact_name') or vals.get('phone') or vals.get('email_from')):
                partner, _nuevo = Partner._fin_buscar_o_crear({
                    'name': vals.get('contact_name') or vals.get('name') or vals.get('phone'),
                    'phone': vals.get('phone'),
                    'email': vals.get('email_from'),
                    'fin_origen': vals.get('fin_origen') or 'manual',
                    'fin_demo': vals.get('fin_demo', False),
                })
                vals['partner_id'] = partner.id
            if vals.get('partner_id'):
                partner = Partner.sudo().browse(vals['partner_id'])
                if not partner.fin_es_cliente:
                    partner.fin_es_cliente = True
                if not vals.get('name'):
                    vals['name'] = partner.name
                if not self.env.context.get('fin_permitir_duplicado'):
                    abierta = self.sudo().search([('partner_id', '=', partner.id), ('fin_cerrada', '=', False)], limit=1)
                    if abierta:
                        raise UserError(
                            f'{partner.name} ya tiene una oportunidad abierta ("{abierta.name}", '
                            f'de {abierta.user_id.name or "nadie"}). No se crea otra.')
            if vals.get('phone'):
                vals['phone'] = normalizar_telefono(vals['phone']) or vals['phone']
        leads = super().create(vals_list)
        if automatico:
            for lead in leads:
                # Odoo pone por defecto al usuario que crea (el sistema en webhooks, el admin
                # en carga manual): solo se respeta si es una vendedora; si no, rotación.
                if lead.user_id and lead.user_id.fin_es_vendedora:
                    lead.sudo().fin_asignado_por = lead.fin_asignado_por or self.env.user.name
                else:
                    lead.sudo().with_context(fin_sistema=True).user_id = False
                    lead.sudo()._fin_asignar()
                lead.sudo()._fin_crear_primer_contacto()
                lead.sudo()._fin_avisar_nueva()
        return leads

    def write(self, vals):
        sistema = self.env.context.get('fin_sistema') or self.env.su
        if 'user_id' in vals and not sistema and self.env.user.fin_solo_propias:
            if any(lead.user_id.id != vals['user_id'] for lead in self):
                raise UserError('Solo el administrador puede reasignar oportunidades.')
        if 'stage_id' in vals:
            nueva = self.env['crm.stage'].browse(vals['stage_id'])
            self._fin_validar_etapa(nueva, vals)
            if nueva.fin_cerrada and 'date_closed' not in vals:
                vals['date_closed'] = fin_ahora(self.env)
            if not nueva.fin_cerrada:
                vals.setdefault('date_closed', False)
        res = super().write(vals)
        if 'stage_id' in vals and self.env['crm.stage'].browse(vals['stage_id']).fin_cerrada:
            self._fin_cerrar_tareas_abiertas()
        return res

    def _fin_validar_etapa(self, nueva, vals):
        for lead in self:
            vieja = lead.stage_id
            if vieja == nueva:
                continue
            if nueva.fin_codigo == '00' and vieja.fin_codigo and vieja.fin_codigo != '00':
                raise UserError('Una oportunidad no puede volver a "00 · No contactado".')
            producto = vals.get('fin_producto', lead.fin_producto)
            if (nueva.fin_codigo or '00') > '10' and producto in (False, 'sin_definir'):
                raise UserError(f'Definí el producto de "{lead.name}" antes de avanzar de "10 · Contactado".')
            if nueva.is_won and not vals.get('fin_importe_final', lead.fin_importe_final):
                raise UserError(f'Para pasar "{lead.name}" a Cerrado ganado cargá el importe final (botón "Ganado").')
            if nueva.fin_es_perdido and not vals.get('lost_reason_id', lead.lost_reason_id.id):
                raise UserError(f'Para pasar "{lead.name}" a Cerrado perdido indicá el motivo (botón "Perdido").')

    def _fin_cerrar_tareas_abiertas(self):
        actividades = self.sudo().activity_ids
        if actividades:
            actividades.with_context(fin_permitir_borrar=True).unlink()

    # ------------------------------------------------------------------ asignación
    def _fin_asignar(self):
        """Asigna por rotación o por carga. Bloquea la configuración para evitar
        que dos oportunidades simultáneas caigan a la misma vendedora."""
        self.ensure_one()
        config = self.env['financiera.config']._get()
        self.env.cr.execute('SELECT id FROM financiera_config WHERE id = %s FOR UPDATE', [config.id])
        config.invalidate_recordset(['ultimo_asignado_id'])
        Users = self.env['res.users'].sudo()
        todas = Users.search([('fin_es_vendedora', '=', True), ('share', '=', False)], order='fin_orden, id')
        candidatas = todas.filtered('fin_disponible')
        abiertas = {u.id: u.fin_abiertas_count for u in candidatas}
        candidatas = candidatas.filtered(lambda u: not u.fin_tope_abiertas or abiertas[u.id] < u.fin_tope_abiertas)
        if not candidatas:
            self._fin_avisar_admin('Oportunidad sin asignar',
                                   f'"{self.name}" no se pudo asignar: no hay vendedoras disponibles.')
            return False
        if config.modo_asignacion == 'carga':
            minimo = min(abiertas[u.id] for u in candidatas)
            candidatas = candidatas.filtered(lambda u: abiertas[u.id] == minimo)
            motivo = 'Asignación por carga'
        else:
            motivo = 'Rotación automática'
        orden = todas.ids
        ultimo = orden.index(config.ultimo_asignado_id.id) if config.ultimo_asignado_id.id in orden else -1
        elegida = min(candidatas, key=lambda u: (orden.index(u.id) - ultimo - 1) % len(orden))
        self.with_context(fin_sistema=True).write({'user_id': elegida.id, 'fin_asignado_por': motivo})
        config.ultimo_asignado_id = elegida
        return elegida

    # ------------------------------------------------------------------ tareas
    def _fin_crear_primer_contacto(self):
        self.ensure_one()
        if not self.user_id:
            return
        horario = Horario(self.env)
        entrada = fin_ahora(self.env)
        if horario.es_habil(entrada):
            vence = horario.sumar_horas(entrada, horario.config.sla_horas or 1.0)
        else:
            vence = horario.inicio_habil(entrada)
        self.env['mail.activity'].sudo().with_context(mail_activity_quick_update=True, fin_sistema=True).create({
            'res_model_id': self.env['ir.model']._get_id('crm.lead'),
            'res_id': self.id,
            'activity_type_id': self.env.ref('financiera_crm.actividad_primer_contacto').id,
            'summary': f'{self.partner_id.name or self.name} — Primer contacto',
            'user_id': self.user_id.id,
            'fin_fecha_hora': vence,
            'fin_tipo': 'primer_contacto',
            'fin_modalidad': 'whatsapp' if self.fin_origen == 'whatsapp' else 'llamada',
            'fin_recordatorio': '15min',
        })

    def _fin_registrar_primer_contacto(self, fecha):
        horario = Horario(self.env)
        etapa_10 = self.env.ref('financiera_crm.stage_10')
        for lead in self:
            if lead.fin_fecha_primer_contacto:
                continue
            horas = horario.horas_entre(lead.date_open or lead.create_date, fecha)
            vals = {
                'fin_fecha_primer_contacto': fecha,
                'fin_horas_primer_contacto': horas,
                'fin_dias_primer_contacto': round(horas / horario.horas_por_dia(), 2),
                'fin_cliente_escribio': False,
                'fin_vendedora_respondio_wa': False,
            }
            if lead.fin_codigo_etapa == '00':
                vals['stage_id'] = etapa_10.id
            lead.with_context(fin_sistema=True).write(vals)

    # ------------------------------------------------------------------ avisos
    def _fin_avisar(self, users, titulo, mensaje, tipo='info'):
        config = self.env['financiera.config']._get()
        for user in users:
            user._bus_send('simple_notification', {
                'title': titulo, 'message': mensaje, 'type': tipo, 'sticky': True,
            })
            if config.notificar_email and user.email:
                self.env['mail.mail'].sudo().create({
                    'subject': titulo, 'email_to': user.email,
                    'body_html': Markup('<p>%s</p>') % mensaje, 'auto_delete': True,
                })

    def _fin_avisar_admin(self, titulo, mensaje):
        admins = self.env.ref('financiera_crm.group_admin').sudo().all_user_ids.filtered(
            lambda u: not u.share and u.id != self.env.ref('base.user_root').id)
        self._fin_avisar(admins, titulo, mensaje, 'warning')

    def _fin_avisar_nueva(self):
        for lead in self.filtered('user_id'):
            producto = NOMBRE_PRODUCTO.get(lead.fin_producto, '')
            lead._fin_avisar(lead.user_id, 'Nueva oportunidad asignada',
                             f'{lead.name} · {producto} · origen {NOMBRE_ORIGEN.get(lead.fin_origen, "")}')

    # ------------------------------------------------------------------ botones
    def action_fin_ganado(self):
        return self._fin_abrir_cierre('ganado')

    def action_fin_perdido(self):
        return self._fin_abrir_cierre('perdido')

    def _fin_abrir_cierre(self, tipo):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cerrado ganado' if tipo == 'ganado' else 'Cerrado perdido',
            'res_model': 'financiera.cierre.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_lead_id': self.id, 'default_tipo': tipo,
                        'default_importe_final': self.fin_importe_final or self.fin_importe_solicitado},
        }

    def action_fin_whatsapp(self):
        self.ensure_one()
        numero = (normalizar_telefono(self.phone or self.partner_id.phone) or '').lstrip('+')
        if not numero:
            raise UserError('El contacto no tiene teléfono cargado.')
        if self.fin_codigo_etapa == '00' and not self.fin_vendedora_respondio_wa:
            self.with_context(fin_sistema=True).fin_vendedora_respondio_wa = True
        texto = quote(f'Hola {self.partner_id.name or ""}, te escribo de la financiera.')
        return {'type': 'ir.actions.act_url', 'url': f'https://wa.me/{numero}?text={texto}', 'target': 'new'}

    def action_fin_nueva_tarea(self):
        self.ensure_one()
        horario = Horario(self.env)
        sugerida = self.env['mail.activity']._fin_escalonar(self.user_id or self.env.user, horario.inicio_dia_siguiente(fin_ahora(self.env)))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Nueva tarea',
            'res_model': 'mail.activity',
            'view_mode': 'form',
            'views': [(self.env.ref('financiera_crm.view_tarea_form').id, 'form')],
            'target': 'new',
            'context': {
                'default_res_model_id': self.env['ir.model']._get_id('crm.lead'),
                'default_res_id': self.id,
                'default_user_id': (self.user_id or self.env.user).id,
                'default_activity_type_id': self.env.ref('financiera_crm.actividad_seguimiento').id,
                'default_summary': f'{self.partner_id.name or self.name} — Seguimiento',
                'default_fin_tipo': 'seguimiento',
                'default_fin_modalidad': 'llamada',
                'default_fin_fecha_hora': fields.Datetime.to_string(sugerida),
            },
        }

    def action_fin_cerrar_primer_contacto(self):
        """Atajo cuando la vendedora ya respondió por WhatsApp a una oportunidad en 00."""
        self.ensure_one()
        actividad = self.activity_ids.filtered(lambda a: a.fin_tipo == 'primer_contacto')[:1]
        if not actividad:
            raise UserError('No hay una tarea de primer contacto abierta.')
        return actividad.with_context(default_conclusion='respondio').action_fin_concluir()

    def action_fin_ver_contacto(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'res.partner', 'res_id': self.partner_id.id,
            'view_mode': 'form', 'views': [(self.env.ref('financiera_crm.view_contacto_form').id, 'form')],
        }

    # ------------------------------------------------------------------ cron
    @api.model
    def _fin_cron_sin_seguimiento(self):
        """Oportunidades abiertas sin ninguna tarea: se crea una tarea de revisión y se avisa al admin."""
        huerfanas = self.sudo().search([
            ('fin_cerrada', '=', False), ('type', '=', 'opportunity'),
            ('activity_ids', '=', False), ('user_id', '!=', False),
        ])
        horario = Horario(self.env)
        ahora = fields.Datetime.now()
        for lead in huerfanas:
            self.env['mail.activity'].sudo().with_context(mail_activity_quick_update=True, fin_sistema=True).create({
                'res_model_id': self.env['ir.model']._get_id('crm.lead'),
                'res_id': lead.id,
                'activity_type_id': self.env.ref('financiera_crm.actividad_seguimiento').id,
                'summary': 'Revisar oportunidad sin seguimiento',
                'user_id': lead.user_id.id,
                'fin_fecha_hora': horario.inicio_habil(ahora),
                'fin_tipo': 'seguimiento',
                'fin_modalidad': 'otro',
            })
        if huerfanas:
            nombres = ', '.join(huerfanas.mapped('name')[:5])
            huerfanas[:1]._fin_avisar_admin('Oportunidades sin seguimiento',
                                            f'{len(huerfanas)} sin tarea futura: {nombres}')
        return len(huerfanas)

    def _fin_nota(self, texto):
        self.message_post(body=Markup('<p>%s</p>') % escape(texto), subtype_xmlid='mail.mt_note')
