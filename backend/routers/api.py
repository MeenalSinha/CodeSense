"""
CodeSense — REST API Routers
All HTTP endpoints for the CodeSense platform.
"""
from __future__ import annotations

import time
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse

from backend.models.schemas import (
    AnalyzeRequest, AnalyzeResponse,
    ExplainRequest, ExplainResponse,
    ExecuteRequest, ExecuteResponse,
    MentorRequest, MentorResponse,
    ProblemHintRequest, ProblemHintResponse,
    SubmitSolutionRequest, SubmitSolutionResponse,
    HealthResponse,
)
from backend.services.ast_service import ast_analyzer
from backend.services.llm_service import llm_service
from backend.services.execution_service import execution_service
from backend.services.practice_service import practice_service, PROBLEM_BANK
from backend.core.config import get_settings, logger


# ─── HEALTH ROUTER ────────────────────────────────────────────────────────────

health_router = APIRouter(prefix="/api", tags=["health"])


@health_router.get("/health", response_model=HealthResponse)
async def health():
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        llm_backend=llm_service.active_backend,
        llm_available=llm_service.active_backend != "mock",
        services={
            "ast_analyzer": True,
            "execution_sandbox": True,
            "llm_service": True,
            "practice_service": True,
        },
    )


# ─── ANALYSIS ROUTER ──────────────────────────────────────────────────────────

analysis_router = APIRouter(prefix="/api/analyze", tags=["analysis"])


@analysis_router.post("", response_model=AnalyzeResponse)
async def analyze_code(req: AnalyzeRequest):
    """
    Full code analysis pipeline:
    1. AST → execution graph
    2. LLM → plain English + why-this-works
    Returns everything needed to power the IDE sidebar.
    """
    t0 = time.perf_counter()

    if req.language != "python":
        raise HTTPException(status_code=400, detail="CodeSense only supports Python.")

    # Step 1: AST analysis
    graph = ast_analyzer.analyze(req.code)

    # Step 2: LLM explanation (async)
    explanation = await llm_service.explain(req.code, graph)

    # Skill updates
    skill_updates = ast_analyzer.concepts_to_skill_updates(graph.concepts)

    elapsed = (time.perf_counter() - t0) * 1000
    logger.info("analyze_complete", concepts=graph.concepts, ms=round(elapsed, 2))

    return AnalyzeResponse(
        graph=graph,
        plain_english=explanation.plain_english,
        why_this_works=explanation.why_this_works,
        concepts=graph.concepts,
        skill_updates=skill_updates,
        analysis_time_ms=elapsed,
    )


@analysis_router.post("/graph")
async def get_graph_only(req: AnalyzeRequest):
    """AST → graph only (no LLM). Fast path for live flowchart updates."""
    graph = ast_analyzer.analyze(req.code)
    return {"graph": graph.model_dump(), "concepts": graph.concepts}


# ─── EXPLAIN ROUTER ───────────────────────────────────────────────────────────

explain_router = APIRouter(prefix="/api/explain", tags=["explain"])


@explain_router.post("", response_model=ExplainResponse)
async def explain_code(req: ExplainRequest):
    """
    LLM-powered explanation endpoint.
    Supports English and Hindi (lang=hi).
    """
    graph = req.graph or ast_analyzer.analyze(req.code)
    result = await llm_service.explain(req.code, graph, lang=req.language)
    return result


# ─── EXECUTE ROUTER ───────────────────────────────────────────────────────────

execute_router = APIRouter(prefix="/api/execute", tags=["execute"])


@execute_router.post("", response_model=ExecuteResponse)
async def execute_code(req: ExecuteRequest):
    """
    Sandboxed Python execution.
    Uses RestrictedPython — safe, no filesystem/network access.
    """
    result = execution_service.execute(req.code, stdin=req.stdin, timeout=req.timeout)
    return result


# ─── MENTOR ROUTER ────────────────────────────────────────────────────────────

mentor_router = APIRouter(prefix="/api/mentor", tags=["mentor"])


@mentor_router.post("/chat", response_model=MentorResponse)
async def mentor_chat(req: MentorRequest):
    """
    Socratic mentor chatbot.
    Uses Mistral-7B with system prompt that FORBIDS direct answers.
    Context-aware: reads current code and errors.
    """
    history = [{"role": m.role, "content": m.content} for m in req.history]
    result = await llm_service.mentor_reply(
        message=req.message,
        history=history,
        code=req.current_code,
        error=req.current_error,
    )
    return result


@mentor_router.post("/hint", response_model=ProblemHintResponse)
async def get_hint(req: ProblemHintRequest):
    """Get a Socratic hint for a practice problem at the specified level."""
    hint_resp = practice_service.get_hint(req.problem_id, req.hint_level, req.current_code)
    if not hint_resp:
        raise HTTPException(status_code=404, detail=f"Problem {req.problem_id} not found.")

    # If we have a live LLM backend, generate a contextual hint
    if llm_service.active_backend != "mock" and req.current_code:
        from backend.services.practice_service import PROBLEM_MAP
        problem = PROBLEM_MAP.get(req.problem_id)
        if problem:
            generated = await llm_service.generate_hint(
                problem_description=problem.description,
                current_code=req.current_code,
                hint_level=req.hint_level,
                lang=req.language,
            )
            hint_resp.hint = generated

    return hint_resp


# ─── PRACTICE ROUTER ──────────────────────────────────────────────────────────

practice_router = APIRouter(prefix="/api/practice", tags=["practice"])


@practice_router.get("/problems")
async def list_problems(
    category: str | None = Query(None),
    difficulty: str | None = Query(None),
):
    """List all practice problems, optionally filtered."""
    return {"problems": practice_service.list_problems(category, difficulty)}


@practice_router.get("/problems/{problem_id}")
async def get_problem(problem_id: int):
    """Get full problem details including starter code."""
    problem = practice_service.get_problem(problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"Problem {problem_id} not found.")
    return problem


@practice_router.post("/submit", response_model=SubmitSolutionResponse)
async def submit_solution(req: SubmitSolutionRequest):
    """
    Submit a solution for evaluation.
    Runs test cases, returns score, feedback, and skill XP updates.
    """
    result = practice_service.submit_solution(req.problem_id, req.code)
    logger.info(
        "solution_submitted",
        problem_id=req.problem_id,
        passed=result.passed,
        score=result.score,
    )
    return result


@practice_router.get("/categories")
async def get_categories():
    """Get all available problem categories and difficulties."""
    cats = list({p.category for p in PROBLEM_BANK})
    diffs = ["easy", "medium", "hard"]
    return {"categories": sorted(cats), "difficulties": diffs}
