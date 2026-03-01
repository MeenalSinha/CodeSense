"""
CodeSense — LLM Service (Mistral-7B)
Handles all AI inference for:
  - Code explanation (plain English)
  - "Why this works" reasoning
  - Socratic mentor hints (never direct answers)

Backends (in priority order):
  1. Ollama  — local Mistral-7B-Instruct (preferred, on-device)
  2. HuggingFace Inference API — fallback if Ollama unavailable
  3. Mock  — deterministic rule-based responses for demo/offline mode

The service NEVER outputs direct code solutions.
All prompts enforce the Socratic method.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncIterator

import httpx

from backend.core.config import get_settings, logger
from backend.models.schemas import (
    ExecutionGraph, ExplainResponse, MentorResponse, LLMBackend,
)


# ─── SYSTEM PROMPTS ────────────────────────────────────────────────────────────

SYSTEM_EXPLAIN = """You are CodeSense, an expert Python educator. Your job is to explain Python code in plain English.

Rules:
- Never give full code solutions
- Use simple, beginner-friendly language
- Focus on WHAT the code does and WHY it's written that way
- Mention key concepts used
- Keep explanations under 150 words
- Respond ONLY with valid JSON: {"plain_english": "...", "why_this_works": "...", "concepts": [...]}"""

SYSTEM_MENTOR = """You are a Socratic Python mentor for CodeSense. You NEVER directly solve problems.

Your method:
- Ask guiding questions that make students think
- Reveal ONE insight at a time, never the full solution
- Reference the student's actual code when relevant
- Encourage them to reason about execution step-by-step
- If they're stuck, give a conceptual analogy, not code
- Keep responses under 120 words
- Be warm, patient, and encouraging

FORBIDDEN:
- Writing working solution code
- Saying "here's how to fix it: [code]"
- Giving away the answer directly"""

SYSTEM_EXPLAIN_HI = """आप CodeSense हैं, एक Python शिक्षक। Python code को सरल हिंदी में समझाएं।

