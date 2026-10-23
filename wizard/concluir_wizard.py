from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.crm_lead import fin_ahora
from ..models.mail_activity import CONCLUSIONES, MODALIDADES, RECORDATORIOS, TIPOS
from ..models.utils import Horario


class FinancieraConcluirWizard(models.TransientModel):
    """Reemplaza el "Marcar como hecho": obliga a dejar programada la próxima tarea."""
    _name = 'financiera.concluir.wizard'
    _description = 'Concluir tarea'

    activity_id = fields.Many2one('mail.activity', string='Tarea', required=True, ondelete='cascade')
    lead_id = fields.Many2one('crm.lead', string='Oportunidad', compute='_compute_lead')
    contacto = fields.Char(compute='_compute_lead')
    tarea_asunto = fields.Char(related='activity_id.summary', string='Tarea a concluir')
    es_primer_contacto = fields.Boolean(compute='_compute_lead')
    conclusion = fields.Selection(CONCLUSIONES, string='¿Cómo terminó?', required=True)
    nota = fields.Text('Nota')

    nueva_etapa_id = fields.Many2one(
        'crm.stage', string='Cambiar etapa a',
        domain="[('fin_codigo', 'in', ['20', '30', '40', '50'])]")
    nueva_etapa_codigo = fields.Char(related='nueva_etapa_id.fin_codigo')
    importe_final = fields.Monetary('Importe final', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda s: s.env.company.currency_id)
    lost_reason_id = fields.Many2one('crm.lost.reason', string='Motivo de pérdida')
    producto = fields.Selection(related='lead_id.fin_producto', readonly=False, string='Producto')

    proxima_asunto = fields.Char('Asunto de la próxima tarea')
    proxima_fecha = fields.Datetime('Fecha y hora de la próxima tarea')
    proxima_modalidad = fields.Selection(MODALIDADES, string='Modalidad', default='llamada')
    proxima_tipo = fields.Selection([t for t in TIPOS if t[0] != 'resumen_whatsapp' and t[0] != 'primer_contacto'],
                                    string='Tipo', default='seguimiento')
    proxima_recordatorio = fields.Selection(RECORDATORIOS, string='Recordatorio', default='15min')
    requiere_proxima = fields.Boolean(compute='_compute_requiere_proxima')
    aviso = fields.Char(compute='_compute_aviso')

    @api.depends('activity_id')
    def _compute_lead(self):
        for wiz in self:
            act = wiz.activity_id
            lead = self.env['crm.lead'].browse(act.res_id) if act.res_model == 'crm.lead' else self.env['crm.lead']
            wiz.lead_id = lead
            wiz.contacto = lead.partner_id.name or lead.name
            wiz.es_primer_contacto = act.fin_tipo == 'primer_contacto'

    @api.depends('nueva_etapa_id')
    def _compute_requiere_proxima(self):
        for wiz in self:
            wiz.requiere_proxima = not wiz.nueva_etapa_id.fin_cerrada

    @api.depends('conclusion', 'proxima_fecha', 'lead_id')
    def _compute_aviso(self):
        horario = Horario(self.env)
        intentos_max = horario.config.intentos_max or 5
        for wiz in self:
            avisos = []
            if wiz.conclusion == 'no_respondio' and wiz.lead_id.fin_intentos + 1 >= intentos_max:
                avisos.append(f'Lleva {wiz.lead_id.fin_intentos + 1} intentos sin respuesta: '
                              f'considerá pasarla a Cerrado perdido ("No responde").')
            if wiz.conclusion == 'reprogramar' and wiz.proxima_fecha and not horario.es_habil(wiz.proxima_fecha):
                avisos.append('Atención: el horario acordado queda fuera del horario laboral.')
            wiz.aviso = ' '.join(avisos) or False

    @api.onchange('conclusion')
    def _onchange_conclusion(self):
        if not self.activity_id or not self.conclusion:
            return
        horario = Horario(self.env)
        nombre = self.contacto or ''
        user = self.activity_id.user_id or self.env.user
        siguiente = horario.inicio_dia_siguiente(fin_ahora(self.env))
        Act = self.env['mail.activity']
        self.proxima_modalidad = self.activity_id.fin_modalidad or 'llamada'
        if self.conclusion == 'no_respondio':
            self.proxima_asunto = f'{nombre} — Reintento de contacto'
            self.proxima_fecha = Act._fin_escalonar(user, siguiente)
            self.proxima_recordatorio = 'dia'
            self.proxima_tipo = 'seguimiento'
        elif self.conclusion == 'respondio':
            self.proxima_asunto = f'{nombre} — Seguimiento'
            self.proxima_fecha = Act._fin_escalonar(user, siguiente)
            self.proxima_recordatorio = '15min'
        else:
            self.proxima_asunto = f'{nombre} — Llamar (horario pedido por el cliente)'
            self.proxima_fecha = False
            self.proxima_recordatorio = '15min'
            self.proxima_modalidad = 'llamada'

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if res.get('conclusion') and res.get('activity_id'):
            wiz = self.new(res)
            wiz._onchange_conclusion()
            for campo in ('proxima_asunto', 'proxima_fecha', 'proxima_modalidad', 'proxima_recordatorio', 'proxima_tipo'):
                if campo in fields_list:
                    res[campo] = wiz[campo]
        return res

    def action_confirmar(self):
        self.ensure_one()
        act = self.activity_id
        lead = self.lead_id
        if not act.exists():
            raise UserError('La tarea ya no existe.')
        cierra = self.nueva_etapa_id.fin_cerrada
        if self.nueva_etapa_id.is_won and not self.importe_final:
            raise UserError('Cargá el importe final para pasar a Cerrado ganado.')
        if self.nueva_etapa_id.fin_es_perdido and not self.lost_reason_id:
            raise UserError('Indicá el motivo de pérdida.')
        if lead and not cierra and lead.fin_codigo_etapa not in ('00',) and lead.fin_producto in (False, 'sin_definir') \
                and (self.nueva_etapa_id.fin_codigo or '00') > '10':
            raise UserError('Definí el producto antes de avanzar de etapa.')
        if not cierra and (not self.proxima_asunto or not self.proxima_fecha):
            raise UserError('Programá la próxima tarea (asunto y fecha): una oportunidad abierta '
                            'nunca puede quedar sin tarea futura.')
        ahora = fin_ahora(self.env)
        etiqueta = dict(CONCLUSIONES)[self.conclusion]

        # 1) Próxima tarea primero: la oportunidad nunca queda sin tarea.
        if lead and not cierra:
            ctx = {'mail_activity_quick_update': True}
            if self.conclusion == 'reprogramar':
                ctx['fin_fuera_horario_ok'] = True
            self.env['mail.activity'].with_context(**ctx).create({
                'res_model_id': self.env['ir.model']._get_id('crm.lead'),
                'res_id': lead.id,
                'activity_type_id': self.env.ref(
                    'financiera_crm.actividad_documentacion' if self.proxima_tipo == 'documentacion'
                    else 'financiera_crm.actividad_seguimiento').id,
                'summary': self.proxima_asunto,
                'user_id': (act.user_id or self.env.user).id,
                'fin_fecha_hora': self.proxima_fecha,
                'fin_tipo': self.proxima_tipo,
                'fin_modalidad': self.proxima_modalidad,
                'fin_recordatorio': self.proxima_recordatorio,
            })

        # 2) Cerrar la tarea actual con su conclusión.
        es_primer_contacto = act.fin_tipo == 'primer_contacto'
        act.write({'fin_conclusion': self.conclusion, 'fin_nota_cierre': self.nota})
        feedback = etiqueta + (f': {self.nota}' if self.nota else '')
        act.with_context(fin_concluir_ok=True).action_feedback(feedback=feedback)

        if not lead:
            return {'type': 'ir.actions.act_window_close'}

        # 3) Efectos sobre la oportunidad.
        if es_primer_contacto:
            lead._fin_registrar_primer_contacto(ahora)
        if self.conclusion == 'no_respondio':
            lead.with_context(fin_sistema=True).fin_intentos += 1
        elif self.conclusion == 'respondio':
            lead.with_context(fin_sistema=True).fin_intentos = 0
        if self.nueva_etapa_id:
            vals = {'stage_id': self.nueva_etapa_id.id}
            if self.nueva_etapa_id.is_won:
                vals['fin_importe_final'] = self.importe_final
            if self.nueva_etapa_id.fin_es_perdido:
                vals['lost_reason_id'] = self.lost_reason_id.id
            lead.write(vals)
        return {'type': 'ir.actions.act_window_close', 'infos': {'done': True}}
