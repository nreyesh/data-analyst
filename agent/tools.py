"""Deterministic RBAC-enforced tool wrappers for the Conversational Supervisor Agent."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from agent.state import UserContext
from analytics.tools.building_tools import get_building_summary, get_system_diagnostics
from analytics.tools.unit_tools import get_unit_bill_details
from analytics.workflow import run_analytics_workflow

logger = logging.getLogger("agent.tools")


def _format_clp(amount: int | float | None) -> str:
    """Format integer CLP amount using Chilean dot thousand separators (e.g. $145.320 CLP)."""
    if amount is None:
        return "$0 CLP"
    val = int(round(amount))
    formatted = f"{abs(val):,}".replace(",", ".")
    sign = "-" if val < 0 else ""
    return f"{sign}${formatted} CLP"


def _normalize_unit(unit_code: str | None) -> str | None:
    """Normalize apartment code into 'TOWER-NUMBER' format when possible."""
    if not unit_code:
        return None
    raw = unit_code.strip().upper()
    m1 = re.match(r"^([A-Z]{1,2})\s*[-]\s*(\d{1,4})$", raw)
    if m1:
        return f"{m1.group(1)}-{int(m1.group(2))}"
    m2 = re.match(r"^(\d{1,4})\s*[-]?\s*([A-Z]{1,2})$", raw)
    if m2:
        return f"{m2.group(2)}-{int(m2.group(1))}"
    return raw


def _detect_unauthorized_unit_in_text(query: str, assigned_unit: str) -> str | None:
    """Inspect query text for explicit references to an apartment unit other than assigned_unit."""
    norm_assigned = _normalize_unit(assigned_unit) or assigned_unit.upper()
    assigned_parts = norm_assigned.split("-")
    assigned_tower = assigned_parts[0] if len(assigned_parts) == 2 else ""
    assigned_num = assigned_parts[1] if len(assigned_parts) == 2 else norm_assigned

    # 1. Check explicit TOWER-NUM or NUM-TOWER patterns (e.g. A-102, 102B, A-504)
    for m in re.finditer(r"\b([A-Za-z]{1,2})\s*[-]\s*(\d{1,4})\b", query):
        candidate = f"{m.group(1).upper()}-{int(m.group(2))}"
        if candidate != norm_assigned:
            return candidate

    for m in re.finditer(r"\b(\d{1,4})\s*([A-Za-z]{1,2})\b", query):
        # Ignore common measurements or years like 2026, m3
        word_after = m.group(2).upper()
        if word_after in ("M", "K", "B") and int(m.group(1)) > 1900:
            continue
        candidate = f"{word_after}-{int(m.group(1))}"
        if candidate != norm_assigned:
            return candidate

    # 2. Check explicit 'depto/departamento/unidad <num>'
    for m in re.finditer(r"\b(?:depto|departamento|dpto|unidad)\s*[-#]?\s*(\d{1,4})\s*([A-Za-z])?\b", query, re.I):
        num = str(int(m.group(1)))
        tower = (m.group(2) or "").upper()
        if tower:
            candidate = f"{tower}-{num}"
            if candidate != norm_assigned:
                return candidate
        elif num != assigned_num:
            return f"Depto {num}"

    # 3. Check explicit 'torre <X>' if resident is in another tower
    for m in re.finditer(r"\btorre\s+([A-Za-z])\b", query, re.I):
        req_tower = m.group(1).upper()
        if assigned_tower and req_tower != assigned_tower:
            return f"Torre {req_tower}"

    return None


def _is_cross_unit_cohort_query(query: str) -> bool:
    """Detect if a resident query asks for multi-unit rankings or neighbor comparisons."""
    return bool(
        re.search(
            r"\b(departamentos\s+con\s+mayor|departamentos\s+en\s+mora|ranking\s+de\s+departamentos|"
            r"todos\s+los\s+departamentos|otros\s+departamentos|vecinos)\b",
            query,
            re.I,
        )
    )


def lookup_unit_bill(
    user_context: UserContext,
    unit_code: str | None = None,
    period_date: str | None = None,
    query_text: str = "",
    client: Any | None = None,
) -> dict[str, Any]:
    """Fast-path apartment unit bill snapshot lookup with deterministic RBAC enforcement."""
    norm_requested = _normalize_unit(unit_code)

    if user_context.role == "resident":
        assigned_unit = _normalize_unit(user_context.unit_code) or ""
        if norm_requested and norm_requested != assigned_unit:
            # Allow bare unit number if it matches the resident's unit number (e.g. "504" for "B-504")
            assigned_num = assigned_unit.split("-")[-1]
            if norm_requested != assigned_num:
                logger.warning(
                    "RBAC block: Resident '%s' (%s) attempted to query unit '%s'",
                    user_context.email,
                    assigned_unit,
                    norm_requested,
                )
                msg = (
                    f"Acceso denegado: Tu perfil de residente solo está autorizado para "
                    f"consultar la unidad **{assigned_unit}**."
                )
                return {"error": msg, "unauthorized": True, "summary": msg}

        unauth_in_text = _detect_unauthorized_unit_in_text(query_text, assigned_unit)
        if unauth_in_text:
            logger.warning(
                "RBAC block: Resident '%s' (%s) referenced unauthorized unit '%s' in query",
                user_context.email,
                assigned_unit,
                unauth_in_text,
            )
            msg = (
                f"Acceso denegado: Tu perfil de residente solo está autorizado para "
                f"consultar la unidad **{assigned_unit}** (no puedes consultar `{unauth_in_text}`)."
            )
            return {"error": msg, "unauthorized": True, "summary": msg}

        effective_unit = assigned_unit
    else:
        effective_unit = norm_requested or ""
        if not effective_unit:
            msg = "Por favor indica el código del departamento que deseas consultar (por ejemplo, `B-504`)."
            return {"error": msg, "needs_unit": True, "summary": msg}

    data = get_unit_bill_details(unit_code=effective_unit, period_date=period_date, client=client)
    if "error" in data:
        return {
            "error": data["error"],
            "summary": f"No se encontró información de cobro para la unidad **{effective_unit}**: {data['error']}",
            "data": data,
        }

    period_str = data.get("period_date", "último periodo")
    total_month = _format_clp(data.get("calculated_total_month"))
    dept_sub = _format_clp(data.get("calculated_dept_subtotal"))
    consumo_sub = _format_clp(data.get("calculated_consumo_total"))
    charges_sub = _format_clp(data.get("calculated_unit_subtotal"))
    alicuota = data.get("alicuota_total", 0)
    last_pay_date = data.get("last_payment_date") or "Sin registro"
    last_pay_amt = _format_clp(data.get("last_payment_amount")) if data.get("last_payment_amount") else "N/A"

    lines = [
        f"### Resumen de Nota de Cobro — Unidad **{data.get('unit_code', effective_unit)}** (`{period_str}`)",
        f"- **Total Mes**: **{total_month}** (Alícuota: `{alicuota}%`)",
        f"- **Subtotal Depto (Cuota Base + Fondos)**: {dept_sub}",
        f"- **Agua Caliente**: {consumo_sub}",
        f"- **Cargos Particulares (Multas y otros)**: {charges_sub}",
        f"- **Último Pago Registrado**: {last_pay_amt} (Fecha: `{last_pay_date}`)",
    ]

    concepts = data.get("concepts", [])
    if concepts:
        lines.append("\n**Desglose Subtotal Depto:**")
        for c in concepts:
            c_name = c.get("concept_name") or c.get("concept") or "Concepto"
            c_detail = f" ({c.get('detail')})" if c.get("detail") else ""
            lines.append(f"- {c_name}{c_detail}: {_format_clp(c.get('amount'))}")

    consumptions = data.get("consumptions", [])
    if consumptions:
        lines.append("\n**Consumos Individuales:**")
        for cons in consumptions:
            cons_total = cons.get("total_amount") if cons.get("total_amount") is not None else cons.get("total")
            lines.append(
                f"- {cons.get('item_name')}: `{cons.get('consumption')} m³` "
                f"(Lectura `{cons.get('previous_reading')}` → `{cons.get('current_reading')}`, "
                f"Tarifa `{_format_clp(cons.get('unit_rate'))}/m³`) = **{_format_clp(cons_total)}**"
            )

    unit_charges = data.get("unit_charges", [])
    if unit_charges:
        lines.append("\n**Cargos Particulares de la Unidad:**")
        for ch in unit_charges:
            ch_name = ch.get("charge_name") or ch.get("name") or "Cargo"
            lines.append(f"- {ch_name}: {_format_clp(ch.get('amount'))}")

    step_detail = (
        f"GET /api/v1/unit-bills?unit_code={effective_unit}"
        + (f"&period_date={period_date}" if period_date else "")
        if user_context.role == "admin"
        else f"Información verificada en los registros oficiales de la unidad {effective_unit} ({period_str})."
    )

    return {
        "summary": "\n".join(lines),
        "data": data,
        "unit_code": data.get("unit_code", effective_unit),
        "period_date": data.get("period_date"),
        "step_name": f"Estado de cuenta verificado ({effective_unit})",
        "step_detail": step_detail,
    }


def lookup_building_summary(
    user_context: UserContext,
    period_date: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Fast-path macro building expense report lookup (permitted for both admin and resident)."""
    _ = user_context  # Macro building totals are public to all co-owners under Ley 21.442
    data = get_building_summary(period_date=period_date, client=client)
    if "error" in data:
        return {
            "error": data["error"],
            "summary": f"No se encontró el informe general del edificio: {data['error']}",
            "data": data,
        }

    period_str = data.get("period_date", "último periodo")
    op_total = _format_clp(data.get("operational_total"))
    fondos_total = _format_clp(data.get("fondos_total"))
    grand_total = _format_clp(data.get("calculated_grand_total"))

    lines = [
        f"### Resumen de Gastos Comunes del Edificio (`{period_str}`)",
        f"- **Total Gasto Operacional**: **{op_total}**",
        f"- **Total Recaudación de Fondos**: **{fondos_total}**",
        f"- **Total General del Mes**: **{grand_total}**",
    ]

    sections = data.get("sections", [])
    if sections:
        lines.append("\n**Subtotales por Categoría:**")
        for sec in sections:
            lines.append(
                f"- {sec.get('category_name')} (`{sec.get('expense_type')}`): "
                f"**{_format_clp(sec.get('calculated_subtotal'))}**"
            )

    step_detail = (
        f"GET /api/v1/reports" + (f"/period/{period_date}" if period_date else " (latest)")
        if user_context.role == "admin"
        else f"Información verificada en el informe general del edificio ({period_str})."
    )

    return {
        "summary": "\n".join(lines),
        "data": data,
        "period_date": data.get("period_date"),
        "step_name": "Resumen del edificio verificado",
        "step_detail": step_detail,
    }