नियम:
- पूरा code solution कभी न दें
- सरल भाषा का उपयोग करें
- बताएं कि code क्या करता है और क्यों
- JSON में जवाब दें: {"plain_english": "...", "why_this_works": "...", "concepts": [...]}"""


# ─── MOCK LLM (rule-based fallback) ───────────────────────────────────────────

class MockLLM:
    """
    Deterministic rule-based responses for demo / offline mode.
    Simulates Mistral-7B behavior using AST analysis output.
    """

    def explain(self, code: str, graph: ExecutionGraph | None, lang: str = "en") -> ExplainResponse:
        concepts = graph.concepts if graph else self._detect_concepts(code)
        has_loop = graph.has_loops if graph else "for" in code or "while" in code
        has_cond = graph.has_conditions if graph else "if" in code
        has_fn = graph.has_functions if graph else "def " in code
        has_cls = graph.has_classes if graph else "class " in code
        has_rec = graph.has_recursion if graph else False

        parts = []
        if has_fn: parts.append("defines reusable functions")
        if has_cls: parts.append("defines classes (object blueprints)")
        if has_loop: parts.append("uses loops to repeat operations")
        if has_cond: parts.append("branches based on conditions")
        if "import" in code: parts.append("imports external modules")
        if "print" in code: parts.append("outputs results to the console")
        if not parts: parts.append("runs a sequence of Python statements")

        plain = f"This code {', '.join(parts)}."

        why_parts = []
        if has_fn:
            why_parts.append("Functions let you write logic once and call it many times — reducing repetition and making code testable.")
        if has_loop:
            why_parts.append("Loops automate repetition — instead of writing the same code N times, you write it once and let Python repeat it.")
        if has_cond:
            why_parts.append("Conditions let your program make decisions — different execution paths activate based on what's true at runtime.")
        if has_cls:
            why_parts.append("Classes bundle related data and behaviour together — making complex systems easier to reason about.")
        if has_rec:
            why_parts.append("Recursion solves problems by breaking them into identical smaller sub-problems — elegant but requires a careful base case.")
        if not why_parts:
            why_parts.append("Python executes your code top-to-bottom. Each statement runs in sequence, building on what came before.")

        if lang == "hi":
            plain = f"यह code {', '.join(parts)} करता है।"
            why_parts = ["Python code ऊपर से नीचे execute होता है। " + " ".join(why_parts[:1])]

        return ExplainResponse(
            plain_english=plain,
            why_this_works=" ".join(why_parts),
            concepts=concepts,
            llm_backend_used="mock",
        )

    def mentor_reply(self, message: str, code: str, error: str) -> str:
        msg = message.lower()

        if error and ("error" in msg or "bug" in msg or "wrong" in msg or "fix" in msg):
            return (
                f"I see there's an error: `{error[:60]}`. Before I help, "
                "let me ask: what did you *expect* the code to do? "
                "And what is it actually doing differently? "
                "That gap between expectation and reality is exactly where the bug lives. 🔍"
            )
        if "recursion" in msg or "recursive" in msg:
            return (
                "Recursion can feel magical! Here's a key question: "
                "every recursive function calls itself — but it must *stop* at some point. "
                "What do you think happens if it never stops? "
                "And what condition should make it stop? That's your base case. 🌀"
            )
        if "loop" in msg:
            return (
                "Great loop question! Consider: what would happen if you had to write "
                "the same operation 100 times *without* a loop? "
                "Now — what exactly changes on each iteration? That changing thing "
                "is what the loop variable captures. What is it in your code? 🔄"
            )
        if "why" in msg and ("return" in msg or "returns" in msg):
            return (
                "Think of a function like a vending machine 🎰. "
                "You put something in (arguments), it processes, and gives something back. "
                "`return` is what it gives back. "
                "What do you think happens if you call a function but never `return`? "
                "What value would the caller receive?"
            )
        if "list" in msg or "index" in msg:
            return (
                "Lists are ordered sequences — like a numbered shelf. 📦 "
                "Python starts counting at 0, not 1. So `my_list[0]` is the *first* item. "
                "Can you predict what `my_list[-1]` gives? Think about it before checking!"
            )
        if "variable" in msg or "assign" in msg:
            return (
                "Variables are like labelled boxes. 📦 "
                "When Python sees `x = 5`, it creates a box labelled 'x' and puts 5 inside. "
                "Later when you write `x = 10`, what happens to the old 5? "
                "Does the box get a new label, or does the value inside change?"
            )
        if "class" in msg or "object" in msg:
            return (
                "Classes are blueprints, objects are the actual things built from them. 🏗️ "
                "Think of a cookie cutter (class) and the cookies it makes (objects). "
                "In your code — what data does this class store, and what can it *do*? "
                "What would be different about each instance?"
            )
        if "hint" in msg or "stuck" in msg or "help" in msg:
            return (
                "Let's break this down. What's the *simplest possible* version of this problem "
                "you could solve? For example, if you need to sum a list, can you first "
                "write code that sums just two numbers? Start tiny, then expand. "
                "What's your first smallest step? 🪜"
            )
        if code and ("def " in code or "class " in code):
            return (
                "I can see your code. Before running it, let me ask: "
                "can you trace through it *manually* with a simple example? "
                "Pick small input values and follow each line. "
                "What value would each variable hold after line 1? Line 2? "
                "This mental simulation is the most powerful debugging skill you can build. 🧠"
            )

        return (
            f"Interesting question! Rather than explaining directly, "
            "let me ask: what's your current mental model of how this works? "
            "Even a rough guess helps us find exactly where the understanding breaks down. "
            "What do you think happens step-by-step? 🤔"
        )

    @staticmethod
    def _detect_concepts(code: str) -> list[str]:
        concepts = []
        if "=" in code and "==" not in code: concepts.append("variables")
        if "for " in code or "while " in code: concepts.append("loops")
        if "if " in code: concepts.append("conditions")
        if "def " in code: concepts.append("functions")
        if "class " in code: concepts.append("classes")
        if "import" in code: concepts.append("imports")
        if "print(" in code: concepts.append("output")
        return concepts


# ─── OLLAMA CLIENT ─────────────────────────────────────────────────────────────

class OllamaLLM:
    """Calls local Ollama server running Mistral-7B-Instruct."""

    def __init__(self, base_url: str, model: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def _chat(self, system: str, user_prompt: str, max_tokens: int = 512, temp: float = 0.4) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temp,
                "num_predict": max_tokens,
                "stop": ["</s>", "[INST]", "[/INST]"],
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"].strip()

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                models = resp.json().get("models", [])
                return any(self.model.split(":")[0] in m.get("name", "") for m in models)
        except Exception:
            return False

    async def explain(self, code: str, graph: ExecutionGraph | None, lang: str = "en") -> ExplainResponse:
        t0 = time.perf_counter()
        system = SYSTEM_EXPLAIN_HI if lang == "hi" else SYSTEM_EXPLAIN
        concepts_hint = f"Detected concepts: {', '.join(graph.concepts)}" if graph else ""
        prompt = f"```python\n{code}\n```\n{concepts_hint}\n\nExplain this code."

        raw = await self._chat(system, prompt)

        # Extract JSON from response
        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group() if match else raw)
            return ExplainResponse(
                plain_english=data.get("plain_english", raw[:200]),
                why_this_works=data.get("why_this_works", ""),
                concepts=data.get("concepts", graph.concepts if graph else []),
                llm_backend_used="ollama",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception:
            return ExplainResponse(
                plain_english=raw[:300],
                why_this_works="",
                concepts=graph.concepts if graph else [],
                llm_backend_used="ollama",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    async def mentor_reply(
        self,
        message: str,
        history: list[dict],
        code: str,
        error: str,
    ) -> str:
        t0 = time.perf_counter()
        context_parts = []
        if code:
            context_parts.append(f"Student's current code:\n```python\n{code[:800]}\n```")
        if error:
            context_parts.append(f"Current error: {error[:200]}")
        context = "\n".join(context_parts)

        messages = [{"role": "system", "content": SYSTEM_MENTOR}]
        if context:
            messages.append({"role": "system", "content": context})

        for msg in history[-6:]:  # last 6 exchanges
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": message})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.6, "num_predict": 200},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()


# ─── HUGGINGFACE INFERENCE API CLIENT ──────────────────────────────────────────

class HuggingFaceLLM:
    """
    Uses HuggingFace Inference API as a fallback.
    Requires HF_API_TOKEN env variable.
    """

    def __init__(self, model_id: str, api_token: str, timeout: float):
        self.model_id = model_id
        self.api_token = api_token
        self.timeout = timeout
        self.api_url = f"https://api-inference.huggingface.co/models/{model_id}"

    async def _query(self, prompt: str, max_tokens: int = 400) -> str:
        headers = {"Authorization": f"Bearer {self.api_token}"}
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": max_tokens,
                "temperature": 0.4,
                "do_sample": True,
                "return_full_text": False,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.api_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data[0].get("generated_text", "").strip()
            return str(data)

    async def is_available(self) -> bool:
        return bool(self.api_token)

    async def explain(self, code: str, graph: ExecutionGraph | None, lang: str = "en") -> ExplainResponse:
        t0 = time.perf_counter()
        concepts = ", ".join(graph.concepts) if graph else ""
        prompt = (
            f"[INST] {SYSTEM_EXPLAIN}\n\n"
            f"Code:\n```python\n{code[:1000]}\n```\n"
            f"Detected: {concepts}\n[/INST]"
        )
        raw = await self._query(prompt)
        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group() if match else "{}")
            return ExplainResponse(
                plain_english=data.get("plain_english", raw[:200]),
                why_this_works=data.get("why_this_works", ""),
                concepts=data.get("concepts", graph.concepts if graph else []),
                llm_backend_used="huggingface",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception:
            return ExplainResponse(
                plain_english=raw[:300] or "Explanation unavailable.",
                why_this_works="",
                concepts=graph.concepts if graph else [],
                llm_backend_used="huggingface",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    async def mentor_reply(self, message: str, history: list[dict], code: str, error: str) -> str:
        ctx = ""
        if code:
            ctx += f"\nStudent code:\n```python\n{code[:500]}\n```"
        if error:
            ctx += f"\nError: {error[:100]}"
        prompt = f"[INST] {SYSTEM_MENTOR}{ctx}\n\nStudent: {message} [/INST]"
        return await self._query(prompt, max_tokens=200)


# ─── LLM SERVICE (orchestrator) ────────────────────────────────────────────────

class LLMService:
    """
    Orchestrates backend selection:
    Ollama → HuggingFace → Mock (degradation chain)
    """

    def __init__(self):
        settings = get_settings()
        self._mock = MockLLM()
        self._ollama = OllamaLLM(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.llm_timeout,
        )
        self._hf = HuggingFaceLLM(
            model_id=settings.hf_model_id,
            api_token=settings.hf_api_token,
            timeout=settings.llm_timeout,
        )
        self._preferred = settings.llm_backend
        self._active_backend: str = "mock"
        self._checked = False

    async def _resolve_backend(self) -> str:
        if self._checked:
            return self._active_backend
        self._checked = True

        if self._preferred == "mock":
            self._active_backend = "mock"
            return "mock"

        if self._preferred in ("ollama", "auto"):
            if await self._ollama.is_available():
                self._active_backend = "ollama"
                logger.info("llm_backend_selected", backend="ollama", model=self._ollama.model)
                return "ollama"

        if self._preferred in ("hf", "auto"):
            if await self._hf.is_available():
                self._active_backend = "huggingface"
                logger.info("llm_backend_selected", backend="huggingface")
                return "huggingface"

        logger.info("llm_backend_selected", backend="mock", reason="no_live_backend")
        self._active_backend = "mock"
        return "mock"

    async def explain(
        self,
        code: str,
        graph: ExecutionGraph | None = None,
        lang: str = "en",
    ) -> ExplainResponse:
        t0 = time.perf_counter()
        backend = await self._resolve_backend()
        try:
            if backend == "ollama":
                result = await self._ollama.explain(code, graph, lang)
            elif backend == "huggingface":
                result = await self._hf.explain(code, graph, lang)
            else:
                result = self._mock.explain(code, graph, lang)
            result.latency_ms = (time.perf_counter() - t0) * 1000
            return result
        except Exception as exc:
            logger.warning("llm_explain_failed", backend=backend, error=str(exc))
            result = self._mock.explain(code, graph, lang)
            result.latency_ms = (time.perf_counter() - t0) * 1000
            return result

    async def mentor_reply(
        self,
        message: str,
        history: list[dict] | None = None,
        code: str = "",
        error: str = "",
    ) -> MentorResponse:
        t0 = time.perf_counter()
        backend = await self._resolve_backend()
        history = history or []
        try:
            if backend == "ollama":
                reply = await self._ollama.mentor_reply(message, history, code, error)
            elif backend == "huggingface":
                reply = await self._hf.mentor_reply(message, history, code, error)
            else:
                reply = self._mock.mentor_reply(message, code, error)
        except Exception as exc:
            logger.warning("llm_mentor_failed", backend=backend, error=str(exc))
            reply = self._mock.mentor_reply(message, code, error)

        return MentorResponse(
            reply=reply,
            is_socratic=True,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    async def generate_hint(
        self,
        problem_description: str,
        current_code: str,
        hint_level: int,
        lang: str = "en",
    ) -> str:
        """Generate a Socratic hint for a practice problem."""
        level_guidance = {
            1: "Give a very vague conceptual nudge — just point them in the right direction.",
            2: "Give a slightly more specific hint about the approach, but no code.",
            3: "Give a concrete structural hint (e.g. 'think about what happens when the list is empty') — still no solution code.",
        }
        guidance = level_guidance.get(hint_level, level_guidance[1])

        system = f"{SYSTEM_MENTOR}\n\nHint level {hint_level}/3: {guidance}"
        message = (
            f"Problem: {problem_description[:300]}\n\n"
            f"Student's code so far:\n```python\n{current_code[:500]}\n```\n\n"
            f"Give a level-{hint_level} Socratic hint. No solution code."
        )
        backend = await self._resolve_backend()
        try:
            if backend == "ollama":
                return await self._ollama.mentor_reply(message, [], current_code, "")
            elif backend == "huggingface":
                return await self._hf.mentor_reply(message, [], current_code, "")
        except Exception:
            pass
        return self._mock.mentor_reply(f"hint for {problem_description[:50]}", current_code, "")

    @property
    def active_backend(self) -> str:
        return self._active_backend


# Singleton
llm_service = LLMService()
