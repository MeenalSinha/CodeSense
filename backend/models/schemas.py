"""
CodeSense — Shared Pydantic Models
All request/response schemas used across the API.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal, Any
from enum import Enum


# ─── ENUMS ────────────────────────────────────────────────────────────────────

class NodeType(str, Enum):
    START = "start"
    END = "end"
    ASSIGN = "assign"
    CONDITION = "condition"
    LOOP = "loop"
    FUNCTION_DEF = "function_def"
    FUNCTION_CALL = "function_call"
    RETURN = "return"
    OUTPUT = "output"
    IMPORT = "import"
    CLASS_DEF = "class_def"
    EXCEPTION = "exception"
    STATEMENT = "statement"
    BRANCH_TRUE = "branch_true"
    BRANCH_FALSE = "branch_false"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    SECURITY_VIOLATION = "security_violation"


class LLMBackend(str, Enum):
    OLLAMA = "ollama"
    HUGGINGFACE = "hf"
    MOCK = "mock"


# ─── GRAPH / AST MODELS ───────────────────────────────────────────────────────

class FlowNode(BaseModel):
    id: str
    type: NodeType
    label: str
    detail: str = ""
    color: str = "#4F6EF7"
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowEdge(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    label: str = ""
    conditional: bool = False

    class Config:
        populate_by_name = True


class ExecutionGraph(BaseModel):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    concepts: list[str] = Field(default_factory=list)
    complexity_score: int = Field(default=0, ge=0, le=100)
    line_count: int = 0
    has_loops: bool = False
    has_conditions: bool = False
    has_functions: bool = False
    has_classes: bool = False
    has_recursion: bool = False
    has_exceptions: bool = False


# ─── ANALYSIS REQUEST/RESPONSE ────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=10_000)
    language: str = Field(default="python")

    @field_validator("code")
    @classmethod
    def must_be_python_ish(cls, v: str) -> str:
        # Basic sanity — not enforcing hard block, just trimming
        return v.strip()


class AnalyzeResponse(BaseModel):
    graph: ExecutionGraph
    plain_english: str
    why_this_works: str
    concepts: list[str]
    skill_updates: dict[str, int] = Field(default_factory=dict)
    analysis_time_ms: float = 0.0


# ─── EXECUTION REQUEST/RESPONSE ───────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=10_000)
    stdin: str = ""
    timeout: int = Field(default=5, ge=1, le=10)


class ExecuteResponse(BaseModel):
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    execution_time_ms: float = 0.0
    memory_used_kb: int = 0


# ─── EXPLANATION (LLM) ────────────────────────────────────────────────────────

class ExplainRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=10_000)
    graph: Optional[ExecutionGraph] = None
    language: str = "en"  # "en" | "hi"
    explain_type: Literal["plain", "why", "both"] = "both"


class ExplainResponse(BaseModel):
    plain_english: str
    why_this_works: str
    concepts: list[str]
    llm_backend_used: str
    latency_ms: float = 0.0


# ─── MENTOR CHAT ─────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class MentorRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    current_code: str = ""
    current_error: str = ""
    problem_id: Optional[int] = None
    language: str = "en"


class MentorResponse(BaseModel):
    reply: str
    is_socratic: bool = True
    suggested_concepts: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0


# ─── PRACTICE ────────────────────────────────────────────────────────────────

class ProblemHintRequest(BaseModel):
    problem_id: int
    current_code: str = ""
    hint_level: int = Field(default=1, ge=1, le=3)
    language: str = "en"


class ProblemHintResponse(BaseModel):
    hint: str
    hint_level: int
    remaining_hints: int
    is_socratic: bool = True


class SubmitSolutionRequest(BaseModel):
    problem_id: int
    code: str = Field(..., min_length=1, max_length=10_000)


class TestResult(BaseModel):
    test_case: int
    passed: bool
    expected: str
    actual: str
    error: str = ""


class SubmitSolutionResponse(BaseModel):
    passed: bool
    score: int = Field(ge=0, le=100)
    test_results: list[TestResult]
    feedback: str
    skill_updates: dict[str, int] = Field(default_factory=dict)
    execution_time_ms: float = 0.0


# ─── WEBSOCKET MESSAGES ───────────────────────────────────────────────────────

class WSMessageType(str, Enum):
    ANALYZE = "analyze"
    ANALYZE_RESULT = "analyze_result"
    EXECUTE = "execute"
    EXECUTE_RESULT = "execute_result"
    MENTOR_CHAT = "mentor_chat"
    MENTOR_REPLY = "mentor_reply"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"


class WSMessage(BaseModel):
    type: WSMessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""


class WSErrorPayload(BaseModel):
    code: str
    message: str
    detail: str = ""


# ─── HEALTH ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    llm_backend: str
    llm_available: bool
    services: dict[str, bool] = Field(default_factory=dict)
