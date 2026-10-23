import json
import logging

_logger = logging.getLogger(__name__)

PARAM_MENUS = 'financiera_crm.menus_originales'


def post_init_hook(env):
    config = env.ref('financiera_crm.config_principal')
    calendario = env.ref('financiera_crm.calendario_financiera')
    company = env.company

    # Compañía: guaraníes, horario de la financiera, zona horaria de Asunción
    pyg = env.ref('base.PYG')
    pyg.active = True
    company.write({
        'name': 'Financiera Demo S.A.',
        'currency_id': pyg.id,
        'resource_calendar_id': calendario.id,
    })
    company.partner_id.write({'tz': 'America/Asuncion', 'city': 'Asunción', 'country_id': env.ref('base.py').id})
    config.write({'calendario_id': calendario.id, 'equipo_id': env.ref('financiera_crm.equipo_financiera').id})
    config.action_generar_tokens()

    # Etapas y motivos nativos de Odoo que no se usan
    etapas_viejas = env['crm.stage'].search([('fin_codigo', '=', False)])
    if not env['crm.lead'].with_context(active_test=False).search_count([('stage_id', 'in', etapas_viejas.ids)]):
        etapas_viejas.unlink()
    env['crm.lost.reason'].search([('id', 'not in', [
        env.ref(f'financiera_crm.motivo_{m}').id for m in
        ('no_califica', 'no_responde', 'otra_entidad', 'tasa', 'desistio', 'datos_invalidos')])]).active = False

    # Usuarios de demo: Administrador + 2 vendedoras (las contraseñas se ponen al desplegar)
    admin = env.ref('base.user_admin')
    admin.write({'name': 'Administrador', 'tz': 'America/Asuncion', 'lang': 'es_PY'})
    grupo_vendedora = env.ref('financiera_crm.group_vendedora')
    Users = env['res.users'].with_context(no_reset_password=True)
    vendedoras = env['res.users']
    for login, nombre, orden in (('vendedora1', 'Vendedora 1', 10), ('vendedora2', 'Vendedora 2', 20)):
        user = Users.search([('login', '=', login)], limit=1)
        if not user:
            user = Users.create({
                'login': login, 'name': nombre, 'email': f'{login}@financiera.demo',
                'group_ids': [(6, 0, [grupo_vendedora.id])], 'tz': 'America/Asuncion', 'lang': 'es_PY',
                'fin_orden': orden,
            })
        vendedoras |= user
    env.ref('financiera_crm.equipo_financiera').write({'member_ids': [(6, 0, vendedoras.ids)], 'user_id': admin.id})
    accion_inicio = env.ref('financiera_crm.action_mi_dia')
    (admin | vendedoras).write({'action_id': accion_inicio.id})

    _ocultar_menus(env)
    env['financiera.demo'].cargar()


def _ocultar_menus(env):
    """Deja visible solo el CRM de la financiera (y Ajustes para el admin)."""
    raiz = env.ref('financiera_crm.menu_raiz')
    ajustes = env.ref('base.menu_administration')
    oculto = env.ref('financiera_crm.group_menus_odoo')
    originales = {}
    for menu in env['ir.ui.menu'].with_context(active_test=False).search([('parent_id', '=', False)]):
        if menu in (raiz | ajustes):
            continue
        originales[str(menu.id)] = menu.group_ids.ids
        menu.group_ids = [(6, 0, [oculto.id])]
    env['ir.config_parameter'].sudo().set_str(PARAM_MENUS, json.dumps(originales))


def uninstall_hook(env):
    originales = json.loads(env['ir.config_parameter'].sudo().get_str(PARAM_MENUS) or '{}')
    for menu_id, grupos in originales.items():
        menu = env['ir.ui.menu'].browse(int(menu_id)).exists()
        if menu:
            menu.group_ids = [(6, 0, env['res.groups'].browse(grupos).exists().ids)]
    env['ir.config_parameter'].sudo().set_str(PARAM_MENUS, '')
