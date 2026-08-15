"""Recursive-descent / precedence-climbing parser: tokens -> AST.

Grammar (supporting functional + infix arithmetic form)::

    expr          := term (('+' | '-') term)*
    term          := factor (('*' | '/') factor)*
    factor        := ('-' | '+') factor | primary
    primary       := '(' expr ')' | NUMBER | BOOL | ident_or_call
    ident_or_call := IDENT ( '(' arglist? ')' )?
    arglist       := arg (',' arg)*
    arg           := IDENT '=' expr          (keyword argument)
                   | expr                     (positional argument)

Infix operators (+, -, *, /) desugar directly to their canonical KB operator
calls (add, subtract, multiply, divide).
"""

from __future__ import annotations

import math

from app.validator.ast_nodes import Boolean, Field, KeywordArg, Node, Number, OperatorCall
from app.validator.lexer import Token, tokenize

# Bound on AST nesting depth. Real alphas are shallow (depth < ~20); this keeps a
# pathological (or generator-produced) input from blowing the Python recursion
# limit and escaping validate() as an unhandled RecursionError -> HTTP 500.
_MAX_DEPTH = 150

_INFIX_OPS: dict[str, tuple[str, int]] = {
    "plus": ("add", 10),
    "minus": ("subtract", 10),
    "star": ("multiply", 20),
    "slash": ("divide", 20),
}


class ParseError(ValueError):
    """Raised on a malformed expression; carries the source offset."""

    def __init__(self, message: str, pos: int) -> None:
        super().__init__(message)
        self.pos = pos


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0
        self._depth = 0

    def _peek(self) -> Token:
        return self.tokens[self.i]

    def _at(self, offset: int) -> Token:
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else self.tokens[-1]

    def _advance(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _expect(self, kind: str) -> Token:
        tok = self._peek()
        if tok.kind != kind:
            raise ParseError(f"expected {kind!r}, got {tok.kind} {tok.text!r}", tok.pos)
        return self._advance()

    def parse(self) -> Node:
        node = self._expr(0)
        trailing = self._peek()
        if trailing.kind != "eof":
            raise ParseError(f"unexpected trailing token {trailing.text!r}", trailing.pos)
        return node

    def _expr(self, min_precedence: int = 0) -> Node:
        self._depth += 1
        if self._depth > _MAX_DEPTH:
            raise ParseError("expression nesting too deep", self._peek().pos)
        try:
            left = self._factor()
            while self._peek().kind in _INFIX_OPS:
                op_token = self._peek()
                op_name, prec = _INFIX_OPS[op_token.kind]
                if prec < min_precedence:
                    break
                self._advance()  # consume operator
                right = self._expr(prec + 1)  # left-associative: strictly higher precedence for RHS
                left = OperatorCall(
                    op_name,
                    [left, right],
                    [],
                    start=left.start,
                    end=right.end,
                )
            return left
        finally:
            self._depth -= 1

    def _factor(self) -> Node:
        tok = self._peek()
        if tok.kind in ("minus", "plus"):
            self._advance()
            inner = self._factor()
            # Folded into the literal when we can: -3 is a Number, not a negate()
            # wrapper, which keeps normalization and the structural hash stable.
            if isinstance(inner, Number):
                value = -inner.value if tok.kind == "minus" else inner.value
                return Number(value, inner.is_int, start=tok.pos, end=inner.end)
            if tok.kind == "plus":
                return inner
            return OperatorCall(
                "multiply",
                [inner, Number(-1.0, True, start=tok.pos, end=tok.pos + 1)],
                [],
                start=tok.pos,
                end=inner.end,
            )
        return self._primary()

    def _primary(self) -> Node:
        tok = self._peek()
        if tok.kind == "lparen":
            self._advance()  # consume '('
            inner = self._expr(0)
            self._expect("rparen")
            return inner
        if tok.kind == "number":
            self._advance()
            is_int = "." not in tok.text
            value = float(tok.text)
            if not math.isfinite(value):
                # A digit run long enough to overflow to inf (or a nan) would later
                # crash int()-based normalization; reject it as a parse error.
                raise ParseError("numeric literal is out of range", tok.pos)
            return Number(value, is_int, start=tok.pos, end=tok.pos + len(tok.text))
        if tok.kind == "bool":
            self._advance()
            return Boolean(tok.text == "true", start=tok.pos, end=tok.pos + len(tok.text))
        if tok.kind == "ident":
            return self._ident_or_call()
        raise ParseError(f"unexpected token {tok.text!r}", tok.pos)

    def _ident_or_call(self) -> Node:
        name = self._advance()  # ident
        if self._peek().kind != "lparen":
            return Field(name.text, start=name.pos, end=name.pos + len(name.text))
        self._advance()  # consume '('
        args: list[Node] = []
        kwargs: list[KeywordArg] = []
        if self._peek().kind != "rparen":
            self._arg(args, kwargs)
            while self._peek().kind == "comma":
                self._advance()
                self._arg(args, kwargs)
        rparen = self._expect("rparen")
        return OperatorCall(name.text, args, kwargs, start=name.pos, end=rparen.pos + 1)

    def _arg(self, args: list[Node], kwargs: list[KeywordArg]) -> None:
        tok = self._peek()
        if tok.kind == "ident" and self._at(1).kind == "eq":
            self._advance()  # ident
            self._advance()  # '='
            value = self._expr(0)
            kwargs.append(KeywordArg(tok.text, value, start=tok.pos, end=value.end))
            return
        if kwargs:
            raise ParseError("positional argument after keyword argument", tok.pos)
        args.append(self._expr(0))


def parse(expression: str) -> Node:
    """Tokenize + parse ``expression`` into an AST (raises ``ParseError``/``LexError``)."""
    return _Parser(tokenize(expression)).parse()
