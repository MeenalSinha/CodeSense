"""
CodeSense — Practice Problems Service
Manages the problem bank, evaluates submissions, and delivers Socratic hints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import time

from backend.models.schemas import (
    SubmitSolutionResponse, TestResult, ProblemHintResponse,
    ExecutionStatus,
)
from backend.services.execution_service import execution_service
from backend.core.config import logger


@dataclass
class TestCase:
    input: str
    expected_output: str
    description: str = ""


@dataclass
class Problem:
    id: int
    title: str
    category: str          # Variables | Loops | Conditions | Functions | Classes
    difficulty: str        # easy | medium | hard
    description: str
    starter_code: str
    test_cases: list[TestCase]
    hints: list[str]       # Ordered from vague → specific (max 3)
    concept_tags: list[str]
    explanation: str = ""  # What this problem teaches
    forbidden_patterns: list[str] = field(default_factory=list)  # e.g. ["sum(", "sorted("] for challenges


# ─── PROBLEM BANK ─────────────────────────────────────────────────────────────

PROBLEM_BANK: list[Problem] = [

    Problem(
        id=1,
        title="Swap Without Temp Variable",
        category="Variables",
        difficulty="easy",
        description=(
            "Given two variables `a = 5` and `b = 10`, swap their values without using "
            "a third temporary variable. Python has an elegant one-liner for this."
        ),
        starter_code="a = 5\nb = 10\n\n# Swap a and b here (no temp variable!)\n\n\nprint(f'a = {a}, b = {b}')",
        test_cases=[
            TestCase(input="", expected_output="a = 10, b = 5"),
        ],
        hints=[
            "Python supports assigning multiple values at once on a single line. What if both sides of `=` had two values?",
            "Think about tuple packing and unpacking: `x, y = y, x` — Python evaluates the right side first.",
            "The answer is exactly one line: `a, b = b, a`. Why does this work without a temp? Python creates a temporary tuple on the right side.",
        ],
        concept_tags=["variables", "tuple-unpacking", "assignment"],
        explanation="Demonstrates Python's parallel assignment — a key idiomatic feature that beginners often miss.",
    ),

    Problem(
        id=2,
        title="FizzBuzz",
        category="Conditions",
        difficulty="easy",
        description=(
            "Print numbers 1–20. But: for multiples of 3 print 'Fizz', "
            "for multiples of 5 print 'Buzz', and for multiples of both print 'FizzBuzz'."
        ),
        starter_code="for i in range(1, 21):\n    # Your condition logic here\n    pass",
        test_cases=[
            TestCase(
                input="",
                expected_output="1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz\n16\n17\nFizz\n19\nBuzz",
            ),
        ],
        hints=[
            "The modulo operator `%` gives the remainder of division. What does `15 % 3` equal? What does `15 % 5` equal?",
            "The ORDER of your if/elif/else matters. Check for FizzBuzz (divisible by BOTH) BEFORE checking Fizz or Buzz alone.",
            "Structure: `if i % 15 == 0: print('FizzBuzz')` — then `elif i % 3 == 0` — then `elif i % 5 == 0` — then `else`.",
        ],
        concept_tags=["loops", "conditions", "modulo", "elif"],
        explanation="A classic that teaches condition ordering and the modulo operator.",
    ),

    Problem(
        id=3,
        title="Recursive Sum",
        category="Functions",
        difficulty="medium",
        description=(
            "Write a recursive function `sum_list(lst)` that returns the sum of all "
            "integers in a list WITHOUT using Python's built-in `sum()` function."
        ),
        starter_code="def sum_list(lst):\n    # Base case: what's the sum of an empty list?\n    # Recursive case: first element + sum of the rest\n    pass\n\nprint(sum_list([1, 2, 3, 4, 5]))  # Expected: 15\nprint(sum_list([]))               # Expected: 0\nprint(sum_list([7]))              # Expected: 7",
        test_cases=[
            TestCase(input="", expected_output="15\n0\n7"),
        ],
        hints=[
            "Every recursive function needs TWO things: a base case (when to stop) and a recursive case (how to get simpler). What is the simplest possible input to `sum_list`?",
            "The sum of an empty list is 0. The sum of [1,2,3] is 1 + sum([2,3]). Can you express `sum_list(lst)` in terms of `lst[0]` and `sum_list(lst[1:])`?",
            "Base case: `if not lst: return 0`. Recursive case: `return lst[0] + sum_list(lst[1:])`. Trace through sum_list([1,2,3]) manually — what call stack does Python build?",
        ],
        concept_tags=["functions", "recursion", "lists", "base-case"],
        forbidden_patterns=["sum("],
        explanation="Introduces recursive thinking — breaking a problem into a base case and a self-similar sub-problem.",
    ),

    Problem(
        id=4,
        title="Grade Classifier",
        category="Conditions",
        difficulty="easy",
        description=(
            "Write a function `grade(score)` that returns the letter grade: "
            "'A' (90+), 'B' (80-89), 'C' (70-79), 'D' (60-69), 'F' (below 60)."
        ),
        starter_code="def grade(score):\n    # Use if/elif/else\n    pass\n\nprint(grade(95))   # A\nprint(grade(83))   # B\nprint(grade(72))   # C\nprint(grade(61))   # D\nprint(grade(45))   # F",
        test_cases=[
            TestCase(input="", expected_output="A\nB\nC\nD\nF"),
        ],
        hints=[
            "Use an if/elif/else chain. The `>=` operator means 'greater than or equal to'. What condition identifies a score of 90 or above?",
            "Order matters: once Python finds a TRUE condition, it skips the rest. Start from the highest grade and work down.",
            "Structure: `if score >= 90: return 'A'` then `elif score >= 80: return 'B'` and so on, ending with `else: return 'F'`.",
        ],
        concept_tags=["functions", "conditions", "comparison", "elif"],
        explanation="Reinforces if/elif/else chaining and the importance of condition ordering.",
    ),

    Problem(
        id=5,
        title="Count Vowels",
        category="Loops",
        difficulty="easy",
        description=(
            "Write a function `count_vowels(text)` that returns the number of vowels "
            "(a, e, i, o, u — both upper and lower case) in a string."
        ),
        starter_code="def count_vowels(text):\n    # Iterate over characters and count\n    pass\n\nprint(count_vowels('Hello World'))   # 3\nprint(count_vowels('Python'))        # 1\nprint(count_vowels('aeiou'))         # 5",
        test_cases=[
            TestCase(input="", expected_output="3\n1\n5"),
        ],
        hints=[
            "You can iterate over a string character by character with a for loop: `for char in text`. What would you check for each character?",
            "Create a string of all vowels: `vowels = 'aeiouAEIOU'`. Then check `if char in vowels`. The `in` operator checks membership.",
            "Use a counter variable starting at 0. Increment it when you find a vowel. Return the counter at the end.",
        ],
        concept_tags=["loops", "strings", "conditions", "counting"],
        explanation="Practices string iteration, the `in` operator, and the counter pattern.",
    ),

    Problem(
        id=6,
        title="Flatten Nested List",
        category="Functions",
        difficulty="medium",
        description=(
            "Write a function `flatten(nested)` that takes a list of lists and returns "
            "a single flat list with all elements. Do NOT use itertools."
        ),
        starter_code="def flatten(nested):\n    # nested is a list of lists\n    pass\n\nprint(flatten([[1, 2], [3, 4], [5]]))      # [1, 2, 3, 4, 5]\nprint(flatten([[10], [], [20, 30]]))       # [10, 20, 30]",
        test_cases=[
            TestCase(input="", expected_output="[1, 2, 3, 4, 5]\n[10, 20, 30]"),
        ],
        hints=[
            "You need to loop through the outer list AND each inner list. Two nested for-loops work here.",
            "Start with an empty result list. For each sublist in `nested`, loop over its items and append them to result.",
            "Or use a list comprehension: `[item for sublist in nested for item in sublist]` — read it as: for each sublist, for each item in that sublist.",
        ],
        concept_tags=["loops", "lists", "functions", "comprehensions"],
        explanation="Teaches nested iteration and the list-building pattern.",
    ),

    Problem(
        id=7,
        title="Binary Search",
        category="Functions",
        difficulty="hard",
        description=(
            "Implement `binary_search(arr, target)` that searches a SORTED list for target. "
            "Return the index if found, else -1. Must run in O(log n) time."
        ),
        starter_code="def binary_search(arr, target):\n    left, right = 0, len(arr) - 1\n    \n    while left <= right:\n        # Calculate mid, compare, adjust bounds\n        pass\n    \n    return -1\n\nprint(binary_search([1, 3, 5, 7, 9, 11], 7))   # 3\nprint(binary_search([1, 3, 5, 7, 9, 11], 4))   # -1\nprint(binary_search([2], 2))                    # 0",
        test_cases=[
            TestCase(input="", expected_output="3\n-1\n0"),
        ],
        hints=[
            "Binary search works by repeatedly halving the search space. Calculate `mid = (left + right) // 2`. If `arr[mid] == target`, you found it!",
            "If `arr[mid] < target`, the target must be in the RIGHT half — so `left = mid + 1`. If `arr[mid] > target`, it's in the LEFT half — so `right = mid - 1`.",
            "The loop continues while `left <= right`. When `left > right`, the search space is empty — return -1.",
        ],
        concept_tags=["functions", "loops", "algorithms", "binary-search"],
        explanation="Introduces algorithmic thinking and the divide-and-conquer approach.",
    ),

    Problem(
        id=8,
        title="Caesar Cipher",
        category="Functions",
        difficulty="medium",
        description=(
            "Write `caesar(text, shift)` that encrypts a string using Caesar cipher: "
            "shift each letter forward by `shift` positions (wraps around Z→A). "
            "Non-letters remain unchanged."
        ),
        starter_code="def caesar(text, shift):\n    result = ''\n    for char in text:\n        # Handle uppercase, lowercase, and non-letters\n        pass\n    return result\n\nprint(caesar('Hello, World!', 3))  # Khoor, Zruog!\nprint(caesar('Python', 1))         # Qzuipo",
        test_cases=[
            TestCase(input="", expected_output="Khoor, Zruog!\nQzuipo"),
        ],
        hints=[
            "`ord()` gives the ASCII code of a character, `chr()` converts back. `ord('A')` is 65, `ord('Z')` is 90. How would you shift 'Y' by 3 without going past 'Z'?",
            "Use modulo to wrap around: `(ord(char) - ord('A') + shift) % 26 + ord('A')` gives the shifted uppercase letter.",
            "Handle uppercase and lowercase separately using `char.isupper()` and `char.islower()`. Leave other characters (spaces, punctuation) unchanged.",
        ],
        concept_tags=["functions", "strings", "loops", "modulo", "ascii"],
        explanation="Combines string manipulation, ASCII arithmetic, and modular wrap-around.",
    ),
]

PROBLEM_MAP: dict[int, Problem] = {p.id: p for p in PROBLEM_BANK}


# ─── PRACTICE SERVICE ─────────────────────────────────────────────────────────

class PracticeService:

    def list_problems(
        self,
        category: str | None = None,
        difficulty: str | None = None,
    ) -> list[dict]:
        results = PROBLEM_BANK
        if category and category.lower() != "all":
            results = [p for p in results if p.category.lower() == category.lower()]
        if difficulty:
            results = [p for p in results if p.difficulty.lower() == difficulty.lower()]
        return [self._problem_summary(p) for p in results]

    def get_problem(self, problem_id: int) -> dict | None:
        p = PROBLEM_MAP.get(problem_id)
        if not p:
            return None
        return {
            "id": p.id,
            "title": p.title,
            "category": p.category,
            "difficulty": p.difficulty,
            "description": p.description,
            "starter_code": p.starter_code,
            "concept_tags": p.concept_tags,
            "explanation": p.explanation,
            "hint_count": len(p.hints),
        }

    def get_hint(self, problem_id: int, hint_level: int, current_code: str = "") -> ProblemHintResponse | None:
        p = PROBLEM_MAP.get(problem_id)
        if not p:
            return None
        hint_level = max(1, min(hint_level, len(p.hints)))
        hint = p.hints[hint_level - 1]
        return ProblemHintResponse(
            hint=hint,
            hint_level=hint_level,
            remaining_hints=len(p.hints) - hint_level,
            is_socratic=True,
        )

    def submit_solution(self, problem_id: int, code: str) -> SubmitSolutionResponse:
        t0 = time.perf_counter()
        p = PROBLEM_MAP.get(problem_id)
        if not p:
            return SubmitSolutionResponse(
                passed=False, score=0, test_results=[],
                feedback="Problem not found.",
                execution_time_ms=0,
            )

        # Check forbidden patterns
        for pattern in p.forbidden_patterns:
            if pattern in code:
                return SubmitSolutionResponse(
                    passed=False, score=0, test_results=[],
                    feedback=f"Challenge: You used `{pattern}` which is not allowed for this problem. Try implementing it from scratch!",
                    execution_time_ms=0,
                )

        test_results: list[TestResult] = []
        all_passed = True

        for i, tc in enumerate(p.test_cases, 1):
            # Inject test code
            full_code = code + "\n" + self._make_test_driver(p, tc)
            result = execution_service.execute(full_code, stdin=tc.input)

            actual = result.stdout.strip()
            expected = tc.expected_output.strip()
            passed = actual == expected and result.status == ExecutionStatus.SUCCESS

            if not passed:
                all_passed = False

            error_msg = result.stderr if result.status != ExecutionStatus.SUCCESS else ""
            test_results.append(TestResult(
                test_case=i,
                passed=passed,
                expected=expected,
                actual=actual if result.status == ExecutionStatus.SUCCESS else f"ERROR: {error_msg[:100]}",
                error=error_msg[:200],
            ))

        passed_count = sum(1 for r in test_results if r.passed)
        score = int((passed_count / len(test_results)) * 100) if test_results else 0

        feedback = self._generate_feedback(all_passed, passed_count, len(test_results), p)
        skill_updates = {}
        if all_passed:
            xp = {"easy": 15, "medium": 25, "hard": 40}.get(p.difficulty, 15)
            skill_updates = {tag: xp for tag in p.concept_tags}

        return SubmitSolutionResponse(
            passed=all_passed,
            score=score,
            test_results=test_results,
            feedback=feedback,
            skill_updates=skill_updates,
            execution_time_ms=(time.perf_counter() - t0) * 1000,
        )

    @staticmethod
    def _make_test_driver(problem: Problem, tc: TestCase) -> str:
        """The test code is already embedded in starter_code print calls — just run it."""
        return ""  # starter already includes print statements for testing

    @staticmethod
    def _generate_feedback(passed: bool, passed_count: int, total: int, p: Problem) -> str:
        if passed:
            return (
                f"🎉 All {total} test(s) passed! Great work on '{p.title}'. "
                f"You've demonstrated understanding of: {', '.join(p.concept_tags)}. "
                f"Challenge: can you think of an alternative approach?"
            )
        if passed_count == 0:
            return (
                f"Not there yet — 0/{total} tests passed. "
                "Look at your output vs the expected output carefully. "
                "Try tracing through your code manually with a small example. "
                "What does each line actually do?"
            )
        return (
            f"Good progress! {passed_count}/{total} tests passed. "
            "Some edge cases are failing. Think about: what happens with empty input? "
            "What about single-element inputs? Edge cases reveal gaps in logic."
        )

    @staticmethod
    def _problem_summary(p: Problem) -> dict:
        return {
            "id": p.id,
            "title": p.title,
            "category": p.category,
            "difficulty": p.difficulty,
            "concept_tags": p.concept_tags,
            "hint_count": len(p.hints),
        }


# Singleton
practice_service = PracticeService()
