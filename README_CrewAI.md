# Lab 1 — CrewAI + MCP Stocks Analysis

Four CrewAI agents (research, technical, sector, report) analyse a stock by
calling tools on a **real MCP server**. The crew starts the server as a child
process and talks to it over stdio, the same way Claude Desktop or an IDE would.
Every tool call is recorded, and the finished report is checked against what the
tools actually returned.

This is Lab 1 of four. Labs 2–4 run the same idea as separate services; see the
[main README](README.md). The Lab 1 design (YAML crew, stdio MCP, grounding
check) comes from [zviba/mcp_stocks_demo_crewai_exercise](https://github.com/zviba/mcp_stocks_demo_crewai_exercise).

## Architecture

```
streamlit_crewai_app.py                 UI: provider, language/tone, per-agent tools, results
        │
        ▼
src/stocks_crew/crew.py                 run_analysis(): the MCP *client* and the crew builder
        │   reads config/agents.yaml + config/tasks.yaml
        │   asks llm.py for the model            ──►  packages/shared-config  (get_llm)
        │   wraps kickoff() in a ToolTracer       ──►  packages/shared-observability
        │   checks the report afterwards          ──►  packages/shared-guardrails (grounding)
        │
        │  JSON-RPC over stdio (crewai_tools.MCPServerAdapter)
        ▼
src/stocks_crew/mcp_server.py           FastMCP server: 5 @mcp.tool() wrappers
        ▼
src/stocks_crew/tools.py                the five tools; return JSON strings
        ▼
src/stocks_crew/analytics.py            SMA / EMA / RSI / gap / volatility / 52-week flags
packages/shared-datasource/datasource.py   yfinance + Yahoo search
```

### The five MCP tools

| Tool | Returns |
|---|---|
| `search_symbols` | symbol, name, region, currency |
| `latest_quote` | price, change, change percent, volume |
| `price_series` | OHLCV rows |
| `indicators` | last close, SMA, EMA, RSI |
| `detect_events` | gap up/down, volatility spike, 52-week high/low flags |

There is deliberately no "explain" tool. The LLM sits above MCP, in the crew; if
the crew's own output were also a tool, it could call itself. (`apps/mcp-server`
in Labs 2–4 still has a REST `/explain`.)

### The crew

Defined in `src/stocks_crew/config/`, not in Python:

| Agent | Tools it may call | Job |
|---|---|---|
| `research` | `search_symbols`, `latest_quote`, `price_series` | confirm the symbol, current price, recent history |
| `technical` | `indicators`, `detect_events` | read the indicators and flags |
| `sector` | `search_symbols`, `latest_quote`, `indicators` | compare against 3–5 peers, on quote/indicator values only |
| `report` | none | write the final report in the chosen language |

Tasks run in file order (`Process.sequential`); `context:` lists earlier tasks
whose output a task receives. `{symbol}`, `{language}`, `{tone}` and
`{horizon_days}` are filled in at run time.

The tool list per agent is an **allow-list**. You can change it per run in the
UI; the crew builder only hands each agent the tools it is allowed.

## Run it

