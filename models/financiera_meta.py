import json
import logging
import random

import requests

from odoo import api, fields, models

from .utils import normalizar_telefono

_logger = logging.getLogger(__name__)

CAMPOS_DESTINO = [
    ('nombre', 'Nombre completo'),
    ('telefono', 'Teléfono / WhatsApp'),
    ('email', 'Email'),
    ('ci', 'Cédula (CI)'),
    ('ciudad', 'Ciudad'),
    ('producto', 'Producto'),
    ('importe', 'Importe solicitado'),
    ('plazo', 'Plazo (meses)'),
    ('vehiculo', 'Vehículo'),
    ('ingreso', 'Ingreso mensual'),
    ('situacion', 'Situación laboral'),
    ('nota', 'Nota (se agrega a la descripción)'),
]

PALABRAS_PRODUCTO = {
    'prestamo': ('prest', 'crédito personal', 'credito personal', 'loan'),
    'tarjeta': ('tarjeta', 'card'),
    'seguro': ('seguro', 'vehic', 'auto', 'insurance'),
}


class FinancieraMetaMapeo(models.Model):
    _name = 'financiera.meta.mapeo'
    _description = 'Mapeo de preguntas de Meta a campos'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    pregunta = fields.Char('Pregunta del formulario (field name)', required=True,
                           help='Nombre técnico de la pregunta tal como llega en field_data (ej. full_name).')
    campo = fields.Selection(CAMPOS_DESTINO, string='Campo de destino', required=True)


class FinancieraMetaLead(models.Model):
    """Cola de leads recibidos de Meta: nunca se pierde un lead aunque la API falle."""
    _name = 'financiera.meta.lead'
    _description = 'Lead recibido de Meta'
    _order = 'id desc'

    leadgen_id = fields.Char('Leadgen ID', index=True, required=True)
    form_id = fields.Char('Formulario')
    ad_id = fields.Char('Anuncio')
    campana = fields.Char('Campaña')
    payload = fields.Text('Payload del webhook')
    respuesta = fields.Text('Respuesta de Graph API')
    estado = fields.Selection([
        ('pendiente', 'Pendiente'), ('procesado', 'Procesado'), ('duplicado', 'Contacto existente'),
        ('error', 'Error (se reintenta)'),
    ], default='pendiente', index=True)
    intentos = fields.Integer(default=0)
    error = fields.Text()
    lead_id = fields.Many2one('crm.lead', string='Oportunidad')

    _leadgen_unico = models.Constraint('UNIQUE(leadgen_id)', 'Ese lead de Meta ya se recibió.')

    @api.model
    def _encolar(self, valor, payload):
        existente = self.sudo().search([('leadgen_id', '=', str(valor.get('leadgen_id')))], limit=1)
        if existente:
            return existente
        return self.sudo().create({
            'leadgen_id': str(valor.get('leadgen_id')),
            'form_id': str(valor.get('form_id') or ''),
            'ad_id': str(valor.get('ad_id') or ''),
            'campana': valor.get('campaign_name') or valor.get('ad_name') or '',
            'payload': json.dumps(payload, ensure_ascii=False),
        })

    def _procesar(self):
        config = self.env['financiera.config']._get()
        for rec in self.filtered(lambda r: r.estado in ('pendiente', 'error')):
            try:
                datos = rec._obtener_datos(config)
                rec._crear_oportunidad(datos)
            except Exception as exc:  # noqa: BLE001 — se reintenta por cron
                _logger.warning('Meta lead %s: %s', rec.leadgen_id, exc)
                rec.write({'estado': 'error', 'intentos': rec.intentos + 1, 'error': str(exc)[:2000]})

    def _obtener_datos(self, config):
        payload = json.loads(self.payload or '{}')
        if payload.get('field_data'):  # simulación o payload ya enriquecido
            return payload
        if not config.meta_access_token:
            raise ValueError('Falta el token de acceso de Graph API en la configuración.')
        url = f'https://graph.facebook.com/{config.meta_graph_version or "v21.0"}/{self.leadgen_id}'
        resp = requests.get(url, params={
            'access_token': config.meta_access_token,
            'fields': 'field_data,created_time,ad_name,campaign_name,form_id,ad_id',
        }, timeout=15)
        self.respuesta = resp.text[:10000]
        resp.raise_for_status()
        return resp.json()

    def _crear_oportunidad(self, datos):
        mapeo = {m.pregunta.lower(): m.campo for m in self.env['financiera.meta.mapeo'].sudo().search([])}
        valores = {}
        for item in datos.get('field_data', []):
            campo = mapeo.get((item.get('name') or '').lower())
            if campo:
                valores[campo] = ', '.join(item.get('values') or [])
        producto = 'sin_definir'
        texto_producto = (valores.get('producto') or '').lower()
        for clave, palabras in PALABRAS_PRODUCTO.items():
            if any(p in texto_producto for p in palabras):
                producto = clave
                break
        campana = ' / '.join(filter(None, [datos.get('campaign_name') or self.campana,
                                           datos.get('ad_name'), datos.get('form_id') or self.form_id]))
        lead, nueva = self.env['crm.lead'].sudo()._fin_ingresar({
            'nombre': valores.get('nombre'), 'telefono': valores.get('telefono'), 'email': valores.get('email'),
            'ci': valores.get('ci'), 'ciudad': valores.get('ciudad'), 'producto': producto,
            'importe': valores.get('importe'), 'plazo': valores.get('plazo'), 'vehiculo': valores.get('vehiculo'),
            'nota': valores.get('nota'), 'origen': 'meta', 'campana': campana,
            'payload': json.dumps(datos, ensure_ascii=False),
        })
        self.write({'estado': 'procesado' if nueva else 'duplicado', 'lead_id': lead.id, 'error': False})

    @api.model
    def _cron_reintentar(self):
        self.search([('estado', 'in', ('pendiente', 'error')), ('intentos', '<', 10)], limit=50)._procesar()

    @api.model
    def _simular(self):
        """Simula un lead de Meta (para la demo, sin llamar a la API)."""
        nombres = ['Lucía Benítez', 'Marcos Giménez', 'Sofía Cáceres', 'Diego Villalba', 'Rocío Ayala']
        nombre = random.choice(nombres)
        telefono = f'09{random.choice("6789")}{random.randint(1000000, 9999999)}'
        producto = random.choice(['Préstamo personal', 'Tarjeta de crédito', 'Seguro vehicular'])
        payload = {
            'leadgen_id': f'SIM-{random.randint(10**9, 10**10)}',
            'campaign_name': 'Campaña demo octubre', 'ad_name': f'Anuncio {producto}', 'form_id': 'form-demo',
            'field_data': [
                {'name': 'full_name', 'values': [nombre]},
                {'name': 'phone_number', 'values': [telefono]},
                {'name': 'email', 'values': [nombre.split()[0].lower() + '@ejemplo.com.py']},
                {'name': 'producto_interes', 'values': [producto]},
                {'name': 'monto', 'values': [str(random.choice([5, 10, 15, 25])) + '000000']},
                {'name': 'ciudad', 'values': [random.choice(['Asunción', 'Luque', 'San Lorenzo', 'Lambaré'])]},
            ],
        }
        rec = self._encolar(payload, payload)
        rec._procesar()
        return rec.lead_id


