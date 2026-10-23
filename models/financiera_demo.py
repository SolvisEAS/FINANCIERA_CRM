"""Datos de demo realistas (Paraguay). Todo queda marcado con fin_demo para borrarlo con un clic."""
import random
from datetime import datetime, time, timedelta

from odoo import api, fields, models

from .utils import Horario, a_naive_utc

NOMBRES = ['María', 'José', 'Ana', 'Carlos', 'Rosa', 'Juan', 'Laura', 'Luis', 'Carmen', 'Pedro', 'Mirian',
           'Ramón', 'Gloria', 'Óscar', 'Patricia', 'Derlis', 'Fátima', 'Néstor', 'Silvia', 'Hugo', 'Liz', 'Alcides',
           'Noelia', 'Rubén', 'Andrea', 'Víctor', 'Romina', 'Celso', 'Belén', 'Arnaldo', 'Natalia', 'Édgar',
           'Graciela', 'Blas', 'Viviana', 'Cristian', 'Nilda', 'Aníbal', 'Leticia', 'Fabio', 'Lourdes', 'Gustavo',
           'Sandra', 'Teodoro', 'Raquel', 'Mauricio', 'Perla', 'Wilfrido']
APELLIDOS = ['González', 'Benítez', 'Martínez', 'López', 'Giménez', 'Vera', 'Duarte', 'Ramírez', 'Báez', 'Rojas',
             'Acosta', 'Ortiz', 'Villalba', 'Cáceres', 'Fernández', 'Ayala', 'Riveros', 'Insfrán', 'Cabrera',
             'Galeano', 'Ledesma', 'Sanabria', 'Franco', 'Aquino', 'Cardozo', 'Ojeda', 'Sosa', 'Espínola']
CIUDADES = ['Asunción', 'San Lorenzo', 'Luque', 'Lambaré', 'Fernando de la Mora', 'Capiatá', 'Limpio',
            'Mariano Roque Alonso', 'Ñemby', 'Encarnación', 'Ciudad del Este', 'Villa Elisa']
VEHICULOS = ['Toyota Corolla 2019', 'Kia Picanto 2021', 'Hyundai HB20 2020', 'Chevrolet Onix 2022',
             'Volkswagen Gol 2018', 'Nissan Kicks 2023', 'Toyota Hilux 2020']
ORIGENES = ['meta', 'meta', 'meta', 'whatsapp', 'whatsapp', 'importacion', 'manual', 'referido']
CAMPANAS = ['Préstamos rápidos · Octubre', 'Tarjeta sin anualidad · Formulario 2', 'Seguro auto · Video 15s']
MENSAJES_CLIENTE = ['Hola, quiero saber los requisitos', '¿Cuánto es la tasa para 12 meses?',
                    'Les mando mi cédula por acá', '¿Puedo ir mañana a la oficina?', 'Gracias, lo voy a pensar']
MENSAJES_VENDEDORA = ['¡Hola! Con gusto te ayudo 😊', 'Te paso los requisitos: CI, último recibo y factura de ANDE',
                      'La tasa depende del plazo, ¿qué monto necesitás?', 'Perfecto, te espero a las 10:00']


