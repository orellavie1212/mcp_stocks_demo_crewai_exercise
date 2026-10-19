# crew.py — the agentic layer, and the MCP *client*.
#
# This is the file the refactor was about. The previous version imported the
# tool functions straight out of mcp_server.py and re-wrapped them with CrewAI's
# @tool decorator, so the agents called Python functions in this process and the
# @mcp.tool() decorators were decoration in the literal sense — no server was
# ever started, no JSON-RPC ever crossed a boundary.
#
# Now the crew is a genuine MCP client: it spawns `python -m stocks_crew.mcp_server`
# as a child process and reaches its tools over real JSON-RPC on stdio, exactly
# the way an external MCP host (Claude Desktop, an IDE, another team's agent)
# would. Nothing in this file knows what the tools *do*; it only knows their
# names, which is the point of the protocol.
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from . import _paths
from . import llm as llm_config
from grounding import check_report  # noqa: E402  (packages/shared-guardrails)
from models import validate_symbol  # noqa: E402  (packages/shared-models)
from tool_tracer import ToolTracer  # noqa: E402  (packages/shared-observability)

logger = logging.getLogger(__name__)

# Roles, backstories and task briefs live in YAML rather than in this file, so a
# prompt can be tweaked without touching Python. See config/agents.yaml and
# config/tasks.yaml.
CONFIG_DIR = Path(__file__).parent / "config"

# The UI and the API pass ISO codes ("he"). Interpolating the bare code into an
# English prompt is a weak instruction: by the time the writer runs it has read
# two English task outputs, and "Language: he" is both terse and ambiguous in
# English. Spelling the language out — in English *and* in the language itself —
# is the difference between a Hebrew report and an English one.
LANGUAGE_NAMES = {
    "en": "English",
    "he": "Hebrew (עברית)",
}


def language_name(code: str) -> str:
    """Map an ISO code to a prompt-friendly language name; pass others through."""
    key = (code or "en").strip()
    return LANGUAGE_NAMES.get(key.lower(), key)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _load_config(name: str) -> dict:
    """Read and cache config/<name>.yaml."""
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class _SafeDict(dict):
    """Leaves unknown {placeholders} alone instead of raising KeyError."""

    def __missing__(self, key):
        return "{" + key + "}"


def _render(value: Any, ctx: dict) -> Any:
    """Fill {placeholders} through nested strings/lists/dicts of a config block."""
    if isinstance(value, str):
        return value.format_map(_SafeDict(ctx)).strip()
    if isinstance(value, list):
        return [_render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    return value


def default_agent_tools() -> Dict[str, List[str]]:
    """The per-agent tool allow-lists as declared in agents.yaml."""
    return {
        name: list(spec.get("tools") or [])
        for name, spec in _load_config("agents").items()
    }


# ---------------------------------------------------------------------------
# The MCP connection
# ---------------------------------------------------------------------------


def server_params():
    """Describe how to launch the MCP server as a child process.

    Uses `sys.executable -m` rather than a hardcoded "python3" so the child
    lands in the same (uv-managed) virtualenv as the parent.
    """
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "stocks_crew.mcp_server"],
        # PYTHONPATH makes `stocks_crew` importable in the child even when the
        # package was never installed (make dev-install installs requirements,
        # not this package). The parent's own path is kept after it.
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(_paths.SRC_DIR), os.environ.get("PYTHONPATH", "")) if p
            ),
        },
    )


def _tool_description(tool) -> str:
    """The docstring the server sent, without CrewAI's schema preamble.

    The MCP adapter rewrites `description` into a composite that begins with
    "Tool Name: …\nTool Arguments: {…}\nTool Description: <the real one>". For a
    UI listing we only want the last part.
    """
    raw = (getattr(tool, "description", "") or "").strip()
    marker = "Tool Description:"
    return raw.split(marker, 1)[1].strip() if marker in raw else raw


def list_mcp_tools() -> List[Dict[str, str]]:
    """Start the MCP server and ask it what it offers.

    This is a real handshake, not a hardcoded list — which is exactly what makes
    it worth showing in the UI. If someone deletes a @mcp.tool() decorator, this
    stops reporting the tool.
    """
    from crewai_tools import MCPServerAdapter

    with MCPServerAdapter(server_params()) as mcp_tools:
        return [
            {"name": getattr(t, "name", "?"), "description": _tool_description(t)}
            for t in mcp_tools
        ]


