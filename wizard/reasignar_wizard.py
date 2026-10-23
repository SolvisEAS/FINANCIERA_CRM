from odoo import api, fields, models
from odoo.exceptions import AccessError


class FinancieraReasignarWizard(models.TransientModel):
    _name = 'financiera.reasignar.wizard'
    _description = 'Reasignar oportunidades'

    lead_ids = fields.Many2many('crm.lead', string='Oportunidades')
    user_id = fields.Many2one('res.users', string='Reasignar a', required=True,
                              domain="[('fin_es_vendedora', '=', True)]")
    cantidad = fields.Integer(compute='_compute_cantidad')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'crm.lead' and self.env.context.get('active_ids'):
            res['lead_ids'] = [(6, 0, self.env.context['active_ids'])]
        return res

    @api.depends('lead_ids')
    def _compute_cantidad(self):
        for wiz in self:
            wiz.cantidad = len(wiz.lead_ids)

    def action_confirmar(self):
        self.ensure_one()
        if not self.env.user.fin_es_admin:
            raise AccessError('Solo el administrador puede reasignar.')
        self.lead_ids.with_context(fin_sistema=True).write({
            'user_id': self.user_id.id,
            'fin_asignado_por': f'Reasignada por {self.env.user.name}',
        })
        self.lead_ids.activity_ids.sudo().write({'user_id': self.user_id.id})
        for lead in self.lead_ids:
            lead._fin_nota(f'Reasignada a {self.user_id.name} por {self.env.user.name}.')
        self.env['crm.lead']._fin_avisar(self.user_id, 'Oportunidades reasignadas',
                                         f'Te asignaron {len(self.lead_ids)} oportunidad(es).')
        return {'type': 'ir.actions.act_window_close'}
