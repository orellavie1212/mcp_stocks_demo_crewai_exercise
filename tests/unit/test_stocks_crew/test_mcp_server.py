"""End-to-end MCP tests: launch the server as a subprocess and speak JSON-RPC to it.

This is the regression test that keeps MCP from quietly becoming decorative
again — which is exactly what it was in the previous version of this demo, where
the @mcp.tool() decorators existed but no server was ever started. If someone
removes `mcp.run()`, or prints to stdout anywhere inside the server process, the
handshake here fails.
"""

import json
import os
import sys
from contextlib import asynccontextmanager

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "search_symbols",
    "latest_quote",
    "price_series",
    "indicators",
    "detect_events",
}


@asynccontextmanager
async def mcp_session():
    """Spawn `python -m stocks_crew.mcp_server` and hand back an initialized session."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "stocks_crew.mcp_server"],
        env={**os.environ, "PYTHONPATH": os.path.join(REPO_ROOT, "src")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def test_server_starts_and_lists_exactly_the_expected_tools():
    async with mcp_session() as session:
        result = await session.list_tools()

    assert {t.name for t in result.tools} == EXPECTED_TOOLS


async def test_every_tool_is_described_for_the_agent():
    async with mcp_session() as session:
        result = await session.list_tools()

    for tool in result.tools:
        assert tool.description and tool.description.strip(), f"{tool.name} has no description"
        assert tool.inputSchema["type"] == "object"


async def test_indicators_advertises_its_optional_windows():
    async with mcp_session() as session:
        result = await session.list_tools()

    schema = next(t for t in result.tools if t.name == "indicators").inputSchema
    assert schema["required"] == ["symbol"]
    assert {"window_sma", "window_ema", "window_rsi"} <= set(schema["properties"])


async def test_explain_is_not_exposed_over_mcp():
    # The old demo had an `explain` tool that called OpenAI from inside the MCP
    # server. The crew is the LLM layer now; exposing its own output as a tool
    # would let it call itself.
    async with mcp_session() as session:
        result = await session.list_tools()

    assert "explain" not in {t.name for t in result.tools}


@pytest.mark.network
async def test_a_bad_symbol_comes_back_as_an_error_object_not_an_exception():
    # Reaches yfinance, but with a symbol that will not resolve; the point is
    # that the tool answers in-band rather than killing the JSON-RPC channel.
    async with mcp_session() as session:
        result = await session.call_tool("indicators", {"symbol": "__NOT_A_REAL_TICKER__"})

    payload = json.loads(result.content[0].text)
    assert payload["error"] in {"no_data", "indicators_failed"}


@pytest.mark.network
async def test_real_round_trip_for_a_live_symbol():
    async with mcp_session() as session:
        result = await session.call_tool("indicators", {"symbol": "AAPL"})

    payload = json.loads(result.content[0].text)
    assert payload["symbol"] == "AAPL"
    assert isinstance(payload["last_close"], (int, float))
