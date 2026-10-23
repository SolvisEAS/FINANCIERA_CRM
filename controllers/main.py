import hashlib
import hmac
import json
import logging

from odoo import fields, http
from odoo.http import request

from ..wizard.importar_wizard import generar_plantilla

_logger = logging.getLogger(__name__)


def _json(datos, status=200):
    return request.make_response(json.dumps(datos, ensure_ascii=False), status=status,
                                 headers=[('Content-Type', 'application/json; charset=utf-8')])


class FinancieraController(http.Controller):

    # ------------------------------------------------------------------ Meta Lead Ads
    @http.route('/financiera/meta/webhook', type='http', auth='public', methods=['GET'], csrf=False)
    def meta_verificar(self, **params):
        config = request.env['financiera.config'].sudo()._get()
        if params.get('hub.mode') == 'subscribe' and config.meta_verify_token \
                and hmac.compare_digest(params.get('hub.verify_token') or '', config.meta_verify_token):
            return request.make_response(params.get('hub.challenge', ''))
        return request.make_response('Token inválido', status=403)

    @http.route('/financiera/meta/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def meta_recibir(self, **_params):
        config = request.env['financiera.config'].sudo()._get()
        cuerpo = request.httprequest.get_data()
        if config.meta_app_secret:
            firma = request.httprequest.headers.get('X-Hub-Signature-256', '')
            esperada = 'sha256=' + hmac.new(config.meta_app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(firma, esperada):
                return request.make_response('Firma inválida', status=403)
        try:
            payload = json.loads(cuerpo or b'{}')
        except ValueError:
            return request.make_response('JSON inválido', status=400)
        Cola = request.env['financiera.meta.lead'].sudo()
        recibidos = Cola.browse()
        for entrada in payload.get('entry', []):
            for cambio in entrada.get('changes', []):
                if cambio.get('field') == 'leadgen' and cambio.get('value', {}).get('leadgen_id'):
                    recibidos |= Cola._encolar(cambio['value'], cambio)
        config.meta_ultimo_evento = fields.Datetime.now()
        recibidos._procesar()  # si falla queda en cola y el cron reintenta
        return request.make_response('EVENT_RECEIVED')

    # ------------------------------------------------------------------ WhatsApp (puente QR)
    @http.route('/financiera/whatsapp/entrante', type='http', auth='public', methods=['POST'], csrf=False)
    def whatsapp_entrante(self, **_params):
        """Recibe mensajes del puente de WhatsApp QR.

        JSON: {"telefono": "+5959…", "texto": "…", "entrante": true, "nombre": "…",
               "numero_vendedora": "+5959…", "fecha": "2026-10-22 13:05:00"}
        Header: X-Financiera-Token: <token de la configuración>
        """
        config = request.env['financiera.config'].sudo()._get()
        token = request.httprequest.headers.get('X-Financiera-Token', '')
        if not config.whatsapp_token or not hmac.compare_digest(token, config.whatsapp_token):
            return _json({'ok': False, 'error': 'token inválido'}, 403)
        try:
            datos = json.loads(request.httprequest.get_data() or b'{}')
        except ValueError:
            return _json({'ok': False, 'error': 'JSON inválido'}, 400)
        mensajes = datos if isinstance(datos, list) else [datos]
        resultado = []
        Resumen = request.env['financiera.whatsapp.resumen'].sudo()
        for msg in mensajes:
            if not msg.get('telefono') or not msg.get('texto'):
                continue
            lead = Resumen._registrar_mensaje(
                msg['telefono'], msg['texto'], entrante=msg.get('entrante', True), nombre=msg.get('nombre'),
                fecha=fields.Datetime.to_datetime(msg.get('fecha')) if msg.get('fecha') else None,
                numero_vendedora=msg.get('numero_vendedora'),
            )
            resultado.append({'telefono': msg['telefono'], 'oportunidad_id': lead.id or None})
        return _json({'ok': True, 'procesados': resultado})

    # ------------------------------------------------------------------ Plantilla de importación
    @http.route('/financiera/plantilla.xlsx', type='http', auth='user')
    def plantilla(self, **_params):
        if not request.env.user.fin_es_admin:
            return request.not_found()
        return request.make_response(generar_plantilla(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="plantilla_oportunidades.xlsx"'),
        ])