def _select_tools(mcp_tools, wanted: set):
    """Pick the named MCP tools out of the live adapter list."""
    if not wanted:
        return []
    selected = [t for t in mcp_tools if getattr(t, "name", "") in wanted]
    missing = wanted - {getattr(t, "name", "") for t in selected}
    if missing:
        logger.warning(
            "Requested MCP tools not offered by the server: %s (server offers %s)",
            sorted(missing),
            sorted(getattr(t, "name", "?") for t in mcp_tools),
        )
    return selected


# ---------------------------------------------------------------------------
# CrewAI's own task guardrail
# ---------------------------------------------------------------------------
#
# CrewAI ships a `HallucinationGuardrail` that attaches to a Task and is supposed
# to score the task's output for faithfulness before the crew moves on. It is
# wired up here, on whichever tasks ask for it in tasks.yaml, because it is the
# framework-native answer to the question grounding.py answers by hand — and
# because the comparison is the lesson.
#
# READ THIS BEFORE RELYING ON IT: in the open-source `crewai` package the class
# is a *placeholder*. Its __call__ returns `(True, output)` for every input and
# logs "Hallucination detection is a no-op in open source, use it for free at
# https://app.crewai.com". Threshold and context are stored and ignored. Handed a
# report that says a stock trades at 999999.00 and that you should buy it, it
# passes. Only the hosted platform supplies the real implementation (it installs
# a module-level `_validate_output_hook` that this class defers to).
#
# So this is switched on for what it teaches, and `guardrail_status()` reports
# `enforcing: False` so the UI can say so out loud. The class exists, the wiring
# is real, the check is not — and a no-op guardrail that nobody knows is a no-op
# is worse than no guardrail, because it buys confidence it has not earned. Which
# is the whole argument for the deterministic check in grounding.py: 190 lines
# of Python that runs everywhere, needs no vendor account, and cannot itself
# hallucinate.

#: Tasks whose YAML carries this key get a HallucinationGuardrail attached. The
#: value is either `true` or a mapping of the constructor's optional arguments
#: (`threshold`, `context`).
GUARDRAIL_KEY = "hallucination_guardrail"


def _hallucination_guardrail(spec: Dict[str, Any], llm: Any) -> Optional[Any]:
    """Build CrewAI's HallucinationGuardrail for a task, if its YAML asks for one.

    Returns None when the task does not opt in, and — deliberately — also when
    the installed crewai is too old to have the class. A missing optional
    guardrail should not take the whole run down with an ImportError.

    Note what the guardrail can and cannot see. It is constructed *before*
    kickoff, so the only reference `context` available is text we can write here;
    the tool output the report should be grounded in does not exist yet. Leaving
    `context` unset makes CrewAI fall back to the task's own `expected_output`,
    which is the honest default: the acceptance criterion is the only ground
    truth that exists at build time. grounding.py runs afterwards and therefore
    gets the real evidence — the same asymmetry that makes an after-the-fact
    check the stronger of the two.
    """
    requested = spec.get(GUARDRAIL_KEY)
    if not requested:
        return None

    try:
        from crewai.tasks.hallucination_guardrail import HallucinationGuardrail
    except ImportError:  # pragma: no cover - depends on the installed crewai
        logger.warning(
            "crewai.tasks.hallucination_guardrail is unavailable in this crewai "
            "version; continuing without it. grounding.py still runs."
        )
        return None

    options = requested if isinstance(requested, dict) else {}
    return HallucinationGuardrail(llm=llm, **options)