class FinancieraDemo(models.AbstractModel):
    _name = 'financiera.demo'
    _description = 'Generador de datos de demo'

    @api.model
    def borrar(self):
        Lead = self.env['crm.lead'].sudo().with_context(active_test=False, fin_permitir_borrar=True)
        leads = Lead.search([('fin_demo', '=', True)])
        leads.activity_ids.with_context(fin_permitir_borrar=True).unlink()
        leads.unlink()
        self.env['financiera.objetivo'].sudo().search([('fin_demo', '=', True)]).unlink()
        partners = self.env['res.partner'].sudo().with_context(active_test=False).search([('fin_demo', '=', True)])
        partners.unlink()
        return True

    @api.model
    def cargar(self):
        self.borrar()
        rnd = random.Random(20261022)
        vendedoras = self.env['res.users'].sudo().search([('fin_es_vendedora', '=', True)], order='fin_orden, id')
        if len(vendedoras) < 2:
            return False
        v1, v2 = vendedoras[0], vendedoras[1]
        horario = Horario(self.env)
        tz = horario.tz
        ahora = fields.Datetime.now()
        hoy = horario.dia_local(ahora)
        etapas = {s.fin_codigo: s for s in self.env['crm.stage'].search([('fin_codigo', '!=', False)])}
        motivos = self.env['crm.lost.reason'].search([])
        Lead = self.env['crm.lead'].sudo().with_context(
            fin_sin_automatismos=True, fin_permitir_duplicado=True, mail_create_nolog=True, tracking_disable=True,
            mail_notrack=True)
        Act = self.env['mail.activity'].sudo().with_context(mail_activity_quick_update=True, fin_sistema=True)
        modelo_lead = self.env['ir.model']._get_id('crm.lead')
        tipo_pc = self.env.ref('financiera_crm.actividad_primer_contacto')
        tipo_seg = self.env.ref('financiera_crm.actividad_seguimiento')
        tipo_doc = self.env.ref('financiera_crm.actividad_documentacion')

        def momento_habil(dias_atras):
            dia = hoy - timedelta(days=dias_atras)
            while dia.weekday() >= 5:
                dia -= timedelta(days=1)
            hora = rnd.choice([8.75, 9.5, 10.25, 11.0, 14.5, 15.25, 16.0, 17.25])
            local = tz.localize(datetime.combine(dia, time(int(hora), int((hora % 1) * 60))))
            return min(a_naive_utc(local), ahora - timedelta(minutes=30))

        # (etapa, vendedora, producto, días atrás) — 48 oportunidades
        plan = []
        plan += [('00', v, p, d) for v, p, d in [(v1, 'prestamo', 0), (v2, 'tarjeta', 0), (v1, 'seguro', 1),
                                                   (v2, 'sin_definir', 0), (v1, 'tarjeta', 2)]]
        plan += [('10', v, p, d) for v, p, d in [(v1, 'prestamo', 3), (v2, 'tarjeta', 4), (v1, 'tarjeta', 2),
                                                   (v2, 'seguro', 6), (v1, 'prestamo', 8), (v2, 'prestamo', 9),
                                                   (v1, 'seguro', 5)]]
        plan += [('20', v, p, d) for v, p, d in [(v1, 'tarjeta', 7), (v2, 'prestamo', 10), (v1, 'seguro', 12),
                                                   (v2, 'tarjeta', 11), (v1, 'prestamo', 13)]]
        plan += [('30', v, p, d) for v, p, d in [(v2, 'prestamo', 14), (v1, 'tarjeta', 15), (v2, 'seguro', 16),
                                                   (v1, 'prestamo', 17)]]
        ganadas = [(v1, 'tarjeta')] * 11 + [(v1, 'prestamo')] * 3 + [(v1, 'seguro')] * 1 + \
                  [(v2, 'tarjeta')] * 2 + [(v2, 'prestamo')] * 2 + [(v2, 'seguro')] * 1
        dias_mes = max((hoy - hoy.replace(day=1)).days, 1)
        plan += [('40', v, p, rnd.randint(1, min(dias_mes, 20))) for v, p in ganadas]
        plan += [('50', v, p, d) for v, p, d in [(v2, 'prestamo', 6), (v1, 'tarjeta', 9), (v2, 'seguro', 12),
                                                   (v1, 'prestamo', 15), (v2, 'tarjeta', 18), (v1, 'seguro', 19),
                                                   (v2, 'prestamo', 4)]]

        nombres_usados = set()
        creadas = self.env['crm.lead']
        for indice, (codigo, vendedora, producto, dias) in enumerate(plan):
            while True:
                nombre = f'{rnd.choice(NOMBRES)} {rnd.choice(APELLIDOS)} {rnd.choice(APELLIDOS)}'
                if nombre not in nombres_usados:
                    nombres_usados.add(nombre)
                    break
            telefono = f'09{rnd.choice("6789")}{rnd.choice("12345")}{rnd.randint(100000, 999999)}'
            origen = 'whatsapp' if producto == 'sin_definir' else rnd.choice(ORIGENES)
            partner = self.env['res.partner'].sudo().create({
                'name': nombre, 'phone': telefono, 'fin_es_cliente': True, 'fin_demo': True,
                'email': f'{nombre.split()[0].lower()}.{nombre.split()[1].lower()}@ejemplo.com.py'
                         .replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
                         .replace('ñ', 'n'),
                'city': rnd.choice(CIUDADES), 'fin_ci': str(rnd.randint(1500000, 6900000)),
                'fin_origen': origen,
            })
            creado = momento_habil(dias)
            fuera_horario = codigo == '00' and indice == 4
            if fuera_horario:  # entró el viernes pasado a las 18:10
                viernes = hoy - timedelta(days=(hoy.weekday() - 4) % 7 or 7)
                creado = a_naive_utc(tz.localize(datetime.combine(viernes, time(18, 10))))
            importe = {'prestamo': rnd.choice([5, 8, 10, 15, 20, 30]) * 1_000_000,
                       'tarjeta': rnd.choice([3, 5, 8, 10]) * 1_000_000,
                       'seguro': rnd.choice([2, 3, 4]) * 1_000_000,
                       'sin_definir': 0}[producto]
            vals = {
                'name': nombre, 'partner_id': partner.id, 'user_id': vendedora.id,
                'stage_id': etapas[codigo].id, 'fin_producto': producto, 'fin_origen': origen,
                'fin_importe_solicitado': importe, 'fin_demo': True,
                'fin_asignado_por': 'Rotación automática',
                'fin_situacion_laboral': rnd.choice(['dependiente', 'dependiente', 'independiente', 'jubilado']),
                'fin_ingreso_mensual': rnd.choice([3, 4, 5, 7, 10]) * 1_000_000,
            }
            if origen == 'meta':
                vals['fin_campana'] = rnd.choice(CAMPANAS)
            if producto == 'prestamo':
                vals['fin_plazo_meses'] = rnd.choice([6, 12, 18, 24, 36])
            if producto == 'seguro':
                vals['fin_vehiculo'] = rnd.choice(VEHICULOS)
            if codigo == '40':
                vals['fin_importe_final'] = importe * rnd.choice([0.8, 1.0, 1.0, 1.2])
            if codigo == '50':
                vals['lost_reason_id'] = rnd.choice(motivos).id if motivos else False
            lead = Lead.create(vals)
            contacto = None
            if codigo != '00':
                contacto = horario.sumar_horas(horario.inicio_habil(creado), rnd.choice([0.25, 0.5, 0.75, 1.0, 1.5, 3.0, 6.0]))
                contacto = min(contacto, ahora - timedelta(minutes=5))
                horas = horario.horas_entre(creado, contacto)
                lead.write({'fin_fecha_primer_contacto': contacto, 'fin_horas_primer_contacto': horas,
                            'fin_dias_primer_contacto': round(horas / horario.horas_por_dia(), 2)})
            cierre = None
            if codigo in ('40', '50'):
                cierre = min(horario.sumar_horas(contacto, rnd.choice([4, 8, 12, 16])), ahora - timedelta(minutes=1))
                if codigo == '40' and cierre.date() < hoy.replace(day=1):
                    cierre = a_naive_utc(tz.localize(datetime.combine(hoy.replace(day=1), time(10, 0))))
            self.env.cr.execute(
                'UPDATE crm_lead SET create_date = %s, date_open = %s, date_closed = %s, '
                'date_last_stage_update = %s WHERE id = %s',
                [creado, creado, cierre, cierre or contacto or creado, lead.id])
            creadas |= lead

            # Tareas abiertas
            if codigo == '00':
                if fuera_horario:
                    vence = horario.inicio_habil(creado)
                elif dias == 0:
                    vence = horario.sumar_horas(creado, 1.0)
                else:
                    vence = horario.sumar_horas(horario.inicio_habil(creado), 1.0)
                Act.create({'res_model_id': modelo_lead, 'res_id': lead.id, 'activity_type_id': tipo_pc.id,
                            'summary': f'{nombre} — Primer contacto', 'user_id': vendedora.id,
                            'fin_fecha_hora': vence, 'fin_tipo': 'primer_contacto',
                            'fin_modalidad': 'whatsapp' if origen == 'whatsapp' else 'llamada'})
            elif codigo in ('10', '20', '30'):
                opcion = indice % 3
                if opcion == 0:  # vencida (ayer)
                    base = horario.inicio_habil(a_naive_utc(tz.localize(datetime.combine(hoy - timedelta(days=1), time(0)))))
                    if horario.dia_local(base) >= hoy:
                        base = a_naive_utc(tz.localize(datetime.combine(hoy - timedelta(days=3), time(10, 0))))
                    vence = base + timedelta(hours=rnd.choice([1, 2, 6]))
                elif opcion == 1:  # hoy
                    vence = a_naive_utc(tz.localize(datetime.combine(hoy, time(rnd.choice([9, 10, 11, 15, 16, 17]), 0))))
                    vence = horario.inicio_habil(vence)
                else:  # futura
                    vence = self.env['mail.activity']._fin_escalonar(vendedora, horario.inicio_dia_siguiente(ahora))
                tipo = tipo_doc if codigo == '30' else tipo_seg
                Act.create({'res_model_id': modelo_lead, 'res_id': lead.id, 'activity_type_id': tipo.id,
                            'summary': f'{nombre} — {"Documentación" if codigo == "30" else "Seguimiento"}',
                            'user_id': vendedora.id, 'fin_fecha_hora': vence,
                            'fin_tipo': 'documentacion' if codigo == '30' else 'seguimiento',
                            'fin_modalidad': rnd.choice(['llamada', 'whatsapp'])})
                if codigo == '10' and indice % 2:
                    lead.fin_intentos = rnd.randint(1, 3)

        # Resúmenes de WhatsApp en algunas oportunidades
        Resumen = self.env['financiera.whatsapp.resumen'].sudo()
        for lead in creadas.filtered(lambda l: l.fin_origen == 'whatsapp')[:10]:
            dia = horario.dia_local(lead.create_date)
            lineas = []
            hora = 9
            for _i in range(rnd.randint(2, 5)):
                lineas.append(f'{hora:02d}:{rnd.randint(0, 59):02d} {lead.partner_id.name}: {rnd.choice(MENSAJES_CLIENTE)}')
                lineas.append(f'{hora:02d}:{rnd.randint(0, 59):02d} {lead.user_id.name}: {rnd.choice(MENSAJES_VENDEDORA)}')
                hora += 1
            resumen = Resumen.create({'lead_id': lead.id, 'fecha': dia, 'mensajes': len(lineas),
                                      'entrantes': len(lineas) // 2, 'salientes': len(lineas) // 2,
                                      'contenido': '\n'.join(lineas) + '\n', 'fin_demo': True})
            resumen._publicar()
        cliente_escribio = creadas.filtered(lambda l: l.stage_id.fin_codigo == '00' and l.fin_origen != 'whatsapp')[:1]
        if cliente_escribio:
            cliente_escribio.fin_cliente_escribio = True

        # Objetivos del mes actual: 10/10/10 para cada vendedora
        Obj = self.env['financiera.objetivo'].sudo()
        for vendedora in (v1, v2):
            for producto in ('prestamo', 'tarjeta', 'seguro'):
                Obj.create({'user_id': vendedora.id, 'producto': producto, 'mes': str(hoy.month), 'anio': hoy.year,
                            'meta_cantidad': 10, 'meta_importe': 10 * 8_000_000, 'fin_demo': True})

        # Estado de WhatsApp por vendedora
        v1.write({'fin_wa_numero': '+595981100200', 'fin_wa_estado': 'conectado', 'fin_wa_ultimo': ahora - timedelta(minutes=7)})
        v2.write({'fin_wa_numero': '+595982300400', 'fin_wa_estado': 'desconectado', 'fin_wa_ultimo': ahora - timedelta(days=1, hours=3)})
        self.env['crm.lead'].invalidate_model()
        return True
