"""Unit and integration tests for Chainlit web lifecycle callbacks and UI artifact rendering."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
import pytest

from agent.state import FocusContext, SupervisorTurnResult, UserContext
import frontend.app as chainlit_app


class _TestSession:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def clear(self) -> None:
        self._store.clear()


class _TestStep:
    sent_steps: list[_TestStep] = []

    def __init__(self, name: str = "Step", type: str = "tool") -> None:
        self.name = name
        self.type = type
        self.output = ""
        self.status_history: list[str] = [name]

    async def __aenter__(self) -> _TestStep:
        _TestStep.sent_steps.append(self)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        return None

    async def update(self) -> _TestStep:
        self.status_history.append(self.name)
        return self

    async def remove(self) -> bool:
        return True


class _TestImage:
    def __init__(
        self,
        path: str,
        name: str = "chart",
        display: str = "inline",
        size: str = "large",
    ) -> None:
        self.path = path
        self.name = name
        self.display = display
        self.size = size


class _TestMessage:
    sent_messages: list[_TestMessage] = []

    def __init__(
        self,
        content: str,
        elements: list[Any] | None = None,
        actions: list[Any] | None = None,
    ) -> None:
        self.content = content
        self.elements = elements or []
        self.actions = actions or []

    async def send(self) -> _TestMessage:
        _TestMessage.sent_messages.append(self)
        return self

    async def update(self) -> _TestMessage:
        return self


class _TestAskActionMessage:
    sent_asks: list[_TestAskActionMessage] = []
    simulated_response: dict[str, Any] | None = None

    def __init__(
        self,
        content: str,
        actions: list[Any],
        timeout: int = 180,
        raise_on_timeout: bool = False,
    ) -> None:
        self.content = content
        self.actions = actions
        self.timeout = timeout
        self.raise_on_timeout = raise_on_timeout

    async def send(self) -> dict[str, Any] | None:
        _TestAskActionMessage.sent_asks.append(self)
        return _TestAskActionMessage.simulated_response

    async def update(self) -> _TestAskActionMessage:
        return self


@pytest.fixture(autouse=True)
def setup_chainlit_test_doubles(monkeypatch):
    """Provide isolated session, Message, Step, and AskActionMessage doubles for offline unit testing."""
    monkeypatch.setenv("FIREBASE_API_KEY", "")
    session = _TestSession()
    _TestMessage.sent_messages.clear()
    _TestAskActionMessage.sent_asks.clear()
    _TestStep.sent_steps.clear()
    _TestAskActionMessage.simulated_response = None

    monkeypatch.setattr(chainlit_app.cl, "user_session", session)
    monkeypatch.setattr(chainlit_app.cl, "Message", _TestMessage)
    monkeypatch.setattr(chainlit_app.cl, "Step", _TestStep)
    monkeypatch.setattr(chainlit_app.cl, "Image", _TestImage)
    monkeypatch.setattr(chainlit_app.cl, "AskActionMessage", _TestAskActionMessage)


def test_password_auth_callback_resident_and_admin():
    """Verify Chainlit password_auth_callback authenticates resident and admin profiles."""
    res_user = chainlit_app.password_auth_callback("residente504b@condominio.cl", "residente123")
    assert res_user is not None
    assert res_user.metadata["role"] == "resident"
    assert res_user.metadata["unit_code"] == "B-504"

    adm_user = chainlit_app.password_auth_callback("admin@condominio.cl", "admin123")
    assert adm_user is not None
    assert adm_user.metadata["role"] == "admin"
    assert adm_user.metadata["unit_code"] is None

    bad_user = chainlit_app.password_auth_callback("admin@condominio.cl", "bad_password")
    assert bad_user is None


def test_on_chat_start_resident_and_admin_greetings():
    """Verify @cl.on_chat_start initializes ephemeral RAM session and sends role-specific welcome."""
    res_user = chainlit_app.password_auth_callback("residente504b@condominio.cl", "residente123")
    chainlit_app.cl.user_session.set("user", res_user)

    msg_res = asyncio.run(chainlit_app.on_chat_start())
    assert "Residente - Unidad B-504" in msg_res.content
    assert chainlit_app.cl.user_session.get("messages") == []
    assert isinstance(chainlit_app.cl.user_session.get("focus"), FocusContext)

    adm_user = chainlit_app.password_auth_callback("admin@condominio.cl", "admin123")
    chainlit_app.cl.user_session.clear()
    chainlit_app.cl.user_session.set("user", adm_user)

    msg_adm = asyncio.run(chainlit_app.on_chat_start())
    assert "Administración / Comité" in msg_adm.content


def test_on_message_ask_action_message_and_auto_resumption():
    """Verify ambiguous queries render cl.AskActionMessage bordered buttons, preserve explanation, and auto-resume."""
    adm_user = chainlit_app.password_auth_callback("admin@condominio.cl", "admin123")
    chainlit_app.cl.user_session.set("user", adm_user)
    asyncio.run(chainlit_app.on_chat_start())

    # Simulate user clicking the 2nd button in AskActionMessage
    _TestAskActionMessage.simulated_response = {
        "name": "clarification_choice",
        "label": "Torre B - 504",
        "payload": {"choice": "Torre B - 504", "thread_id": "thread_ui_777"},
    }

    calls: list[dict[str, Any]] = []

    def fake_supervisor_two_turn(
        user_message: str,
        user_context: UserContext,
        messages=None,
        focus=None,
        clarification_choice=None,
    ):
        calls.append(
            {
                "user_message": user_message,
                "clarification_choice": clarification_choice,
                "thread_id": focus.active_analytics_thread_id if focus else None,
            }
        )
        if not clarification_choice:
            updated_focus = FocusContext(active_analytics_thread_id="thread_ui_777")
            res = SupervisorTurnResult(
                reply="¿A qué torre corresponde el departamento 504?",
                route="run_deep_financial_analysis",
                step_name="Motor Analítico",
                needs_clarification=True,
                clarification_prompt="Se encontraron múltiples unidades 504. Selecciona una:",
                candidate_options=["Torre A - 504", "Torre B - 504"],
                thread_id="thread_ui_777",
                focus=updated_focus,
            )
            return res, [], updated_focus

        updated_focus = FocusContext(active_analytics_thread_id=None, last_unit="B-504")
        res = SupervisorTurnResult(
            reply="El departamento B-504 registra un total de $145.320 CLP.",
            route="run_deep_financial_analysis",
            step_name="Motor Analítico",
            needs_clarification=False,
            focus=updated_focus,
        )
        return res, [], updated_focus

    final_msg = asyncio.run(
        chainlit_app.on_message(
            SimpleNamespace(content="¿Cuánto debe el 504?"),
            supervisor_runner=fake_supervisor_two_turn,
        )
    )

    # Verify AskActionMessage was sent with both options and preserved explanation + choice
    assert len(_TestAskActionMessage.sent_asks) == 1
    ask_obj = _TestAskActionMessage.sent_asks[0]
    assert len(ask_obj.actions) == 2
    assert ask_obj.actions[0].label == "Torre A - 504"
    assert ask_obj.actions[1].label == "Torre B - 504"
    assert "Se encontraron múltiples unidades 504" in ask_obj.content
    assert "Opción seleccionada:** Torre B - 504" in ask_obj.content

    # Verify automatic second call resumed the thread and rendered final answer
    assert len(calls) == 2
    assert calls[1]["clarification_choice"] == "Torre B - 504"
    assert calls[1]["thread_id"] == "thread_ui_777"
    assert "B-504 registra un total" in final_msg.content


def test_on_message_inline_chart_attachment():
    """Verify chart_file_path attaches an inline cl.Image element to the message."""
    res_user = chainlit_app.password_auth_callback("residente504b@condominio.cl", "residente123")
    chainlit_app.cl.user_session.set("user", res_user)
    asyncio.run(chainlit_app.on_chat_start())

    def fake_supervisor_with_chart(
        user_message: str,
        user_context: UserContext,
        messages=None,
        focus=None,
        clarification_choice=None,
    ):
        updated_focus = FocusContext(last_unit="B-504")
        res = SupervisorTurnResult(
            reply=(
                "Aquí está la evolución histórica de tu gasto común.\n\n"
                "![Gráfico Analítico](/home/nereyes/agentes/data-analyst/data/output/charts/trend_b504.png)\n\n"
                "Interpretación de la tendencia."
            ),
            route="run_deep_financial_analysis",
            step_name="Motor Analítico",
            step_detail="SELECT period_date, calculated_total_month FROM unit_bills;",
            chart_file_path="data/output/charts/trend_b504.png",
            focus=updated_focus,
        )
        return res, [], updated_focus

    sent_msg = asyncio.run(
        chainlit_app.on_message(
            SimpleNamespace(content="¿Cómo ha evolucionado mi gasto?"),
            supervisor_runner=fake_supervisor_with_chart,
        )
    )
    assert "evolución histórica" in sent_msg.content
    assert "![Gráfico Analítico]" not in sent_msg.content
    assert "/home/nereyes" not in sent_msg.content
    assert len(sent_msg.elements) == 1
    assert sent_msg.elements[0].path == "data/output/charts/trend_b504.png"
    assert sent_msg.elements[0].display == "inline"
    assert sent_msg.elements[0].size == "large"


def test_live_step_status_updates_and_role_aware_step_detail():
    """Verify cl.Step updates in-place with human-friendly labels and hides technical SQL from residents while showing it to admins."""
    from agent.tools import lookup_unit_bill

    res_ctx = UserContext(uid="u_res", email="residente504b@condominio.cl", role="resident", unit_code="B-504")
    adm_ctx = UserContext(uid="u_adm", email="admin@condominio.cl", role="admin", unit_code=None)

    fake_bill = {
        "unit_code": "B-504",
        "period_date": "2026-08-01",
        "calculated_total_month": 125465,
        "calculated_dept_subtotal": 108776,
        "calculated_consumo_total": 14189,
        "calculated_unit_subtotal": 2500,
        "alicuota_total": 1.25,
        "concepts": [],
        "consumptions": [],
    }
    from unittest.mock import patch

    with patch("agent.tools.get_unit_bill_details", return_value=fake_bill):
        res_out = lookup_unit_bill(user_context=res_ctx, unit_code="B-504", period_date="2026-08-01")
        adm_out = lookup_unit_bill(user_context=adm_ctx, unit_code="B-504", period_date="2026-08-01")

    # Resident step_detail is human-friendly and contains no technical endpoint
    assert "GET /api/v1" not in res_out["step_detail"]
    assert "Información verificada en los registros oficiales" in res_out["step_detail"]
    # Admin step_detail retains full technical endpoint
    assert "GET /api/v1/unit-bills?unit_code=B-504" in adm_out["step_detail"]

    # Verify live status progression in Chainlit Step during deep analysis
    res_user = chainlit_app.password_auth_callback("residente504b@condominio.cl", "residente123")
    chainlit_app.cl.user_session.set("user", res_user)
    asyncio.run(chainlit_app.on_chat_start())

    def fake_streaming_supervisor(
        user_message: str,
        user_context: UserContext,
        messages=None,
        focus=None,
        clarification_choice=None,
        status_callback=None,
    ):
        if status_callback:
            status_callback("Revisando el historial mensual de gastos...")
            status_callback("Extrayendo el desglose detallado de gastos...")
            status_callback("Preparando el gráfico comparativo...")
            status_callback("Redactando el informe y conclusiones...")
        updated_focus = FocusContext(last_unit="B-504")
        res = SupervisorTurnResult(
            reply="Resumen listo.",
            route="run_deep_financial_analysis",
            step_name="Análisis financiero completado",
            step_detail="Información verificada en los registros financieros oficiales de la unidad B-504.",
            focus=updated_focus,
        )
        return res, [], updated_focus

    asyncio.run(
        chainlit_app.on_message(
            SimpleNamespace(content="¿Por qué subió mi gasto este año?"),
            supervisor_runner=fake_streaming_supervisor,
        )
    )

    assert len(_TestStep.sent_steps) == 1
    live_step = _TestStep.sent_steps[0]
    assert live_step.status_history[0] == "Analizando tu consulta..."
    assert "Revisando el historial mensual de gastos..." in live_step.status_history
    assert "Extrayendo el desglose detallado de gastos..." in live_step.status_history
    assert "Preparando el gráfico comparativo..." in live_step.status_history
    assert live_step.name == "Análisis financiero completado"
    assert "SELECT" not in live_step.output
    assert "Información verificada" in live_step.output
