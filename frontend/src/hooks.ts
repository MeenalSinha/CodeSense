/**
 * CodeSense — React Hooks for Backend Integration
 * Provides:
 *   useCodeAnalysis  — debounced live analysis (graph + LLM)
 *   useCodeExecution — sandboxed execution
 *   useMentorChat    — Socratic mentor via WS
 *   useBackendHealth — LLM/service status indicator
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { api, wsClient, type AnalyzeResponse, type ExecuteResponse } from "./client";

// ─── DEBOUNCE HELPER ──────────────────────────────────────────────────────────

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

// ─── useCodeAnalysis ──────────────────────────────────────────────────────────
/**
 * Debounced live code analysis.
 * On each code change (after 600ms pause):
 *   1. Sends AST request → updates graph immediately
 *   2. LLM explanation arrives asynchronously
 */
export function useCodeAnalysis(code: string, language = "en") {
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendAvailable, setBackendAvailable] = useState(true);

  const debouncedCode = useDebounce(code, 600);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!debouncedCode.trim()) {
      setAnalysis(null);
      return;
    }

    abortRef.current?.abort();
    abortRef.current = new AbortController();

    setLoading(true);
    setError(null);

    api.analyze(debouncedCode, language)
      .then((result) => {
        setAnalysis(result);
        setBackendAvailable(true);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setError(err.message);
          setBackendAvailable(false);
          // Fallback: use mock client-side analysis
          console.warn("[CodeSense] Backend unavailable, using client-side fallback");
        }
      })
      .finally(() => setLoading(false));

    return () => abortRef.current?.abort();
  }, [debouncedCode, language]);

  return { analysis, loading, error, backendAvailable };
}

// ─── useCodeExecution ─────────────────────────────────────────────────────────
/**
 * Executes code via REST API (sandboxed).
 */
export function useCodeExecution() {
  const [result, setResult] = useState<ExecuteResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(async (code: string, stdin = "") => {
    if (!code.trim() || running) return;
    setRunning(true);
    setError(null);
    try {
      const res = await api.execute(code, stdin);
      setResult(res);
    } catch (err: any) {
      setError(err.message);
      // Fallback mock execution indicator
      setResult({
        status: "error",
        stdout: "",
        stderr: "Backend unavailable — using simulated execution.",
        execution_time_ms: 0,
      });
    } finally {
      setRunning(false);
    }
  }, [running]);

  return { result, running, error, execute };
}

// ─── useMentorChat ────────────────────────────────────────────────────────────
/**
 * Socratic mentor chat via REST.
 * Maintains conversation history.
 */
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export function useMentorChat(currentCode = "", currentError = "", language = "en") {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: "assistant",
      content: "👋 Hi! I'm your CodeSense mentor. I'll guide you with questions, not answers. What are you trying to understand?",
    },
  ]);
  const [loading, setLoading] = useState(false);

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || loading) return;

      const userMsg: ChatMessage = { role: "user", content: text };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);

      try {
        const history = messages.map((m) => ({ role: m.role, content: m.content }));
        const res = await api.mentor.chat(text, history, currentCode, currentError, language);
        setMessages((prev) => [...prev, { role: "assistant", content: res.reply }]);
      } catch {
        // Fallback: local Socratic rule-based response
        const fallback = generateFallbackReply(text, currentCode);
        setMessages((prev) => [...prev, { role: "assistant", content: fallback }]);
      } finally {
        setLoading(false);
      }
    },
    [messages, loading, currentCode, currentError, language]
  );

  const clearHistory = useCallback(() => {
    setMessages([{
      role: "assistant",
      content: "Fresh start! What would you like to explore?",
    }]);
  }, []);

  return { messages, loading, sendMessage, clearHistory };
}

// ─── useBackendHealth ──────────────────────────────────────────────────────────
/**
 * Polls backend health every 30s.
 * Shows LLM backend status (ollama / hf / mock).
 */
export function useBackendHealth() {
  const [health, setHealth] = useState<{
    available: boolean;
    llm_backend: string;
    llm_available: boolean;
  }>({ available: false, llm_backend: "unknown", llm_available: false });

  useEffect(() => {
    const check = () =>
      api.health()
        .then((h) => setHealth({ available: true, llm_backend: h.llm_backend, llm_available: h.llm_available }))
        .catch(() => setHealth((prev) => ({ ...prev, available: false })));

    check();
    const interval = setInterval(check, 30_000);
    return () => clearInterval(interval);
  }, []);

  return health;
}

// ─── FALLBACK (offline Socratic rules) ───────────────────────────────────────

function generateFallbackReply(question: string, code: string): string {
  const q = question.toLowerCase();
  if (q.includes("recursion")) return "Every recursive function needs a base case — what makes yours stop? 🌀";
  if (q.includes("loop")) return "What changes on each iteration? That changing thing is what the loop variable captures. 🔄";
  if (q.includes("error") || q.includes("bug")) return "What did you *expect* vs what *actually* happened? That gap is where the bug lives. 🔍";
  if (q.includes("return")) return "Think of a function like a vending machine. What does it give back? `return` is that thing. 🎰";
  if (q.includes("variable")) return "Variables are labelled boxes 📦. When you assign, what goes in the box? What happens to the old value?";
  if (code.includes("def ")) return "Can you trace through your function manually with small input? What does each line hold after it runs? 🧠";
  return "What's your current mental model? Stating your guess — even if wrong — helps us pinpoint the confusion. 🤔";
}
