from odoo import api, fields, models


class CrmStage(models.Model):
    _inherit = 'crm.stage'

    fin_codigo = fields.Char('Código', help='00, 10, 20… Se muestra delante del nombre.')
    fin_es_perdido = fields.Boolean('Etapa de pérdida')
    fin_cerrada = fields.Boolean('Etapa de cierre', compute='_compute_fin_cerrada', store=True)

    @api.depends('is_won', 'fin_es_perdido')
    def _compute_fin_cerrada(self):
        for stage in self:
            stage.fin_cerrada = stage.is_won or stage.fin_es_perdido
