/**
 * CodeSense Frontend API Client
 * Typed client for all REST endpoints and WebSocket protocol.
 * Works with the FastAPI backend at VITE_API_URL.
 */

const API_BASE = import.meta?.env?.VITE_API_URL || "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");

// ─── TYPES ────────────────────────────────────────────────────────────────────

export interface FlowNode {
  id: string;
  type: string;
  label: string;
  detail: string;
  color: string;
  line_start?: number;
  line_end?: number;
  metadata?: Record<string, unknown>;
}

export interface FlowEdge {
  from: string;
  to: string;
  label?: string;
  conditional?: boolean;
}

export interface ExecutionGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
  concepts: string[];
  complexity_score: number;
  line_count: number;
  has_loops: boolean;
  has_conditions: boolean;
  has_functions: boolean;
  has_classes: boolean;
  has_recursion: boolean;
  has_exceptions: boolean;
}

export interface AnalyzeResponse {
  graph: ExecutionGraph;
  plain_english: string;
  why_this_works: string;
  concepts: string[];
  skill_updates: Record<string, number>;
  analysis_time_ms: number;
}

export interface ExecuteResponse {
  status: "success" | "error" | "timeout" | "security_violation";
  stdout: string;
  stderr: string;
  execution_time_ms: number;
  memory_used_kb?: number;
}

export interface MentorResponse {
  reply: string;
  is_socratic: boolean;
  suggested_concepts?: string[];
  latency_ms: number;
}

export interface Problem {
  id: number;
  title: string;
  category: string;
  difficulty: "easy" | "medium" | "hard";
  description: string;
  starter_code: string;
  concept_tags: string[];
  hint_count: number;
  explanation?: string;
}

export interface SubmitResponse {
  passed: boolean;
  score: number;
  test_results: Array<{
    test_case: number;
    passed: boolean;
    expected: string;
    actual: string;
    error?: string;
  }>;
  feedback: string;
  skill_updates: Record<string, number>;
  execution_time_ms: number;
}

export interface HintResponse {
  hint: string;
  hint_level: number;
  remaining_hints: number;
  is_socratic: boolean;
}

// ─── HTTP CLIENT ─────────────────────────────────────────────────────────────

async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ─── REST API ─────────────────────────────────────────────────────────────────

export const api = {
  /** Full analysis: AST graph + LLM explanation */
  analyze: (code: string, language = "python"): Promise<AnalyzeResponse> =>
    apiRequest("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ code, language }),
    }),

  /** Fast graph-only (no LLM) */
  graphOnly: (code: string): Promise<{ graph: ExecutionGraph; concepts: string[] }> =>
    apiRequest("/api/analyze/graph", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  /** Sandboxed code execution */
  execute: (code: string, stdin = "", timeout = 5): Promise<ExecuteResponse> =>
    apiRequest("/api/execute", {
      method: "POST",
      body: JSON.stringify({ code, stdin, timeout }),
    }),

  /** LLM explanation (language: "en" | "hi") */
  explain: (code: string, language = "en"): Promise<{ plain_english: string; why_this_works: string; concepts: string[] }> =>
    apiRequest("/api/explain", {
      method: "POST",
      body: JSON.stringify({ code, language }),
    }),

  mentor: {
    /** Socratic mentor chat */
    chat: (
      message: string,
      history: Array<{ role: string; content: string }> = [],
      currentCode = "",
      currentError = "",
      language = "en"
    ): Promise<MentorResponse> =>
      apiRequest("/api/mentor/chat", {
        method: "POST",
        body: JSON.stringify({
          message,
          history,
          current_code: currentCode,
          current_error: currentError,
          language,
        }),
      }),

    /** Get hint for practice problem */
    hint: (
      problemId: number,
      hintLevel: number,
      currentCode = "",
      language = "en"
    ): Promise<HintResponse> =>
      apiRequest("/api/mentor/hint", {
        method: "POST",
        body: JSON.stringify({
          problem_id: problemId,
          hint_level: hintLevel,
          current_code: currentCode,
          language,
        }),
      }),
  },

  practice: {
    /** List problems (optionally filtered) */
    list: (category?: string, difficulty?: string): Promise<{ problems: Problem[] }> => {
      const params = new URLSearchParams();
      if (category) params.set("category", category);
      if (difficulty) params.set("difficulty", difficulty);
      return apiRequest(`/api/practice/problems?${params}`);
    },

    /** Get full problem with starter code */
    get: (problemId: number): Promise<Problem> =>
      apiRequest(`/api/practice/problems/${problemId}`),

    /** Submit solution for evaluation */
    submit: (problemId: number, code: string): Promise<SubmitResponse> =>
      apiRequest("/api/practice/submit", {
        method: "POST",
        body: JSON.stringify({ problem_id: problemId, code }),
      }),

    /** Available categories */
    categories: (): Promise<{ categories: string[]; difficulties: string[] }> =>
      apiRequest("/api/practice/categories"),
  },

  /** Health check */
  health: (): Promise<{ status: string; llm_backend: string; llm_available: boolean }> =>
    apiRequest("/api/health"),
};


