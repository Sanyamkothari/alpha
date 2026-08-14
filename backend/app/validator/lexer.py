"""Tokenizer for the functional & infix WorldQuant expression grammar.

Supports identifiers, numeric literals (int/float, optional leading sign handled
by the parser), booleans, and the punctuation ``( ) , = - + * /``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = re.compile(r"\d+\.\d+|\.\d+|\d+")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BOOLEANS = {"true", "false"}

_PUNCT = {
    "(": "lparen",
    ")": "rparen",
    ",": "comma",
    "=": "eq",
    "-": "minus",
    "+": "plus",
    "*": "star",
    "/": "slash",
}


class LexError(ValueError):
    """Raised on an unexpected character; carries the source offset."""

    def __init__(self, message: str, pos: int) -> None:
        super().__init__(message)
        self.pos = pos


@dataclass
class Token:
    kind: str  # number | ident | bool | lparen | rparen | comma | eq | minus | plus | eof
    text: str
    pos: int  # start offset in the source


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in _PUNCT:
            tokens.append(Token(_PUNCT[c], c, i))
            i += 1
            continue
        m = _NUMBER.match(src, i)
        if m:
            tokens.append(Token("number", m.group(), i))
            i = m.end()
            continue
        m = _IDENT.match(src, i)
        if m:
            word = m.group()
            tokens.append(Token("bool" if word in _BOOLEANS else "ident", word, i))
            i = m.end()
            continue
        raise LexError(f"unexpected character {c!r}", i)
    tokens.append(Token("eof", "", n))
    return tokens