class CrmLeadMeta(models.Model):
    _inherit = 'crm.lead'

    @api.model
    def _fin_ingresar(self, datos):
        """Entrada única de leads (Meta, WhatsApp, importación).

        Reutiliza el contacto si el teléfono o email ya existe. Si ese contacto tiene una
        oportunidad abierta, no crea otra: deja una nota y avisa a la vendedora dueña.
        Devuelve (oportunidad, es_nueva).
        """
        Partner = self.env['res.partner'].sudo()
        origen = datos.get('origen') or 'manual'
        partner, _nuevo = Partner._fin_buscar_o_crear({
            'name': datos.get('nombre') or normalizar_telefono(datos.get('telefono')) or 'Sin nombre',
            'phone': datos.get('telefono'),
            'email': datos.get('email'),
            'fin_ci': datos.get('ci'),
            'city': datos.get('ciudad'),
            'fin_origen': origen,
            'fin_demo': datos.get('demo', False),
        })
        abierta = self.sudo().search([('partner_id', '=', partner.id), ('fin_cerrada', '=', False)], limit=1)
        etiqueta = dict(self._fields['fin_origen'].selection).get(origen, origen)
        if abierta:
            detalle = f'Nuevo ingreso por {etiqueta}'
            if datos.get('campana'):
                detalle += f' ({datos["campana"]})'
            if datos.get('nota'):
                detalle += f': {datos["nota"]}'
            abierta._fin_nota(detalle)
            if abierta.user_id:
                abierta._fin_avisar(abierta.user_id, 'Volvió a escribir un cliente tuyo',
                                    f'{partner.name} ingresó de nuevo por {etiqueta}.')
            return abierta, False

        def a_numero(valor):
            try:
                return float(''.join(c for c in str(valor) if c.isdigit()) or 0)
            except ValueError:
                return 0.0

        vals = {
            'name': partner.name,
            'partner_id': partner.id,
            'fin_producto': datos.get('producto') or 'sin_definir',
            'fin_origen': origen,
            'fin_campana': datos.get('campana'),
            'fin_importe_solicitado': a_numero(datos.get('importe')) if datos.get('importe') else 0.0,
            'fin_plazo_meses': int(a_numero(datos.get('plazo'))) if datos.get('plazo') else 0,
            'fin_vehiculo': datos.get('vehiculo'),
            'fin_meta_payload': datos.get('payload'),
            'fin_demo': datos.get('demo', False),
        }
        if datos.get('nota'):
            vals['description'] = datos['nota']
        if datos.get('user_id'):
            vals['user_id'] = datos['user_id']
            vals['fin_asignado_por'] = datos.get('asignado_por') or 'Asignación directa'
        lead = self.sudo().with_context(fin_permitir_duplicado=True).create(vals)
        return lead, True
