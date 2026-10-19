"""The tool layer: JSON in, JSON out, and failures reported in band.

An MCP tool that raises kills the JSON-RPC channel; one that returns
{"error": ...} lets the agent read the problem and react. These tests pin that
behaviour without touching the network.
"""

import json

import pandas as pd
import pytest

from stocks_crew import tools


@pytest.fixture
def broken_datasource(monkeypatch):
    """Make every datasource call raise, so the error paths are exercised."""

    def boom(*args, **kwargs):
        raise RuntimeError("yahoo is down")

    for name in ("ds_search", "ds_quote", "ds_series"):
        monkeypatch.setattr(tools, name, boom)


def test_all_five_tools_are_registered_for_the_server():
    assert [fn.__name__ for fn in tools.ALL_TOOLS] == [
        "search_symbols",
        "latest_quote",
        "price_series",
        "indicators",
        "detect_events",
    ]


def test_every_tool_returns_a_json_string(monkeypatch):
    monkeypatch.setattr(tools, "ds_search", lambda q: [{"symbol": "AAPL", "name": "Apple"}])
    monkeypatch.setattr(tools, "ds_quote", lambda s: {"symbol": s, "price": 1.0})
    monkeypatch.setattr(
        tools,
        "ds_series",
        lambda s, i="daily", lb=180: pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
                "open": [1.0, 2.0],
                "high": [1.0, 2.0],
                "low": [1.0, 2.0],
                "close": [1.0, 2.0],
                "volume": [10, 20],
            }
        ),
    )

    for fn in tools.ALL_TOOLS:
        out = fn("AAPL")
        assert isinstance(out, str)
        json.loads(out)  # must parse


@pytest.mark.parametrize("fn_name", ["latest_quote", "indicators", "detect_events"])
def test_object_tools_report_failure_in_band(broken_datasource, fn_name):
    payload = json.loads(getattr(tools, fn_name)("AAPL"))
    assert payload["symbol"] == "AAPL"
    assert payload["error"].endswith("_failed")
    assert "yahoo is down" in payload["message"]


def test_search_reports_failure_in_band(broken_datasource):
    payload = json.loads(tools.search_symbols("apple"))
    assert payload[0]["error"] == "search_failed"


def test_indicators_says_no_data_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(tools, "ds_series", lambda *a, **k: pd.DataFrame())
    payload = json.loads(tools.indicators("NOPE"))
    assert payload["error"] == "no_data"


def test_indicators_nulls_out_what_it_cannot_compute(monkeypatch):
    """Three closes is not enough for a 20-day SMA: report null, not a number.

    This is the honest-reporting contract the technical agent depends on — a
    null tells it to say "not enough history" instead of quoting a figure
    computed from three days and calling it a 20-day average.
    """
    monkeypatch.setattr(
        tools,
        "ds_series",
        lambda *a, **k: pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
                # One up day and one down day, so RSI has a loss to divide by.
                "close": [10.0, 11.0, 10.5],
            }
        ),
    )
    payload = json.loads(tools.indicators("AAPL"))
    assert payload["last_close"] == 10.5
    # SMA(20) needs min_periods=max(3, 10)=10 points; three is not enough.
    assert payload["sma"] is None
    # EMA and RSI are exponentially weighted, so they are defined from the
    # second point on -- thin, but not fabricated.
    assert payload["ema"] is not None
    assert payload["rsi"] is not None


def test_rsi_is_null_rather_than_100_when_nothing_ever_fell(monkeypatch):
    """A straight line up gives RSI no losses to divide by.

    The tool reports null instead of inventing 100. Worth knowing before an
    agent reads it: "null" means undefined here, not "we failed to fetch".
    """
    monkeypatch.setattr(
        tools,
        "ds_series",
        lambda *a, **k: pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
                "close": [10.0, 11.0, 12.0],
            }
        ),
    )
    payload = json.loads(tools.indicators("AAPL"))
    assert payload["rsi"] is None


@pytest.mark.network
def test_indicators_against_the_live_feed():
    payload = json.loads(tools.indicators("AAPL"))
    assert payload["symbol"] == "AAPL"
    assert isinstance(payload["last_close"], float)
