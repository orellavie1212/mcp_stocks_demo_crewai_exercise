"""stocks_crew -- Lab 1: a CrewAI crew that talks to a real MCP server.

Layers, bottom up:

    (datasource.py lives in packages/shared-datasource: yfinance + Yahoo search)
    analytics.py    pure pandas: SMA, EMA, RSI, gap/vol/52w flags
    tools.py        the five tool functions; return JSON strings
    mcp_server.py   FastMCP registration + main() -> stdio transport
    llm.py          which model the crew uses (wraps packages/shared-config)
    crew.py         the client: spawns mcp_server as a subprocess, hands its
                    tools to the CrewAI agents declared in config/agents.yaml
                    (four as shipped: research, technical, sector, report)
    api.py          optional FastAPI bridge over the same tools (:8001)

The provider switcher, evidence-grounding guardrail and tool tracer are NOT
reimplemented here: they are the same packages/shared-* modules Labs 2-4 use.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
