"""
CodeSense — Python Execution Service (Sandboxed)

Executes user Python code safely using:
  - AST-based static security scan (blocks dangerous names/imports/attrs)
  - Plain compile() + exec() with a hand-crafted restricted globals dict
  - print() captured to a StringIO buffer
  - Thread-based timeout

No dependency on RestrictedPython — plain stdlib only.
"""
from __future__ import annotations

import ast
import io
import time
import threading
import traceback
from typing import Any

from backend.core.config import get_settings, logger
from backend.models.schemas import ExecuteResponse, ExecutionStatus


# ─── MODULE ALLOW / BLOCK LISTS ───────────────────────────────────────────────

BLOCKED_IMPORTS = frozenset({
    "os", "sys", "subprocess", "socket", "urllib", "http", "requests",
    "ftplib", "telnetlib", "smtplib", "shutil", "pathlib", "glob",
    "importlib", "pkgutil", "ctypes", "cffi", "gc", "threading",
    "multiprocessing", "signal", "mmap", "resource",
    "pickle", "shelve", "marshal", "sqlite3", "zipfile", "tarfile",
    "builtins", "__builtins__",
})

SAFE_IMPORTS = frozenset({
    "math", "random", "statistics", "decimal", "fractions",
    "collections", "itertools", "functools", "operator",
    "string", "re", "textwrap",
    "datetime", "time", "calendar",
    "json", "csv",
    "typing", "dataclasses", "enum", "abc",
    "copy", "pprint",
})


# ─── SAFE IMPORT GATE ─────────────────────────────────────────────────────────

def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0) -> Any:
    base = name.split(".")[0]
    if base in BLOCKED_IMPORTS:
        raise ImportError(
            f"Module '{name}' is not available in the CodeSense sandbox.\n"
            f"Allowed: {', '.join(sorted(SAFE_IMPORTS))}"
        )
    if base not in SAFE_IMPORTS:
        raise ImportError(
            f"Module '{name}' is not allowed in the sandbox.\n"
            f"Allowed: {', '.join(sorted(SAFE_IMPORTS))}"
        )
    return __import__(name, globals, locals, fromlist, level)


# ─── SAFE GLOBALS BUILDER ─────────────────────────────────────────────────────

def _build_safe_globals(stdout_buf: io.StringIO) -> dict:
    """
    Returns a clean globals dict for exec().
    print() is redirected to stdout_buf.
    All dangerous builtins are excluded.
    """

    def safe_print(*args, sep=" ", end="\n", file=None, flush=False):
        stdout_buf.write(sep.join(str(a) for a in args) + end)

    def safe_range(*args):
        r = range(*args)
        if len(r) > 100_000:
            raise ValueError("range() too large — max 100,000 steps in sandbox.")
        return r

    def safe_input(prompt=""):
        safe_print(str(prompt), end="")
        return ""

    safe_builtins = {
        "print": safe_print,
        "input": safe_input,
        "int": int, "float": float, "str": str, "bool": bool,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "frozenset": frozenset, "bytes": bytes, "bytearray": bytearray,
        "complex": complex,
        "len": len, "type": type,
        "isinstance": isinstance, "issubclass": issubclass,
        "hasattr": hasattr, "getattr": getattr,
        "callable": callable, "repr": repr, "id": id, "hash": hash,
        "range": safe_range,
        "enumerate": enumerate, "zip": zip,
        "map": map, "filter": filter,
        "reversed": reversed, "sorted": sorted,
        "iter": iter, "next": next,
        "min": min, "max": max, "sum": sum, "abs": abs,
        "round": round, "pow": pow, "divmod": divmod,
        "all": all, "any": any,
        "chr": chr, "ord": ord,
        "hex": hex, "oct": oct, "bin": bin,
        "format": format,
        "Exception": Exception, "ValueError": ValueError,
        "TypeError": TypeError, "IndexError": IndexError,
        "KeyError": KeyError, "AttributeError": AttributeError,
        "NameError": NameError, "ZeroDivisionError": ZeroDivisionError,
        "StopIteration": StopIteration, "RuntimeError": RuntimeError,
        "NotImplementedError": NotImplementedError, "OverflowError": OverflowError,
        "ImportError": ImportError, "OSError": OSError,
        "AssertionError": AssertionError, "RecursionError": RecursionError,
        "__build_class__": __build_class__,
        "__name__": "__main__",
        "__import__": _safe_import,
    }

    return {"__builtins__": safe_builtins}