def guardrail_status() -> Dict[str, Any]:
    """Which tasks carry CrewAI's guardrail, and whether it actually enforces.

    Rides back on the run result so the UI never implies a check is running when
    it is not. `enforcing` is detected rather than hardcoded: the placeholder's
    `__call__` defers to a module-level `_validate_output_hook`, which only the
    hosted platform installs, so "is that hook callable?" is the same question
    the class itself asks. A build that does not define the hook at all is not
    the placeholder, and is taken at its word.
    """
    tasks = [
        name
        for name, spec in _load_config("tasks").items()
        if spec.get(GUARDRAIL_KEY)
    ]
    status: Dict[str, Any] = {"attached_to": tasks, "available": False, "enforcing": False}
    try:
        from crewai.tasks import hallucination_guardrail as module
    except ImportError:  # pragma: no cover - depends on the installed crewai
        status["note"] = "This crewai build has no HallucinationGuardrail."
        return status

    status["available"] = True
    sentinel = "_validate_output_hook"
    status["enforcing"] = (
        callable(getattr(module, sentinel)) if hasattr(module, sentinel) else True
    )
    status["note"] = (
        "CrewAI's HallucinationGuardrail is attached but is a no-op in the "
        "open-source package — it passes every output. The grounding and advice "
        "checks above are the ones doing the work."
        if not status["enforcing"]
        else "CrewAI's HallucinationGuardrail is active on: " + ", ".join(tasks)
    )
    return status


# ---------------------------------------------------------------------------
# Building the crew
# ---------------------------------------------------------------------------


def build_crew(
    mcp_tools,
    symbol: str,
    language: str = "en",
    tone: str = "professional",
    horizon_days: int = 30,
    tool_overrides: Optional[Dict[str, List[str]]] = None,
    llm: Any = None,
) -> Tuple[Any, Dict[str, List[str]]]:
    """Assemble the sequential crew described by config/*.yaml around live MCP tools.

    Returns (crew, tools_per_agent) so the caller can report which tools each
    agent was actually given — the allow-list, before anyone has called anything.

    `llm` is a crewai.LLM from llm.resolve(); omitted, the default provider from
    .env is used. Nothing else in this function knows which vendor is behind it.
    """
    from crewai import Agent, Crew, Process, Task

    if llm is None:
        llm = llm_config.resolve().llm

    ctx = {
        "symbol": symbol,
        "language": language_name(language),
        "tone": tone,
        "horizon_days": horizon_days,
    }
    overrides = tool_overrides or {}

    agents: Dict[str, Any] = {}
    granted: Dict[str, List[str]] = {}
    for name, spec in _load_config("agents").items():
        spec = _render(spec, ctx)
        wanted = overrides.get(name, spec.get("tools") or [])
        selected = _select_tools(mcp_tools, set(wanted))
        granted[name] = [getattr(t, "name", "?") for t in selected]
        agents[name] = Agent(
            role=spec["role"],
            goal=spec["goal"],
            backstory=spec["backstory"],
            tools=selected,
            llm=llm,
            verbose=True,
            allow_delegation=False,
        )

    tasks: Dict[str, Any] = {}
    for name, spec in _load_config("tasks").items():
        spec = _render(spec, ctx)
        tasks[name] = Task(
            description=spec["description"],
            expected_output=spec["expected_output"],
            agent=agents[spec["agent"]],
            # Task order in the YAML is the run order, so anything a task lists
            # as context has already been built by the time we get here.
            context=[tasks[c] for c in spec.get("context") or []],
            # None for tasks that do not ask for one, which is what Task expects.
            guardrail=_hallucination_guardrail(spec, llm),
        )

    crew = Crew(
        agents=list(agents.values()),
        tasks=list(tasks.values()),
        process=Process.sequential,
        verbose=True,
    )
    return crew, granted


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failed(symbol: str, error: str, code: str = "analysis_failed") -> Dict[str, Any]:
    return {
        "success": False,
        "error": error,
        "error_code": code,
        "symbol": symbol,
        "timestamp": _now(),
    }