Python 3.12 (3.11 also works; 3.13 is not verified).

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
make dev-install       # installs requirements.txt + every apps/*/requirements.txt
```

`uv sync` installs Lab 1 alone, from `pyproject.toml` and `uv.lock`.

```bash
make lab1-tools        # real MCP handshake, lists the 5 tools. No LLM, no tokens
make lab1-ui           # Streamlit on http://localhost:8501
```

There is no API server to start first. A one-off run from the command line:

```bash
PYTHONPATH=src python -m stocks_crew.crew NVDA                       # provider from .env
PYTHONPATH=src python -m stocks_crew.crew NVDA --provider=openai
PYTHONPATH=src python -m stocks_crew.crew NVDA --out=nvda.json      # clean JSON file
```

CrewAI prints its own progress to stdout, so redirecting stdout does not give
valid JSON; use `--out`.

The MCP server on its own (stdio; a silent terminal is correct, stdout carries
the protocol):

```bash
PYTHONPATH=src python -m stocks_crew.mcp_server     # or: stocks-crew-mcp after `uv sync`
npx @modelcontextprotocol/inspector python -m stocks_crew.mcp_server
```

### Choosing the model

Lab 1 uses the same provider switcher as Labs 2–4 (`packages/shared-config`).
Set `LLM_PROVIDER` in `.env`, or pick one in the Streamlit sidebar.

| `LLM_PROVIDER` | Needs |
|---|---|
| `vertex_ai` (default in `.env.example`) | `GCP_PROJECT` in `.env` and `gcloud auth application-default login`. No key |
| `google_ai_studio` | `GEMINI_API_KEY` |
| `openai` | `OPENAI_API_KEY` |

A key typed into the sidebar applies to that run only. Leave `LITELLM_BASE_URL`
empty for Lab 1; it is for the Docker stack.

## Guardrails and tracing

- **Tool trace.** `ToolTracer` (`packages/shared-observability/tool_tracer.py`)
  listens on CrewAI's event bus and records every tool call: name, the arguments
  the model chose, duration, success, and the full output. The UI shows it under
  the report.
- **Grounding check.** After the run, `packages/shared-guardrails/grounding.py`
  compares every figure in the report with the full tool output and flags any
  figure no tool returned, plus advice-shaped phrasing ("you should buy"). It is
  plain Python, not another model. It reports; it does not block.
- **CrewAI's `HallucinationGuardrail`.** Attached to the report task in
  `tasks.yaml`. In the open-source `crewai` package it is a placeholder that
  passes every output. The UI says so. The grounding check is the one doing the
  work.
- **Symbol validation.** `shared-models.validate_symbol` rejects anything that is
  not 1–10 letters, digits, `.` or `-`, because the symbol is interpolated into
  agent prompts.

What the grounding check cannot see: whether the peers the sector agent chose are
really peers. Their numbers are verified; their relevance is not. See
[EXERCISE_INSTRUCTIONS.md](EXERCISE_INSTRUCTIONS.md), "The gap that remains".

## Changing the crew

**Add or edit an agent:** a block in `config/agents.yaml` (`role`, `goal`,
`backstory`, `tools`). **Add or edit a task:** a block in `config/tasks.yaml`
(`description`, `expected_output`, `agent`, `context`). Order in the file is run
order, and `context` may only name tasks above it. No Python changes. The
exercise walks through this: [EXERCISE_INSTRUCTIONS.md](EXERCISE_INSTRUCTIONS.md)
and `hints/`.

**Add a tool:** write the function in `tools.py`, wrap it with `@mcp.tool()` in
`mcp_server.py` (the docstring there is what the agent reads), add its name to
`EXPECTED_TOOLS` in `tests/unit/test_stocks_crew/test_mcp_server.py`, and list it
under an agent's `tools:` in `agents.yaml`. Check with `make lab1-tools`.

**Nothing may print to stdout** in `tools.py`, `analytics.py`, the shared
datasource or the server. stdout is the MCP channel; a stray `print()` corrupts
it. Use `logging`.

## Optional HTTP bridge

```bash
make lab1-api          # http://127.0.0.1:8001/docs
```

`stocks_crew.api` exposes `/search`, `/quote`, `/series`, `/indicators`,
`/events` and `/bundle` over the same `tools.py` functions. The crew does not use
it; it is for poking at the data layer with `curl`. Symbols are validated
(HTTP 422 for a bad one).

## Tests

```bash
python -m pytest tests/unit/test_stocks_crew      # offline, no tokens
python -m pytest -m network                       # also hits Yahoo Finance
```

`make test` runs the whole unit suite. Tests need `pytest`, `pytest-asyncio` and
`httpx` (the `dev` group in `pyproject.toml`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No module named 'crewai_tools'` | `make dev-install` (needs `crewai-tools[mcp]`) |
| Sidebar says "Could not reach the MCP server" | run `make lab1-tools` to see the actual error |
| Browser shows only "Connection error" | old Streamlit with Starlette 1.4+; `pip install -U "streamlit>=1.66"` |
| `gemini_api_key_required` / `openai_api_key_required` | set that provider's key in `.env` or the sidebar |
| `gcp_project_required` or "Permission denied on resource project your-gcp-project-id" | `GCP_PROJECT` is still the placeholder, or there is no `.env`: `cp .env.example .env`, set `GCP_PROJECT`, restart |
| Vertex AI auth errors | `gcloud auth application-default login` |
| LLM calls fail with an unknown host `litellm-proxy` | empty `LITELLM_BASE_URL` in `.env` |
| Windows console floods with `charmap` codec errors | run inside WSL, or use the Streamlit UI (it switches stdout to UTF-8) |
| Report has an "ungrounded figures" list | the model wrote a number no tool returned; read the trace |

Data comes from Yahoo Finance via yfinance. Nothing here is investment advice.
