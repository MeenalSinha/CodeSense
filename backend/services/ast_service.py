"""
CodeSense — AST Analysis Service
Converts Python source code into a structured execution graph (JSON)
using Python's built-in `ast` module for real static analysis.

Graph nodes represent control flow elements.
Graph edges represent execution paths.
This powers the flowchart and explanation pipeline.
"""
from __future__ import annotations

import ast
import time
from typing import Optional
from dataclasses import dataclass, field

from backend.models.schemas import (
    ExecutionGraph, FlowNode, FlowEdge, NodeType,
)
from backend.core.config import logger


# ─── COLOUR PALETTE (matches frontend) ────────────────────────────────────────
NODE_COLORS = {
    NodeType.START: "#22C55E",
    NodeType.END: "#EF4444",
    NodeType.ASSIGN: "#4F6EF7",
    NodeType.CONDITION: "#F59E0B",
    NodeType.LOOP: "#F59E0B",
    NodeType.FUNCTION_DEF: "#A855F7",
    NodeType.FUNCTION_CALL: "#4F6EF7",
    NodeType.RETURN: "#A855F7",
    NodeType.OUTPUT: "#4F6EF7",
    NodeType.IMPORT: "#8B90A0",
    NodeType.CLASS_DEF: "#A855F7",
    NodeType.EXCEPTION: "#EF4444",
    NodeType.STATEMENT: "#4F6EF7",
    NodeType.BRANCH_TRUE: "#22C55E",
    NodeType.BRANCH_FALSE: "#EF4444",
}

CONCEPT_EXPLANATIONS = {
    "variables":    "Variables store named values that your program can reference and modify.",
    "loops":        "Loops repeat a block of code — automating repetition without copy-pasting.",
    "conditions":   "Conditions let your program take different paths based on what's true at runtime.",
    "functions":    "Functions package reusable logic — write once, call many times.",
    "classes":      "Classes are blueprints for objects — bundling data and behaviour together.",
    "recursion":    "Recursion solves problems by having a function call itself with a simpler input.",
    "exceptions":   "Exception handling lets your program recover gracefully from errors.",
    "imports":      "Imports bring in external modules — Python's ecosystem at your fingertips.",
    "comprehensions":"List/dict comprehensions are concise, readable ways to transform data.",
    "lambdas":      "Lambdas are anonymous functions — useful for short, one-off transformations.",
    "generators":   "Generators produce values lazily — efficient for large data streams.",
    "decorators":   "Decorators wrap functions to add behaviour without changing their source.",
    "context_managers": "Context managers handle setup/teardown automatically (e.g. opening files).",
}


@dataclass
class _BuildState:
    """Mutable state threaded through the AST walk."""
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    counter: int = 0
    concepts: set[str] = field(default_factory=set)
    has_loops: bool = False
    has_conditions: bool = False
    has_functions: bool = False
    has_classes: bool = False
    has_recursion: bool = False
    has_exceptions: bool = False
    defined_functions: set[str] = field(default_factory=set)

    def next_id(self) -> str:
        self.counter += 1
        return f"n{self.counter}"

    def add_node(
        self,
        ntype: NodeType,
        label: str,
        detail: str = "",
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        metadata: dict = None,
    ) -> FlowNode:
        node = FlowNode(
            id=self.next_id(),
            type=ntype,
            label=label[:40],
            detail=detail[:80],
            color=NODE_COLORS.get(ntype, "#4F6EF7"),
            line_start=line_start,
            line_end=line_end,
            metadata=metadata or {},
        )
        self.nodes.append(node)
        return node

    def add_edge(self, from_id: str, to_id: str, label: str = "", conditional: bool = False) -> FlowEdge:
        edge = FlowEdge(**{"from": from_id, "to": to_id, "label": label, "conditional": conditional})
        self.edges.append(edge)
        return edge


def _unparse_safe(node: ast.AST) -> str:
    """Safe ast.unparse with length capping."""
    try:
        return ast.unparse(node)[:60]
    except Exception:
        return str(type(node).__name__)


def _process_body(
    stmts: list[ast.stmt],
    state: _BuildState,
    entry_id: str,
) -> str:
    """
    Process a list of statements, threading control flow.
    Returns the id of the last node in this block.
    """
    prev_id = entry_id

    for stmt in stmts:
        prev_id = _process_stmt(stmt, state, prev_id)

    return prev_id


