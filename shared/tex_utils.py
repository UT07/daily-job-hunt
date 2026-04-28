"""LaTeX → plaintext conversion for AI prompt context.

This is intentionally minimal — not a full LaTeX renderer. Strips comments,
common formatting commands, and environment delimiters while preserving
the text content. Good enough to feed cover letter content to an LLM.
"""
from __future__ import annotations

import re
from typing import Optional

# Strip line comments (% to end of line, not preceded by \)
_COMMENT = re.compile(r"(?<!\\)%[^\n]*")

# Strip \begin{env} and \end{env} delimiters but keep inner content
_BEGIN_END = re.compile(r"\\(begin|end)\{[^}]*\}")

# Match commands like \textbf{X}, \section{X}, \emph{X} — keep the inner X
_BRACED_COMMAND = re.compile(r"\\[a-zA-Z]+\*?\{([^{}]*)\}")

# Match commands without args like \\, \item, \maketitle — replace with space
_BARE_COMMAND = re.compile(r"\\[a-zA-Z]+\*?")
_DOUBLE_BACKSLASH = re.compile(r"\\\\")

# Collapse 3+ blank lines to 2
_TRIPLE_BLANK = re.compile(r"\n{3,}")


def tex_to_plaintext(tex: Optional[str]) -> str:
    """Convert LaTeX source to plaintext suitable for AI prompts.

    Not a full renderer. Strips comments, environments, and commands;
    preserves text content. Idempotent.
    """
    if not tex:
        return ""

    text = tex

    # Strip comments first (before they confuse downstream regexes)
    # Also strip the newline to avoid blank lines from comment-only lines
    text = _COMMENT.sub("", text).replace("\n\n", "\n")

    # Strip environment delimiters
    text = _BEGIN_END.sub("", text)

    # Resolve braced commands repeatedly until none remain (handles nesting)
    # Add space after content to avoid joining adjacent words
    prev = None
    while prev != text:
        prev = text
        text = _BRACED_COMMAND.sub(r"\1 ", text)

    # Replace \\ with newline before stripping bare commands
    text = _DOUBLE_BACKSLASH.sub("\n", text)

    # Strip remaining bare commands (replace with space to avoid joining words)
    text = _BARE_COMMAND.sub(" ", text)

    # Strip stray braces
    text = text.replace("{", "").replace("}", "")

    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))

    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