def check_system_status(
    user_context: UserContext,
    client: Any | None = None,
) -> dict[str, Any]:
    """Admin-only system diagnostics and spool queue verification."""
    if user_context.role != "admin":
        logger.warning(
            "RBAC block: Resident '%s' attempted to invoke admin tool check_system_status",
            user_context.email,
        )
        msg = "Acceso denegado: El diagnóstico del sistema y estado de sincronización es exclusivo para la Administración."
        return {"error": msg, "unauthorized": True, "summary": msg}

    data = get_system_diagnostics(client=client)
    if "error" in data:
        return {
            "error": data["error"],
            "summary": f"Error al consultar el estado del sistema: {data['error']}",
            "data": data,
        }

    periods = data.get("loaded_periods", [])
    periods_str = ", ".join(f"`{p}`" for p in periods) if periods else "Ninguno"
    lines = [
        "### Diagnóstico del Sistema y Estado de Sincronización",
        f"- **Estado del Servicio**: `{data.get('status', 'healthy')}`",
        f"- **Informes Generales Cargados ({data.get('loaded_reports_count', 0)})**: {periods_str}",
        f"- **Último Periodo Disponible**: `{data.get('latest_period') or 'N/A'}`",
        f"- **Cola Spool Pendientes**: `{data.get('pending_count', 0)}` archivos",
        f"- **Cola Spool Sincronizados**: `{data.get('synced_count', 0)}` archivos",
        f"- **Cola Spool Fallidos**: `{data.get('failed_count', 0)}` archivos",
    ]
    return {
        "summary": "\n".join(lines),
        "data": data,
        "step_name": "Diagnóstico del sistema",
        "step_detail": "GET /api/v1/reports & GET /api/v1/sync/status",
    }


