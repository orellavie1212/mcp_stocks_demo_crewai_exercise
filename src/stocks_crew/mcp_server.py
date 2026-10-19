# mcp_server.py — a real MCP server over stdio.
#
# Run it directly:
#     uv run stocks-crew-mcp
#     uv run python -m stocks_crew.mcp_server
#
# It will sit on stdin and print nothing to stdout; that silence is correct,
# because stdout carries the JSON-RPC frames. crew.py spawns this module as a
# subprocess and talks to it through MCPServerAdapter.
#
# Each tool below is a thin @mcp.tool() wrapper over a function in tools.py.
# The split is deliberate: api.py's data routes call the same tools.py functions
# directly, in process, so the analytics stay importable and testable without
# starting a server. What lives *here* is the MCP contract — the tool name (the
# function name), the input schema (the type annotations), and the description
# (the docstring). That docstring is what the agents on the other side actually
# read when they decide which tool to call, so it is written for them.
#
# Note there is no `explain` tool here, though the previous version of this demo
# had one. The LLM layer sits *above* MCP, in the crew that consumes these tools
# — if the crew's own output were also a tool, the crew could call itself.
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools

mcp = FastMCP("stocks-analyzer")


@mcp.tool()
def search_symbols(query: str) -> str:
    """Look up ticker symbols by company name or partial ticker.

    Call this to confirm a symbol exists before analysing it. Do not guess or
    invent ticker symbols.

    Args:
        query: Company name or ticker text, e.g. "NVIDIA" or "NVDA".

    Returns:
        JSON array of {symbol, name, region, currency} objects.
    """
    return tools.search_symbols(query)


@mcp.tool()
def latest_quote(symbol: str) -> str:
    """Get the latest price, absolute/percent change, and volume for a symbol.

    Call this for current price data. Do not estimate or recall prices.

    Args:
        symbol: Ticker symbol, e.g. "AAPL".

    Returns:
        JSON object with symbol, price, change, change_percent, volume, timestamp.
    """
    return tools.latest_quote(symbol)


@mcp.tool()
def price_series(symbol: str, interval: str = "daily", lookback: int = 180) -> str:
    """Get the historical OHLCV price series for a symbol.

    Call this for historical prices. Do not invent history.

    Args:
        symbol: Ticker symbol, e.g. "AAPL".
        interval: Bar size; only "daily" is supported today.
        lookback: Number of trading days to return (default: 180).

    Returns:
        JSON array of {date, open, high, low, close, volume} rows, dates in ISO format.
    """
    return tools.price_series(symbol, interval, lookback)


@mcp.tool()
def indicators(
    symbol: str,
    window_sma: int = 20,
    window_ema: int = 50,
    window_rsi: int = 14,
) -> str:
    """Compute the current technical indicator snapshot for a symbol.

    Call this rather than computing SMA/EMA/RSI yourself.

    Args:
        symbol: Ticker symbol, e.g. "AAPL".
        window_sma: Simple moving average window in days (default: 20).
        window_ema: Exponential moving average span in days (default: 50).
        window_rsi: RSI period in days (default: 14).

    Returns:
        JSON object with symbol, last_close, sma, ema, rsi. Any indicator with
        insufficient history comes back as null.
    """
    return tools.indicators(symbol, window_sma, window_ema, window_rsi)


@mcp.tool()
def detect_events(symbol: str) -> str:
    """Detect notable technical events on the most recent trading day.

    Checks for price gaps (>=3% vs. the previous close), volatility spikes
    (a daily move beyond 2x the 20-day rolling volatility), and 52-week extremes.
    Call this rather than inferring events from the price series.

    Args:
        symbol: Ticker symbol, e.g. "AAPL".

    Returns:
        JSON object with symbol, date, and the boolean flags gap_up, gap_down,
        vol_spike, is_52w_high, is_52w_low.
    """
    return tools.detect_events(symbol)


def main() -> None:
    """Entry point for the `stocks-crew-mcp` console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
