"""LangGraph Conversational Supervisor orchestrating Fast-Path tools, Deep Analytics, and RBAC."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Literal
from google import genai
from google.genai import types
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from agent.state import (
    FocusContext,
    SupervisorDecision,
    SupervisorRoute,
    SupervisorState,
    SupervisorTurnResult,
    UserContext,
)
from agent.tools import (
    check_system_status,
    lookup_building_summary,
    lookup_unit_bill,
    run_deep_financial_analysis,
)
from analytics.config import get_analytics_settings
from analytics.nodes import generate_content_with_fallback, get_gemini_client

logger = logging.getLogger("agent.supervisor")

MAX_EPHEMERAL_MESSAGES = 8
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "supervisor_system.md"

SPANISH_MONTHS: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def load_supervisor_prompt() -> str:
    """Load the supervisor system prompt from markdown template."""
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "Eres el Supervisor Conversacional de Inteligencia Financiera de Condominios."


def _previous_month_iso(period_date: str) -> str:
    """Compute the previous month ISO date 'YYYY-MM-01' from 'YYYY-MM-01'."""
    m = re.match(r"^(\d{4})-(\d{2})(?:-\d{2})?$", period_date.strip())
    if not m:
        return period_date
    year = int(m.group(1))
    month = int(m.group(2))
    if month == 1:
        return f"{year - 1:04d}-12-01"
    return f"{year:04d}-{month - 1:02d}-01"


def _extract_period_from_text(text: str, focus: FocusContext) -> str | None:
    """Extract ISO period 'YYYY-MM-01' from Spanish text or resolve relative follow-up ('mes anterior')."""
    lowered = text.lower()

    # 1. Explicit ISO YYYY-MM-01 or YYYY-MM
    m_iso = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])(?:-01)?\b", lowered)
    if m_iso:
        return f"{m_iso.group(1)}-{m_iso.group(2)}-01"

    # 2. Relative anaphora: "el mes anterior" / "mes pasado"
    if re.search(r"\b(mes\s+anterior|mes\s+pasado|periodo\s+anterior)\b", lowered):
        if focus.last_period:
            return _previous_month_iso(focus.last_period)

    # 3. Spanish month name + optional year (defaults to year of focus.last_period or 2026)
    for month_name, month_num in SPANISH_MONTHS.items():
        if re.search(rf"\b{month_name}\b", lowered):
            m_year = re.search(r"\b(20\d{2})\b", lowered)
            if m_year:
                year = int(m_year.group(1))
            elif focus.last_period and re.match(r"^\d{4}-", focus.last_period):
                year = int(focus.last_period[:4])
            else:
                year = 2026
            return f"{year:04d}-{month_num:02d}-01"

    return None


def _extract_unit_from_text(text: str) -> str | None:
    """Extract explicit apartment code from user query if present."""
    m1 = re.search(r"\b([A-Za-z]{1,2})\s*[-]\s*(\d{1,4})\b", text)
    if m1:
        return f"{m1.group(1).upper()}-{int(m1.group(2))}"

    m2 = re.search(
        r"\b(?:depto|departamento|dpto|unidad)\s*[-#]?\s*(\d{1,4})\s*[-]?\s*([A-Za-z]{1,2})\b",
        text,
        re.I,
    )
    if m2:
        return f"{m2.group(2).upper()}-{int(m2.group(1))}"

    m3 = re.search(r"\b(\d{1,4})\s*[-]\s*([A-Za-z]{1,2})\b", text)
    if m3:
        return f"{m3.group(2).upper()}-{int(m3.group(1))}"

    m4 = re.search(r"\b(?:depto|departamento|dpto|unidad)\s*[-#]?\s*(\d{1,4})\b", text, re.I)
    if m4:
        return str(int(m4.group(1)))

    return None


def classify_intent_deterministic(
    user_message: str,
    user_context: UserContext,
    focus: FocusContext,
) -> SupervisorDecision:
    """Classify user intent and resolve multi-turn focus deterministically."""
    text = user_message.strip()
    lowered = text.lower()

    period_date = _extract_period_from_text(text, focus)
    extracted_unit = _extract_unit_from_text(text)
    effective_unit = extracted_unit or (
        user_context.unit_code if user_context.role == "resident" else focus.last_unit
    )

    # 1. Follow-up anaphora (e.g., "¿Y el mes anterior?", "¿Y en julio?")
    is_short_followup = bool(
        re.match(
            r"^[¿?¡!\s]*(y\s+)?(el\s+mes\s+anterior|el\s+mes\s+pasado|en\s+[a-záéíóú]+(\s+20\d{2})?|y\s+el\s+depto|y\s+la\s+unidad)\b",
            lowered,
        )
    )
    if is_short_followup and focus.last_route in ("lookup_unit_bill", "lookup_building_summary"):
        target_period = period_date or focus.last_period
        return SupervisorDecision(
            route=focus.last_route,
            reasoning="Resolución de pregunta de seguimiento manteniendo la ruta previa y actualizando el periodo.",
            resolved_query=f"Consulta de seguimiento ({focus.last_route}) para periodo {target_period}",
            unit_code=effective_unit,
            period_date=target_period,
        )

    # 2. System diagnostics / spool status
    if re.search(
        r"\b(spool|sincronizaci[oó]n|cola\s+de|estado\s+del\s+sistema|diagn[oó]stico|meses\s+cargados|"
        r"periodos\s+cargados|informes\s+cargados)\b",
        lowered,
    ):
        return SupervisorDecision(
            route="check_system_status",
            reasoning="Consulta sobre diagnóstico del sistema, periodos cargados o cola de sincronización.",
            resolved_query=text,
            unit_code=None,
            period_date=None,
        )

    # 3. Deep financial analysis (trends, evolution, variance, why it rose/fell, charts, comparisons)
    has_causal_or_trend = bool(
        re.search(
            r"\b(evolucion\w*|tendencia\w*|por\s*qu[eé]|poruqe|subi\w*|baj\w*|aument\w*|increment\w*|"
            r"disminu\w*|alza\w*|variaci[oó]n\w*|vario|vari[oó]|variaron|cambios?|cambi[oó]|"
            r"compar\w*|gr[aá]fico\w*|hist[oó]rico\w*|desglos\w*.*entre|detall\w*.*var|"
            r"m[aá]s\s+altos?|m[aá]s\s+caros?|mayor\s+consumo|ranking|en\s+mora|"
            r"este\s+a[ñn]o|[uú]ltimos\s+\d*\s*meses|mes\s+a\s+mes|entre\s+estas\s+fechas|"
            r"entre\s+[a-záéíóú]+\s+y\s+[a-záéíóú]+|de\s+[a-záéíóú]+\s+a\s+[a-záéíóú]+)\b",
            lowered,
        )
    )
    is_followup_to_deep_analysis = (
        focus.last_route == "run_deep_financial_analysis"
        and bool(
            re.search(
                r"\b(estas\s+fechas|esos\s+meses|ese\s+per[ií]odo|dicho\s+per[ií]odo|"
                r"el\s+concepto|la\s+categor[ií]a|los\s+fondos|el\s+fondo|detall\w*|explic\w*|desglos\w*)\b",
                lowered,
            )
        )
    )
    if has_causal_or_trend or is_followup_to_deep_analysis:
        is_explicit_macro = bool(
            re.search(
                r"\b(edificio|comunidad|condominio|gasto\s+operacional)\b",
                lowered,
            )
        ) and not bool(
            re.search(
                r"\b(mi\s+gasto|mi\s+depto|mi\s+departamento|mi\s+unidad|mi\s+cuenta)\b",
                lowered,
            )
        )
        # Preserve building domain on follow-up if previous deep analysis was building-wide
        keep_building_domain = is_explicit_macro or (
            is_followup_to_deep_analysis and focus.last_domain == "building" and extracted_unit is None
        )
        target_unit_for_analysis = None if keep_building_domain else extracted_unit

        resolved_q = text
        if (is_short_followup or is_followup_to_deep_analysis) and focus.last_domain:
            domain_hint = "edificio (expense_reports/expense_sections/expense_items)" if keep_building_domain else f"unidad {target_unit_for_analysis or focus.last_unit}"
            resolved_q = f"{text} (Contexto previo: análisis de {domain_hint}, periodo={focus.last_period})"
        return SupervisorDecision(
            route="run_deep_financial_analysis",
            reasoning="Consulta analítica de evolución temporal, causa de variación, seguimiento analítico o visualización.",
            resolved_query=resolved_q,
            unit_code=target_unit_for_analysis,
            period_date=period_date,
        )

    # 4. Explicit Macro building snapshot lookup
    is_explicit_building = bool(
        re.search(
            r"\b(edificio|gasto\s+operacional|fondo\w*\s+del\s+edificio|total\s+general\s+del\s+edificio|"
            r"informe\s+general|gasto\s+com[uú]n\s+total\s+del\s+edificio|resumen\s+del\s+edificio)\b",
            lowered,
        )
    )
    if is_explicit_building and extracted_unit is None:
        return SupervisorDecision(
            route="lookup_building_summary",
            reasoning="Consulta puntual del resumen de gastos comunes a nivel de edificio.",
            resolved_query=text,
            unit_code=None,
            period_date=period_date,
        )

    # 5. Unit bill snapshot lookup (explicit unit terms)
    has_unit_keywords = bool(
        re.search(
            r"\b(cu[aá]nto\s+debo|mi\s+cuenta|mi\s+gasto|mi\s+departamento|mi\s+depto|mi\s+unidad|"
            r"nota\s+de\s+cobro|[uú]ltimo\s+pago|lectura\w*|agua\s+caliente|calefacci[oó]n|"
            r"al[ií]cuota|deuda\s+del|detalle\s+de\s+mi)\b",
            lowered,
        )
    )
    if has_unit_keywords or extracted_unit is not None:
        return SupervisorDecision(
            route="lookup_unit_bill",
            reasoning="Consulta puntual de estado de cuenta, saldo o lecturas de una unidad.",
            resolved_query=text,
            unit_code=extracted_unit or (user_context.unit_code if user_context.role == "resident" else focus.last_unit),
            period_date=period_date,
        )

    # 6. Role-aware general expense snapshot (e.g., "dame el detalle del gasto comun de este mes",
    #    "cuel fue el gasto comun de este mes", "cuanto fue el gasto de este mes")
    has_general_expense_terms = bool(
        re.search(
            r"\b(gasto\s*com[uú]n\w*|gastos\s*comunes|detalle\s+del\s+gasto|total\s+del\s+mes|"
            r"gasto\s+de\s+este\s+mes|cuenta\s+de\s+este\s+mes|cobro\s+de\s+este\s+mes|"
            r"cu[aá]nto\s+fue\s+el\s+gasto|cu[aáe]l\s+fue\s+el\s+gasto|resumen\s+de\s+gastos|"
            r"fondo\w*|total\s+general|cu[aá]nto\s+gast[oó])\b",
            lowered,
        )
    )
    if has_general_expense_terms:
        if user_context.role == "resident":
            return SupervisorDecision(
                route="lookup_unit_bill",
                reasoning="Residente consultando el detalle o monto del gasto común del mes; se enruta a su unidad asignada.",
                resolved_query=text,
                unit_code=user_context.unit_code,
                period_date=period_date,
            )
        return SupervisorDecision(
            route="lookup_building_summary",
            reasoning="Administrador consultando el resumen o monto de gastos comunes del mes a nivel edificio.",
            resolved_query=text,
            unit_code=None,
            period_date=period_date,
        )

    # 7. Explicit Greetings / Help menu ONLY when message is a greeting or help inquiry
    is_pure_greeting_or_help = bool(
        re.match(
            r"^[¡!¿?\s]*(hola|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches|hey|saludos|"
            r"gracias|muchas\s+gracias|ayuda|help|qu[eé]\s+puedes\s+hacer|qui[eé]n\s+eres)[¡!¿?.\s]*$",
            lowered,
        )
    )
    if is_pure_greeting_or_help or not lowered:
        role_label = (
            f"Residente (Unidad **{user_context.unit_code}**)"
            if user_context.role == "resident"
            else "Administración / Comité"
        )
        reply = (
            f"¡Hola! Soy el asistente financiero del condominio (Perfil activo: {role_label}).\n\n"
            "Puedo ayudarte con:\n"
            "- **Consultas rápidas de tu unidad**: *«¿Cuánto debo pagar este mes?»*, *«¿Cuál fue mi consumo de agua caliente?»*\n"
            "- **Resumen general del edificio**: *«¿Cuál fue el gasto común total del edificio en septiembre?»*\n"
            "- **Análisis de evolución y variaciones**: *«¿Cómo ha evolucionado el gasto?»*, *«¿Por qué subió mi gasto común?»*"
        )
        if user_context.role == "admin":
            reply += "\n- **Diagnóstico del sistema**: *«¿Qué meses están cargados y cuál es el estado del spool?»*"

        return SupervisorDecision(
            route="direct_response",
            reasoning="Saludo o consulta conversacional general.",
            resolved_query=text,
            direct_reply=reply,
        )

    # 8. Non-greeting financial/analytical fallback -> delegate to deep analytics engine
    return SupervisorDecision(
        route="run_deep_financial_analysis",
        reasoning="Consulta abierta delegada al motor analítico.",
        resolved_query=text,
        unit_code=extracted_unit or (user_context.unit_code if user_context.role == "resident" else focus.last_unit),
        period_date=period_date,
    )


def supervisor_router_node(
    state: SupervisorState,
    llm_client: genai.Client | None = None,
) -> dict[str, Any]:
    """Classify user message into a SupervisorDecision, handling clarification resumptions first."""
    user_context = state["user_context"]
    focus = state.get("focus") or FocusContext()
    user_msg = state.get("user_message", "")
    clarification_choice = state.get("clarification_choice")

    # Fast-track resumption when user clicks a clarification button or answers an active interrupt
    if clarification_choice and focus.active_analytics_thread_id:
        decision = SupervisorDecision(
            route="run_deep_financial_analysis",
            reasoning="Reanudando flujo analítico pausado con la selección de aclaración del usuario.",
            resolved_query=clarification_choice,
            unit_code=user_context.unit_code if user_context.role == "resident" else focus.last_unit,
            period_date=focus.last_period,
        )
        return {"decision": decision}

    heuristic_decision = classify_intent_deterministic(user_msg, user_context, focus)
    if heuristic_decision.route == "direct_response":
        return {"decision": heuristic_decision}

    is_unclassified_fallback = (
        heuristic_decision.reasoning == "Consulta abierta delegada al motor analítico."
    )
    active_llm_client = llm_client
    if active_llm_client is None and is_unclassified_fallback:
        settings = get_analytics_settings()
        if settings.gemini_api_key:
            try:
                active_llm_client = get_gemini_client()
            except Exception as exc:
                logger.warning("Could not initialize Gemini client in supervisor router: %s", exc)
                active_llm_client = None

    if active_llm_client is not None:
        try:
            system_instruction = load_supervisor_prompt()
            history_lines = []
            for msg in (state.get("messages") or [])[-MAX_EPHEMERAL_MESSAGES:]:
                prefix = "Usuario" if isinstance(msg, HumanMessage) else "Asistente"
                history_lines.append(f"{prefix}: {msg.content}")

            context_prompt = (
                f"UserContext: role={user_context.role}, unit_code={user_context.unit_code}\n"
                f"FocusContext: {focus.model_dump_json()}\n"
                f"Recent Conversation:\n" + "\n".join(history_lines) + "\n\n"
                f"Current User Message: {user_msg}"
            )
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SupervisorDecision,
                system_instruction=system_instruction,
                temperature=0.0,
            )
            response = generate_content_with_fallback(
                client=active_llm_client,
                contents=[context_prompt],
                config=config,
            )
            decision = SupervisorDecision.model_validate_json(response.text)
            if not decision.period_date:
                decision.period_date = _extract_period_from_text(user_msg, focus)
            if not decision.resolved_query:
                decision.resolved_query = heuristic_decision.resolved_query
            # Guard: never allow LLM to return direct_response greeting for a non-greeting query
            if decision.route == "direct_response":
                return {"decision": heuristic_decision}
            return {"decision": decision}
        except Exception as exc:
            logger.warning(
                "Supervisor LLM routing failed (%s); falling back to deterministic classifier.",
                exc,
            )

    return {"decision": heuristic_decision}


def execute_tool_node(
    state: SupervisorState,
    http_client: Any | None = None,
    workflow_fn: Callable[..., Any] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Execute the selected RBAC-enforced tool and build SupervisorTurnResult."""
    decision = state["decision"]
    assert decision is not None
    user_context = state["user_context"]
    focus = (state.get("focus") or FocusContext()).model_copy(deep=True)
    user_msg = state.get("user_message", "")
    clarification_choice = state.get("clarification_choice")

    route: SupervisorRoute = decision.route

    if route == "lookup_unit_bill":
        if status_callback:
            status_callback("Consultando el estado de cuenta de la unidad...")
        tool_out = lookup_unit_bill(
            user_context=user_context,
            unit_code=decision.unit_code,
            period_date=decision.period_date,
            query_text=user_msg,
            client=http_client,
        )
        if not tool_out.get("error"):
            focus.last_domain = "unit"
            focus.last_route = "lookup_unit_bill"
            focus.last_unit = tool_out.get("unit_code") or decision.unit_code
            focus.last_period = tool_out.get("period_date") or decision.period_date

        turn_result = SupervisorTurnResult(
            reply=tool_out["summary"],
            route=route,
            step_name=tool_out.get("step_name", "Consulta unidad"),
            step_detail=tool_out.get("step_detail"),
            focus=focus,
        )

    elif route == "lookup_building_summary":
        if status_callback:
            status_callback("Consultando el resumen de gastos del edificio...")
        tool_out = lookup_building_summary(
            user_context=user_context,
            period_date=decision.period_date,
            client=http_client,
        )
        if not tool_out.get("error"):
            focus.last_domain = "building"
            focus.last_route = "lookup_building_summary"
            focus.last_period = tool_out.get("period_date") or decision.period_date

        turn_result = SupervisorTurnResult(
            reply=tool_out["summary"],
            route=route,
            step_name=tool_out.get("step_name", "Consulta edificio"),
            step_detail=tool_out.get("step_detail"),
            focus=focus,
        )

    elif route == "check_system_status":
        if status_callback:
            status_callback("Verificando los períodos cargados en el sistema...")
        tool_out = check_system_status(
            user_context=user_context,
            client=http_client,
        )
        if not tool_out.get("error"):
            focus.last_domain = "system"
            focus.last_route = "check_system_status"

        turn_result = SupervisorTurnResult(
            reply=tool_out["summary"],
            route=route,
            step_name=tool_out.get("step_name", "Diagnóstico del sistema"),
            step_detail=tool_out.get("step_detail"),
            focus=focus,
        )

    elif route == "run_deep_financial_analysis":
        active_thread = focus.active_analytics_thread_id if clarification_choice else None
        tool_out = run_deep_financial_analysis(
            user_context=user_context,
            query=decision.resolved_query or user_msg,
            unit_code=decision.unit_code,
            thread_id=active_thread,
            resume_choice=clarification_choice,
            workflow_fn=workflow_fn,
            status_callback=status_callback,
        )
        focus.last_route = "run_deep_financial_analysis"
        effective_tool_unit = tool_out.get("unit_code")
        if effective_tool_unit:
            focus.last_domain = "unit"
            focus.last_unit = effective_tool_unit
        else:
            focus.last_domain = "building"
            if decision.unit_code is None:
                focus.last_unit = None
        if decision.period_date:
            focus.last_period = decision.period_date

        if tool_out.get("needs_clarification"):
            focus.active_analytics_thread_id = tool_out.get("thread_id")
        else:
            focus.active_analytics_thread_id = None

        turn_result = SupervisorTurnResult(
            reply=tool_out["summary"],
            route=route,
            step_name=tool_out.get("step_name", "Análisis financiero completado"),
            step_detail=tool_out.get("step_detail"),
            chart_file_path=tool_out.get("chart_file_path"),
            needs_clarification=bool(tool_out.get("needs_clarification")),
            clarification_prompt=tool_out.get("clarification_prompt"),
            candidate_options=list(tool_out.get("candidate_options") or []),
            thread_id=tool_out.get("thread_id"),
            focus=focus,
        )

    else:
        tool_out = {"summary": decision.direct_reply or "¿En qué te puedo ayudar hoy?"}
        turn_result = SupervisorTurnResult(
            reply=tool_out["summary"],
            route="direct_response",
            focus=focus,
        )

    new_messages: list[BaseMessage] = [
        HumanMessage(content=clarification_choice or user_msg),
        AIMessage(content=turn_result.reply),
    ]
    return {
        "tool_output": tool_out,
        "turn_result": turn_result,
        "focus": focus,
        "messages": new_messages,
    }


