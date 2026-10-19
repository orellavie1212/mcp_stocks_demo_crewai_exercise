# console.py — one Windows papercut, fixed once.
#
# CrewAI's built-in console listener prints emoji ("🔧 Task started", "🚀 Crew
# completed"). On Windows, Python's stdout defaults to the legacy ANSI codepage
# (cp1252), which cannot encode them, so every one of those lines turns into
#
#     [CrewAIEventsBus] Sync handler error in on_task_started:
#     'charmap' codec can't encode character '\U0001f527'
#
# The crew still runs and the trace is unaffected — but a wall of red errors in
# the middle of a demo is not what you want to explain.
#
# Called explicitly from the entry points (crew's __main__, api, the Streamlit
# app) rather than from __init__, so importing the package stays free of side
# effects. Deliberately NOT called by mcp_server: stdout there is the JSON-RPC
# channel and the MCP SDK owns its encoding.
from __future__ import annotations

import sys


def use_utf8_console() -> None:
    """Switch stdout/stderr to UTF-8 where the platform left them narrower."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # already replaced by a capture object
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
