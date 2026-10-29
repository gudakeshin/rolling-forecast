"""Custom KPI formulas: a small, safe arithmetic language over P&L references.

    [Gross Margin] / [Revenue]
    ([category:Revenue] - [category:COGS]) / [category:Revenue]
    [4000-100] * 12

Grammar (no eval, no names other than bracketed references)::

    expr   := term (('+' | '-') term)*
    term   := unary (('*' | '/') unary)*
    unary  := '-' unary | atom
    atom   := NUMBER | '[' reference ']' | '(' expr ')'

A reference is ``[item:<name or account code>]``, ``[category:<name>]``, or a
bare ``[<name>]`` that resolves to a line item (by name or account code) and
otherwise to a category. Resolution against a company's chart of accounts is
done by the caller (app/services/kpis.py); this module only parses and
evaluates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

MAX_FORMULA_LENGTH = 500
MAX_REFERENCES = 50
_MAX_DEPTH = 40

_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+")


@dataclass(frozen=True)
class Ref:
    """A bracketed reference. ``kind`` is "item", "category" or "any" (bare)."""

    kind: str
    name: str

    def __str__(self) -> str:
        return f"[{self.name}]" if self.kind == "any" else f"[{self.kind}:{self.name}]"


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Neg:
    operand: "Node"


@dataclass(frozen=True)
class BinOp:
    op: str
    left: "Node"
    right: "Node"


Node = Union[Ref, Num, Neg, BinOp]


class FormulaError(ValueError):
    """A formula that can't be parsed or resolved."""


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.depth = 0

    def parse(self) -> Node:
        node = self._expr()
        self._skip_ws()
        if self.pos != len(self.text):
            raise FormulaError(f"Unexpected '{self.text[self.pos]}' at position {self.pos + 1}")
        return node

    def _skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str:
        self._skip_ws()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _expr(self) -> Node:
        node = self._term()
        while self._peek() in ("+", "-"):
            op = self.text[self.pos]
            self.pos += 1
            node = BinOp(op, node, self._term())
        return node

    def _term(self) -> Node:
        node = self._unary()
        while self._peek() in ("*", "/"):
            op = self.text[self.pos]
            self.pos += 1
            node = BinOp(op, node, self._unary())
        return node

    def _unary(self) -> Node:
        if self._peek() == "-":
            self.pos += 1
            return Neg(self._nested(self._unary))
        return self._atom()

    def _nested(self, rule):
        self.depth += 1
        if self.depth > _MAX_DEPTH:
            raise FormulaError("Formula is nested too deeply")
        try:
            return rule()
        finally:
            self.depth -= 1

    def _atom(self) -> Node:
        ch = self._peek()
        if ch == "(":
            self.pos += 1
            node = self._nested(self._expr)
            if self._peek() != ")":
                raise FormulaError("Missing closing ')'")
            self.pos += 1
            return node
        if ch == "[":
            end = self.text.find("]", self.pos + 1)
            if end == -1:
                raise FormulaError("Missing closing ']'")
            raw = self.text[self.pos + 1 : end].strip()
            self.pos = end + 1
            return _reference(raw)
        match = _NUMBER.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            return Num(float(match.group()))
        if not ch:
            raise FormulaError("Formula ends unexpectedly")
        raise FormulaError(
            f"Unexpected '{ch}' at position {self.pos + 1}; wrap line items and "
            "categories in brackets, e.g. [Revenue]"
        )


def _reference(raw: str) -> Ref:
    if not raw:
        raise FormulaError("Empty reference []")
    prefix, sep, rest = raw.partition(":")
    if sep and prefix.strip().lower() in ("item", "category"):
        name = rest.strip()
        if not name:
            raise FormulaError(f"Empty reference [{raw}]")
        return Ref(prefix.strip().lower(), name)
    return Ref("any", raw)


def parse_formula(text: str) -> Node:
    """Parse a formula into an expression tree. Raises FormulaError."""
    if not text or not text.strip():
        raise FormulaError("Formula is empty")
    if len(text) > MAX_FORMULA_LENGTH:
        raise FormulaError(f"Formula exceeds {MAX_FORMULA_LENGTH} characters")
    node = _Parser(text).parse()
    if len(references(node)) > MAX_REFERENCES:
        raise FormulaError(f"Formula has more than {MAX_REFERENCES} references")
    return node


def references(node: Node) -> list[Ref]:
    """Distinct references in first-appearance order."""
    found: list[Ref] = []

    def walk(n: Node) -> None:
        if isinstance(n, Ref):
            if n not in found:
                found.append(n)
        elif isinstance(n, Neg):
            walk(n.operand)
        elif isinstance(n, BinOp):
            walk(n.left)
            walk(n.right)

    walk(node)
    return found


def evaluate(node: Node, values: dict[Ref, float | None]) -> float | None:
    """Evaluate with each reference's value. None (no value, or division by
    zero) propagates: a KPI with a missing input has no value rather than a
    misleading number."""
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Ref):
        return values.get(node)
    if isinstance(node, Neg):
        inner = evaluate(node.operand, values)
        return None if inner is None else -inner
    left = evaluate(node.left, values)
    right = evaluate(node.right, values)
    if left is None or right is None:
        return None
    if node.op == "+":
        return left + right
    if node.op == "-":
        return left - right
    if node.op == "*":
        return left * right
    return None if right == 0 else left / right