// ─── WEBSOCKET CLIENT ─────────────────────────────────────────────────────────

type WSHandler = (payload: Record<string, unknown>, requestId: string) => void;

export class CodeSenseWS {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, WSHandler[]>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 2000;
  private pingInterval: ReturnType<typeof setInterval> | null = null;
  private _connected = false;
  private requestCounter = 0;

  constructor(private url = `${WS_BASE}/ws`) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this._connected = true;
        this.reconnectDelay = 2000;
        this._startPing();
        resolve();
      };

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          const type: string = msg.type;
          const payload = msg.payload || {};
          const requestId: string = msg.request_id || "";
          (this.handlers.get(type) || []).forEach((h) => h(payload, requestId));
        } catch (e) {
          console.error("[CodeSenseWS] parse error", e);
        }
      };

      this.ws.onclose = () => {
        this._connected = false;
        this._stopPing();
        this._scheduleReconnect();
      };

      this.ws.onerror = (err) => {
        reject(err);
      };
    });
  }

  on(type: string, handler: WSHandler): () => void {
    if (!this.handlers.has(type)) this.handlers.set(type, []);
    this.handlers.get(type)!.push(handler);
    return () => {
      const list = this.handlers.get(type) || [];
      const idx = list.indexOf(handler);
      if (idx >= 0) list.splice(idx, 1);
    };
  }

  send(type: string, payload: Record<string, unknown> = {}): string {
    const requestId = `req_${++this.requestCounter}_${Date.now()}`;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, payload, request_id: requestId }));
    }
    return requestId;
  }

  // Convenience senders
  analyze(code: string, language = "en"): string {
    return this.send("analyze", { code, language });
  }

  execute(code: string, stdin = "", timeout = 5): string {
    return this.send("execute", { code, stdin, timeout });
  }

  mentorChat(
    message: string,
    history: Array<{ role: string; content: string }> = [],
    currentCode = "",
    currentError = ""
  ): string {
    return this.send("mentor_chat", { message, history, current_code: currentCode, current_error: currentError });
  }

  get connected(): boolean {
    return this._connected;
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this._stopPing();
    this.ws?.close();
    this.ws = null;
  }

  private _startPing() {
    this.pingInterval = setInterval(() => {
      this.send("ping");
    }, 20_000);
  }

  private _stopPing() {
    if (this.pingInterval) clearInterval(this.pingInterval);
  }

  private _scheduleReconnect() {
    this.reconnectTimer = setTimeout(async () => {
      try {
        await this.connect();
      } catch {
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30_000);
        this._scheduleReconnect();
      }
    }, this.reconnectDelay);
  }
}

// Singleton WebSocket instance
export const wsClient = new CodeSenseWS();
