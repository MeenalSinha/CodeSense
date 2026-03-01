"""
CodeSense — WebSocket Router
Real-time bidirectional channel for:
  - Live code analysis (type → debounced analyze → flowchart update)
  - Code execution with streaming output
  - Mentor chat with streaming LLM replies
  - Anti-vibe prediction checking

Protocol:
  Client sends: { type, payload, request_id }
  Server sends: { type, payload, request_id }
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from backend.models.schemas import (
    WSMessage, WSMessageType, WSErrorPayload,
    AnalyzeRequest, ExecuteRequest, MentorRequest,
)
from backend.services.ast_service import ast_analyzer
from backend.services.llm_service import llm_service
from backend.services.execution_service import execution_service
from backend.core.config import logger


ws_router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Tracks active WebSocket connections."""

    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)
        logger.info("ws_connected", total=len(self._connections))

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)
        logger.info("ws_disconnected", total=len(self._connections))

    @property
    def connection_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


async def _send(ws: WebSocket, msg_type: WSMessageType, payload: dict, request_id: str = ""):
    """Helper to send a structured WS message."""
    await ws.send_text(json.dumps({
        "type": msg_type.value,
        "payload": payload,
        "request_id": request_id,
    }))


async def _send_error(ws: WebSocket, code: str, message: str, request_id: str = ""):
    await _send(ws, WSMessageType.ERROR, {
        "code": code,
        "message": message,
    }, request_id)


# ─── MESSAGE HANDLERS ─────────────────────────────────────────────────────────

async def handle_analyze(ws: WebSocket, payload: dict, request_id: str):
    """
    Fast path: AST graph only (no LLM) for live flowchart.
    Then async LLM explanation sent as follow-up.
    """
    code = payload.get("code", "").strip()
    if not code:
        return

    t0 = time.perf_counter()

    # Immediate: AST graph (< 10ms)
    graph = ast_analyzer.analyze(code)
    graph_ms = (time.perf_counter() - t0) * 1000

    await _send(ws, WSMessageType.ANALYZE_RESULT, {
        "graph": graph.model_dump(),
        "concepts": graph.concepts,
        "graph_ms": round(graph_ms, 2),
        "phase": "graph",
    }, request_id)

    # Deferred: LLM explanation (100ms–3s depending on backend)
    lang = payload.get("language", "en")
    explanation = await llm_service.explain(code, graph, lang=lang)

    await _send(ws, WSMessageType.ANALYZE_RESULT, {
        "plain_english": explanation.plain_english,
        "why_this_works": explanation.why_this_works,
        "concepts": explanation.concepts,
        "llm_backend": explanation.llm_backend_used,
        "llm_ms": round(explanation.latency_ms, 2),
        "skill_updates": ast_analyzer.concepts_to_skill_updates(graph.concepts),
        "phase": "explanation",
    }, request_id)


async def handle_execute(ws: WebSocket, payload: dict, request_id: str):
    """Run code in sandbox, stream result back."""
    code = payload.get("code", "")
    stdin = payload.get("stdin", "")
    timeout = min(payload.get("timeout", 5), 10)

    if not code.strip():
        await _send_error(ws, "EMPTY_CODE", "No code to execute.", request_id)
        return

    # Send "running" signal
    await _send(ws, WSMessageType.EXECUTE_RESULT, {
        "status": "running",
        "phase": "start",
    }, request_id)

    # Execute in thread (non-blocking event loop)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: execution_service.execute(code, stdin=stdin, timeout=timeout),
    )

    await _send(ws, WSMessageType.EXECUTE_RESULT, {
        "status": result.status.value,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "execution_time_ms": round(result.execution_time_ms, 2),
        "phase": "complete",
    }, request_id)


async def handle_mentor_chat(ws: WebSocket, payload: dict, request_id: str):
    """Socratic mentor reply via LLM."""
    message = payload.get("message", "").strip()
    if not message:
        return

    history = payload.get("history", [])
    code = payload.get("current_code", "")
    error = payload.get("current_error", "")

    result = await llm_service.mentor_reply(
        message=message,
        history=history,
        code=code,
        error=error,
    )

    await _send(ws, WSMessageType.MENTOR_REPLY, {
        "reply": result.reply,
        "is_socratic": result.is_socratic,
        "latency_ms": round(result.latency_ms, 2),
    }, request_id)


# ─── MAIN WEBSOCKET ENDPOINT ──────────────────────────────────────────────────

@ws_router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()

            try:
                data = json.loads(raw)
                msg_type = WSMessageType(data.get("type", ""))
                payload: dict[str, Any] = data.get("payload", {})
                request_id: str = data.get("request_id", "")
            except (json.JSONDecodeError, ValueError) as exc:
                await _send_error(ws, "INVALID_MESSAGE", f"Bad message format: {exc}")
                continue

            # Dispatch
            try:
                if msg_type == WSMessageType.PING:
                    await _send(ws, WSMessageType.PONG, {"ts": time.time()}, request_id)

                elif msg_type == WSMessageType.ANALYZE:
                    await handle_analyze(ws, payload, request_id)

                elif msg_type == WSMessageType.EXECUTE:
                    await handle_execute(ws, payload, request_id)

                elif msg_type == WSMessageType.MENTOR_CHAT:
                    await handle_mentor_chat(ws, payload, request_id)

                else:
                    await _send_error(ws, "UNKNOWN_TYPE", f"Unknown message type: {msg_type}", request_id)

            except Exception as exc:
                logger.error("ws_handler_error", type=msg_type, error=str(exc))
                tb = traceback.format_exc()
                await _send_error(ws, "HANDLER_ERROR", str(exc)[:200], request_id)

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        logger.error("ws_fatal", error=str(exc))
        manager.disconnect(ws)
