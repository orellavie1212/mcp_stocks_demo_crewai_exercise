"""The tool trace is the demo's evidence, so it gets its own tests.

These drive the CrewAI event bus directly rather than running a crew: emit the
events a real run would emit, and check the tracer turns them into records. No
LLM, no MCP subprocess.
"""

from stocks_crew import _paths  # noqa: F401
from datetime import datetime, timedelta, timezone

from crewai.events import (
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
    crewai_event_bus,
)

from tool_tracer import PREVIEW_CHARS, ToolTracer  # packages/shared-observability


def _finished(tool_name="indicators", output="{\"rsi\": 61.4}", seconds=0.25, **kw):
    start = datetime.now(timezone.utc)
    return ToolUsageFinishedEvent(
        tool_name=tool_name,
        tool_args={"symbol": "AAPL"},
        started_at=start,
        finished_at=start + timedelta(seconds=seconds),
        output=output,
        agent_role="Technical Analysis Expert",
        **kw,
    )


def test_a_finished_tool_call_is_recorded():
    with ToolTracer() as tracer:
        crewai_event_bus.emit(None, _finished())

    assert len(tracer.calls) == 1
    call = tracer.calls[0]
    assert call["tool_name"] == "indicators"
    assert call["arguments"] == {"symbol": "AAPL"}
    assert call["success"] is True
    assert call["duration_seconds"] == 0.25
    assert call["agent_role"] == "Technical Analysis Expert"
    assert "61.4" in call["result_preview"]


def test_an_erroring_tool_call_is_recorded_as_failed():
    with ToolTracer() as tracer:
        crewai_event_bus.emit(
            None,
            ToolUsageErrorEvent(
                tool_name="latest_quote",
                tool_args={"symbol": "AAPL"},
                error="connection reset",
            ),
        )

    call = tracer.calls[0]
    assert call["success"] is False
    assert "connection reset" in call["error"]


def test_long_output_is_truncated_to_a_preview():
    with ToolTracer() as tracer:
        crewai_event_bus.emit(None, _finished(output="x" * (PREVIEW_CHARS * 3)))

    assert len(tracer.calls[0]["result_preview"]) == PREVIEW_CHARS


def test_evidence_keeps_the_full_output_the_preview_truncates():
    """guardrails.py checks against `evidence`, so it must not be truncated.

    A 240-character preview of a 180-row price series would make row 40 look
    like an invented number.
    """
    with ToolTracer() as tracer:
        crewai_event_bus.emit(None, _finished(output="y" * (PREVIEW_CHARS * 3)))

    assert len(tracer.calls[0]["result_preview"]) == PREVIEW_CHARS
    assert len(tracer.evidence[0]) == PREVIEW_CHARS * 3


def test_a_failed_call_contributes_no_evidence():
    """An error string is not a tool result; its digits must ground nothing."""
    with ToolTracer() as tracer:
        crewai_event_bus.emit(
            None,
            ToolUsageErrorEvent(tool_name="latest_quote", tool_args={}, error="503 at 189.43"),
        )

    assert tracer.calls, "the failed call should still be traced"
    assert tracer.evidence == []


def test_summary_counts_successes_and_failures():
    with ToolTracer() as tracer:
        crewai_event_bus.emit(None, _finished(tool_name="indicators"))
        crewai_event_bus.emit(None, _finished(tool_name="detect_events"))
        crewai_event_bus.emit(
            None,
            ToolUsageErrorEvent(tool_name="latest_quote", tool_args={}, error="boom"),
        )

    summary = tracer.summary()
    assert summary["tool_calls_count"] == 3
    assert summary["tool_calls_successful"] == 2
    assert [c["tool_name"] for c in summary["tool_trace"]][:2] == ["indicators", "detect_events"]


def test_the_tracer_stops_listening_when_the_block_ends():
    """Otherwise every run would inherit the previous run's handlers."""
    with ToolTracer() as first:
        crewai_event_bus.emit(None, _finished())

    with ToolTracer() as second:
        crewai_event_bus.emit(None, _finished())

    assert len(first.calls) == 1, "the first tracer kept recording after its block ended"
    assert len(second.calls) == 1


def test_a_broken_callback_does_not_break_the_run():
    def explode(_entry):
        raise RuntimeError("the UI fell over")

    with ToolTracer(on_call=explode) as tracer:
        crewai_event_bus.emit(None, _finished())

    assert len(tracer.calls) == 1
