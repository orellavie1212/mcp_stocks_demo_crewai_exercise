# CrewAI + MCP Stocks Analysis Demo

This demo showcases how to integrate CrewAI with stock analysis tools to perform comprehensive stock analysis using specialized AI agents. The simplified architecture uses direct function calls without HTTP/API layers.

## 🎯 Overview

The demo uses four specialized CrewAI agents working together:

1. **Research Agent**: Gathers basic stock information and historical data
2. **Technical Analyst**: Performs technical analysis using indicators and market events
3. **Sector Analyst**: Compares stock performance against sector peers
4. **Report Writer**: Synthesizes findings into a comprehensive investment report

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend Layer                            │
│  streamlit_crewai_app.py - Web UI                           │
└───────────────────────┬─────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────┐
│                  Agent Layer                                │
│  agents.py - CrewAI agents, tasks, and crew                 │
└───────────────────────┬─────────────────────────────────────┘
                        │ Direct function calls
┌───────────────────────▼─────────────────────────────────────┐
│                  MCP Server Layer                            │
│  mcp_server.py - All tool definitions                        │
│  • @mcp.tool() functions (FastMCP protocol)                  │
│  • @tool() functions (CrewAI tools)                          │
│  • Business logic: indicators, events, explanations          │
│  • Agent-safe parsers (JSON → readable text)                 │
│  • Tool tracing and verification                             │
└───────────────────────┬─────────────────────────────────────┘
                        │ Function calls
┌───────────────────────▼─────────────────────────────────────┐
│                  Data Layer                                  │
│  datasource.py - Yahoo Finance integration                   │
│  • search_symbols() - Symbol lookup                          │
│  • latest_quote() - Current prices                           │
│  • price_series() - Historical data                          │
└─────────────────────────────────────────────────────────────┘

Optional HTTP Layer (for external clients):
┌───────────────────────▼─────────────────────────────────────┐
│                  API Layer                                   │
│  api.py - FastAPI HTTP bridge                                │
│  • Exposes MCP tools as HTTP endpoints                       │
│  • Used by external clients (not needed for CrewAI)         │
└─────────────────────────────────────────────────────────────┘
```

**Simplified Architecture Benefits:**
- ✅ No HTTP/API server needed
- ✅ Direct function calls (faster)
- ✅ Simpler setup and deployment
- ✅ Fewer dependencies

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set OpenAI API Key (Optional)

The OpenAI API key can be set via environment variable or entered in the Streamlit UI:

```bash
export OPENAI_API_KEY="your-openai-api-key-here"
```

**Note**: The API key is only needed for the AI-powered explanation tool. Basic stock data and technical indicators work without it.

### 3. Start MCP API Server (Optional but Recommended)

The API server is needed for HTTP access and health checks:

```bash
uvicorn api:app --host 127.0.0.1 --port 8001
```

**Note**: CrewAI tools call MCP functions directly, so the API server is optional for CrewAI usage. However, it's recommended for tool verification and external access.

### 4. Run the Demo

#### Option A: Streamlit Web Interface (Recommended)
```bash
streamlit run streamlit_crewai_app.py
```

This will:
- Launch a web interface at http://localhost:8501
- Provide an interactive UI for running CrewAI analysis
- Show tool call traces for MCP verification
- Display agent information and progress

#### Option B: Direct Python Script
```python
from agents import run_crewai_analysis

result = run_crewai_analysis(
    symbol="AAPL",
    openai_api_key="your-key-here"
)
print(result)
```

## 📊 Example Usage

### Analyze Apple Stock
```python
from agents import run_crewai_analysis