def build_supervisor_graph(
    llm_client: genai.Client | None = None,
    http_client: Any | None = None,
    workflow_fn: Callable[..., Any] | None = None,
    status_callback: Callable[[str], None] | None = None,
):
    """Compile the Conversational Supervisor LangGraph state machine."""
    graph = StateGraph(SupervisorState)

    def _router(state: SupervisorState) -> dict[str, Any]:
        return supervisor_router_node(state, llm_client=llm_client)

    def _executor(state: SupervisorState) -> dict[str, Any]:
        return execute_tool_node(
            state,
            http_client=http_client,
            workflow_fn=workflow_fn,
            status_callback=status_callback,
        )

    graph.add_node("supervisor_router", _router)
    graph.add_node("execute_tool", _executor)

    graph.add_edge(START, "supervisor_router")
    graph.add_edge("supervisor_router", "execute_tool")
    graph.add_edge("execute_tool", END)

    return graph.compile(name="ConversationalSupervisorGraph")


def run_supervisor_turn(
    user_message: str,
    user_context: UserContext,
    messages: list[BaseMessage] | None = None,
    focus: FocusContext | None = None,
    clarification_choice: str | None = None,
    llm_client: genai.Client | None = None,
    http_client: Any | None = None,
    workflow_fn: Callable[..., Any] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> tuple[SupervisorTurnResult, list[BaseMessage], FocusContext]:
    """Execute one conversational turn through the Supervisor graph and trim ephemeral history to 8 messages.

    Returns:
        Tuple of (SupervisorTurnResult, updated_ephemeral_messages, updated_focus).
    """
    prior_messages = list(messages or [])[-MAX_EPHEMERAL_MESSAGES:]
    active_focus = focus or FocusContext()

    compiled = build_supervisor_graph(
        llm_client=llm_client,
        http_client=http_client,
        workflow_fn=workflow_fn,
        status_callback=status_callback,
    )

    initial_state: SupervisorState = {
        "messages": prior_messages,
        "user_message": user_message,
        "clarification_choice": clarification_choice,
        "user_context": user_context,
        "focus": active_focus,
        "decision": None,
        "tool_output": None,
        "turn_result": None,
    }

    final_state = compiled.invoke(initial_state)
    turn_result: SupervisorTurnResult = final_state["turn_result"]
    updated_messages = list(final_state.get("messages") or [])[-MAX_EPHEMERAL_MESSAGES:]
    updated_focus: FocusContext = final_state.get("focus") or turn_result.focus

    return turn_result, updated_messages, updated_focus
