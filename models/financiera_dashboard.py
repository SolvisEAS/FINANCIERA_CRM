from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

from .utils import PRODUCTOS, Horario, a_naive_utc

NOMBRE_PRODUCTO = dict(PRODUCTOS)


class FinancieraDashboard(models.AbstractModel):
    _name = 'financiera.dashboard'
    _description = 'Datos de los paneles'

    # ------------------------------------------------------------------ helpers
    def _vistas(self):
        ref = self.env.ref
        return {
            'lead_kanban': ref('financiera_crm.view_oportunidad_kanban').id,
            'lead_list': ref('financiera_crm.view_oportunidad_list').id,
            'lead_form': ref('financiera_crm.view_oportunidad_form').id,
            'lead_search': ref('financiera_crm.view_oportunidad_search').id,
            'tarea_list': ref('financiera_crm.view_tarea_list').id,
            'tarea_form': ref('financiera_crm.view_tarea_form').id,
            'tarea_search': ref('financiera_crm.view_tarea_search').id,
            'objetivo_list': ref('financiera_crm.view_objetivo_list').id,
        }

    def _usuario_filtro(self, user_id=None):
        """Las vendedoras solo ven lo suyo; el admin puede filtrar por vendedora o ver todo."""
        user = self.env.user
        if user.fin_solo_propias:
            return user
        if user_id:
            return self.env['res.users'].browse(int(user_id))
        return self.env['res.users']

    def _dom_user(self, users, campo='user_id'):
        return [(campo, 'in', users.ids)] if users else []

    def _rango(self, periodo, desde=None, hasta=None):
        """Devuelve (inicio, fin, inicio_ant, fin_ant, etiqueta, etiqueta_ant) en UTC naive."""
        horario = Horario(self.env)
        tz = horario.tz
        hoy = horario.dia_local(fields.Datetime.now())
        lunes = hoy - timedelta(days=hoy.weekday())
        if periodo == 'semana_pasada':
            ini = lunes - timedelta(days=7)
            fin = lunes
            ini_ant, fin_ant = ini - timedelta(days=7), ini
            etiquetas = ('Semana pasada', 'Semana anterior')
        elif periodo == 'este_mes':
            ini = hoy.replace(day=1)
            fin = ini + relativedelta(months=1)
            ini_ant, fin_ant = ini - relativedelta(months=1), ini
            etiquetas = ('Este mes', 'Mes pasado')
        elif periodo == 'mes_pasado':
            fin = hoy.replace(day=1)
            ini = fin - relativedelta(months=1)
            ini_ant, fin_ant = ini - relativedelta(months=1), ini
            etiquetas = ('Mes pasado', 'Mes anterior')
        elif periodo == 'rango' and desde and hasta:
            ini = fields.Date.to_date(desde)
            fin = fields.Date.to_date(hasta) + timedelta(days=1)
            dur = fin - ini
            ini_ant, fin_ant = ini - dur, ini
            etiquetas = ('Rango elegido', 'Período anterior')
        else:
            ini, fin = lunes, lunes + timedelta(days=7)
            ini_ant, fin_ant = ini - timedelta(days=7), ini
            etiquetas = ('Esta semana', 'Semana pasada')

        def utc(d):
            return a_naive_utc(tz.localize(datetime.combine(d, time(0, 0))))
        return utc(ini), utc(fin), utc(ini_ant), utc(fin_ant), etiquetas[0], etiquetas[1]

    @staticmethod
    def _variacion(actual, anterior):
        if not anterior:
            return None if not actual else 100.0
        return round((actual - anterior) / anterior * 100.0, 1)

    def _tiempo_promedio(self, users, ini, fin):
        Lead = self.env['crm.lead'].sudo()
        datos = Lead._read_group(
            self._dom_user(users) + [('fin_fecha_primer_contacto', '>=', ini), ('fin_fecha_primer_contacto', '<', fin)],
            [], ['fin_horas_primer_contacto:avg', '__count'])
        promedio, cantidad = datos[0] if datos else (0.0, 0)
        return round(promedio or 0.0, 2), cantidad

    # ------------------------------------------------------------------ Mi día
    @api.model
    def get_mi_dia(self):
        user = self.env.user
        users = user if user.fin_solo_propias else self.env['res.users']
        Lead = self.env['crm.lead']
        Act = self.env['mail.activity']
        horario = Horario(self.env)
        ahora = fields.Datetime.now()
        hoy = horario.dia_local(ahora)
        inicio_hoy = a_naive_utc(horario.tz.localize(datetime.combine(hoy, time(0, 0))))
        fin_hoy = inicio_hoy + timedelta(days=1)
        ini_sem, fin_sem, ini_ant, fin_ant, *_ = self._rango('esta_semana')

        dom_abiertas = self._dom_user(users) + [('fin_cerrada', '=', False)]
        dom_act = [('res_model', '=', 'crm.lead')] + self._dom_user(users)
        dominios = {
            'sin_contactar': dom_abiertas + [('stage_id.fin_codigo', '=', '00')],
            'nuevas_semana': self._dom_user(users) + [('create_date', '>=', ini_sem), ('create_date', '<', fin_sem)],
            'sin_tarea': dom_abiertas + [('activity_ids', '=', False)],
        }
        dom_act_hoy = dom_act + [('fin_fecha_hora', '>=', ahora), ('fin_fecha_hora', '<', fin_hoy)]
        dom_act_vencidas = dom_act + [('fin_fecha_hora', '<', ahora)]
        kpis = {
            'sin_contactar': Lead.search_count(dominios['sin_contactar']),
            'tareas_hoy': Act.search_count(dom_act_hoy),
            'tareas_vencidas': Act.search_count(dom_act_vencidas),
            'nuevas_semana': Lead.search_count(dominios['nuevas_semana']),
            'sin_tarea': Lead.search_count(dominios['sin_tarea']),
        }

        tareas = Act.search(dom_act + [('fin_fecha_hora', '<', fin_hoy)], order='fin_fecha_hora', limit=60)
        lista = []
        for act in tareas:
            lead = Lead.browse(act.res_id)
            lista.append({
                'id': act.id,
                'lead_id': act.res_id,
                'asunto': act.summary or act.activity_type_id.name,
                'oportunidad': lead.name,
                'producto': lead.fin_producto,
                'producto_nombre': NOMBRE_PRODUCTO.get(lead.fin_producto, 'Sin definir'),
                'hora': horario.local(act.fin_fecha_hora).strftime('%H:%M'),
                'dia': horario.local(act.fin_fecha_hora).strftime('%d/%m'),
                'vencida': act.fin_fecha_hora < ahora,
                'primer_contacto': act.fin_tipo == 'primer_contacto',
                'modalidad': dict(act._fields['fin_modalidad'].selection).get(act.fin_modalidad, ''),
                'vendedora': act.user_id.name,
                'cliente_escribio': lead.fin_cliente_escribio,
            })
        # Ya vienen ordenadas por fecha y hora; el orden estable sube primer contacto y vencidas.
        lista.sort(key=lambda t: (not t['primer_contacto'], not t['vencida']))

        sin_contactar = []
        for lead in Lead.search(dominios['sin_contactar'], order='date_open', limit=8):
            sin_contactar.append({
                'id': lead.id, 'nombre': lead.name, 'horas': lead.fin_horas_sin_contactar,
                'semaforo': lead.fin_semaforo, 'producto': lead.fin_producto,
                'producto_nombre': NOMBRE_PRODUCTO.get(lead.fin_producto, 'Sin definir'),
                'vendedora': lead.user_id.name,
            })

        tiempo_actual, n_actual = self._tiempo_promedio(users, ini_sem, fin_sem)
        tiempo_ant, n_ant = self._tiempo_promedio(users, ini_ant, fin_ant)
        return {
            'vistas': self._vistas(),
            'usuario': user.name,
            'es_admin': user.fin_es_admin,
            'fecha': horario.local(ahora).strftime('%d/%m/%Y'),
            'kpis': kpis,
            'dominios': dict(dominios, tareas_hoy=dom_act_hoy, tareas_vencidas=dom_act_vencidas),
            'tareas': lista,
            'sin_contactar': sin_contactar,
            'objetivos': self._objetivos_usuario(users),
            'tiempo': {'actual': tiempo_actual, 'n_actual': n_actual, 'anterior': tiempo_ant, 'n_anterior': n_ant,
                       'sla': horario.config.sla_horas},
        }

    def _objetivos_usuario(self, users):
        hoy = fields.Date.context_today(self)
        Obj = self.env['financiera.objetivo'].sudo()
        dominio = [('mes', '=', str(hoy.month)), ('anio', '=', hoy.year), ('tipo_periodo', '=', 'mensual')]
        dominio += [('user_id', 'in', users.ids)] if users else [('user_id', '!=', False)]
        objetivos = Obj.search(dominio)
        filas = []
        for producto, nombre in PRODUCTOS:
            grupo = objetivos.filtered(lambda o: o.producto == producto)
            meta = sum(grupo.mapped('meta_cantidad'))
            logrado = sum(grupo.mapped('logrado_cantidad'))
            filas.append({'producto': producto, 'nombre': nombre, 'meta': meta, 'logrado': logrado,
                          'pct': round(logrado / meta * 100.0, 1) if meta else 0.0})
        con_meta = [f for f in filas if f['meta']]
        tope = self.env['financiera.config']._get().tope_100_promedio
        promedio = round(sum(min(f['pct'], 100.0) if tope else f['pct'] for f in con_meta) / len(con_meta), 1) if con_meta else 0.0
        total_meta = sum(f['meta'] for f in con_meta)
        unificado = round(sum(f['logrado'] for f in con_meta) / total_meta * 100.0, 1) if total_meta else 0.0
        return {'filas': filas, 'promedio': promedio, 'unificado': unificado, 'tope': tope}

    # ------------------------------------------------------------------ Semana vs semana
    @api.model
    def get_semana(self, periodo='esta_semana', user_id=None, desde=None, hasta=None):
        users = self._usuario_filtro(user_id)
        Lead = self.env['crm.lead'].sudo()
        ini, fin, ini_ant, fin_ant, etiqueta, etiqueta_ant = self._rango(periodo, desde, hasta)
        base = self._dom_user(users)

        def nuevas(a, b):
            return base + [('create_date', '>=', a), ('create_date', '<', b)]

        def cerradas(a, b, extra):
            return base + [('date_closed', '>=', a), ('date_closed', '<', b)] + extra

        def por_producto(dominio):
            datos = dict(Lead._read_group(dominio, ['fin_producto'], ['__count']))
            return {p: datos.get(p, 0) for p, _n in PRODUCTOS}

        n_act, n_ant = Lead.search_count(nuevas(ini, fin)), Lead.search_count(nuevas(ini_ant, fin_ant))
        prod_act, prod_ant = por_producto(nuevas(ini, fin)), por_producto(nuevas(ini_ant, fin_ant))

        def top(datos):
            if not any(datos.values()):
                return None
            clave = max(datos, key=datos.get)
            return {'producto': clave, 'nombre': NOMBRE_PRODUCTO[clave], 'cantidad': datos[clave]}

        def ganadas(a, b):
            grupos = Lead._read_group(cerradas(a, b, [('fin_es_ganada', '=', True)]), [], ['__count', 'fin_importe_final:sum'])
            cantidad, importe = grupos[0] if grupos else (0, 0.0)
            total = Lead.search_count(cerradas(a, b, [('fin_cerrada', '=', True)]))
            return {'cantidad': cantidad, 'importe': importe or 0.0,
                    'conversion': round(cantidad / total * 100.0, 1) if total else None, 'cerradas': total}

        g_act, g_ant = ganadas(ini, fin), ganadas(ini_ant, fin_ant)

        etapas = self.env['crm.stage'].search([('fin_codigo', '!=', False), ('fin_cerrada', '=', False)], order='sequence')
        pipeline = []
        for etapa in etapas:
            grupos = Lead._read_group(base + [('stage_id', '=', etapa.id)], [], ['__count', 'fin_importe_solicitado:sum'])
            cantidad, importe = grupos[0] if grupos else (0, 0.0)
            pipeline.append({'etapa': etapa.name, 'cantidad': cantidad, 'importe': importe or 0.0, 'id': etapa.id})

        perdidas = [
            {'motivo': motivo.name if motivo else 'Sin motivo', 'cantidad': cantidad}
            for motivo, cantidad in Lead._read_group(
                cerradas(ini, fin, [('fin_es_perdida', '=', True)]), ['lost_reason_id'], ['__count'])
        ]
        perdidas.sort(key=lambda p: -p['cantidad'])

        vendedoras = self.env['res.users'].sudo().search([('fin_es_vendedora', '=', True)], order='fin_orden, id')
        if users:
            vendedoras = vendedoras & users
        ranking = []
        for v in vendedoras:
            uv = v
            t_act, _ = self._tiempo_promedio(uv, ini, fin)
            t_ant, _ = self._tiempo_promedio(uv, ini_ant, fin_ant)
            gan = Lead._read_group(cerradas(ini, fin, [('fin_es_ganada', '=', True), ('user_id', '=', v.id)]),
                                   [], ['__count', 'fin_importe_final:sum'])
            cant, imp = gan[0] if gan else (0, 0.0)
            tot = Lead.search_count(cerradas(ini, fin, [('fin_cerrada', '=', True), ('user_id', '=', v.id)]))
            ranking.append({
                'id': v.id, 'nombre': v.name,
                'nuevas': Lead.search_count(nuevas(ini, fin) + [('user_id', '=', v.id)]),
                'ganadas': cant, 'importe': imp or 0.0,
                'conversion': round(cant / tot * 100.0, 1) if tot else None,
                'tiempo_actual': t_act, 'tiempo_anterior': t_ant,
            })
        ranking.sort(key=lambda r: (-r['importe'], -r['ganadas']))

        return {
            'vistas': self._vistas(),
            'etiqueta': etiqueta, 'etiqueta_ant': etiqueta_ant,
            'es_admin': self.env.user.fin_es_admin,
            'vendedoras': [{'id': v.id, 'nombre': v.name} for v in self.env['res.users'].sudo().search(
                [('fin_es_vendedora', '=', True)], order='fin_orden, id')] if self.env.user.fin_es_admin else [],
            'nuevas': {'actual': n_act, 'anterior': n_ant, 'variacion': self._variacion(n_act, n_ant),
                       'dominio': nuevas(ini, fin)},
            'por_producto': [{'producto': p, 'nombre': n, 'actual': prod_act[p], 'anterior': prod_ant[p]}
                             for p, n in PRODUCTOS],
            'top_actual': top(prod_act), 'top_anterior': top(prod_ant),
            'ganadas': {'actual': g_act, 'anterior': g_ant,
                        'variacion': self._variacion(g_act['cantidad'], g_ant['cantidad']),
                        'variacion_importe': self._variacion(g_act['importe'], g_ant['importe']),
                        'dominio': cerradas(ini, fin, [('fin_es_ganada', '=', True)])},
            'pipeline': pipeline,
            'perdidas': perdidas,
            'perdidas_dominio': cerradas(ini, fin, [('fin_es_perdida', '=', True)]),
            'ranking': ranking if self.env.user.fin_es_admin else [],
            'tiempos': [{'nombre': r['nombre'], 'actual': r['tiempo_actual'], 'anterior': r['tiempo_anterior']}
                        for r in ranking],
            'dominio_base': base,
        }

    # ------------------------------------------------------------------ Objetivos
    @api.model
    def get_objetivos(self, mes=None, anio=None):
        hoy = fields.Date.context_today(self)
        mes, anio = int(mes or hoy.month), int(anio or hoy.year)
        users = self._usuario_filtro()
        Obj = self.env['financiera.objetivo'].sudo()
        dominio = [('mes', '=', str(mes)), ('anio', '=', anio), ('tipo_periodo', '=', 'mensual'), ('user_id', '!=', False)]
        if users:
            dominio.append(('user_id', 'in', users.ids))
        objetivos = Obj.search(dominio)
        horario = Horario(self.env)
        inicio_mes = hoy.replace(year=anio, month=mes, day=1)
        fin_mes = inicio_mes + relativedelta(months=1) - timedelta(days=1)
        dias_mes = horario.dias_habiles(inicio_mes, fin_mes) or 1
        if hoy < inicio_mes:
            transcurridos = 0
        else:
            transcurridos = horario.dias_habiles(inicio_mes, min(hoy, fin_mes))
        filas = []
        for vendedora in objetivos.mapped('user_id').sorted(lambda u: (u.fin_orden, u.id)):
            propios = objetivos.filtered(lambda o: o.user_id == vendedora)
            celdas = []
            for producto, nombre in PRODUCTOS:
                o = propios.filtered(lambda x: x.producto == producto)[:1]
                proyeccion = round(o.logrado_cantidad / transcurridos * dias_mes, 1) if o and transcurridos else 0.0
                celdas.append({
                    'producto': producto, 'nombre': nombre, 'id': o.id or False,
                    'meta': o.meta_cantidad if o else 0, 'logrado': o.logrado_cantidad if o else 0,
                    'pct': round(o.cumplimiento, 1) if o else 0.0, 'semaforo': o.semaforo if o else False,
                    'proyeccion': proyeccion,
                })
            glob = Obj._fin_global(propios)
            total_logrado = sum(propios.mapped('logrado_cantidad'))
            filas.append({
                'id': vendedora.id, 'nombre': vendedora.name, 'celdas': celdas,
                'promedio': glob['promedio'], 'unificado': glob['unificado'],
                'proyeccion_total': round(total_logrado / transcurridos * dias_mes, 1) if transcurridos else 0.0,
                'meta_total': sum(propios.mapped('meta_cantidad')),
            })
        return {
            'vistas': self._vistas(),
            'mes': mes, 'anio': anio, 'filas': filas,
            'productos': [{'producto': p, 'nombre': n} for p, n in PRODUCTOS],
            'tope': self.env['financiera.config']._get().tope_100_promedio,
            'dias_mes': dias_mes, 'transcurridos': transcurridos,
            'es_admin': self.env.user.fin_es_admin,
        }

    @api.model
    def fin_cambiar_tope(self, valor):
        if not self.env.user.fin_es_admin:
            raise AccessError('Solo el administrador puede cambiar este parámetro.')
        self.env['financiera.config']._get().tope_100_promedio = bool(valor)
        return True
