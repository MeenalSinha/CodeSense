# 🧠 CodeSense — Backend & Full-Stack Setup

> *"CodeSense turns vibe coding into real Python understanding."*

## Architecture

```
codesense/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── core/
│   │   └── config.py              # Settings, logging (pydantic-settings)
│   ├── models/
│   │   └── schemas.py             # All Pydantic request/response models
│   ├── routers/
│   │   ├── api.py                 # REST endpoints (analyze, execute, mentor, practice)
│   │   └── websocket.py           # WS endpoint (real-time analysis + chat)
│   └── services/
│       ├── ast_service.py         # Python AST → ExecutionGraph (flowchart data)
│       ├── llm_service.py         # Mistral-7B via Ollama / HuggingFace / Mock
│       ├── execution_service.py   # RestrictedPython sandboxed runner
│       └── practice_service.py    # Problem bank, hints, submission evaluation
├── src/
│   └── api/
│       ├── client.ts              # Typed REST + WebSocket client
│       └── hooks.ts               # React hooks (useCodeAnalysis, useMentorChat…)
├── requirements.txt
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

---

## Quick Start

### Option A — Local (no Docker)

```bash
# 1. Clone and install
cd codesense
pip install -r requirements.txt

# 2. (Optional) Start Ollama with Mistral-7B
# Install Ollama from https://ollama.ai
ollama pull mistral:7b-instruct
ollama serve

# 3. Configure
cp .env.example .env
# Edit LLM_BACKEND=ollama (or =mock for demo without GPU)

# 4. Run backend
python backend/main.py
# → API running at http://localhost:8000
# → Docs at http://localhost:8000/api/docs
```

### Option B — Docker Compose (recommended for demo)

```bash
docker compose up --build
# Pulls Mistral-7B automatically on first run (~4GB)
# Backend: http://localhost:8000
```

### Option C — Demo Mode (no GPU, no LLM)

```bash
# Set LLM_BACKEND=mock in .env
# Uses intelligent rule-based Socratic responses
# All other features (AST, execution, flowchart) work fully
LLM_BACKEND=mock python backend/main.py
```

---

## API Reference

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | Backend + LLM status |
| `POST` | `/api/analyze` | Full analysis: AST graph + LLM explanation |
| `POST` | `/api/analyze/graph` | Fast graph-only (no LLM) |
| `POST` | `/api/execute` | Sandboxed Python execution |
| `POST` | `/api/explain` | LLM explanation (en/hi) |
| `POST` | `/api/mentor/chat` | Socratic mentor chat |
| `POST` | `/api/mentor/hint` | Practice problem hint |
| `GET`  | `/api/practice/problems` | List problems (filterable) |
| `GET`  | `/api/practice/problems/{id}` | Problem detail + starter code |
| `POST` | `/api/practice/submit` | Submit solution + get score |

### WebSocket — `ws://localhost:8000/ws`

**Message format:**
```json
{ "type": "...", "payload": {...}, "request_id": "req_1" }
```

| Type | Direction | Description |
|------|-----------|-------------|
| `analyze` | → | Send code, get graph + explanation |
| `analyze_result` | ← | AST graph (fast), then LLM (deferred) |
| `execute` | → | Run code in sandbox |
| `execute_result` | ← | stdout, stderr, timing |
| `mentor_chat` | → | Ask mentor a question |
| `mentor_reply` | ← | Socratic response |
| `ping/pong` | ↔ | Keepalive |

---

## LLM Integration: Mistral-7B

### Priority Chain

```
1. Ollama (local)       ← Best: fully on-device, no API keys
2. HuggingFace API      ← Fallback: cloud inference
3. Mock (rule-based)    ← Demo mode: no GPU required
```

### System Prompts

All LLM calls use carefully crafted system prompts that:
- **FORBID** direct code solutions
- Enforce the **Socratic method** (questions → understanding)
- Keep responses **under 150 words** (optimized for UI)
- Context-inject **current code** and **detected concepts**

---

## AST Analysis Engine

The `ast_service.py` converts Python source to a typed `ExecutionGraph`:

```python
graph = ast_analyzer.analyze("""
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
""")

# Returns:
# ExecutionGraph(
#   nodes=[START, def factorial(), IF n<=1, RETURN 1, RETURN n*factorial(), END],
#   edges=[...connected flow...],
#   concepts=["functions", "conditions", "recursion"],
#   has_recursion=True,
#   complexity_score=55,
# )
```

**Node types detected:** start, end, assign, condition, loop, function_def, function_call, return, output, import, class_def, exception, statement, branch_true, branch_false

---

## Sandboxed Execution

`execution_service.py` uses **RestrictedPython** with:

- ✅ Safe builtins only (`print`, `range`, `len`, `int`, `str`, etc.)
- ✅ Safe imports only (`math`, `random`, `collections`, `itertools`, etc.)
- ❌ Blocked: `os`, `sys`, `subprocess`, `socket`, `open`, `eval`, `exec`
- ⏱️ Hard timeout (default 5s) via threading
- 🔍 Static AST scan before compilation

---

## Connecting Frontend to Backend

In your React app, import the hooks:

```typescript
import { useCodeAnalysis, useCodeExecution, useMentorChat } from "./api/hooks";

function IDE() {
  const [code, setCode] = useState("");
  const { analysis, loading } = useCodeAnalysis(code);          // live graph + explanations
  const { result, running, execute } = useCodeExecution();       // sandboxed run
  const { messages, sendMessage } = useMentorChat(code);         // Socratic chat

  // analysis.graph.nodes  → power your Flowchart component
  // analysis.plain_english → Explain tab
  // analysis.why_this_works → Why tab
  // analysis.skill_updates  → Skill meter XP
}
```

Or use the low-level typed client directly:

```typescript
import { api, wsClient } from "./api/client";

// REST
const result = await api.execute(code);
const analysis = await api.analyze(code);

// WebSocket (real-time)
await wsClient.connect();
wsClient.on("analyze_result", (payload) => {
  if (payload.phase === "graph") updateFlowchart(payload.graph);
  if (payload.phase === "explanation") updateSidebar(payload.plain_english);
});
wsClient.analyze(code); // triggers both phases
```

---

## Practice Problem Bank

8 problems across 4 categories:

| # | Title | Category | Difficulty |
|---|-------|----------|------------|
| 1 | Swap Without Temp | Variables | Easy |
| 2 | FizzBuzz | Conditions | Easy |
| 3 | Recursive Sum | Functions | Medium |
| 4 | Grade Classifier | Conditions | Easy |
| 5 | Count Vowels | Loops | Easy |
| 6 | Flatten Nested List | Functions | Medium |
| 7 | Binary Search | Functions | Hard |
| 8 | Caesar Cipher | Functions | Medium |

Each has 3 Socratic hints (vague → specific), test cases, and skill XP rewards.

---

## Key Design Decisions

1. **Two-phase analysis** — AST graph sent immediately (<10ms), LLM explanation async (500ms–3s). Frontend updates in two passes, feeling instant.

2. **Mock LLM fallback** — `LLM_BACKEND=mock` gives intelligent rule-based responses. Judges can see full UI without needing GPU.

3. **WebSocket + REST** — Both available. WS for live typing (debounced), REST for explicit actions (run, submit).

4. **Socratic enforcement at the prompt level** — The LLM literally cannot answer directly because the system prompt forbids it and demonstrates the method.

5. **RestrictedPython over subprocess** — Lower overhead, faster startup, no container escape risk for simple educational code.