result = run_crewai_analysis("AAPL", "your-openai-api-key")
```

### Analyze NVIDIA Stock
```python
result = run_crewai_analysis("NVDA", "your-openai-api-key")
```

## 🔧 Available Tools

All tools are defined in `mcp_server.py` with both `@mcp.tool()` (FastMCP protocol) and `@tool()` (CrewAI) decorators:

| Tool | Description | Returns |
|------|-------------|---------|
| `search_symbols` | Search for stock symbols by company name or ticker | Formatted list of matching symbols |
| `get_quote` | Get latest price, change %, volume | Formatted quote with current price and change |
| `get_price_series` | Get historical OHLCV data | Summary statistics (high, low, period change) |
| `get_indicators` | Get SMA, EMA, RSI technical indicators | Formatted indicators with interpretations |
| `get_events` | Detect gaps, volatility spikes, 52w extremes | List of detected market events |
| `get_explanation` | Get AI-powered technical analysis explanation | AI-generated analysis with rationale |

**Key Features:**
- ✅ **Agent-safe wrappers**: Tools return readable, formatted text (not raw JSON)
- ✅ **Direct function calls**: CrewAI tools call MCP functions directly (no HTTP overhead)
- ✅ **Tool tracing**: All tool calls are logged with timing and verification
- ✅ **Explicit requirements**: Tasks explicitly require specific tool calls

## 📋 Agent Roles

### Research Agent
- **Role**: Stock Research Specialist
- **Goal**: Gather comprehensive basic information about stocks
- **Tools**: `search_symbols`, `get_quote`, `get_price_series`
- **Output**: Structured research report with current data and historical summary

### Technical Analyst
- **Role**: Technical Analysis Expert
- **Goal**: Perform detailed technical analysis using indicators and events
- **Tools**: `get_indicators`, `get_events`, `get_explanation`
- **Output**: Technical analysis with trend assessment and key levels

### Sector Analyst
- **Role**: Sector Comparison Specialist
- **Goal**: Compare stock performance against sector peers
- **Tools**: `search_symbols`, `get_quote`, `get_indicators`
- **Output**: Sector comparison analysis with peer performance metrics

### Report Writer
- **Role**: Financial Report Writer
- **Goal**: Create comprehensive investment reports
- **Tools**: None (synthesis only)
- **Output**: Professional investment analysis report

## 📈 Sample Output

The demo generates a comprehensive report including:

1. **Executive Summary** - Key findings and recommendations
2. **Current Market Position** - Price, volume, recent changes
3. **Technical Analysis** - Indicators, events, trends
4. **Sector Comparison** - Peer performance and relative positioning
5. **Risk Assessment** - Based on technical signals
6. **Key Takeaways** - Actionable insights
7. **Professional Disclaimers** - Legal and risk notices
8. **Tool Call Trace** - Verification of MCP tool usage with:
   - Tool names and arguments
   - Execution timing
   - Success/failure status
   - Result previews

## 🛠️ Customization

### Adding New Agents
```python
from crewai import Agent
from mcp_server import your_custom_tool

new_agent = Agent(
    role="Your Custom Role",
    goal="Your specific goal",
    backstory="Your agent's background",
    tools=[your_custom_tool],
    verbose=True
)
```

### Creating Custom Tools

Add tools to `mcp_server.py` with both decorators:

```python
# In mcp_server.py

# FastMCP tool (for MCP protocol clients)
@mcp.tool()
def your_mcp_tool(param: str) -> str:
    """MCP tool description."""
    result = your_datasource_function(param)
    return json.dumps(result, ensure_ascii=False)

# CrewAI tool (for CrewAI agents)
@tool("your_tool_name")
def your_tool(param: str) -> str:
    """
    Your tool description for agents.
    
    YOU MUST CALL THIS TOOL to get data. Do not guess or estimate.
    
    Args:
        param: Parameter description
    
    Returns:
        Formatted, readable text (not raw JSON)
    """
    start_time = time.time()
    try:
        json_result = your_mcp_tool(param)  # Call MCP tool
        parsed_result = _parse_your_result(json_result)  # Parse to readable format
        _log_tool_call("your_tool", {"param": param}, start_time, True, parsed_result)
        return parsed_result
    except Exception as e:
        _log_tool_call("your_tool", {"param": param}, start_time, False, error=str(e))
        return f"Error: {str(e)}"
```

**Important**: 
- Add to `TOOL_REGISTRY` in `mcp_server.py`
- Use agent-safe parsers to return readable text
- Include tool tracing for verification

### Modifying Tasks
```python
from crewai import Task

custom_task = Task(
    description="Your task description",
    expected_output="Expected output format",
    agent=your_agent,
    tools=[your_tools],
    context=[previous_tasks]  # Dependencies
)
```

## 🔍 Troubleshooting

### Missing Dependencies
```bash
# Install all requirements
pip install -r requirements.txt

# Or install CrewAI specifically
pip install crewai langchain langchain-openai

# Install data dependencies
pip install pandas numpy yfinance openai
```

### OpenAI API Issues
- Ensure your API key is valid and has sufficient credits
- Check that the key has access to GPT-4 models
- Verify the key is properly set (can be entered in Streamlit UI or environment variable)
- **Note**: The API key is only needed for the explanation tool. Other tools work without it.

### Import Errors
```bash
# Make sure you're in the correct directory
cd mcp_stocks_demo_crewai_exercise

