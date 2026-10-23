from odoo import SUPERUSER_ID, api, fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    fin_disponible = fields.Boolean('Disponible para asignación', default=True,
                                    help='Desactivar en vacaciones o ausencias: no recibe oportunidades nuevas.')
    fin_tope_abiertas = fields.Integer('Tope de oportunidades abiertas', default=0, help='0 = sin tope.')
    fin_orden = fields.Integer('Orden en la rotación', default=10)
    fin_es_vendedora = fields.Boolean(compute='_compute_fin_roles', search='_search_fin_es_vendedora')
    fin_es_admin = fields.Boolean(compute='_compute_fin_roles')
    fin_solo_propias = fields.Boolean(compute='_compute_fin_roles',
                                      help='Técnico: restringe la vista a sus propios registros.')
    fin_abiertas_count = fields.Integer('Oportunidades abiertas', compute='_compute_fin_abiertas')

    fin_wa_numero = fields.Char('WhatsApp de la vendedora')
    fin_wa_estado = fields.Selection([
        ('conectado', 'Conectado'),
        ('desconectado', 'Desconectado'),
    ], string='Estado WhatsApp (QR)', default='desconectado')
    fin_wa_ultimo = fields.Datetime('Último mensaje sincronizado')

    @api.depends_context('uid')
    def _compute_fin_roles(self):
        for user in self:
            vendedora = user._has_group('financiera_crm.group_vendedora')
            admin = user._has_group('financiera_crm.group_admin')
            user.fin_es_vendedora = vendedora and not admin
            user.fin_es_admin = admin
            user.fin_solo_propias = vendedora and not admin

    def _search_fin_es_vendedora(self, operator, value):
        vendedora = self.env.ref('financiera_crm.group_vendedora')
        admin = self.env.ref('financiera_crm.group_admin')
        ids = self.sudo().with_context(active_test=False).search([
            ('all_group_ids', 'in', vendedora.id), ('all_group_ids', 'not in', admin.id),
            ('share', '=', False), ('id', '!=', SUPERUSER_ID),
        ]).ids
        positivo = (operator in ('=', 'in') and bool(value)) or (operator in ('!=', 'not in') and not value)
        return [('id', 'in' if positivo else 'not in', ids)]

    def _compute_fin_abiertas(self):
        datos = self.env['crm.lead'].sudo()._read_group(
            [('user_id', 'in', self.ids), ('stage_id.fin_cerrada', '=', False), ('type', '=', 'opportunity')],
            ['user_id'], ['__count'])
        conteo = {user.id: count for user, count in datos}
        for user in self:
            user.fin_abiertas_count = conteo.get(user.id, 0)

    def _has_group(self, xmlid):
        group = self.env.ref(xmlid, raise_if_not_found=False)
        return bool(group) and group.id in self.sudo().all_group_ids.ids