def _is_explicit_building_macro_query(query: str) -> bool:
    """Detect if a query explicitly asks about building-wide macro expenses without mentioning the user's apartment."""
    has_building = bool(
        re.search(
            r"\b(edificio|comunidad|condominio|gasto\s+operacional|fondos\s+y\s+recargos|"
            r"fondo\s+de\s+reserva|fondo\s+impermeabilizaci[oó]n|remuneraciones|mantenciones)\b",
            query,
            re.I,
        )
    )
    has_unit_self = bool(
        re.search(
            r"\b(mi\s+gasto|mi\s+depto|mi\s+departamento|mi\s+unidad|mi\s+cuenta|mi\s+boleta|"
            r"depto|departamento|unidad)\b",
            query,
            re.I,
        )
    )
    return has_building and not has_unit_self


def run_deep_financial_analysis(
    user_context: UserContext,
    query: str,
    unit_code: str | None = None,
    thread_id: str | None = None,
    resume_choice: str | None = None,
    workflow_fn: Callable[..., Any] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Execute the 9-node Analytics LangGraph state machine with deterministic RBAC tenant scoping."""
    norm_requested = _normalize_unit(unit_code)

    if user_context.role == "resident":
        assigned_unit = _normalize_unit(user_context.unit_code) or ""
        if norm_requested and norm_requested != assigned_unit:
            assigned_num = assigned_unit.split("-")[-1]
            if norm_requested != assigned_num:
                msg = (
                    f"Acceso denegado: Tu perfil de residente solo está autorizado para "
                    f"analizar la unidad **{assigned_unit}**."
                )
                return {"error": msg, "unauthorized": True, "summary": msg}

        unauth_in_text = _detect_unauthorized_unit_in_text(query, assigned_unit)
        if unauth_in_text:
            msg = (
                f"Acceso denegado: Tu perfil de residente solo está autorizado para "
                f"analizar la unidad **{assigned_unit}** (no puedes consultar `{unauth_in_text}`)."
            )
            return {"error": msg, "unauthorized": True, "summary": msg}

        if _is_cross_unit_cohort_query(query):
            msg = (
                f"Acceso denegado: Las comparativas o rankings entre múltiples departamentos "
                f"están restringidos a la Administración. Puedes consultar la evolución de tu unidad (**{assigned_unit}**) "
                f"o los gastos generales del edificio."
            )
            return {"error": msg, "unauthorized": True, "summary": msg}

        # Allow resident to analyze building-wide macro expenses when explicitly requested
        if norm_requested is None and _is_explicit_building_macro_query(query):
            effective_unit = None
        else:
            effective_unit = assigned_unit
    else:
        effective_unit = norm_requested

    runner = workflow_fn or run_analytics_workflow
    import inspect
    sig = inspect.signature(runner)
    kwargs: dict[str, Any] = {
        "query": query,
        "target_unit": effective_unit,
        "thread_id": thread_id,
        "resume_choice": resume_choice,
    }
    if "status_callback" in sig.parameters:
        kwargs["status_callback"] = status_callback

    result = runner(**kwargs)

    if result.needs_clarification:
        step_name = "Revisión inicial completada"
    else:
        step_name = "Análisis financiero completado"

    if user_context.role == "admin":
        step_detail = result.sql_query or f"Análisis ejecutado para consulta: {query}"
    else:
        scope_label = f"la unidad {effective_unit}" if effective_unit else "el condominio"
        step_detail = f"Información verificada en los registros financieros oficiales de {scope_label}."

    return {
        "summary": result.summary,
        "is_authorized": result.is_authorized,
        "needs_clarification": result.needs_clarification,
        "clarification_prompt": result.clarification_prompt,
        "candidate_options": result.candidate_options,
        "thread_id": result.thread_id,
        "sql_query": result.sql_query,
        "data": result.data,
        "chart_file_path": result.chart_file_path,
        "unit_code": effective_unit,
        "step_name": step_name,
        "step_detail": step_detail,
    }
