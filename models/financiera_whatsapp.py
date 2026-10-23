import random
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models

from .utils import Horario, normalizar_telefono


class FinancieraWhatsappResumen(models.Model):
    """Un resumen por oportunidad y día: agrupa los mensajes para no inundar el historial."""
    _name = 'financiera.whatsapp.resumen'
    _description = 'Resumen diario de WhatsApp'
    _order = 'fecha desc, id desc'

    lead_id = fields.Many2one('crm.lead', string='Oportunidad', required=True, index=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', related='lead_id.partner_id', store=True, string='Contacto')
    user_id = fields.Many2one('res.users', related='lead_id.user_id', store=True, string='Vendedora')
    fecha = fields.Date('Día', required=True, index=True)
    mensajes = fields.Integer('Mensajes')
    entrantes = fields.Integer('Del cliente')
    salientes = fields.Integer('De la vendedora')
    contenido = fields.Text('Conversación')
    message_id = fields.Many2one('mail.message', string='Mensaje en el historial', ondelete='set null')
    fin_demo = fields.Boolean(copy=False)

    _dia_unico = models.Constraint('UNIQUE(lead_id, fecha)', 'Ya hay un resumen para ese día.')

    def _cuerpo(self):
        self.ensure_one()
        lineas = (self.contenido or '').splitlines()[-40:]
        filas = Markup('').join(Markup('<li>%s</li>') % linea for linea in lineas)
        return Markup(
            '<div class="o_fin_resumen_wa"><b>💬 Resumen WhatsApp · %s</b> '
            '<span class="text-muted">(%s mensajes: %s del cliente, %s de la vendedora)</span>'
            '<ul class="mb-0">%s</ul></div>'
        ) % (self.fecha.strftime('%d/%m/%Y'), self.mensajes, self.entrantes, self.salientes, filas)

    def _publicar(self):
        tipo = self.env.ref('financiera_crm.actividad_resumen_whatsapp')
        for rec in self:
            cuerpo = rec._cuerpo()
            if rec.message_id:
                rec.message_id.sudo().write({'body': cuerpo})
            else:
                rec.message_id = rec.lead_id.sudo().message_post(
                    body=cuerpo, message_type='comment', subtype_xmlid='mail.mt_activities',
                    mail_activity_type_id=tipo.id,
                )

    @api.model
    def _registrar_mensaje(self, telefono, texto, entrante=True, nombre=None, fecha=None, numero_vendedora=None):
        """Registra un mensaje de WhatsApp. Crea contacto y oportunidad si el número es nuevo."""
        Lead = self.env['crm.lead'].sudo()
        fecha = fecha or fields.Datetime.now()
        e164 = normalizar_telefono(telefono)
        vendedora = self.env['res.users']
        if numero_vendedora:
            vendedora = self.env['res.users'].sudo().search(
                [('fin_wa_numero', '=', normalizar_telefono(numero_vendedora))], limit=1)
            if vendedora:
                vendedora.write({'fin_wa_estado': 'conectado', 'fin_wa_ultimo': fecha})
        partner = self.env['res.partner']._fin_buscar(telefono=e164)
        lead = partner and Lead.search([('partner_id', '=', partner.id), ('fin_cerrada', '=', False)], limit=1)
        if not lead:
            if not entrante:
                return Lead
            lead, _nueva = Lead._fin_ingresar({
                'nombre': nombre or e164, 'telefono': e164, 'origen': 'whatsapp', 'producto': 'sin_definir',
                'user_id': vendedora.id or False,
                'asignado_por': vendedora and 'WhatsApp de la vendedora' or False,
            })
        dia = Horario(self.env).dia_local(fecha)
        resumen = self.sudo().search([('lead_id', '=', lead.id), ('fecha', '=', dia)], limit=1)
        if not resumen:
            resumen = self.sudo().create({'lead_id': lead.id, 'fecha': dia})
        hora = Horario(self.env).local(fecha).strftime('%H:%M')
        autor = (lead.partner_id.name or 'Cliente') if entrante else (lead.user_id.name or 'Vendedora')
        resumen.write({
            'mensajes': resumen.mensajes + 1,
            'entrantes': resumen.entrantes + (1 if entrante else 0),
            'salientes': resumen.salientes + (0 if entrante else 1),
            'contenido': (resumen.contenido or '') + f'{hora} {autor}: {texto}\n',
        })
        resumen._publicar()
        en_00 = lead.fin_codigo_etapa == '00' and lead.activity_ids.filtered(lambda a: a.fin_tipo == 'primer_contacto')
        if entrante and en_00:
            lead.fin_cliente_escribio = True
            if lead.user_id:
                lead._fin_avisar(lead.user_id, 'El cliente escribió, respondé',
                                 f'{lead.partner_id.name}: {texto[:80]}', 'warning')
        if not entrante and en_00:
            lead.fin_vendedora_respondio_wa = True
        return lead

    @api.model
    def _simular(self):
        """Simula un mensaje entrante de un número nuevo (demo)."""
        nombre = random.choice(['Fernando Ortiz', 'Gabriela Rojas', 'Nelson Duarte', 'Liz Martínez', 'Hugo Acosta'])
        telefono = f'09{random.choice("6789")}{random.randint(1000000, 9999999)}'
        return self._registrar_mensaje(telefono, 'Hola, quiero información sobre préstamos 🙂', True, nombre)


class CrmLeadWhatsapp(models.Model):
    _inherit = 'crm.lead'

    fin_whatsapp_ids = fields.One2many('financiera.whatsapp.resumen', 'lead_id', string='WhatsApp')
