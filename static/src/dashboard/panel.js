import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
    "Septiembre", "Octubre", "Noviembre", "Diciembre"];

const formatoGs = new Intl.NumberFormat("es-PY", { maximumFractionDigits: 0 });

export class FinancieraPanel extends Component {
    static template = "financiera_crm.Panel";

    props = useProps(standardActionServiceProps);

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.actionService = useService("action");
        const hoy = new Date();
        this.meses = MESES;
        this.state = proxy({
            cargando: true,
            datos: null,
            periodo: "esta_semana",
            userId: "",
            desde: "",
            hasta: "",
            mes: hoy.getMonth() + 1,
            anio: hoy.getFullYear(),
        });
        onWillStart(() => this.cargar());
    }

    get panel() {
        return this.props.action.params?.panel || "mi_dia";
    }

    get titulo() {
        return { mi_dia: "Mi día", semana: "Esta semana vs. semana pasada", objetivos: "Objetivos" }[this.panel];
    }

    async cargar() {
        this.state.cargando = true;
        let datos;
        if (this.panel === "semana") {
            datos = await this.orm.call("financiera.dashboard", "get_semana", [], {
                periodo: this.state.periodo,
                user_id: this.state.userId ? parseInt(this.state.userId) : false,
                desde: this.state.desde || false,
                hasta: this.state.hasta || false,
            });
        } else if (this.panel === "objetivos") {
            datos = await this.orm.call("financiera.dashboard", "get_objetivos", [], {
                mes: this.state.mes,
                anio: this.state.anio,
            });
        } else {
            datos = await this.orm.call("financiera.dashboard", "get_mi_dia", []);
        }
        this.state.datos = datos;
        this.state.cargando = false;
    }

    // ---------------------------------------------------------------- formato
    gs(valor) {
        return "₲ " + formatoGs.format(Math.round(valor || 0));
    }

    gsCorto(valor) {
        valor = valor || 0;
        if (valor >= 1e9) {
            return "₲ " + (valor / 1e9).toFixed(1).replace(".", ",") + " mil M";
        }
        if (valor >= 1e6) {
            return "₲ " + (valor / 1e6).toFixed(1).replace(".", ",") + " M";
        }
        return this.gs(valor);
    }

    pct(valor) {
        return valor === null || valor === undefined ? "—" : `${String(valor).replace(".", ",")} %`;
    }

    horas(valor) {
        const h = Math.floor(valor || 0);
        const m = Math.round(((valor || 0) - h) * 60);
        return m ? `${h} h ${m} min` : `${h} h`;
    }

    flecha(variacion) {
        if (variacion === null || variacion === undefined) {
            return "";
        }
        return variacion > 0 ? "↑" : variacion < 0 ? "↓" : "→";
    }

    claseVariacion(variacion, invertir = false) {
        if (!variacion) {
            return "o_fin_neutro";
        }
        const bueno = invertir ? variacion < 0 : variacion > 0;
        return bueno ? "o_fin_sube" : "o_fin_baja";
    }

    ancho(valor, maximo) {
        if (!maximo) {
            return "width: 0%";
        }
        return `width: ${Math.min(100, Math.round((valor / maximo) * 100))}%`;
    }

    maximo(lista, ...campos) {
        let max = 0;
        for (const item of lista || []) {
            for (const campo of campos) {
                max = Math.max(max, item[campo] || 0);
            }
        }
        return max;
    }

    semaforoPct(valor) {
        return valor >= 100 ? "verde" : valor >= 70 ? "amarillo" : "rojo";
    }

    // ---------------------------------------------------------------- navegación
    abrirOportunidades(dominio, nombre) {
        const v = this.state.datos.vistas;
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: nombre,
            res_model: "crm.lead",
            domain: dominio,
            views: [[v.lead_list, "list"], [v.lead_kanban, "kanban"], [v.lead_form, "form"]],
            search_view_id: [v.lead_search],
            context: { create: false },
        });
    }

    abrirTareas(dominio, nombre) {
        const v = this.state.datos.vistas;
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: nombre,
            res_model: "mail.activity",
            domain: dominio,
            views: [[v.tarea_list, "list"], [v.tarea_form, "form"]],
            search_view_id: [v.tarea_search],
            context: { create: false },
        });
    }

    abrirOportunidad(id) {
        const v = this.state.datos.vistas;
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "crm.lead",
            res_id: id,
            views: [[v.lead_form, "form"]],
        });
    }

    async concluir(actividadId) {
        const accion = await this.orm.call("mail.activity", "action_fin_concluir", [[actividadId]]);
        await this.actionService.doAction(accion, { onClose: () => this.cargar() });
    }

    // ---------------------------------------------------------------- filtros
    cambiarPeriodo(ev) {
        this.state.periodo = ev.target.value;
        if (this.state.periodo !== "rango" || (this.state.desde && this.state.hasta)) {
            this.cargar();
        }
    }

    cambiarVendedora(ev) {
        this.state.userId = ev.target.value;
        this.cargar();
    }

    cambiarFecha(campo, ev) {
        this.state[campo] = ev.target.value;
        if (this.state.desde && this.state.hasta) {
            this.cargar();
        }
    }

    moverMes(delta) {
        let mes = this.state.mes + delta;
        let anio = this.state.anio;
        if (mes < 1) {
            mes = 12;
            anio -= 1;
        } else if (mes > 12) {
            mes = 1;
            anio += 1;
        }
        this.state.mes = mes;
        this.state.anio = anio;
        this.cargar();
    }

    async cambiarTope(ev) {
        await this.orm.call("financiera.dashboard", "fin_cambiar_tope", [ev.target.checked]);
        this.cargar();
    }

    abrirCargaObjetivos() {
        this.actionService.doAction("financiera_crm.action_objetivos");
    }
}

registry.category("actions").add("financiera_panel", FinancieraPanel);