def _process_stmt(stmt: ast.stmt, state: _BuildState, prev_id: str) -> str:
    """Dispatch on statement type and return the exit node id."""

    # ── Import ──────────────────────────────────────────────────────────────
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        state.concepts.add("imports")
        names = ", ".join(
            alias.name for alias in stmt.names
        ) if hasattr(stmt, "names") else ""
        module = getattr(stmt, "module", "") or ""
        label = f"import {module or names}"
        node = state.add_node(
            NodeType.IMPORT, label,
            f"Loads: {names[:50]}",
            line_start=stmt.lineno,
        )
        state.add_edge(prev_id, node.id)
        return node.id

    # ── Assignment ────────────────────────────────────────────────────────
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        state.concepts.add("variables")
        try:
            if isinstance(stmt, ast.Assign):
                targets = " = ".join(_unparse_safe(t) for t in stmt.targets)
                value = _unparse_safe(stmt.value)
                label = f"{targets} = {value}"
                detail = f"Assigns {value} to {targets}"
            elif isinstance(stmt, ast.AnnAssign):
                label = f"{_unparse_safe(stmt.target)}: {_unparse_safe(stmt.annotation)}"
                detail = "Annotated assignment"
            else:
                op_map = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=", ast.Div: "/="}
                op = op_map.get(type(stmt.op), "op=")
                label = f"{_unparse_safe(stmt.target)} {op} {_unparse_safe(stmt.value)}"
                detail = "Augmented assignment"
        except Exception:
            label = "assignment"
            detail = ""

        # Detect list/dict comprehensions
        try:
            val = stmt.value if isinstance(stmt, ast.Assign) else None
            if val and isinstance(val, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
                state.concepts.add("comprehensions")
        except Exception:
            pass

        node = state.add_node(NodeType.ASSIGN, label, detail, line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    # ── Function Definition ───────────────────────────────────────────────
    if isinstance(stmt, ast.FunctionDef) or isinstance(stmt, ast.AsyncFunctionDef):
        state.concepts.add("functions")
        state.has_functions = True
        state.defined_functions.add(stmt.name)

        args = ast.unparse(stmt.args) if hasattr(ast, "unparse") else ""
        decorators = [_unparse_safe(d) for d in stmt.decorator_list]
        if decorators:
            state.concepts.add("decorators")

        fn_node = state.add_node(
            NodeType.FUNCTION_DEF,
            f"def {stmt.name}({args[:30]})",
            f"Lines {stmt.lineno}–{stmt.end_lineno}  |  {len(stmt.body)} statements",
            line_start=stmt.lineno,
            line_end=stmt.end_lineno,
            metadata={"name": stmt.name, "args": args, "decorators": decorators},
        )
        state.add_edge(prev_id, fn_node.id)

        # Recurse into body — detect self-calls (recursion)
        for node in ast.walk(ast.Module(body=stmt.body, type_ignores=[])):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name == stmt.name:
                    state.has_recursion = True
                    state.concepts.add("recursion")

        body_exit = _process_body(stmt.body, state, fn_node.id)
        return fn_node.id  # caller continues from fn_node (definition, not call)

    # ── Class Definition ──────────────────────────────────────────────────
    if isinstance(stmt, ast.ClassDef):
        state.concepts.add("classes")
        state.has_classes = True
        bases = ", ".join(_unparse_safe(b) for b in stmt.bases)
        node = state.add_node(
            NodeType.CLASS_DEF,
            f"class {stmt.name}({bases[:20]})",
            f"{len([s for s in stmt.body if isinstance(s, ast.FunctionDef)])} methods",
            line_start=stmt.lineno,
            line_end=stmt.end_lineno,
        )
        state.add_edge(prev_id, node.id)
        _process_body(stmt.body, state, node.id)
        return node.id

    # ── Return ────────────────────────────────────────────────────────────
    if isinstance(stmt, ast.Return):
        value = _unparse_safe(stmt.value) if stmt.value else "None"
        node = state.add_node(NodeType.RETURN, f"return {value}", "Exits function", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    # ── If / Elif / Else ─────────────────────────────────────────────────
    if isinstance(stmt, ast.If):
        state.concepts.add("conditions")
        state.has_conditions = True
        cond = _unparse_safe(stmt.test)
        cond_node = state.add_node(
            NodeType.CONDITION,
            f"if {cond}",
            "Evaluates condition",
            line_start=stmt.lineno,
        )
        state.add_edge(prev_id, cond_node.id)

        # True branch
        true_entry = state.add_node(NodeType.BRANCH_TRUE, "True →", "Condition is met")
        state.add_edge(cond_node.id, true_entry.id, label="yes", conditional=True)
        true_exit = _process_body(stmt.body, state, true_entry.id)

        # False / elif branch
        if stmt.orelse:
            false_entry = state.add_node(NodeType.BRANCH_FALSE, "False →", "Condition not met")
            state.add_edge(cond_node.id, false_entry.id, label="no", conditional=True)
            false_exit = _process_body(stmt.orelse, state, false_entry.id)
            # Merge point
            merge = state.add_node(NodeType.STATEMENT, "↓ merge", "Both branches continue")
            state.add_edge(true_exit, merge.id)
            state.add_edge(false_exit, merge.id)
            return merge.id
        else:
            merge = state.add_node(NodeType.STATEMENT, "↓ continue", "")
            state.add_edge(cond_node.id, merge.id, label="no", conditional=True)
            state.add_edge(true_exit, merge.id)
            return merge.id

    # ── For Loop ──────────────────────────────────────────────────────────
    if isinstance(stmt, ast.For):
        state.concepts.add("loops")
        state.has_loops = True
        target = _unparse_safe(stmt.target)
        iter_ = _unparse_safe(stmt.iter)
        loop_node = state.add_node(
            NodeType.LOOP,
            f"for {target} in {iter_}",
            "Iterates over sequence",
            line_start=stmt.lineno,
            line_end=stmt.end_lineno,
        )
        state.add_edge(prev_id, loop_node.id)

        body_exit = _process_body(stmt.body, state, loop_node.id)
        # Back edge (loop)
        state.add_edge(body_exit, loop_node.id, label="next iteration")

        # Exit after loop
        after = state.add_node(NodeType.STATEMENT, "↓ after loop", "Loop complete")
        state.add_edge(loop_node.id, after.id, label="done")
        return after.id

    # ── While Loop ────────────────────────────────────────────────────────
    if isinstance(stmt, ast.While):
        state.concepts.add("loops")
        state.has_loops = True
        cond = _unparse_safe(stmt.test)
        loop_node = state.add_node(
            NodeType.LOOP,
            f"while {cond}",
            "Repeats while true",
            line_start=stmt.lineno,
            line_end=stmt.end_lineno,
        )
        state.add_edge(prev_id, loop_node.id)

        body_exit = _process_body(stmt.body, state, loop_node.id)
        state.add_edge(body_exit, loop_node.id, label="repeat")

        after = state.add_node(NodeType.STATEMENT, "↓ after loop", "Condition false, exit")
        state.add_edge(loop_node.id, after.id, label="false")
        return after.id

    # ── Try / Except ─────────────────────────────────────────────────────
    if isinstance(stmt, ast.Try):
        state.concepts.add("exceptions")
        state.has_exceptions = True
        try_node = state.add_node(NodeType.EXCEPTION, "try block", "Protected section", line_start=stmt.lineno)
        state.add_edge(prev_id, try_node.id)

        try_exit = _process_body(stmt.body, state, try_node.id)

        for handler in stmt.handlers:
            exc_name = handler.type.id if handler.type and isinstance(handler.type, ast.Name) else "Exception"
            exc_node = state.add_node(NodeType.EXCEPTION, f"except {exc_name}", "Handles error")
            state.add_edge(try_node.id, exc_node.id, label="error", conditional=True)
            _process_body(handler.body, state, exc_node.id)

        if stmt.finalbody:
            finally_node = state.add_node(NodeType.STATEMENT, "finally", "Always runs")
            state.add_edge(try_exit, finally_node.id)
            return finally_node.id

        return try_exit

    # ── With Statement ────────────────────────────────────────────────────
    if isinstance(stmt, ast.With):
        state.concepts.add("context_managers")
        items = ", ".join(_unparse_safe(item.context_expr) for item in stmt.items)
        node = state.add_node(NodeType.STATEMENT, f"with {items[:30]}", "Context manager", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        exit_id = _process_body(stmt.body, state, node.id)
        return exit_id

    # ── Expr (print, function calls, etc.) ───────────────────────────────
    if isinstance(stmt, ast.Expr):
        expr = stmt.value
        if isinstance(expr, ast.Call):
            func_str = _unparse_safe(expr.func)

            # Detect print()
            if func_str in ("print", "sys.stdout.write"):
                state.concepts.add("output")
                args_str = ", ".join(_unparse_safe(a) for a in expr.args)
                node = state.add_node(
                    NodeType.OUTPUT,
                    f"print({args_str[:30]})",
                    "Writes to stdout",
                    line_start=stmt.lineno,
                )
            else:
                # Generic function call
                args_str = ", ".join(_unparse_safe(a) for a in expr.args)
                node = state.add_node(
                    NodeType.FUNCTION_CALL,
                    f"{func_str}({args_str[:25]})",
                    "Function call",
                    line_start=stmt.lineno,
                )

            # Check if known-defined function (recursion via call)
            if isinstance(expr.func, ast.Name) and expr.func.id in state.defined_functions:
                pass  # normal call

            state.add_edge(prev_id, node.id)
            return node.id

        # Lambda, etc.
        if isinstance(expr, ast.Lambda):
            state.concepts.add("lambdas")

        label = _unparse_safe(expr)
        node = state.add_node(NodeType.STATEMENT, label, "", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    # ── Yield / Yield From ────────────────────────────────────────────────
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
        state.concepts.add("generators")

    # ── Pass / Break / Continue ───────────────────────────────────────────
    if isinstance(stmt, ast.Pass):
        node = state.add_node(NodeType.STATEMENT, "pass", "No-op placeholder", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    if isinstance(stmt, ast.Break):
        node = state.add_node(NodeType.STATEMENT, "break", "Exit loop early", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    if isinstance(stmt, ast.Continue):
        node = state.add_node(NodeType.STATEMENT, "continue", "Skip to next iteration", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    # ── Raise ──────────────────────────────────────────────────────────────
    if isinstance(stmt, ast.Raise):
        exc = _unparse_safe(stmt.exc) if stmt.exc else "Exception"
        node = state.add_node(NodeType.EXCEPTION, f"raise {exc}", "Throws exception", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    # ── Delete ────────────────────────────────────────────────────────────
    if isinstance(stmt, ast.Delete):
        targets = ", ".join(_unparse_safe(t) for t in stmt.targets)
        node = state.add_node(NodeType.STATEMENT, f"del {targets}", "Removes variable(s)", line_start=stmt.lineno)
        state.add_edge(prev_id, node.id)
        return node.id

    # ── Fallback ──────────────────────────────────────────────────────────
    label = type(stmt).__name__
    node = state.add_node(NodeType.STATEMENT, label, "", getattr(stmt, "lineno", None))
    state.add_edge(prev_id, node.id)
    return node.id


def _compute_complexity(state: _BuildState, line_count: int) -> int:
    """
    Rough cyclomatic-inspired complexity score 0–100.
    """
    score = 10  # base
    score += min(30, line_count // 3)
    score += 10 if state.has_loops else 0
    score += 10 if state.has_conditions else 0
    score += 10 if state.has_functions else 0
    score += 10 if state.has_classes else 0
    score += 15 if state.has_recursion else 0
    score += 5 if state.has_exceptions else 0
    return min(100, score)


class ASTAnalyzer:
    """
    Main entry point for converting Python source → ExecutionGraph.
    Thread-safe; stateless across calls.
    """

    def analyze(self, source_code: str) -> ExecutionGraph:
        t0 = time.perf_counter()

        # Parse
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            logger.warning("ast_parse_failed", error=str(exc))
            return self._error_graph(str(exc))

        state = _BuildState()

        # Entry node
        start_node = state.add_node(NodeType.START, "START", "Program begins")
        last_id = _process_body(tree.body, state, start_node.id)

        # Exit node
        end_node = state.add_node(NodeType.END, "END", "Program complete")
        state.add_edge(last_id, end_node.id)

        lines = [l for l in source_code.splitlines() if l.strip() and not l.strip().startswith("#")]
        line_count = len(lines)
        complexity = _compute_complexity(state, line_count)

        elapsed = (time.perf_counter() - t0) * 1000
        logger.debug("ast_analysis_complete", nodes=len(state.nodes), edges=len(state.edges), ms=round(elapsed, 2))

        return ExecutionGraph(
            nodes=state.nodes,
            edges=state.edges,
            concepts=sorted(state.concepts),
            complexity_score=complexity,
            line_count=line_count,
            has_loops=state.has_loops,
            has_conditions=state.has_conditions,
            has_functions=state.has_functions,
            has_classes=state.has_classes,
            has_recursion=state.has_recursion,
            has_exceptions=state.has_exceptions,
        )

    @staticmethod
    def _error_graph(message: str) -> ExecutionGraph:
        nodes = [
            FlowNode(id="n1", type=NodeType.START, label="START", color="#22C55E"),
            FlowNode(id="n2", type=NodeType.EXCEPTION, label="SyntaxError", detail=message[:60], color="#EF4444"),
            FlowNode(id="n3", type=NodeType.END, label="END", color="#EF4444"),
        ]
        edges = [
            FlowEdge(**{"from": "n1", "to": "n2"}),
            FlowEdge(**{"from": "n2", "to": "n3"}),
        ]
        return ExecutionGraph(nodes=nodes, edges=edges)

    @staticmethod
    def concepts_to_skill_updates(concepts: list[str], base_xp: int = 5) -> dict[str, int]:
        """Map detected concepts to skill XP increments."""
        skill_map = {
            "variables": "variables",
            "loops": "loops",
            "conditions": "conditions",
            "functions": "functions",
            "classes": "classes",
            "recursion": "recursion",
            "exceptions": "exceptions",
            "comprehensions": "comprehensions",
            "lambdas": "lambdas",
        }
        return {skill_map[c]: base_xp for c in concepts if c in skill_map}


# Singleton instance
ast_analyzer = ASTAnalyzer()
