from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import Horario, a_aware, a_naive_utc

MODALIDADES = [
    ('llamada', 'Llamada'),
    ('whatsapp', 'WhatsApp'),
    ('email', 'Email'),
    ('reunion', 'Reunión presencial'),
    ('otro', 'Otro'),
]
TIPOS = [
    ('primer_contacto', 'Primer contacto'),
    ('seguimiento', 'Seguimiento'),
    ('documentacion', 'Documentación'),
    ('resumen_whatsapp', 'Resumen WhatsApp'),
]
CONCLUSIONES = [
    ('respondio', 'Respondió'),
    ('no_respondio', 'No respondió'),
    ('reprogramar', 'Pidió que lo llamen en otro momento'),
]
RECORDATORIOS = [
    ('no', 'Sin recordatorio'),
    ('15min', '15 min antes'),
    ('1h', '1 h antes'),
    ('dia', 'Al inicio del día'),
]


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    fin_fecha_hora = fields.Datetime('Fecha y hora', index=True)
    fin_modalidad = fields.Selection(MODALIDADES, string='Modalidad')
    fin_tipo = fields.Selection(TIPOS, string='Tipo de tarea', index=True)
    fin_recordatorio = fields.Selection(RECORDATORIOS, string='Recordatorio', default='15min')
    fin_recordatorio_canal = fields.Selection([
        ('alerta', 'Alerta en el sistema'), ('email', 'Email'), ('ambos', 'Alerta y email'),
    ], string='Canal del recordatorio', default='alerta')
    fin_recordatorio_enviado = fields.Boolean(copy=False)
    fin_conclusion = fields.Selection(CONCLUSIONES, string='Conclusión', readonly=True)
    fin_nota_cierre = fields.Text('Nota de cierre', readonly=True)
    fin_vencida = fields.Boolean('Vencida', compute='_compute_fin_vencida', search='_search_fin_vencida')
    fin_bandeja = fields.Selection([
        ('1_vencida', 'Vencidas'), ('2_hoy', 'Hoy'), ('3_proxima', 'Próximas'),
    ], string='Bandeja', compute='_compute_fin_bandeja', store=True, index=True,
        help='Se recalcula al guardar y cada 5 minutos (cron), así las vencidas pasan solas a rojo.')
    fin_es_primer_contacto = fields.Boolean(compute='_compute_fin_es_primer_contacto')
    fin_lead_id = fields.Many2one('crm.lead', string='Oportunidad', compute='_compute_fin_lead', search='_search_fin_lead')
    fin_producto = fields.Selection(related='fin_lead_id.fin_producto')
    fin_telefono = fields.Char(related='fin_lead_id.phone', string='Teléfono')

    # ------------------------------------------------------------------ computes
    def _compute_fin_vencida(self):
        ahora = fields.Datetime.now()
        for act in self:
            act.fin_vencida = bool(act.fin_fecha_hora and act.fin_fecha_hora < ahora) or act.state == 'overdue'

    def _search_fin_vencida(self, operator, value):
        positivo = (operator == '=' and value) or (operator == '!=' and not value)
        dominio = [('fin_fecha_hora', '<', fields.Datetime.now())]
        return dominio if positivo else ['|', ('fin_fecha_hora', '=', False), ('fin_fecha_hora', '>=', fields.Datetime.now())]

    @api.depends('fin_fecha_hora', 'date_deadline')
    def _compute_fin_bandeja(self):
        ahora = fields.Datetime.now()
        horario = Horario(self.env)
        hoy = horario.dia_local(ahora)
        for act in self:
            fecha = act.fin_fecha_hora or (act.date_deadline and fields.Datetime.to_datetime(act.date_deadline))
            if not fecha:
                act.fin_bandeja = '3_proxima'
            elif fecha < ahora:
                act.fin_bandeja = '1_vencida'
            elif horario.dia_local(fecha) == hoy:
                act.fin_bandeja = '2_hoy'
            else:
                act.fin_bandeja = '3_proxima'

    def _compute_fin_es_primer_contacto(self):
        for act in self:
            act.fin_es_primer_contacto = act.fin_tipo == 'primer_contacto'

    @api.model
    def _fin_refrescar_bandeja(self):
        """Pasa a 'Vencidas'/'Hoy' las tareas según la hora actual."""
        acts = self.sudo().search([('res_model', '=', 'crm.lead'), ('fin_bandeja', '!=', '1_vencida')])
        anteriores = {a.id: a.fin_bandeja for a in acts}
        acts._compute_fin_bandeja()
        cambiadas = acts.filtered(lambda a: a.fin_bandeja != anteriores[a.id])
        for act in cambiadas:
            self.env.cr.execute('UPDATE mail_activity SET fin_bandeja = %s WHERE id = %s', [act.fin_bandeja, act.id])
        acts.invalidate_recordset(['fin_bandeja'])
        return len(cambiadas)

    def _compute_fin_lead(self):
        for act in self:
            act.fin_lead_id = act.res_id if act.res_model == 'crm.lead' else False

    def _search_fin_lead(self, operator, value):
        leads = self.env['crm.lead'].search([('id', operator, value)] if not isinstance(value, str)
                                            else [('name', operator, value)])
        return [('res_model', '=', 'crm.lead'), ('res_id', 'in', leads.ids)]

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._fin_sync_fecha(vals)
        acts = super().create(vals_list)
        acts.filtered(lambda a: a.res_model == 'crm.lead')._fin_validar_horario()
        return acts

    def write(self, vals):
        self._fin_sync_fecha(vals)
        if 'fin_fecha_hora' in vals:
            vals.setdefault('fin_recordatorio_enviado', False)
        res = super().write(vals)
        if 'fin_fecha_hora' in vals:
            self.filtered(lambda a: a.res_model == 'crm.lead')._fin_validar_horario()
        return res

    def _fin_sync_fecha(self, vals):
        """date_deadline (fecha nativa de Odoo) se deriva de la fecha y hora."""
        if vals.get('fin_fecha_hora'):
            horario = Horario(self.env)
            vals['date_deadline'] = horario.dia_local(fields.Datetime.to_datetime(vals['fin_fecha_hora']))

    def _fin_validar_horario(self):
        if self.env.context.get('fin_sistema') or self.env.context.get('fin_fuera_horario_ok'):
            return
        horario = Horario(self.env)
        for act in self:
            if act.fin_fecha_hora and act.fin_tipo not in ('resumen_whatsapp',) and not horario.es_habil(act.fin_fecha_hora):
                raise UserError('La tarea queda fuera del horario laboral. Elegí un horario de lunes a viernes '
                                'dentro de la jornada (o usá "Pidió que lo llamen en otro momento").')

    def unlink(self):
        if not self.env.context.get('fin_permitir_borrar') and not self.env.su:
            for act in self.filtered(lambda a: a.res_model == 'crm.lead'):
                lead = self.env['crm.lead'].sudo().browse(act.res_id)
                if lead.exists() and not lead.fin_cerrada and not (lead.activity_ids - self):
                    raise UserError('No se puede borrar la única tarea de una oportunidad abierta. '
                                    'Usá "Concluir" para programar la siguiente.')
        return super().unlink()

    def _action_done(self, feedback=False, attachment_ids=None):
        if not self.env.context.get('fin_concluir_ok'):
            for act in self.filtered(lambda a: a.res_model == 'crm.lead'):
                lead = self.env['crm.lead'].sudo().browse(act.res_id)
                if lead.exists() and not lead.fin_cerrada:
                    raise UserError('Para cerrar una tarea de una oportunidad usá el botón "Concluir": '
                                    'así queda programada la próxima tarea.')
        return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

    # ------------------------------------------------------------------ acciones
    def action_fin_concluir(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Concluir tarea',
            'res_model': 'financiera.concluir.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': dict(self.env.context, default_activity_id=self.id),
        }

    def action_fin_abrir_oportunidad(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'crm.lead', 'res_id': self.res_id,
            'view_mode': 'form', 'views': [(self.env.ref('financiera_crm.view_oportunidad_form').id, 'form')],
        }

    # ------------------------------------------------------------------ escalonamiento
    @api.model
    def _fin_escalonar(self, user, propuesta):
        """Corre la sugerencia al siguiente bloque de 30 min libre si el bloque ya tiene
        el máximo de tareas, o al día hábil siguiente si se alcanzó el tope diario."""
        horario = Horario(self.env)
        config = horario.config
        maximo = max(config.escalonar_max_bloque or 3, 1)
        tope = config.tope_diario_tareas or 0
        dt = horario.inicio_habil(propuesta)
        for _i in range(400):
            local = horario.local(dt)
            bloque_local = local.replace(minute=0 if local.minute < 30 else 30, second=0, microsecond=0)
            bloque = a_naive_utc(bloque_local)
            en_bloque = self.sudo().search_count([
                ('user_id', '=', user.id), ('fin_fecha_hora', '>=', bloque),
                ('fin_fecha_hora', '<', bloque + timedelta(minutes=30)),
            ])
            if tope:
                inicio_dia = a_naive_utc(local.replace(hour=0, minute=0, second=0, microsecond=0))
                en_dia = self.sudo().search_count([
                    ('user_id', '=', user.id), ('fin_fecha_hora', '>=', inicio_dia),
                    ('fin_fecha_hora', '<', inicio_dia + timedelta(days=1)),
                ])
                if en_dia >= tope:
                    dt = horario.inicio_dia_siguiente(dt)
                    continue
            if en_bloque < maximo:
                return dt
            dt = horario.inicio_habil(bloque + timedelta(minutes=30))
        return dt

    # ------------------------------------------------------------------ cron
    @api.model
    def _fin_cron_recordatorios(self):
        """Envía recordatorios de tareas (alerta y/o email)."""
        ahora = fields.Datetime.now()
        horario = Horario(self.env)
        pendientes = self.sudo().search([
            ('fin_recordatorio', 'not in', (False, 'no')), ('fin_recordatorio_enviado', '=', False),
            ('fin_fecha_hora', '!=', False), ('fin_fecha_hora', '<=', ahora + timedelta(hours=1, minutes=5)),
            ('fin_fecha_hora', '>=', ahora - timedelta(hours=12)),
        ])
        enviados = self.browse()
        for act in pendientes:
            if act.fin_recordatorio == '15min':
                momento = act.fin_fecha_hora - timedelta(minutes=15)
            elif act.fin_recordatorio == '1h':
                momento = act.fin_fecha_hora - timedelta(hours=1)
            else:
                momento = horario.inicio_habil(a_naive_utc(horario.local(act.fin_fecha_hora).replace(hour=0, minute=0)))
            if momento > ahora:
                continue
            hora = horario.local(act.fin_fecha_hora).strftime('%H:%M')
            mensaje = f'{act.summary or act.activity_type_id.name} — {hora}'
            if act.fin_recordatorio_canal in ('alerta', 'ambos'):
                act.user_id._bus_send('simple_notification', {
                    'title': 'Recordatorio de tarea', 'message': mensaje, 'type': 'warning', 'sticky': True,
                })
            if act.fin_recordatorio_canal in ('email', 'ambos') and act.user_id.email:
                self.env['mail.mail'].sudo().create({
                    'subject': 'Recordatorio de tarea', 'email_to': act.user_id.email,
                    'body_html': Markup('<p>%s</p>') % mensaje, 'auto_delete': True,
                })
            enviados |= act
        enviados.write({'fin_recordatorio_enviado': True})
        self._fin_refrescar_bandeja()
        return len(enviados)
