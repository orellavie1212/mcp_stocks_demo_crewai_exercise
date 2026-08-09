#!/usr/bin/env python3
"""
CrewAI Agents for Stock Analysis

This module contains all agent-related code including agent creation,
task creation, and the main analysis execution function.
"""

import contextlib
import json
import os
import sys
import time
import threading
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

try:
    from crewai import Agent, Task, Crew, Process
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False

# Same provider switcher (OpenAI/google_ai_studio/vertex_ai, optionally
# routed through the LiteLLM proxy) and evidence-grounding guardrail that
# apps/agent-runtime uses — Lab 1 gets consistent behavior with Labs 2-4
# instead of its own separate, Gemini-only LLM setup.
_here = os.path.dirname(os.path.abspath(__file__))
for _p in [
    os.path.join(_here, "packages", "shared-config"),
    os.path.join(_here, "packages", "shared-guardrails"),
    os.path.join(_here, "packages", "shared-observability"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from config import get_settings, get_llm
    from grounding import check_report as check_report_grounding
    from tool_tracer import ToolTracer
    SHARED_CONFIG_AVAILABLE = True
except ImportError:
    SHARED_CONFIG_AVAILABLE = False

try:
    from mcp_server import (
        get_tools_by_names,
        CREWAI_AVAILABLE as MCP_TOOLS_AVAILABLE,
    )
    if CREWAI_AVAILABLE:
        CREWAI_AVAILABLE = MCP_TOOLS_AVAILABLE
    MCP_API_URL = "http://127.0.0.1:8001"
except ImportError:
    CREWAI_AVAILABLE = False
    MCP_TOOLS_AVAILABLE = False
    MCP_API_URL = "http://127.0.0.1:8001"
    def get_tools_by_names(tool_names: list, openai_api_key: str = "") -> list:
        return []


def _hallucination_guardrail(llm):
    """CrewAI's own built-in output guardrail, attached to the report task
    as a teaching contrast against grounding.check_report() above.

    Same helper as apps/agent-runtime/worker.py::_hallucination_guardrail —
    see that docstring for why this is wired up for the contrast, not the
    protection: it's a no-op in the open-source crewai package (only the
    hosted platform installs the real check it defers to). `enforcing` is
    detected from the module's `_validate_output_hook` sentinel rather than
    hardcoded, so it keeps telling the truth if that ever changes.

    Returns (guardrail_or_None, status_dict).
    """
    try:
        from crewai.tasks.hallucination_guardrail import HallucinationGuardrail
        from crewai.tasks import hallucination_guardrail as _hg_module
    except ImportError:
        return None, {
            "available": False,
            "enforcing": False,
            "note": "This crewai build has no HallucinationGuardrail.",
        }

    enforcing = callable(getattr(_hg_module, "_validate_output_hook", None))
    status = {
        "available": True,
        "enforcing": enforcing,
        "note": (
            "CrewAI's HallucinationGuardrail is active on the report task."
            if enforcing else
            "CrewAI's HallucinationGuardrail is attached to the report task "
            "but is a no-op in the open-source package — it passes every "
            "output. The grounding check above is what's actually verifying "
            "figures against tool evidence."
        ),
    }
    return HallucinationGuardrail(llm=llm), status


def create_agents(
    gemini_api_key: str = "",
    research_tools: Optional[List[str]] = None,
    technical_tools: Optional[List[str]] = None,
    sector_tools: Optional[List[str]] = None,
    report_tools: Optional[List[str]] = None,
    provider: Optional[str] = None,
    api_key: str = "",
) -> Dict[str, Agent]:
    """
    Create CrewAI agents with configurable tools.

    Args:
        gemini_api_key: Gemini API key. Two uses: (1) the crew's own LLM
            when provider/api_key are omitted — kept for backward
            compatibility; (2) always fed to get_explanation, which is a
            separate, Gemini/Vertex-only tool (see mcp_server.py::explain)
            unaffected by provider/api_key below.
        research_tools: List of tool names for research agent
        technical_tools: List of tool names for technical agent
        sector_tools: List of tool names for sector agent
        report_tools: List of tool names for report agent
        provider: Crew LLM vendor — 'openai', 'google_ai_studio', or
            'vertex_ai'. Omitted, follows LLM_PROVIDER (or gemini_api_key,
            for backward compatibility with callers that only ever set
            that). Same switcher apps/agent-runtime uses.
        api_key: This run's key for `provider`, when it isn't Gemini's
            (which uses gemini_api_key instead, for backward compatibility).

    Returns:
        Dictionary of agents
    """
    if not CREWAI_AVAILABLE:
        return {}

    if SHARED_CONFIG_AVAILABLE:
        # Same provider switcher apps/agent-runtime uses (openai /
        # google_ai_studio / vertex_ai, or the LiteLLM proxy when
        # LITELLM_BASE_URL is set) — driven by LLM_PROVIDER in .env by
        # default. provider=/api_key= (or a sidebar-entered gemini_api_key,
        # for the Gemini/Vertex case) override it for a single run.
        if gemini_api_key:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            get_settings.cache_clear()
        effective_api_key = api_key or (gemini_api_key if provider in (None, "google_ai_studio") else "")
        main_llm = get_llm("main", provider=provider, api_key=effective_api_key)
    else:
        # Fallback for running agents.py standalone, without packages/
        # present (e.g. a bare checkout of just the Lab 1 files).
        llm_provider = os.environ.get("LLM_PROVIDER", "google_ai_studio")
        from crewai import LLM as CrewLLM

        gcp_project = os.environ.get("GCP_PROJECT", "local-project")

        if llm_provider == "vertex_ai" or not gemini_api_key:
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("GOOGLE_API_KEY", None)
            vertex_loc = os.environ.get("VERTEX_LOCATION", "global")
            os.environ["VERTEX_LOCATION"] = vertex_loc
            os.environ["VERTEX_AI_LOCATION"] = vertex_loc
            os.environ["VERTEX_API_LOCATION"] = vertex_loc
            main_llm = CrewLLM(
                model="vertex_ai/gemini-3.5-flash",
                vertex_project=gcp_project,
                vertex_location=vertex_loc,
                temperature=0.0
            )
        else:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            main_llm = CrewLLM(
                model="gemini/gemini-3.5-flash",
                api_key=gemini_api_key,
                temperature=0.0
            )

    if research_tools is None:
        research_tools = ["search_symbols", "get_quote", "get_price_series"]
    if technical_tools is None:
        technical_tools = ["get_indicators", "get_events", "get_explanation"]
    if sector_tools is None:
        sector_tools = ["search_symbols", "get_quote", "get_indicators"]
    if report_tools is None:
        report_tools = []

    research_tool_funcs = get_tools_by_names(research_tools, gemini_api_key)
    technical_tool_funcs = get_tools_by_names(technical_tools, gemini_api_key)
    sector_tool_funcs = get_tools_by_names(sector_tools, gemini_api_key)
    report_tool_funcs = get_tools_by_names(report_tools, gemini_api_key)

    research_agent = Agent(
        role="Stock Research Specialist",
        goal="Gather comprehensive basic information about stocks including current quotes, historical data, and company details",
        backstory="""You are an experienced stock researcher with deep knowledge of financial markets.
        Your expertise lies in efficiently gathering and organizing stock data from multiple sources.
        You excel at finding relevant information quickly and presenting it in a clear, structured format.""",
        tools=research_tool_funcs,
        llm=main_llm,
        verbose=True,
        allow_delegation=False
    )

    technical_agent = Agent(
        role="Technical Analysis Expert",
        goal="Perform detailed technical analysis using indicators, patterns, and market events to assess stock momentum and trends",
        backstory="""You are a seasoned technical analyst with 15+ years of experience in chart analysis and market indicators.
        You specialize in interpreting technical signals, identifying patterns, and understanding market psychology.
        Your analysis is methodical and based on proven technical analysis principles.""",
        tools=technical_tool_funcs,
        llm=main_llm,
        verbose=True,
        allow_delegation=False
    )

    sector_agent = Agent(
        role="Sector Comparison Specialist",
        goal="Compare the target stock's performance, valuation, and fundamentals against its sector peers to identify relative strengths and weaknesses",
        backstory="""You are a seasoned sector analyst with 15 years of experience in comparative market analysis.
        You specialize in identifying sector trends, peer comparisons, and relative performance metrics.
        Your expertise lies in understanding how individual stocks perform within their sector context
        and identifying which companies are sector leaders or laggards.""",
        tools=sector_tool_funcs,
        llm=main_llm,
        verbose=True,
        allow_delegation=False
    )

    report_agent = Agent(
        role="Financial Report Writer",
        goal="Create comprehensive, well-structured investment reports that synthesize research, technical analysis, and sector comparison into actionable insights",
        backstory="""You are a professional financial writer with expertise in translating complex market data into clear,
        actionable reports. You have a talent for presenting technical information in an accessible way while maintaining
        accuracy and professional standards. Your reports are known for their clarity and practical insights.""",
        tools=report_tool_funcs,
        llm=main_llm,
        verbose=True,
        allow_delegation=False
    )

    return {
        "research": research_agent,
        "technical": technical_agent,
        "sector": sector_agent,
        "report": report_agent
    }

def create_tasks(
    symbol: str,
    gemini_api_key: str = "",
    research_tools: Optional[List[str]] = None,
    technical_tools: Optional[List[str]] = None,
    sector_tools: Optional[List[str]] = None
) -> List[Task]:
    """
    Create CrewAI tasks with configurable tools.

    Args:
        symbol: Stock symbol to analyze
        gemini_api_key: Gemini API key (passed to explanation tool) (optional if using LLM_PROVIDER=vertex_ai)
        research_tools: List of tool names for research task
        technical_tools: List of tool names for technical task
        sector_tools: List of tool names for sector task

    Returns:
        List of tasks
    """
    if not CREWAI_AVAILABLE:
        return []

    if research_tools is None:
        research_tools = ["search_symbols", "get_quote", "get_price_series"]
    if technical_tools is None:
        technical_tools = ["get_indicators", "get_events", "get_explanation"]
    if sector_tools is None:
        sector_tools = ["search_symbols", "get_quote", "get_indicators"]

    research_tool_funcs = get_tools_by_names(research_tools, gemini_api_key)
    technical_tool_funcs = get_tools_by_names(technical_tools, gemini_api_key)
    sector_tool_funcs = get_tools_by_names(sector_tools, gemini_api_key)
    
    research_task = Task(
        description=f"""
        Conduct comprehensive research on the stock symbol '{symbol}'. 
        
        YOU MUST CALL THESE TOOLS IN ORDER:
        
        1. FIRST: Call search_symbols(q='{symbol}') to verify the symbol exists and get company information
           - You MUST use the actual output from this tool call
           - Do not guess or make up company names
        
        2. SECOND: Call get_quote(symbol='{symbol}') to get the current price, change percentage, and volume
           - You MUST use the actual price data from this tool call
           - Do not estimate or guess prices
        
        3. THIRD: Call get_price_series(symbol='{symbol}') to retrieve historical price data
           - You MUST use the actual historical data from this tool call
           - Do not make up historical prices
        
        After calling all three tools, organize the data in a clear, structured format.
        Your report MUST reference specific values from the tool outputs (e.g., "Current price is $X.XX").
        """,
        expected_output="A comprehensive research report that MUST include: (1) Symbol verification from search_symbols output, (2) Exact current price and change from get_quote output, (3) Historical price summary with specific values from get_price_series output. All data must be traceable to tool calls.",
        agent=None,
        tools=research_tool_funcs
    )
    
    technical_task = Task(
        description=f"""
        Perform detailed technical analysis on '{symbol}' using the research data provided.
        
        YOU MUST CALL THESE TOOLS:
        
        1. Call get_indicators(symbol='{symbol}') to get technical indicators (SMA, EMA, RSI)
           - You MUST use the actual indicator values from this tool call
           - Reference specific values: "SMA is $X.XX", "RSI is YY.YY"
           - Do not calculate indicators yourself
        
        2. Call get_events(symbol='{symbol}') to detect market events
           - You MUST use the actual events detected by this tool call
           - Reference specific events: "Gap Up detected", "52-week high", etc.
           - Do not infer events without calling this tool
        
        3. Call get_explanation(symbol='{symbol}') to get AI-powered analysis
           - This tool automatically fetches indicators and events data
           - You only need to provide the symbol
           - Use the explanation to understand the technical context
        
        After calling all tools, assess overall technical momentum and identify key support/resistance levels.
        Your analysis MUST reference specific values from the tool outputs.
        """,
        expected_output="A detailed technical analysis report that MUST include: (1) Specific indicator values from get_indicators output (SMA, EMA, RSI with exact numbers), (2) Specific events from get_events output, (3) AI explanation from get_explanation output. All analysis must be traceable to tool calls.",
        agent=None,
        tools=technical_tool_funcs,
        context=[research_task]
    )
    
    sector_task = Task(
        description=f"""
        Using the company information from the research analysis and technical indicators from the technical analysis, 
        identify 3-5 key sector peers for the target stock '{symbol}'. Compare the target stock against these peers across multiple dimensions:
        
        1. Price performance (1M, 3M, 6M, 1Y)
        2. Key financial metrics (P/E ratio, market cap, revenue growth)
        3. Technical indicators (RSI, moving averages, volatility)
        4. Sector positioning and competitive advantages
        
        Provide a comprehensive sector comparison that highlights where the target stock stands relative to its peers.
        """,
        expected_output="""A detailed sector comparison report including:
        1. List of 3-5 identified sector peers with brief company descriptions
        2. Comparative performance table showing key metrics for target stock vs peers
        3. Analysis of relative strengths and weaknesses
        4. Sector positioning summary with investment implications
        5. Overall sector outlook and target stock's position within it""",
        agent=None,
        tools=sector_tool_funcs,
        context=[research_task, technical_task]
    )
    
    report_task = Task(
        description=f"""
        Create a comprehensive investment analysis report for '{symbol}' that synthesizes all research, technical analysis, and sector comparison. 
        The report should include:
        
        1. Executive Summary with key findings
        2. Current Market Position (price, volume, recent changes)
        3. Technical Analysis Summary (indicators, events, trends)
        4. Sector Comparison Analysis (peer performance, relative positioning)
        5. Risk Assessment based on technical signals and sector context
        6. Key Takeaways and Observations
        7. Professional disclaimers
        
        Write in a professional, clear style suitable for investment decision-making. 
        Include specific data points and technical observations from all analyses.
        """,
        expected_output="A professional investment analysis report with executive summary, technical analysis, sector comparison, risk assessment, and key takeaways",
        agent=None,
        context=[research_task, technical_task, sector_task]
    )
    
    return [research_task, technical_task, sector_task, report_task]

def run_crewai_analysis(
    symbol: str,
    gemini_api_key: str = "",
    progress_callback: Optional[Callable[[str, Optional[int]], None]] = None,
    verbose_callback: Optional[Callable[[str], None]] = None,
    research_tools: Optional[List[str]] = None,
    technical_tools: Optional[List[str]] = None,
    sector_tools: Optional[List[str]] = None,
    report_tools: Optional[List[str]] = None,
    provider: Optional[str] = None,
    api_key: str = "",
) -> Dict[str, Any]:
    """Run CrewAI analysis with progress tracking.

    provider/api_key pick the crew's own LLM vendor for this run — 'openai',
    'google_ai_studio', or 'vertex_ai' — same switcher apps/agent-runtime
    uses. Omitted, `provider` follows LLM_PROVIDER same as before. Kept
    separate from gemini_api_key, which is Gemini/Vertex-only and also
    feeds the unrelated get_explanation tool (see create_agents' docstring).
    """
    if not CREWAI_AVAILABLE:
        return {"error": "CrewAI not available"}

    gemini_api_key = gemini_api_key.strip() if gemini_api_key else ""
    api_key = api_key.strip() if api_key else ""
    llm_provider = (provider or os.environ.get("LLM_PROVIDER", "google_ai_studio")).strip().lower()

    if llm_provider == "openai":
        if not api_key:
            return {
                "success": False,
                "error": "OpenAI API key is required. Please enter it in the sidebar.",
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
            }
    elif llm_provider != "vertex_ai" and not gemini_api_key:
        env_api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if env_api_key:
            gemini_api_key = env_api_key
        else:
            return {
                "success": False,
                "error": "Gemini API key is required. Please enter it in the sidebar or configure LLM_PROVIDER=vertex_ai to use application default credentials.",
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol
            }

    if llm_provider == "vertex_ai":
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
    elif llm_provider != "openai" and gemini_api_key:
        os.environ["GOOGLE_API_KEY"] = gemini_api_key

    try:
        from mcp_server import clear_tool_trace
        clear_tool_trace()

        if progress_callback:
            progress_callback("🔧 Creating agents...", 5)

        agents = create_agents(
            gemini_api_key, research_tools, technical_tools, sector_tools, report_tools,
            provider=llm_provider, api_key=api_key,
        )

        if progress_callback:
            progress_callback("📋 Setting up tasks...", 8)

        tasks = create_tasks(symbol, gemini_api_key, research_tools, technical_tools, sector_tools)
        
        tasks[0].agent = agents["research"]
        tasks[1].agent = agents["technical"]
        tasks[2].agent = agents["sector"]
        tasks[3].agent = agents["report"]

        hallucination_guardrail, hallucination_status = _hallucination_guardrail(agents["report"].llm)
        tasks[3].guardrail = hallucination_guardrail

        if progress_callback:
            progress_callback("👥 Assembling crew...", 10)
        
        crew = Crew(
            agents=list(agents.values()),
            tasks=tasks,
            process=Process.sequential,
            verbose=True
        )
        
        if progress_callback:
            progress_callback("🚀 Starting comprehensive analysis...", 10)
        
        if progress_callback:
            progress_callback("📊 Executing crew workflow...", 20)
        
        if progress_callback:
            progress_callback("🔍 Testing MCP API connectivity...", 15)
        
        try:
            test_response = requests.get(f"{MCP_API_URL}/health", timeout=5)
            if test_response.status_code != 200:
                raise Exception(f"MCP API server returned status {test_response.status_code}")
        except Exception as e:
            raise Exception(f"MCP API server is not responding at {MCP_API_URL}. Please start the API server: uvicorn api:app --host 127.0.0.1 --port 8001")
        
        if progress_callback:
            progress_callback("📊 Executing analysis...", 20)
        
        def timeout_handler():
            if verbose_callback:
                verbose_callback("⏰ Crew execution timed out after 2 minutes")
            raise TimeoutError("Crew execution timed out")
        
        timer = threading.Timer(120.0, timeout_handler)
        timer.start()
        
        try:
            if progress_callback:
                progress_callback("🔄 Executing tasks...", 30)
            
            from io import StringIO
            
            class VerboseCapture:
                def __init__(self, callback):
                    self.callback = callback
                    self.buffer = StringIO()
                
                def write(self, text):
                    if text.strip():
                        self.callback(f"🤖 {text.strip()}")
                    return len(text)
                
                def flush(self):
                    pass
            
            old_stdout = sys.stdout
            old_stderr = sys.stderr
                
            
            if verbose_callback:
                verbose_capture = VerboseCapture(verbose_callback)
                sys.stdout = verbose_capture
                sys.stderr = verbose_capture

            # ToolTracer listens on CrewAI's own event bus, so it captures
            # every tool call regardless of how Lab 1 wires them up
            # (in-process, unlike apps/agent-runtime's real MCP calls) — it
            # feeds the same evidence-grounding check the other labs use.
            _tracer_cm = ToolTracer() if SHARED_CONFIG_AVAILABLE else contextlib.nullcontext()
            with _tracer_cm as _grounding_tracer:
                result = crew.kickoff()

            if verbose_callback:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            timer.cancel()
        except Exception as e:
            if verbose_callback:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
            timer.cancel()
            raise e

        if progress_callback:
            progress_callback("🎉 Analysis completed successfully!", 100)

        from mcp_server import get_tool_trace
        tool_trace = get_tool_trace()

        result_text = str(result)
        response = {
            "success": True,
            "result": result_text,
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "tool_trace": tool_trace,
            "tool_calls_count": len(tool_trace),
            "tool_calls_successful": sum(1 for t in tool_trace if t.get("success", False))
        }
        if SHARED_CONFIG_AVAILABLE and _grounding_tracer.evidence:
            response["guardrail"] = check_report_grounding(result_text, _grounding_tracer.evidence)
        response["crewai_guardrail"] = hallucination_status
        return response
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol
        }
    finally:
        pass
