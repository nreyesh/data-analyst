"""Chainlit ephemeral web frontend connecting Firebase Auth, RBAC, and the Conversational Supervisor."""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
import re
import sys
from typing import Any, Callable

# Ensure repository root is on sys.path when invoked via `chainlit run frontend/app.py`
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.state import FocusContext, SupervisorTurnResult, UserContext
from agent.supervisor import run_supervisor_turn
from frontend.auth import verify_user_credentials

logger = logging.getLogger("frontend.app")


# ============================================================================
# Chainlit Runtime Import / Offline Test Adapter
# ============================================================================

try:
    import chainlit as cl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - fallback shim when chainlit package is not installed in test venv

    class _UserSessionShim:
        def __init__(self) -> None:
            self._store: dict[str, Any] = {}

        def get(self, key: str, default: Any = None) -> Any:
            return self._store.get(key, default)

        def set(self, key: str, value: Any) -> None:
            self._store[key] = value

        def clear(self) -> None:
            self._store.clear()

    class _UserShim:
        def __init__(
            self,
            identifier: str,
            display_name: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            self.identifier = identifier
            self.display_name = display_name or identifier
            self.metadata = metadata or {}

    class _ActionShim:
        def __init__(
            self,
            name: str,
            payload: dict[str, Any],
            label: str | None = None,
            tooltip: str = "",
            icon: str | None = None,
        ) -> None:
            self.name = name
            self.payload = payload
            self.label = label or payload.get("choice", name)
            self.tooltip = tooltip
            self.icon = icon

    class _ImageShim:
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

    class _StepShim:
        sent_steps: list[_StepShim] = []

        def __init__(self, name: str = "Step", type: str = "tool") -> None:
            self.name = name
            self.type = type
            self.input: str = ""
            self.output: str = ""
            self.status_history: list[str] = [name]

        async def __aenter__(self) -> _StepShim:
            _StepShim.sent_steps.append(self)
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            return None

        async def update(self) -> _StepShim:
            self.status_history.append(self.name)
            return self

        async def remove(self) -> bool:
            return True

    class _MessageShim:
        sent_messages: list[_MessageShim] = []

        def __init__(
            self,
            content: str,
            elements: list[Any] | None = None,
            actions: list[Any] | None = None,
        ) -> None:
            self.content = content
            self.elements = elements or []
            self.actions = actions or []

        async def send(self) -> _MessageShim:
            _MessageShim.sent_messages.append(self)
            return self

        async def update(self) -> _MessageShim:
            return self

    class _AskActionMessageShim:
        sent_asks: list[_AskActionMessageShim] = []
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
            _AskActionMessageShim.sent_asks.append(self)
            return _AskActionMessageShim.simulated_response

        async def update(self) -> _AskActionMessageShim:
            return self

    class _ChainlitModuleShim:
        User = _UserShim
        Action = _ActionShim
        Image = _ImageShim
        Step = _StepShim
        Message = _MessageShim
        AskActionMessage = _AskActionMessageShim
        user_session = _UserSessionShim()

        @staticmethod
        def password_auth_callback(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        @staticmethod
        def on_chat_start(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        @staticmethod
        def on_message(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        @staticmethod
        def action_callback(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                return fn

            return _decorator

    cl = _ChainlitModuleShim()  # type: ignore[assignment]


# ============================================================================
# Helper Functions
# ============================================================================


def build_welcome_markdown(user_context: UserContext) -> str:
    """Build role-aware welcome banner and starter examples for Chainlit session."""
    if user_context.role == "resident":
        badge = f"🏠 **Perfil Activo: Residente - Unidad {user_context.unit_code}**"
        starters = (
            "- *«¿Cuánto debo pagar este mes?»*\n"
            "- *«¿Cuál fue mi consumo de agua caliente en agosto?»*\n"
            "- *«¿Por qué subió mi gasto común respecto al mes anterior?»*\n"
            "- *«¿Cuál fue el gasto operacional total del edificio?»*"
        )
    else:
        badge = "🛡️ **Perfil Activo: Administración / Comité**"
        starters = (
            "- *«¿Cómo ha evolucionado el gasto operacional del edificio?»*\n"
            "- *«¿Cuál fue el resumen de gastos del edificio en septiembre 2026?»*\n"
            "- *«Muéstrame la nota de cobro del departamento B-504»*\n"
            "- *«¿Qué meses están cargados y cuál es el estado del spool?»*"
        )

    return (
        f"{badge}\n\n"
        "Bienvenido al asistente de inteligencia financiera del condominio. "
        "Esta sesión es **efímera y privada** (no se almacena historial al cerrar la ventana).\n\n"
        "**Ejemplos de consultas:**\n"
        f"{starters}"
    )


def _get_session_user_context() -> UserContext:
    """Retrieve verified UserContext from Chainlit session."""
    ctx = cl.user_session.get("user_context")
    if isinstance(ctx, UserContext):
        return ctx

    cl_user = cl.user_session.get("user")
    if cl_user and hasattr(cl_user, "metadata") and isinstance(cl_user.metadata, dict):
        meta = cl_user.metadata
        return UserContext(
            uid=str(meta.get("uid") or getattr(cl_user, "identifier", "session_user")),
            email=str(meta.get("email") or getattr(cl_user, "identifier", "user@condominio.cl")),
            role=meta.get("role", "resident"),
            unit_code=meta.get("unit_code"),
        )

    raise RuntimeError("No authenticated UserContext found in session.")


async def _execute_turn_with_live_step(
    runner: Callable[..., Any],
    user_message: str,
    user_context: UserContext,
    messages: list[Any],
    focus: FocusContext,
    clarification_choice: str | None = None,
) -> tuple[SupervisorTurnResult, list[Any], FocusContext]:
    """Run a supervisor turn inside a live-updating Chainlit Step so the user sees real-time progress."""
    initial_label = (
        "Procesando la opción seleccionada..."
        if clarification_choice
        else "Analizando tu consulta..."
    )
    loop = asyncio.get_running_loop()

    async with cl.Step(name=initial_label, type="run") as step:
        def _on_status(new_label: str) -> None:
            step.name = new_label
            if hasattr(step, "status_history") and isinstance(step.status_history, list):
                step.status_history.append(new_label)
            if hasattr(step, "update"):
                try:
                    asyncio.run_coroutine_threadsafe(step.update(), loop)
                except Exception:
                    pass

        sig = inspect.signature(runner)
        kwargs: dict[str, Any] = {
            "user_message": user_message,
            "user_context": user_context,
            "messages": messages,
            "focus": focus,
            "clarification_choice": clarification_choice,
        }
        if "status_callback" in sig.parameters:
            kwargs["status_callback"] = _on_status

        turn_result, updated_messages, updated_focus = await asyncio.to_thread(
            runner,
            **kwargs,
        )

        if turn_result.step_name:
            step.name = turn_result.step_name
            if hasattr(step, "status_history") and isinstance(step.status_history, list):
                step.status_history.append(turn_result.step_name)
            if turn_result.step_detail:
                step.output = turn_result.step_detail
            elif user_context.role == "admin":
                step.output = turn_result.route
            else:
                step.output = "Información verificada en los registros oficiales de la comunidad."
            if hasattr(step, "update"):
                await step.update()
        else:
            if hasattr(step, "remove"):
                await step.remove()

    return turn_result, updated_messages, updated_focus


async def _render_turn_result(
    turn_result: SupervisorTurnResult,
    user_ctx: UserContext | None = None,
    supervisor_runner: Callable[..., Any] | None = None,
) -> Any:
    """Render inline cl.Image chart or cl.AskActionMessage bordered clarification buttons."""
    # Option B: Render bordered outline buttons via cl.AskActionMessage when clarification is needed
    if turn_result.needs_clarification and turn_result.candidate_options:
        prompt = turn_result.clarification_prompt or turn_result.reply
        actions = [
            cl.Action(
                name="clarification_choice",
                payload={
                    "choice": opt,
                    "thread_id": turn_result.thread_id,
                },
                label=opt,
            )
            for opt in turn_result.candidate_options
        ]
        ask_msg = cl.AskActionMessage(
            content=prompt,
            actions=actions,
            timeout=180,
            raise_on_timeout=False,
        )
        res = await ask_msg.send()
        if res is None:
            ask_msg.content = f"{prompt}\n\n⏳ *Tiempo de espera agotado. Puedes volver a enviar tu consulta cuando gustes.*"
            await ask_msg.update()
            return ask_msg

        payload = res.get("payload", {}) if isinstance(res, dict) else {}
        choice = str(payload.get("choice") or (res.get("label") if isinstance(res, dict) else "")).strip()
        thread_id = payload.get("thread_id") or turn_result.thread_id

        # Preserve the original explanation alongside the user's selected option
        ask_msg.content = f"{prompt}\n\n✅ **Opción seleccionada:** {choice}"
        await ask_msg.update()

        if choice and user_ctx is not None:
            runner = supervisor_runner or run_supervisor_turn
            prior_messages = cl.user_session.get("messages") or []
            focus = cl.user_session.get("focus") or FocusContext()
            if thread_id:
                focus.active_analytics_thread_id = thread_id

            resumed_result, updated_messages, updated_focus = await _execute_turn_with_live_step(
                runner=runner,
                user_message=choice,
                user_context=user_ctx,
                messages=prior_messages,
                focus=focus,
                clarification_choice=choice,
            )
            cl.user_session.set("messages", updated_messages)
            cl.user_session.set("focus", updated_focus)
            return await _render_turn_result(
                resumed_result,
                user_ctx=user_ctx,
                supervisor_runner=supervisor_runner,
            )

        return ask_msg

    elements: list[Any] = []
    if turn_result.chart_file_path:
        chart_path = Path(turn_result.chart_file_path)
        elements.append(
            cl.Image(
                path=str(chart_path),
                name="Gráfico Analítico",
                display="inline",
                size="large",
            )
        )

    clean_reply = re.sub(r"!\[[^\]]*\]\([^)]+\)\n?", "", turn_result.reply).strip()
    return await cl.Message(content=clean_reply, elements=elements).send()


# ============================================================================
# Chainlit Lifecycle Callbacks
# ============================================================================


@cl.password_auth_callback
def password_auth_callback(username: str, password: str) -> Any | None:
    """Authenticate user via Firebase Auth (or server-side dev directory) and issue signed session."""
    user_ctx = verify_user_credentials(username=username, password=password)
    if user_ctx is None:
        return None

    display_name = (
        f"{user_ctx.email.split('@')[0]} ({user_ctx.unit_code})"
        if user_ctx.unit_code
        else f"{user_ctx.email.split('@')[0]} (Admin)"
    )
    return cl.User(
        identifier=user_ctx.uid,
        display_name=display_name,
        metadata=user_ctx.model_dump(),
    )


@cl.on_chat_start
async def on_chat_start() -> Any:
    """Initialize ephemeral in-RAM conversation state and display role-specific welcome message."""
    user_ctx = _get_session_user_context()
    cl.user_session.set("user_context", user_ctx)
    cl.user_session.set("messages", [])
    cl.user_session.set("focus", FocusContext())

    welcome_md = build_welcome_markdown(user_ctx)
    return await cl.Message(content=welcome_md).send()


@cl.on_message
async def on_message(
    message: Any,
    supervisor_runner: Callable[..., Any] | None = None,
) -> Any:
    """Handle incoming chat message, invoke Supervisor, and render response + artifacts."""
    user_ctx = _get_session_user_context()
    prior_messages = cl.user_session.get("messages") or []
    focus = cl.user_session.get("focus") or FocusContext()
    user_text = getattr(message, "content", str(message)).strip()

    runner = supervisor_runner or run_supervisor_turn

    # If an analytics thread is paused awaiting clarification and user typed free text
    clarification_choice = user_text if focus.active_analytics_thread_id else None

    turn_result, updated_messages, updated_focus = await _execute_turn_with_live_step(
        runner=runner,
        user_message=user_text,
        user_context=user_ctx,
        messages=prior_messages,
        focus=focus,
        clarification_choice=clarification_choice,
    )

    cl.user_session.set("messages", updated_messages)
    cl.user_session.set("focus", updated_focus)

    return await _render_turn_result(
        turn_result,
        user_ctx=user_ctx,
        supervisor_runner=supervisor_runner,
    )


@cl.action_callback("clarification_choice")
async def on_clarification_action(
    action: Any,
    supervisor_runner: Callable[..., Any] | None = None,
) -> Any:
    """Fallback handler if a clarification cl.Action callback is triggered directly."""
    user_ctx = _get_session_user_context()
    prior_messages = cl.user_session.get("messages") or []
    focus = cl.user_session.get("focus") or FocusContext()

    payload = getattr(action, "payload", {}) or {}
    choice = str(payload.get("choice") or getattr(action, "label", "")).strip()
    thread_id = payload.get("thread_id")
    if thread_id:
        focus.active_analytics_thread_id = thread_id

    runner = supervisor_runner or run_supervisor_turn

    turn_result, updated_messages, updated_focus = await _execute_turn_with_live_step(
        runner=runner,
        user_message=choice,
        user_context=user_ctx,
        messages=prior_messages,
        focus=focus,
        clarification_choice=choice,
    )

    cl.user_session.set("messages", updated_messages)
    cl.user_session.set("focus", updated_focus)

    return await _render_turn_result(
        turn_result,
        user_ctx=user_ctx,
        supervisor_runner=supervisor_runner,
    )