def run_analysis(
    symbol: str,
    api_key: str = "",
    language: str = "en",
    tone: str = "professional",
    horizon_days: int = 30,
    tool_overrides: Optional[Dict[str, List[str]]] = None,
    progress: Optional[Callable[[str, int], None]] = None,
    on_tool_call: Optional[Callable[[Dict[str, Any]], None]] = None,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the crew described by config/*.yaml against the MCP server.

    Four agents as shipped — research, technical, sector, report — but this
    function never counts them: it runs whatever the YAML declares.

    Always returns a dict; failures are reported in-band as
    {"success": False, "error": ..., "error_code": ...} rather than raised, so
    both the HTTP layer and Streamlit can render them the same way.

    `provider` selects the model vendor ("openai", "google_ai_studio" or
    "vertex_ai"); omitted, it follows LLM_PROVIDER from .env. `api_key` is that
    provider's key for this one run and beats the environment; Vertex AI needs
    none (application-default credentials).

    On success the dict carries `tool_trace` — the record of every tool call the
    agents actually made, collected off the CrewAI event bus (see tool_tracer.py).
    """

    def step(message: str, percent: int) -> None:
        logger.info("[%s] %s", percent, message)
        if progress is not None:
            progress(message, percent)

    try:
        symbol = validate_symbol(symbol)
    except ValueError as e:
        return _failed(symbol, str(e), code="invalid_symbol")

    try:
        config = llm_config.resolve(provider, api_key)
    except llm_config.UnknownProvider as e:
        return _failed(symbol, str(e), code=llm_config.UnknownProvider.error_code)
    except llm_config.MissingAPIKey as e:
        return _failed(symbol, str(e), code=e.error_code)

    try:
        from crewai_tools import MCPServerAdapter

        step("Starting the MCP server…", 10)
        # Context-manager form so the MCP subprocess is always torn down, even
        # if the crew raises partway through.
        with MCPServerAdapter(server_params()) as mcp_tools:
            offered = [getattr(t, "name", "?") for t in mcp_tools]
            step(f"MCP server ready — {len(offered)} tools: {', '.join(offered)}", 20)

            crew, granted = build_crew(
                mcp_tools,
                symbol,
                language=language,
                tone=tone,
                horizon_days=horizon_days,
                tool_overrides=tool_overrides,
                llm=config.llm,
            )
            # The step list is derived, not written out: adding the sector agent
            # to tasks.yaml changed this line's output without changing the line.
            step(
                f"Crew assembled on {config.provider.label} ({config.model}) — "
                "running "
                + " → ".join(_load_config("tasks"))
                + "…",
                30,
            )

            with ToolTracer(on_call=on_tool_call) as tracer:
                result = crew.kickoff()

        # The output guardrail. The prompts *ask* the agents not to invent
        # figures; this checks the report they actually wrote against the tool
        # output they actually received, in plain Python, after the fact. It
        # reports rather than blocks — see grounding.check_report.
        report = str(result)
        guardrail = check_report(report, tracer.evidence)
        # CrewAI's own per-task guardrail already ran, inside kickoff(). Report
        # what it is so the UI can say whether it enforced anything — on
        # open-source crewai it did not. See guardrail_status().
        guardrail["crewai_guardrail"] = guardrail_status()
        if not guardrail["passed"]:
            logger.warning(
                "Guardrail failed for %s — ungrounded figures: %s; advice phrases: %s",
                symbol,
                guardrail["ungrounded_figures"],
                [hit["phrase"] for hit in guardrail["advice_phrases"]],
            )

        step("Analysis complete.", 100)
        return {
            "success": True,
            "symbol": symbol,
            "timestamp": _now(),
            "result": report,
            "provider": config.provider.name,
            "model": config.model,
            "mcp_tools": offered,
            "agent_tools": granted,
            "guardrail": guardrail,
            **tracer.summary(),
        }
    except Exception as e:
        logger.exception("Crew analysis failed for %s", symbol)
        return _failed(symbol, str(e))


if __name__ == "__main__":
    # `uv run python -m stocks_crew.crew --list-tools` — prove the server is real
    # without spending a token.
    from .console import use_utf8_console

    use_utf8_console()
    logging.basicConfig(level="INFO", format="%(levelname)s %(name)s: %(message)s")
    if "--list-tools" in sys.argv:
        print(json.dumps(list_mcp_tools(), indent=2))
    else:
        # `uv run python -m stocks_crew.crew NVDA --provider=gemini`
        target = next((a for a in sys.argv[1:] if not a.startswith("-")), "AAPL")
        chosen = next(
            (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--provider=")),
            None,
        )
        print(json.dumps(run_analysis(target, provider=chosen), indent=2))
