from odoo import fields, models
from odoo.exceptions import UserError


class FinancieraCierreWizard(models.TransientModel):
    _name = 'financiera.cierre.wizard'
    _description = 'Cerrar oportunidad (ganada o perdida)'

    lead_id = fields.Many2one('crm.lead', required=True, ondelete='cascade')
    tipo = fields.Selection([('ganado', 'Cerrado ganado'), ('perdido', 'Cerrado perdido')], required=True)
    importe_final = fields.Monetary('Importe final', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda s: s.env.company.currency_id)
    lost_reason_id = fields.Many2one('crm.lost.reason', string='Motivo de pérdida')
    nota = fields.Text('Nota')

    def action_confirmar(self):
        self.ensure_one()
        if self.tipo == 'ganado':
            if not self.importe_final:
                raise UserError('El importe final es obligatorio.')
            vals = {'stage_id': self.env.ref('financiera_crm.stage_40').id, 'fin_importe_final': self.importe_final}
        else:
            if not self.lost_reason_id:
                raise UserError('El motivo de pérdida es obligatorio.')
            vals = {'stage_id': self.env.ref('financiera_crm.stage_50').id, 'lost_reason_id': self.lost_reason_id.id}
        self.lead_id.write(vals)
        if self.nota:
            self.lead_id._fin_nota(self.nota)
        return {'type': 'ir.actions.act_window_close'}