# Verify all files are present
ls -la
# Should see: datasource.py, mcp_server.py, agents.py, streamlit_crewai_app.py
```

## 📚 Architecture Details

### Layered Design

This demo uses a **layered architecture** where:

1. **`datasource.py`** - Data fetching layer (Yahoo Finance)
2. **`mcp_server.py`** - MCP server with all tool definitions:
   - `@mcp.tool()` functions for FastMCP protocol
   - `@tool()` functions for CrewAI agents
   - Business logic (indicators, events, explanations)
   - Agent-safe parsers (JSON → readable text)
   - Tool tracing and verification
3. **`agents.py`** - CrewAI agents, tasks, and crew
4. **`api.py`** - FastAPI HTTP bridge (optional, for external clients)
5. **`streamlit_crewai_app.py`** - Web interface

**Key Features:**
- ✅ All tool logic centralized in `mcp_server.py`
- ✅ CrewAI tools call MCP functions directly (no HTTP overhead)
- ✅ Agent-safe wrappers parse JSON automatically
- ✅ Tool tracing provides verification
- ✅ Explicit tool requirements prevent hallucinations

### Tool Implementation

All tools are defined in `mcp_server.py` with dual decorators:

```python
# FastMCP tool (returns JSON)
@mcp.tool()
def search_symbols(query: str) -> str:
    """Symbol lookup by company name/ticker."""
    results = ds_search(query)
    return json.dumps(results, ensure_ascii=False)

# CrewAI tool (returns readable text)
@tool("search_symbols")
def search_symbols_tool(q: str) -> str:
    """Search for stock symbols. YOU MUST CALL THIS TOOL."""
    start_time = time.time()
    json_result = search_symbols(q)  # Call MCP tool
    parsed_result = _parse_search_results(json_result)  # Parse to readable
    _log_tool_call("search_symbols", {"q": q}, start_time, True, parsed_result)
    return parsed_result
```

**Benefits:**
- MCP protocol support via `@mcp.tool()`
- CrewAI compatibility via `@tool()`
- Agent-safe data (readable text, not JSON)
- Automatic tool tracing

## 🎓 Learning Outcomes

After running this demo, you'll understand:

1. **CrewAI Agent Architecture** - How to create specialized agents with explicit tool requirements
2. **MCP Integration** - How to build MCP servers with FastMCP and expose tools to CrewAI
3. **Tool Development** - How to create dual-decorated tools (`@mcp.tool()` and `@tool()`)
4. **Agent-Safe Wrappers** - How to parse JSON and return readable text for agents
5. **Tool Tracing** - How to verify and log tool calls for debugging and verification
6. **Workflow Design** - How to design sequential task flows with dependencies
7. **Financial Analysis** - How to structure comprehensive stock analysis
8. **Layered Architecture** - How to separate concerns (data, MCP server, agents, frontend)

## 🌐 Streamlit Web Interface

The Streamlit interface provides a user-friendly web UI for the CrewAI demo:

### Features:
- **Interactive Stock Selection**: Enter any stock symbol
- **Real-time Progress Tracking**: Live updates during analysis
- **Agent Information**: Visual cards showing each agent's role and tools
- **Results Display**: Formatted analysis reports with download option
- **Tool Call Trace**: Verification of MCP tool usage with timing and success status
- **API Server Management**: Start/stop MCP API server from the UI

### UI Components:
- **Sidebar**: Configuration and OpenAI API key input
- **Main Area**: Stock selection, agent cards, and analysis controls
- **Results Section**: Comprehensive analysis reports with formatting
- **Download Feature**: Save analysis results as text files

## 🔗 Project Files

### Core Files:
- `streamlit_crewai_app.py` - **Streamlit web interface**
- `agents.py` - **CrewAI agents, tasks, and crew definition**
- `mcp_server.py` - **MCP server with all tool definitions** (FastMCP + CrewAI tools)
- `datasource.py` - **Yahoo Finance data source**

### Optional Files:
- `api.py` - **FastAPI HTTP bridge** (for external clients, optional for CrewAI)

### Configuration & Documentation:
- `requirements.txt` - Python dependencies including CrewAI and FastMCP
- `README_CrewAI.md` - This documentation file

## 📝 Code Structure

```
mcp_stocks_demo_crewai_exercise/
├── datasource.py          # Data fetching from Yahoo Finance
├── mcp_server.py          # MCP server: @mcp.tool() + @tool() functions
│                           # - FastMCP protocol tools
│                           # - CrewAI tool wrappers
│                           # - Business logic (indicators, events)
│                           # - Agent-safe parsers
│                           # - Tool tracing
├── agents.py              # CrewAI agents, tasks, crew
├── api.py                 # FastAPI HTTP bridge (optional)
├── streamlit_crewai_app.py # Web interface
├── requirements.txt       # Dependencies
└── README_CrewAI.md      # This file
```

## 🚀 Performance & Features

The architecture provides:
- **Fast execution** - CrewAI tools call MCP functions directly (no HTTP overhead)
- **Tool verification** - Complete trace of all tool calls with timing
- **Agent safety** - Tools return readable text, not raw JSON
- **Explicit requirements** - Tasks mandate specific tool calls
- **MCP protocol support** - Tools available via FastMCP for MCP clients
- **Easy debugging** - Tool traces show exactly what was called

## 📄 License

This demo is part of the MCP Stocks Analyzer project. See the main project for licensing information.
