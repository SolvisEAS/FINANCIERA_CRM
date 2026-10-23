import calendar
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import PRODUCTOS

MESES = [(str(i), nombre) for i, nombre in enumerate(
    ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto',
     'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'], start=1)]


class FinancieraObjetivo(models.Model):
    _name = 'financiera.objetivo'
    _description = 'Objetivo de ventas'
    _order = 'anio desc, mes desc, user_id, producto'

    user_id = fields.Many2one('res.users', string='Vendedora', index=True,
                              help='Vacío = objetivo global del equipo.')
    tipo_periodo = fields.Selection([('mensual', 'Mensual'), ('semanal', 'Semanal')],
                                    string='Tipo de período', default='mensual', required=True)
    mes = fields.Selection(MESES, string='Mes', required=True, default=lambda s: str(date.today().month))
    anio = fields.Integer('Año', required=True, default=lambda s: date.today().year)
    fecha_inicio = fields.Date(compute='_compute_fechas', store=True)
    fecha_fin = fields.Date(compute='_compute_fechas', store=True)
    periodo = fields.Char('Período', compute='_compute_fechas', store=True)
    producto = fields.Selection(PRODUCTOS, string='Producto', required=True)
    meta_cantidad = fields.Integer('Meta (cantidad)')
    meta_importe = fields.Monetary('Meta (importe)', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda s: s.env.company.currency_id)
    logrado_cantidad = fields.Integer('Logrado', compute='_compute_logrado')
    logrado_importe = fields.Monetary('Logrado (importe)', compute='_compute_logrado', currency_field='currency_id')
    cumplimiento = fields.Float('% cumplimiento', compute='_compute_logrado',
                                help='Logrado / meta, sin tope: 20 de 10 = 200 %.')
    semaforo = fields.Selection([('verde', 'Cumplido'), ('amarillo', 'En camino'), ('rojo', 'Atrasado')],
                                compute='_compute_logrado')
    fin_demo = fields.Boolean(copy=False)

    _unico = models.Constraint(
        'UNIQUE(user_id, tipo_periodo, mes, anio, producto)',
        'Ya existe un objetivo para esa vendedora, período y producto.',
    )

    @api.depends('mes', 'anio', 'tipo_periodo')
    def _compute_fechas(self):
        nombres = dict(MESES)
        for obj in self:
            mes, anio = int(obj.mes or 1), obj.anio or date.today().year
            obj.fecha_inicio = date(anio, mes, 1)
            obj.fecha_fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
            obj.periodo = f'{nombres.get(obj.mes, "")} {anio}'

    def _compute_logrado(self):
        Lead = self.env['crm.lead'].sudo()
        for obj in self:
            dominio = [
                ('fin_es_ganada', '=', True), ('fin_producto', '=', obj.producto),
                ('date_closed', '>=', fields.Datetime.to_datetime(obj.fecha_inicio)),
                ('date_closed', '<', fields.Datetime.to_datetime(obj.fecha_fin + relativedelta(days=1))),
            ]
            if obj.user_id:
                dominio.append(('user_id', '=', obj.user_id.id))
            ganadas = Lead.search(dominio)
            obj.logrado_cantidad = len(ganadas)
            obj.logrado_importe = sum(ganadas.mapped('fin_importe_final'))
            if obj.meta_cantidad:
                obj.cumplimiento = obj.logrado_cantidad / obj.meta_cantidad * 100.0
            elif obj.meta_importe:
                obj.cumplimiento = obj.logrado_importe / obj.meta_importe * 100.0
            else:
                obj.cumplimiento = 0.0
            obj.semaforo = 'verde' if obj.cumplimiento >= 100 else 'amarillo' if obj.cumplimiento >= 70 else 'rojo'

    @api.model
    def action_copiar_mes_anterior(self):
        """Copia los objetivos del mes anterior al mes actual (los que falten)."""
        hoy = date.today()
        anterior = hoy - relativedelta(months=1)
        previos = self.search([('mes', '=', str(anterior.month)), ('anio', '=', anterior.year),
                               ('tipo_periodo', '=', 'mensual')])
        if not previos:
            raise UserError('No hay objetivos cargados en el mes anterior.')
        existentes = {(o.user_id.id, o.producto) for o in self.search([
            ('mes', '=', str(hoy.month)), ('anio', '=', hoy.year), ('tipo_periodo', '=', 'mensual')])}
        nuevos = [{
            'user_id': o.user_id.id, 'producto': o.producto, 'mes': str(hoy.month), 'anio': hoy.year,
            'meta_cantidad': o.meta_cantidad, 'meta_importe': o.meta_importe,
        } for o in previos if (o.user_id.id, o.producto) not in existentes]
        self.create(nuevos)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'message': f'Se copiaron {len(nuevos)} objetivos al mes actual.', 'type': 'success',
                       'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }

    @api.model
    def _fin_global(self, objetivos):
        """% global por vendedora: promedio simple de los % y total unificado."""
        tope = self.env['financiera.config']._get().tope_100_promedio
        if not objetivos:
            return {'promedio': 0.0, 'unificado': 0.0, 'tope': tope}
        pcts = [min(o.cumplimiento, 100.0) if tope else o.cumplimiento for o in objetivos]
        metas = sum(o.meta_cantidad for o in objetivos)
        logrado = sum(o.logrado_cantidad for o in objetivos)
        return {
            'promedio': round(sum(pcts) / len(pcts), 1),
            'unificado': round(logrado / metas * 100.0, 1) if metas else 0.0,
            'tope': tope,
        }
