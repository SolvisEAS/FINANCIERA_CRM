from odoo import api, fields, models

from .utils import ORIGENES, normalizar_telefono


class ResPartner(models.Model):
    _inherit = 'res.partner'

    fin_es_cliente = fields.Boolean('Es cliente de la financiera', index=True, copy=False)
    fin_ci = fields.Char('Cédula (CI)', index='btree_not_null')
    fin_fecha_nacimiento = fields.Date('Fecha de nacimiento')
    fin_origen = fields.Selection(ORIGENES, string='Origen')
    fin_telefono_e164 = fields.Char('Teléfono normalizado', compute='_compute_fin_telefono_e164',
                                    store=True, index='btree_not_null')
    fin_whatsapp_ids = fields.One2many('financiera.whatsapp.resumen', 'partner_id', string='Historial de WhatsApp')
    fin_demo = fields.Boolean('Dato de demo', copy=False, index='btree_not_null')

    @api.depends('phone')
    def _compute_fin_telefono_e164(self):
        for partner in self:
            partner.fin_telefono_e164 = normalizar_telefono(partner.phone)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('phone') and self.env.context.get('fin_normalizar', True):
                vals['phone'] = normalizar_telefono(vals['phone']) or vals['phone']
            if self.env.context.get('fin_cliente'):
                vals.setdefault('fin_es_cliente', True)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('phone'):
            vals['phone'] = normalizar_telefono(vals['phone']) or vals['phone']
        return super().write(vals)

    @api.model
    def _fin_buscar(self, telefono=None, email=None):
        """Busca un cliente existente por teléfono (E.164) o email."""
        partner = self.browse()
        e164 = normalizar_telefono(telefono)
        if e164:
            partner = self.sudo().search([('fin_telefono_e164', '=', e164), ('fin_es_cliente', '=', True)], limit=1)
        if not partner and email:
            partner = self.sudo().search([('email', '=ilike', email.strip()), ('fin_es_cliente', '=', True)], limit=1)
        return partner

    @api.model
    def _fin_buscar_o_crear(self, vals):
        """Reutiliza el contacto si el teléfono o el email ya existen; si no, lo crea."""
        partner = self._fin_buscar(vals.get('phone'), vals.get('email'))
        if partner:
            faltantes = {k: v for k, v in vals.items() if v and not partner[k]}
            if faltantes:
                partner.sudo().write(faltantes)
            return partner, False
        vals = dict(vals, fin_es_cliente=True)
        return self.sudo().create(vals), True

    def action_fin_oportunidades(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('financiera_crm.action_oportunidades')
        action['domain'] = [('partner_id', '=', self.id)]
        action['context'] = {'default_partner_id': self.id}
        return action
