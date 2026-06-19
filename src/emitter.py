"""CodeEmitter (blueprint §12) — the structural guarantee of correct syntax.

A deliberately *low-abstraction* tool: a line buffer + indent stack. It handles
lines, indentation and block balancing only. It does **not** understand
expressions or language semantics — generators still write the text of each line,
they just never manage whitespace or closing braces by hand.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List


class CodeEmitter:
    def __init__(
        self,
        indent_unit: str = "    ",
        brace_style: bool = False,
        open_token: str = "{",
        close_token: str = "}",
        header_suffix: str = ":",
    ) -> None:
        self._lines: List[str] = []
        self._level = 0
        self._unit = indent_unit
        self._brace = brace_style          # True: C/JS/Go/Java/C++; False: Python
        self._open, self._close = open_token, close_token
        self._suffix = header_suffix       # the ":" for Python

    @property
    def brace_style(self) -> bool:
        return self._brace

    def line(self, text: str = "") -> "CodeEmitter":
        self._lines.append("" if text == "" else self._unit * self._level + text)
        return self

    def lines(self, *texts: str) -> "CodeEmitter":
        for t in texts:
            self.line(t)
        return self

    def comment(self, text: str) -> "CodeEmitter":
        prefix = "// " if self._brace else "# "
        return self.line(prefix + text)

    @contextmanager
    def block(self, header: str) -> Iterator["CodeEmitter"]:
        """C-family: ``header {`` ... ``}``; Python: ``header:`` ... (indentation)."""
        if self._brace:
            self.line(f"{header} {self._open}")
        else:
            self.line(f"{header}{self._suffix}")
        self._level += 1
        try:
            yield self
        finally:
            self._level -= 1
            if self._brace:
                self.line(self._close)

    @contextmanager
    def indented(self) -> Iterator["CodeEmitter"]:
        """Bump indentation by one level without emitting any delimiter.

        For body lines that sit under a label rather than a brace — e.g. the
        statements following a ``case``/``default`` in a switch.
        """
        self._level += 1
        try:
            yield self
        finally:
            self._level -= 1

    def open_brace(self, header: str) -> "CodeEmitter":
        """Open a file-spanning scope (``class``/``func main``/``namespace``).

        The matching :meth:`close_brace` is called later — typically across the
        prologue/epilogue boundary, where a ``with`` block cannot reach.
        """
        self.line(f"{header} {self._open}")
        self._level += 1
        return self

    def close_brace(self) -> "CodeEmitter":
        self._level -= 1
        self.line(self._close)
        return self

    @contextmanager
    def raw_block(self, open_line: str, close_line: str) -> Iterator["CodeEmitter"]:
        """For bare blocks that are not if/for, e.g. a C++ scope or a try block.

        Emits ``open_line``, indents the body, then emits ``close_line``. The
        caller owns the exact text of both delimiters (used for ``} else {``,
        ``} catch (e) {``, Go IIFE wrappers, etc.).
        """
        self.line(open_line)
        self._level += 1
        try:
            yield self
        finally:
            self._level -= 1
            self.line(close_line)

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"
