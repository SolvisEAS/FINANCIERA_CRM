"""Utilidades: teléfonos de Paraguay y cálculo en horario laboral.

Convención: los datetimes que entran y salen de estas funciones son *naive en UTC*
(como los guarda Odoo). Internamente se trabaja con datetimes aware.
"""
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


class ZonaHoraria(ZoneInfo):
    """ZoneInfo con `localize()` para leer igual que el resto del código."""

    def localize(self, dt):
        return dt.replace(tzinfo=self)

PRODUCTOS = [
    ('prestamo', 'Préstamo'),
    ('tarjeta', 'Tarjeta de crédito'),
    ('seguro', 'Seguro vehicular'),
]
PRODUCTOS_CON_SIN_DEFINIR = [('sin_definir', 'Sin definir')] + PRODUCTOS

ORIGENES = [
    ('meta', 'Meta Lead Ads'),
    ('whatsapp', 'WhatsApp'),
    ('importacion', 'Importación'),
    ('manual', 'Manual'),
    ('referido', 'Referido'),
]


def normalizar_telefono(numero):
    """Devuelve el número en E.164 de Paraguay (+595…) o False si no parece válido.

    '0981 123-456' -> '+595981123456'; '595981123456' -> '+595981123456'.
    """
    if not numero:
        return False
    digitos = re.sub(r'\D', '', str(numero))
    if not digitos:
        return False
    if digitos.startswith('00'):
        digitos = digitos[2:]
    if digitos.startswith('595'):
        nacional = digitos[3:]
    elif str(numero).strip().startswith('+'):
        return '+' + digitos  # número de otro país: se respeta
    else:
        nacional = digitos
    nacional = nacional.lstrip('0')
    if len(nacional) < 6:
        return False
    return '+595' + nacional


def float_a_hora(valor):
    horas = int(valor)
    minutos = int(round((valor - horas) * 60))
    if minutos == 60:
        horas, minutos = horas + 1, 0
    return time(min(horas, 23), minutos)


def a_aware(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def a_naive_utc(dt):
    return a_aware(dt).astimezone(timezone.utc).replace(tzinfo=None)


class Horario:
    """Envuelve el calendario laboral de la financiera."""

    def __init__(self, env):
        self.env = env
        self.config = env['financiera.config']._get()
        self.calendario = self.config._calendario()
        self.tz = ZonaHoraria(self.config.zona_horaria or 'America/Asuncion')
        self.hora_inicio = self.config.hora_inicio or 8.5

    # -- intervalos ---------------------------------------------------------
    def intervalos(self, inicio, fin):
        """Intervalos laborales (aware) entre dos datetimes, con feriados descontados.

        En Odoo 20 el calendario no tiene zona horaria propia: se calcula en la zona
        de los datetimes que recibe, por eso se convierten a la hora de Paraguay.
        """
        inicio, fin = a_aware(inicio).astimezone(self.tz), a_aware(fin).astimezone(self.tz)
        if fin <= inicio:
            return []
        res = self.calendario.sudo()._work_intervals_batch(inicio, fin, compute_leaves=True)[False]
        return sorted((a, b) for a, b, _rec in res)

    def es_habil(self, dt):
        dt = a_aware(dt)
        intervalos = self.intervalos(dt, dt + timedelta(minutes=1))
        return bool(intervalos) and intervalos[0][0] <= dt

    def inicio_habil(self, dt):
        """El propio dt si es horario laboral; si no, el próximo inicio de jornada.

        Si el próximo intervalo es el primero de su día, se respeta la hora de inicio
        de tareas (08:30 por defecto) para dar margen de preparación.
        """
        dt = a_aware(dt)
        for a, _b in self.intervalos(dt, dt + timedelta(days=30)):
            if a <= dt:
                return a_naive_utc(dt)
            local = a.astimezone(self.tz)
            primer_intervalo = self.intervalos(
                self.tz.localize(datetime.combine(local.date(), time(0, 0))), a)
            if not primer_intervalo:
                tope = self.tz.localize(datetime.combine(local.date(), float_a_hora(self.hora_inicio)))
                if tope > a:
                    a = tope
            return a_naive_utc(a)
        return a_naive_utc(dt)

    def sumar_horas(self, dt, horas):
        """Avanza `horas` hábiles desde dt."""
        dt = a_aware(dt)
        restante = timedelta(hours=horas)
        for a, b in self.intervalos(dt, dt + timedelta(days=60)):
            a = max(a, dt)
            if b - a >= restante:
                return a_naive_utc(a + restante)
            restante -= b - a
        return a_naive_utc(dt + timedelta(hours=horas))

    def horas_entre(self, inicio, fin):
        """Horas hábiles entre dos momentos; el reloj arranca en el inicio hábil."""
        if not inicio or not fin:
            return 0.0
        arranque = self.inicio_habil(inicio)
        total = sum(((b - a) for a, b in self.intervalos(arranque, fin)), timedelta())
        return round(total.total_seconds() / 3600.0, 2)

    def horas_por_dia(self):
        return self.calendario.hours_per_day or 8.0

    def dia_local(self, dt):
        return a_aware(dt).astimezone(self.tz).date()

    def inicio_dia_siguiente(self, dt):
        """Primera hora hábil del día hábil siguiente al de dt."""
        local = a_aware(dt).astimezone(self.tz)
        manana = self.tz.localize(datetime.combine(local.date() + timedelta(days=1), time(0, 0)))
        return self.inicio_habil(manana)

    def local(self, dt):
        return a_aware(dt).astimezone(self.tz)

    def dias_habiles(self, desde, hasta):
        """Cantidad de días con horario laboral entre dos fechas locales (inclusive)."""
        inicio = self.tz.localize(datetime.combine(desde, time(0, 0)))
        fin = self.tz.localize(datetime.combine(hasta + timedelta(days=1), time(0, 0)))
        return len({a.astimezone(self.tz).date() for a, _b in self.intervalos(inicio, fin)})
