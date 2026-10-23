import secrets

from odoo import api, fields, models
from odoo.exceptions import UserError


class FinancieraConfig(models.Model):
    """Configuración única de la financiera (un solo registro, editable por el admin)."""
    _name = 'financiera.config'
    _description = 'Configuración CRM Financiera'

    name = fields.Char(default='Configuración', required=True)

    # Asignación
    modo_asignacion = fields.Selection([
        ('rotacion', 'Rotación (1 → 2 → 1 → 2)'),
        ('carga', 'Por carga (menos oportunidades abiertas)'),
    ], string='Modo de asignación', default='rotacion', required=True)
    ultimo_asignado_id = fields.Many2one('res.users', string='Última vendedora asignada', readonly=True)
    equipo_id = fields.Many2one('crm.team', string='Equipo de ventas')

    # Horario y tareas
    calendario_id = fields.Many2one('resource.calendar', string='Calendario laboral')
    zona_horaria = fields.Char('Zona horaria', default='America/Asuncion', required=True)
    hora_inicio = fields.Float('Hora de inicio de tareas', default=8.5,
                               help='Primera hora del día en que se agendan tareas (08:30 por defecto).')
    sla_horas = fields.Float('SLA de primer contacto (horas hábiles)', default=1.0)
    escalonar_max_bloque = fields.Integer('Tareas máximas por bloque de 30 min', default=3)
    tope_diario_tareas = fields.Integer('Tope diario de tareas por vendedora', default=24)
    intentos_max = fields.Integer('Intentos sin respuesta antes de sugerir pérdida', default=5)

    # Objetivos
    tope_100_promedio = fields.Boolean(
        'Topear cada producto en 100 % en el promedio',
        help='Si está activo, un sobrecumplimiento en un producto no tapa el incumplimiento en otro.')

    # Notificaciones
    notificar_email = fields.Boolean('Enviar también por email', default=False)

    # Meta Lead Ads
    meta_verify_token = fields.Char('Verify token del webhook', groups='financiera_crm.group_admin')
    meta_access_token = fields.Char('Token de acceso (Graph API)', groups='financiera_crm.group_admin')
    meta_app_secret = fields.Char('App secret (firma)', groups='financiera_crm.group_admin')
    meta_graph_version = fields.Char('Versión de Graph API', default='v21.0')
    meta_ultimo_evento = fields.Datetime('Último evento recibido', readonly=True)
    meta_url_webhook = fields.Char('URL del webhook', compute='_compute_urls')

    # WhatsApp
    whatsapp_token = fields.Char('Token del puente WhatsApp', groups='financiera_crm.group_admin')
    whatsapp_url = fields.Char('URL de entrada WhatsApp', compute='_compute_urls')

    @api.depends_context('uid')
    def _compute_urls(self):
        base = self.env['ir.config_parameter'].sudo().get_str('web.base.url', '')
        for rec in self:
            rec.meta_url_webhook = f'{base}/financiera/meta/webhook'
            rec.whatsapp_url = f'{base}/financiera/whatsapp/entrante'

    @api.model
    def _get(self):
        config = self.env.ref('financiera_crm.config_principal', raise_if_not_found=False)
        if not config:
            config = self.sudo().search([], limit=1) or self.sudo().create({})
        return config.sudo()

    def _calendario(self):
        return self.calendario_id or self.env.company.resource_calendar_id

    def action_generar_tokens(self):
        self.ensure_one()
        vals = {}
        if not self.meta_verify_token:
            vals['meta_verify_token'] = secrets.token_urlsafe(24)
        if not self.whatsapp_token:
            vals['whatsapp_token'] = secrets.token_urlsafe(24)
        self.write(vals)

    def action_cargar_demo(self):
        self.env['financiera.demo'].cargar()
        return self._aviso('Datos de demo cargados.')

    def action_borrar_demo(self):
        self.env['financiera.demo'].borrar()
        return self._aviso('Datos de demo borrados.')

    def action_simular_meta(self):
        lead = self.env['financiera.meta.lead']._simular()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'crm.lead', 'res_id': lead.id,
            'view_mode': 'form', 'target': 'current',
        }

    def action_simular_whatsapp(self):
        lead = self.env['financiera.whatsapp.resumen']._simular()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'crm.lead', 'res_id': lead.id,
            'view_mode': 'form', 'target': 'current',
        }

    def _aviso(self, mensaje):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'message': mensaje, 'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }

    def unlink(self):
        raise UserError('La configuración no se puede borrar.')
