"""
cli/_console.py
===============

One job: make sure printing an LLM's answer cannot kill the program.

THE BUG THIS EXISTS TO FIX
--------------------------
On Windows, a Python process attached to a console gets a stdout encoded with
the machine's legacy "ANSI" code page - `cp1252` in most of Europe and the
Americas, something else again in Japan or China. That encoding has no byte
for the characters a language model reaches for constantly:

    ->  U+2192 arrow          --  U+2014 em dash
    '   U+2019 curly apostrophe   *   U+2022 bullet

So `print(answer)` raises UnicodeEncodeError and the process dies. The damage
is worse than a cosmetic glitch, because of WHEN it happens: retrieval has
finished, the OpenAI call has been made and BILLED, the answer is sitting in
memory - and then it is thrown away without being shown. The user pays for a
question they never get an answer to, and the traceback points at `print`,
which looks like nothing to do with the real cause.

Measured here: 1 of 6 real questions crashed this way, purely on whether the
model happened to use an arrow.

WHY `errors="replace"` AND NOT JUST utf-8
-----------------------------------------
Reconfiguring to UTF-8 fixes the console case, but stdout is not always a
console. When output is piped to a file or another program the encoding is
whatever the caller set, and it can still be one that lacks a character. The
error handler is the belt to UTF-8's braces: with it, an unmappable character
degrades to "?" instead of taking the whole answer down with it. Losing one
glyph is always better than losing the answer.

WHY IT IS DONE HERE AND NOT AT IMPORT TIME
------------------------------------------
This is a deliberate, process-wide side effect, so it belongs in the two
`main()` functions a human actually invokes - not in a module that a test or
a library might import. Importing `graph_rag` must not silently rewrite
someone else's stdout.
"""

import sys


def configure_console() -> None:
    """
    Make `stdout` and `stderr` able to carry any character the model emits.

    Safe to call more than once, and safe to call when the streams have
    already been replaced by something that does not support reconfiguring -
    a pytest capture buffer, for instance - which is why the whole thing is
    wrapped rather than guarded by a version check.
    """
    for stream in (sys.stdout, sys.stderr):
        # `reconfigure` exists on TextIOWrapper (Python 3.7+). Under pytest,
        # or when stdout has been replaced by a StringIO, it is simply absent
        # and there is nothing to fix.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A detached or already-closed stream. Not being able to improve
            # the encoding is not a reason to refuse to run.
            continue
