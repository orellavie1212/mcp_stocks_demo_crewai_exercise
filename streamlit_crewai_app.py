#!/usr/bin/env python3
"""Streamlit UI for the CrewAI + MCP stocks demo.

Run it with:

    streamlit run streamlit_crewai_app.py

The crew runs in this process and spawns the MCP server as a child process on
demand, so there is no API server to start first. `stocks_crew.api` is a
separate, optional entry point for HTTP clients — see the README.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

import streamlit as st

# Lets `streamlit run streamlit_crewai_app.py` work from a plain checkout, with
# or without `uv sync` having installed the package.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

try:
    from dotenv import load_dotenv

    # The sidebar checks os.environ for GCP_PROJECT and the provider keys, so
    # .env has to be in the environment (make exports it too; this covers a
    # plain `streamlit run`). Values already set in the shell win.
    load_dotenv(override=False)
except ImportError:
    pass

from stocks_crew import llm as llm_config  # noqa: E402
from stocks_crew.console import use_utf8_console  # noqa: E402
from stocks_crew.crew import default_agent_tools, list_mcp_tools, run_analysis

# CrewAI's console listener prints emoji; on a Windows cp1252 console that
# raises inside the event bus and floods the terminal with codec errors.
use_utf8_console()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

st.set_page_config(
    page_title="CrewAI + MCP Stocks Analysis",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .main-header { font-size: 2.5rem; font-weight: bold; color: #1f77b4;
                   text-align: center; margin-bottom: 1rem; }
    .agent-card  { border: 1px solid #ddd; border-radius: 10px; padding: 1rem;
                   margin: 0.5rem 0; background-color: #f9f9f9; color: #333; }
    .agent-card h4 { color: #1f77b4; margin-top: 0; }
    .agent-card p  { color: #333; margin: 0.4rem 0; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=300, show_spinner="Starting the MCP server…")
def cached_mcp_tools() -> list[dict]:
    """Spawn the MCP server and ask what it offers.

    Cached because every call is a real subprocess launch + JSON-RPC handshake;
    use the "Re-check" button to force a fresh one.
    """
    return list_mcp_tools()


def render_trace(trace: list[dict]) -> None:
    st.subheader("🔍 Tool call trace")
    if not trace:
        st.warning(
            "No tool calls were recorded. The agents answered without touching "
            "the MCP server — which means every number in the report above is "
            "the model's own invention. This is the failure mode the trace exists "
            "to make visible."
        )
        return

    ok = sum(1 for c in trace if c.get("success"))
    st.info(f"**{len(trace)} tool calls** over MCP — {ok} succeeded, {len(trace) - ok} failed.")
    for i, call in enumerate(trace, 1):
        icon = "✅" if call.get("success") else "❌"
        header = f"{i}. {icon} `{call.get('tool_name', '?')}` — {call.get('agent_role') or 'agent'}"
        with st.expander(header, expanded=False):
            st.markdown(f"**Arguments the model chose:** `{call.get('arguments')}`")
            st.markdown(
                f"**Duration:** {call.get('duration_seconds', 0)}s"
                f"{'  ·  *served from cache*' if call.get('from_cache') else ''}"
            )
            st.markdown(f"**At:** {call.get('timestamp', 'n/a')}")
            if call.get("error"):
                st.error(call["error"])
            if call.get("result_preview"):
                st.code(call["result_preview"], language="json")


def render_guardrail(guardrail: dict) -> None:
    """Show the verdict of the post-run grounding check (packages/shared-guardrails).

    Deliberately placed between the report and the trace: the report is the
    claim, this is the verdict, the trace is the evidence.
    """
    st.subheader("🛡️ Output guardrail")
    if not guardrail:
        st.caption("No guardrail result for this run.")
        return

    ungrounded = guardrail.get("ungrounded_figures") or []
    advice = guardrail.get("advice_phrases") or []

    if guardrail.get("passed"):
        st.success(
            f"✅ Passed — all {guardrail.get('figures_checked', 0)} figures in the report "
            f"match values returned by the tools, and no investment advice was detected."
        )
    else:
        st.error(
            f"❌ Failed — {len(ungrounded)} ungrounded figure(s), "
            f"{len(advice)} advice phrase(s), out of "
            f"{guardrail.get('figures_checked', 0)} figures checked."
        )

    if ungrounded:
        st.markdown(
            "**Figures that appear in no tool output.** The report states these; "
            "nothing the MCP server returned contains them."
        )
        st.code(", ".join(ungrounded))
    if advice:
        st.markdown("**Advice-shaped phrasing** — every task brief forbids this:")
        for hit in advice:
            st.warning(f"`{hit.get('phrase')}` — …{hit.get('context')}…")
    for note in guardrail.get("notes") or []:
        st.caption(note)
    st.caption(
        f"Checked against {guardrail.get('evidence_values', 0)} distinct numbers from the "
        "tool results. This check is deterministic Python, not another model — a "
        "guardrail that can hallucinate is not a guardrail."
    )

    render_crewai_guardrail(guardrail.get("crewai_guardrail") or {})


def render_crewai_guardrail(status: dict) -> None:
    """Say plainly what CrewAI's own HallucinationGuardrail did on this run.

    Which, on the open-source package, is nothing. This panel exists so the
    framework's guardrail cannot be mistaken for a working one — an unenforced
    check that looks enforced is the failure mode worth showing students.
    """
    if not status.get("attached_to"):
        return

    attached = ", ".join(status["attached_to"])
    if status.get("enforcing"):
        st.info(f"🧩 CrewAI `HallucinationGuardrail` ran on: **{attached}**.")
    else:
        st.warning(
            f"🧩 CrewAI `HallucinationGuardrail` is attached to **{attached}** and "
            "**did not check anything**. It is a no-op placeholder in the "
            "open-source `crewai` package: every output passes, whatever it says. "
            "The verdict above is from the grounding check, which actually ran."
        )


def build_download(result: dict) -> str:
    guardrail = result.get("guardrail") or {}
    lines = [result.get("result", ""), "", "=" * 60, "OUTPUT GUARDRAIL", "=" * 60]
    if guardrail:
        lines += [
            f"  Passed:            {guardrail.get('passed')}",
            f"  Figures checked:   {guardrail.get('figures_checked')}",
            f"  Ungrounded:        {', '.join(guardrail.get('ungrounded_figures') or []) or 'none'}",
            f"  Advice phrases:    "
            f"{', '.join(h.get('phrase', '') for h in guardrail.get('advice_phrases') or []) or 'none'}",
        ]
    else:
        lines.append("  (not available)")

    lines += ["", "=" * 60, "TOOL CALL TRACE (MCP)", "=" * 60]
    for call in result.get("tool_trace", []):
        lines += [
            "",
            f"[{call.get('timestamp')}] {call.get('tool_name')}  ({call.get('agent_role')})",
            f"  Arguments: {call.get('arguments')}",
            f"  Success:   {call.get('success')}",
            f"  Duration:  {call.get('duration_seconds')}s",
        ]
        if call.get("error"):
            lines.append(f"  Error:     {call.get('error')}")
    return "\n".join(lines)


def main() -> None:
    st.markdown('<h1 class="main-header">🤖 CrewAI + MCP Stocks Analysis</h1>', unsafe_allow_html=True)
    st.markdown(
        "Four specialized agents analyse a stock by calling a **real MCP server** "
        "over stdio. Every tool call is recorded, so you can check the report "
        "against what the agents actually looked up."
    )

    # ---------------- sidebar ----------------
    with st.sidebar:
        st.header("⚙️ Configuration")

        st.subheader("🧠 Model provider")
        # Same crew, same MCP server, different vendor behind the LLM — the
        # provider is a one-line swap because nothing below build_crew() knows
        # which model it is talking to.
        configured = llm_config.configured_providers()
        provider_names = llm_config.provider_names()
        provider = st.selectbox(
            "Provider",
            provider_names,
            index=provider_names.index(llm_config.default_provider()),
            format_func=lambda name: (
                f"{llm_config.PROVIDERS[name].label}"
                f"{' ✅' if name in configured else ''}"
            ),
        )
        spec = llm_config.PROVIDERS[provider]
        st.caption(f"Model: `{llm_config.model_for(provider)}`")

        api_key = ""
        if not spec.api_key_env:
            # Vertex AI authenticates with application-default credentials
            # (`gcloud auth application-default login`), not a key.
            st.success("✅ Uses your Google Cloud login (application-default credentials)")
            if not os.getenv("GCP_PROJECT") or os.getenv("GCP_PROJECT") == "your-gcp-project-id":
                st.warning("⚠️ Set GCP_PROJECT in .env to your GCP project id")
        else:
            st.subheader(f"🔑 {spec.label} API key")
            # Keyed per provider so switching vendors does not send an OpenAI
            # key to Google.
            session_key = f"api_key_{provider}"
            api_key = st.text_input(
                "Key",
                value=st.session_state.get(session_key, ""),
                type="password",
                key=f"input_{session_key}",
                help=f"Falls back to {spec.api_key_env} from .env if left blank.",
            ) or ""
            if api_key:
                st.session_state[session_key] = api_key
                st.success("✅ Key set for this session")
            elif os.getenv(spec.api_key_env):
                st.info(f"Using {spec.api_key_env} from the environment")
            else:
                st.warning(f"⚠️ No {spec.api_key_env} — the crew cannot run")

        st.markdown("---")
        st.subheader("🔌 MCP server")
        if st.button("🔄 Re-check", use_container_width=True):
            cached_mcp_tools.clear()

        try:
            offered = cached_mcp_tools()
            st.success(f"✅ Handshake OK — {len(offered)} tools")
            for t in offered:
                st.caption(f"• `{t['name']}`")
        except Exception as e:
            offered = []
            st.error(f"❌ Could not reach the MCP server: {e}")

        st.markdown("---")
        st.subheader("📝 Report options")
        language = st.selectbox(
            "Language",
            ["en", "he"],
            index=0,
            format_func=lambda c: {"en": "English", "he": "עברית (Hebrew)"}[c],
        )
        tone = st.selectbox("Tone", ["professional", "concise", "educational"], index=0)
        horizon_days = st.slider("Horizon (days)", 5, 180, 30, step=5)

    tool_names = [t["name"] for t in offered]
    defaults = default_agent_tools()

    # ---------------- agents ----------------
    st.header("👥 The crew")
    st.caption(
        "Each agent may only call the MCP tools selected here. Take a tool away "
        "and watch the report degrade — that is the demo."
    )

    overrides: dict[str, list[str]] = {}
    # Display names only. The crew itself is whatever agents.yaml declares — an
    # agent with no entry here still gets a column, labelled with its key. That
    # is why adding the sector agent needed nothing but YAML.
    labels = {
        "research": "🔍 Research Agent",
        "technical": "📈 Technical Analyst",
        "sector": "🏢 Sector Analyst",
        "report": "📝 Report Writer",
    }
    columns = st.columns(len(defaults) or 1)
    for col, (agent_name, default_tools) in zip(columns, defaults.items()):
        with col:
            st.markdown(
                f'<div class="agent-card"><h4>{labels.get(agent_name, agent_name)}</h4>'
                f"<p>Runs step {list(defaults).index(agent_name) + 1} "
                f"of {len(defaults)}.</p></div>",
                unsafe_allow_html=True,
            )
            overrides[agent_name] = st.multiselect(
                "MCP tools",
                options=tool_names or default_tools,
                default=[t for t in default_tools if not tool_names or t in tool_names],
                key=f"tools_{agent_name}",
            )

    # ---------------- run ----------------
    st.header("🚀 Run analysis")
    col1, col2 = st.columns([2, 1])
    with col1:
        symbol = st.text_input(
            "Stock symbol",
            value=st.session_state.get("selected_symbol", "AAPL"),
            placeholder="AAPL, NVDA, TSLA…",
        ).strip().upper()
        st.session_state["selected_symbol"] = symbol
    with col2:
        st.metric("Selected symbol", symbol or "—")

    run = st.button("🔍 Start analysis", type="primary", disabled=not symbol)
    if st.button("🗑️ Clear results"):
        st.session_state.pop("analysis_result", None)
        st.rerun()

    if run:
        progress_bar = st.progress(0)
        status = st.empty()

        def on_progress(message: str, percent: int) -> None:
            status.text(message)
            progress_bar.progress(min(max(percent, 0), 100))

        with st.spinner("The crew is working…"):
            result = run_analysis(
                symbol,
                provider=provider,
                api_key=api_key,
                language=language,
                tone=tone,
                horizon_days=horizon_days,
                tool_overrides=overrides,
                progress=on_progress,
            )
        st.session_state["analysis_result"] = result
        progress_bar.empty()
        status.empty()

    # ---------------- results ----------------
    result = st.session_state.get("analysis_result")
    if not result:
        return

    st.header("📋 Results")
    if not result.get("success"):
        st.error(f"❌ {result.get('error_code', 'error')}: {result.get('error')}")
        return

    ran_on = result.get("model")
    st.success(
        f"✅ Analysis completed for {result.get('symbol')}"
        + (f" · ran on `{ran_on}`" if ran_on else "")
    )
    st.markdown(result.get("result", ""))

    render_guardrail(result.get("guardrail", {}))
    render_trace(result.get("tool_trace", []))

    with st.expander("🧰 What each agent was allowed to call"):
        st.json(result.get("agent_tools", {}))
        st.caption(f"Server advertised: {', '.join(result.get('mcp_tools', []))}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.download_button(
        "📥 Download report (with tool trace)",
        data=build_download(result),
        file_name=f"crewai_analysis_{result.get('symbol', 'stock')}_{stamp}.txt",
        mime="text/plain",
    )
    st.download_button(
        "📥 Download raw result (JSON)",
        data=json.dumps(result, indent=2, default=str),
        file_name=f"crewai_analysis_{result.get('symbol', 'stock')}_{stamp}.json",
        mime="application/json",
    )

    st.markdown("---")
    st.caption("CrewAI + MCP stocks demo · data from Yahoo Finance · not investment advice")


if __name__ == "__main__":
    main()