# ─── EXECUTION SERVICE ────────────────────────────────────────────────────────

class ExecutionService:

    def __init__(self):
        self.settings = get_settings()

    def execute(self, code: str, stdin: str = "", timeout: int | None = None) -> ExecuteResponse:
        t0 = time.perf_counter()
        timeout = timeout or self.settings.execution_timeout_seconds

        # 1. Static security scan
        security_error = self._static_scan(code)
        if security_error:
            return ExecuteResponse(
                status=ExecutionStatus.SECURITY_VIOLATION,
                stderr=security_error,
                execution_time_ms=(time.perf_counter() - t0) * 1000,
            )

        # 2. Compile
        try:
            byte_code = compile(code, "<codesense>", "exec")
        except SyntaxError as exc:
            return ExecuteResponse(
                status=ExecutionStatus.ERROR,
                stderr=f"SyntaxError on line {exc.lineno}: {exc.msg}",
                execution_time_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            return ExecuteResponse(
                status=ExecutionStatus.ERROR,
                stderr=f"Compile error: {exc}",
                execution_time_ms=(time.perf_counter() - t0) * 1000,
            )

        # 3. Execute in thread with timeout
        stdout_buf = io.StringIO()
        safe_globals = _build_safe_globals(stdout_buf)
        exception_holder: list[Exception] = []

        def _run():
            try:
                exec(byte_code, safe_globals)  # noqa: S102
            except Exception as exc:
                exception_holder.append(exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # 4. Timeout?
        if thread.is_alive():
            return ExecuteResponse(
                status=ExecutionStatus.TIMEOUT,
                stderr=f"Timed out after {timeout}s — check for infinite loops.",
                execution_time_ms=elapsed_ms,
            )

        # 5. Runtime error?
        if exception_holder:
            exc = exception_holder[0]
            tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
            # Only show user code frames, hide internal ones
            user_lines = [l for l in tb_lines if "<codesense>" in l]
            error_type = type(exc).__name__
            error_detail = str(exc)
            if user_lines:
                error_msg = "".join(user_lines).strip() + f"\n{error_type}: {error_detail}"
            else:
                error_msg = f"{error_type}: {error_detail}"
            return ExecuteResponse(
                status=ExecutionStatus.ERROR,
                stdout=stdout_buf.getvalue()[:self.settings.max_output_chars],
                stderr=error_msg[:1000],
                execution_time_ms=elapsed_ms,
            )

        # 6. Success
        output = stdout_buf.getvalue()
        if len(output) > self.settings.max_output_chars:
            output = output[:self.settings.max_output_chars] + "\n… (output truncated)"

        return ExecuteResponse(
            status=ExecutionStatus.SUCCESS,
            stdout=output,
            stderr="",
            execution_time_ms=elapsed_ms,
        )

    @staticmethod
    def _static_scan(code: str) -> str | None:
        """
        AST walk to block dangerous patterns before execution.
        Returns an error string if a violation is found, else None.
        """
        BLOCKED_CALLS = {"eval", "exec", "compile", "breakpoint", "__import__"}
        BLOCKED_ATTRS = {
            "__class__", "__bases__", "__subclasses__", "__mro__",
            "__dict__", "__globals__", "__code__", "__func__",
            "__builtins__", "__loader__", "__spec__",
        }

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
                    return f"Security: '{node.func.id}()' is not allowed in the sandbox."

            if isinstance(node, ast.Import):
                for alias in node.names:
                    base = alias.name.split(".")[0]
                    if base in BLOCKED_IMPORTS:
                        return f"Security: import of '{alias.name}' is not allowed."

            if isinstance(node, ast.ImportFrom):
                base = (node.module or "").split(".")[0]
                if base in BLOCKED_IMPORTS:
                    return f"Security: import from '{node.module}' is not allowed."

            if isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRS:
                return f"Security: attribute '{node.attr}' is restricted."

        return None


# Singleton
execution_service = ExecutionService()
