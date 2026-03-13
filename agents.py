#!/usr/bin/env python3
"""
CrewAI Agents for Stock Analysis

This module contains all agent-related code including agent creation,
task creation, and the main analysis execution function.
"""

import json
import os
import sys
import time
import threading
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable

# Import CrewAI components
try:
    from crewai import Agent, Task, Crew, Process
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False

# Import CrewAI tools directly from MCP server
try:
    from mcp_server import (
        get_tools_by_names,
        CREWAI_AVAILABLE as MCP_TOOLS_AVAILABLE,
    )
    # Use MCP tools availability if CrewAI is available
    if CREWAI_AVAILABLE:
        CREWAI_AVAILABLE = MCP_TOOLS_AVAILABLE
    # MCP API URL for reference
    MCP_API_URL = "http://127.0.0.1:8001"
except ImportError:
    CREWAI_AVAILABLE = False
    MCP_TOOLS_AVAILABLE = False
    MCP_API_URL = "http://127.0.0.1:8001"
    def get_tools_by_names(tool_names: list, openai_api_key: str = "") -> list:
        return []

def create_agents(
    gemini_api_key: str,
    research_tools: Optional[List[str]] = None,
    technical_tools: Optional[List[str]] = None,
    sector_tools: Optional[List[str]] = None,
    report_tools: Optional[List[str]] = None
) -> Dict[str, Agent]:
    """
    Create CrewAI agents with configurable tools.

    Args:
        gemini_api_key: Gemini API key for LLM calls
        research_tools: List of tool names for research agent
        technical_tools: List of tool names for technical agent
        sector_tools: List of tool names for sector agent
        report_tools: List of tool names for report agent

    Returns:
        Dictionary of agents
    """
    if not CREWAI_AVAILABLE:
        return {}

    # Build a shared Gemini LLM instance using crewai.LLM (LiteLLM-backed)
    # This is the pattern from https://ai.google.dev/gemini-api/docs/crewai-example
    # LiteLLM uses GEMINI_API_KEY for the gemini/ model prefix — set it explicitly
    os.environ["GEMINI_API_KEY"] = gemini_api_key
    from crewai import LLM as CrewLLM
    gemini_llm = CrewLLM(
        model="gemini/gemini-2.5-flash",
        api_key=gemini_api_key,
        temperature=0.0)
 
    # Default tools if not provided
    if research_tools is None:
        research_tools = ["search_symbols", "get_quote", "get_price_series"]
    if technical_tools is None:
        technical_tools = ["get_indicators", "get_events", "get_explanation"]
    if sector_tools is None:
        sector_tools = ["search_symbols", "get_quote", "get_indicators"]
    if report_tools is None:
        report_tools = []

    # Get tool functions from names
    research_tool_funcs = get_tools_by_names(research_tools, gemini_api_key)
    technical_tool_funcs = get_tools_by_names(technical_tools, gemini_api_key)
    sector_tool_funcs = get_tools_by_names(sector_tools, gemini_api_key)
    report_tool_funcs = get_tools_by_names(report_tools, gemini_api_key)

    # Research Agent
    research_agent = Agent(
        role="Stock Research Specialist",
        goal="Gather comprehensive basic information about stocks including current quotes, historical data, and company details",
        backstory="""You are an experienced stock researcher with deep knowledge of financial markets.
        Your expertise lies in efficiently gathering and organizing stock data from multiple sources.
        You excel at finding relevant information quickly and presenting it in a clear, structured format.""",
        tools=research_tool_funcs,
        llm=gemini_llm,
        verbose=True,
        allow_delegation=False
    )

    # Technical Analyst
    technical_agent = Agent(
        role="Technical Analysis Expert",
        goal="Perform detailed technical analysis using indicators, patterns, and market events to assess stock momentum and trends",
        backstory="""You are a seasoned technical analyst with 15+ years of experience in chart analysis and market indicators.
        You specialize in interpreting technical signals, identifying patterns, and understanding market psychology.
        Your analysis is methodical and based on proven technical analysis principles.""",
        tools=technical_tool_funcs,
        llm=gemini_llm,
        verbose=True,
        allow_delegation=False
    )

    # Sector Analyst
    sector_agent = Agent(
        role="Sector Comparison Specialist",
        goal="Compare the target stock's performance, valuation, and fundamentals against its sector peers to identify relative strengths and weaknesses",
        backstory="""You are a seasoned sector analyst with 15 years of experience in comparative market analysis.
        You specialize in identifying sector trends, peer comparisons, and relative performance metrics.
        Your expertise lies in understanding how individual stocks perform within their sector context
        and identifying which companies are sector leaders or laggards.""",
        tools=sector_tool_funcs,
        llm=gemini_llm,
        verbose=True,
        allow_delegation=False
    )

    # Report Writer
    report_agent = Agent(
        role="Financial Report Writer",
        goal="Create comprehensive, well-structured investment reports that synthesize research, technical analysis, and sector comparison into actionable insights",
        backstory="""You are a professional financial writer with expertise in translating complex market data into clear,
        actionable reports. You have a talent for presenting technical information in an accessible way while maintaining
        accuracy and professional standards. Your reports are known for their clarity and practical insights.""",
        tools=report_tool_funcs,
        llm=gemini_llm,
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
    gemini_api_key: str,
    research_tools: Optional[List[str]] = None,
    technical_tools: Optional[List[str]] = None,
    sector_tools: Optional[List[str]] = None
) -> List[Task]:
    """
    Create CrewAI tasks with configurable tools.

    Args:
        symbol: Stock symbol to analyze
        gemini_api_key: Gemini API key (passed to explanation tool)
        research_tools: List of tool names for research task
        technical_tools: List of tool names for technical task
        sector_tools: List of tool names for sector task

    Returns:
        List of tasks
    """
    if not CREWAI_AVAILABLE:
        return []

    # Default tools if not provided
    if research_tools is None:
        research_tools = ["search_symbols", "get_quote", "get_price_series"]
    if technical_tools is None:
        technical_tools = ["get_indicators", "get_events", "get_explanation"]
    if sector_tools is None:
        sector_tools = ["search_symbols", "get_quote", "get_indicators"]

    # Get tool functions from names
    research_tool_funcs = get_tools_by_names(research_tools, gemini_api_key)
    technical_tool_funcs = get_tools_by_names(technical_tools, gemini_api_key)
    sector_tool_funcs = get_tools_by_names(sector_tools, gemini_api_key)
    
    # Research Task - EXPLICITLY REQUIRES TOOL CALLS
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
    
    # Technical Analysis Task - EXPLICITLY REQUIRES TOOL CALLS
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
    
    # Sector Comparison Task
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
    
    # Report Task
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
    gemini_api_key: str,
    progress_callback: Optional[Callable[[str, Optional[int]], None]] = None,
    verbose_callback: Optional[Callable[[str], None]] = None,
    research_tools: Optional[List[str]] = None,
    technical_tools: Optional[List[str]] = None,
    sector_tools: Optional[List[str]] = None,
    report_tools: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Run CrewAI analysis with progress tracking"""
    if not CREWAI_AVAILABLE:
        return {"error": "CrewAI not available"}

    # Validate and set Gemini API key
    gemini_api_key = gemini_api_key.strip() if gemini_api_key else ""

    if not gemini_api_key:
        # Fallback to environment variable
        env_api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if env_api_key:
            gemini_api_key = env_api_key
        else:
            return {
                "success": False,
                "error": "Gemini API key is required. Please enter it in the sidebar.",
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol
            }

    # Set GOOGLE_API_KEY so langchain_google_genai can also pick it up from env
    os.environ["GOOGLE_API_KEY"] = gemini_api_key

    try:
        # Clear tool trace at start of analysis
        from mcp_server import clear_tool_trace
        clear_tool_trace()

        # Create agents
        if progress_callback:
            progress_callback("🔧 Creating agents...", 5)

        agents = create_agents(gemini_api_key, research_tools, technical_tools, sector_tools, report_tools)

        # Create tasks
        if progress_callback:
            progress_callback("📋 Setting up tasks...", 8)

        tasks = create_tasks(symbol, gemini_api_key, research_tools, technical_tools, sector_tools)
        
        # Assign agents to tasks
        tasks[0].agent = agents["research"]
        tasks[1].agent = agents["technical"]
        tasks[2].agent = agents["sector"]
        tasks[3].agent = agents["report"]
        
        # Create crew
        if progress_callback:
            progress_callback("👥 Assembling crew...", 10)
        
        crew = Crew(
            agents=list(agents.values()),
            tasks=tasks,
            process=Process.sequential,
            verbose=True
        )
        
        # Execute analysis with detailed progress tracking
        if progress_callback:
            progress_callback("🚀 Starting comprehensive analysis...", 10)
        
        # Use Crew's built-in execution instead of individual task execution
        if progress_callback:
            progress_callback("📊 Executing crew workflow...", 20)
        
        # Test MCP API server connectivity
        if progress_callback:
            progress_callback("🔍 Testing MCP API connectivity...", 15)
        
        try:
            test_response = requests.get(f"{MCP_API_URL}/health", timeout=5)
            if test_response.status_code != 200:
                raise Exception(f"MCP API server returned status {test_response.status_code}")
        except Exception as e:
            raise Exception(f"MCP API server is not responding at {MCP_API_URL}. Please start the API server: uvicorn api:app --host 127.0.0.1 --port 8001")
        
        # Note: OpenAI API key is now passed directly to tools instead of using environment variable
        
        # Execute the crew workflow
        if progress_callback:
            progress_callback("📊 Executing analysis...", 20)
        
        # Add timeout protection for crew execution
        def timeout_handler():
            if verbose_callback:
                verbose_callback("⏰ Crew execution timed out after 2 minutes")
            raise TimeoutError("Crew execution timed out")
        
        # Set up timeout
        timer = threading.Timer(120.0, timeout_handler)  # 2 minutes timeout
        timer.start()
        
        try:
            # Update progress before crew execution
            if progress_callback:
                progress_callback("🔄 Executing tasks...", 30)
            
            # Capture CrewAI verbose output
            from io import StringIO
            
            # Create a custom stdout/stderr capture
            class VerboseCapture:
                def __init__(self, callback):
                    self.callback = callback
                    self.buffer = StringIO()
                
                def write(self, text):
                    if text.strip():  # Only capture non-empty lines
                        self.callback(f"🤖 {text.strip()}")
                    return len(text)
                
                def flush(self):
                    pass
            
            # Capture stdout during crew execution
            old_stdout = sys.stdout
            old_stderr = sys.stderr
                
            
            if verbose_callback:
                verbose_capture = VerboseCapture(verbose_callback)
                sys.stdout = verbose_capture
                sys.stderr = verbose_capture
            
            result = crew.kickoff()
            
            # Restore stdout/stderr
            if verbose_callback:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
            
            timer.cancel()  # Cancel timeout if successful
        except Exception as e:
            # Restore stdout/stderr in case of error
            if verbose_callback:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
            timer.cancel()  # Cancel timeout on error
            raise e
        
        # Final completion message
        if progress_callback:
            progress_callback("🎉 Analysis completed successfully!", 100)
        
        # Get tool trace for verification
        from mcp_server import get_tool_trace
        tool_trace = get_tool_trace()
        
        return {
            "success": True,
            "result": str(result),
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "tool_trace": tool_trace,  # Include tool call trace
            "tool_calls_count": len(tool_trace),
            "tool_calls_successful": sum(1 for t in tool_trace if t.get("success", False))
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol
        }
    finally:
        pass  # GOOGLE_API_KEY set above is fine to leave in env for the session
