# CrewAI + MCP Stocks Analysis Demo

This demo showcases how to integrate CrewAI with the MCP (Model Context Protocol) stocks server to perform comprehensive stock analysis using specialized AI agents.

## 🎯 Overview

The demo uses three specialized CrewAI agents working together:

1. **Research Agent**: Gathers basic stock information and historical data
2. **Technical Analyst**: Performs technical analysis using indicators and market events
3. **Report Writer**: Synthesizes findings into a comprehensive investment report

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Research      │    │   Technical     │    │   Report        │
│   Agent         │───▶│   Analyst       │───▶│   Writer        │
│                 │    │                 │    │                 │
│ • Search symbols│    │ • Get indicators│    │ • Synthesize    │
│ • Get quotes    │    │ • Detect events │    │ • Create report │
│ • Get series    │    │ • AI explanation│    │ • Risk assess   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────┐
                    │   MCP Server    │
                    │                 │
                    │ • Yahoo Finance │
                    │ • Technical     │
                    │   Indicators    │
                    │ • Market Events │
                    │ • LLM Analysis  │
                    └─────────────────┘
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set OpenAI API Key

```bash
export OPENAI_API_KEY="your-openai-api-key-here"
```

### 3. Run the Demo

#### Option A: Streamlit Web Interface (Recommended)
```bash
python run_streamlit_crewai.py
```
This will:
- Automatically start the MCP server
- Launch a web interface at http://localhost:8501
- Provide an interactive UI for running CrewAI analysis

#### Option B: Direct CrewAI Script
```bash
# Start MCP server in one terminal
uvicorn api:app --host 127.0.0.1 --port 8001

# Run CrewAI analysis in another terminal
python crewai_stocks_demo.py --symbol AAPL --openai-api-key YOUR_KEY
```

#### Option C: Direct Streamlit
```bash
# Start MCP server in one terminal
uvicorn api:app --host 127.0.0.1 --port 8001

# Start Streamlit in another terminal
streamlit run streamlit_crewai_app.py
```

## 📊 Example Usage

### Analyze Apple Stock
```bash
python crewai_stocks_demo.py --symbol AAPL --openai-api-key sk-...
```

### Analyze NVIDIA Stock
```bash
python crewai_stocks_demo.py --symbol NVDA --openai-api-key sk-...
```

### Custom MCP Server
```bash
python crewai_stocks_demo.py --symbol TSLA --mcp-url http://localhost:8001 --openai-api-key sk-...
```

## 🔧 Available Tools

The CrewAI agents have access to these MCP tools:

| Tool | Endpoint | Description |
|------|----------|-------------|
| `search_symbols` | `/search` | Search for stock symbols by company name |
| `get_quote` | `/quote` | Get latest price, change %, volume |
| `get_price_series` | `/series` | Get historical OHLCV data |
| `get_indicators` | `/indicators` | Get SMA, EMA, RSI technical indicators |
| `get_events` | `/events` | Detect gaps, volatility spikes, 52w extremes |
| `get_explanation` | `/explain` | Get AI-powered technical analysis explanation |

## 📋 Agent Roles

### Research Agent
- **Role**: Stock Research Specialist
- **Goal**: Gather comprehensive basic information about stocks
- **Tools**: Search, Quote, Price Series
- **Output**: Structured research report with current data and historical summary

### Technical Analyst
- **Role**: Technical Analysis Expert
- **Goal**: Perform detailed technical analysis using indicators and events
- **Tools**: Indicators, Events, AI Explanation
- **Output**: Technical analysis with trend assessment and key levels

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
4. **Risk Assessment** - Based on technical signals
5. **Key Takeaways** - Actionable insights
6. **Professional Disclaimers** - Legal and risk notices

## 🛠️ Customization

### Adding New Agents
```python
new_agent = Agent(
    role="Your Custom Role",
    goal="Your specific goal",
    backstory="Your agent's background",
    tools=[your_custom_tools],
    verbose=True
)
```

### Creating Custom Tools
```python
class CustomTool(MCPTool):
    def __init__(self):
        super().__init__(
            endpoint="/your-endpoint",
            name="your_tool_name",
            description="Your tool description"
        )
```

### Modifying Tasks
```python
custom_task = Task(
    description="Your task description",
    expected_output="Expected output format",
    agent=your_agent,
    tools=[your_tools],
    context=[previous_tasks]  # Dependencies
)
```

## 🔍 Troubleshooting

### MCP Server Not Running
```bash
# Check if server is running
curl http://127.0.0.1:8001/health

# Start server manually
uvicorn api:app --host 127.0.0.1 --port 8001
```

### Missing Dependencies
```bash
# Install all requirements
pip install -r requirements.txt

# Or install CrewAI specifically
pip install crewai langchain langchain-openai
```

### OpenAI API Issues
- Ensure your API key is valid and has sufficient credits
- Check that the key has access to GPT-4 models
- Verify the key is properly set in environment variables

## 📚 Integration with Existing MCP Server

This demo seamlessly integrates with your existing MCP stocks server:

- **No modifications needed** to existing MCP server code
- **HTTP-based integration** - works with any MCP server
- **Tool abstraction** - easy to add new MCP tools
- **Error handling** - graceful fallbacks for failed requests

## 🎓 Learning Outcomes

After running this demo, you'll understand:

1. **CrewAI Agent Architecture** - How to create specialized agents
2. **MCP Integration** - How to connect CrewAI with MCP servers
3. **Tool Development** - How to create custom tools for agents
4. **Workflow Design** - How to design sequential and parallel task flows
5. **Financial Analysis** - How to structure comprehensive stock analysis

## 🌐 Streamlit Web Interface

The Streamlit interface provides a user-friendly web UI for the CrewAI demo:

### Features:
- **Interactive Stock Selection**: Enter any stock symbol
- **Real-time Status Monitoring**: MCP server and CrewAI status
- **Progress Tracking**: Live updates during analysis
- **Agent Information**: Visual cards showing each agent's role and tools
- **Results Display**: Formatted analysis reports with download option
- **Automatic Server Management**: Starts/stops MCP server as needed

### UI Components:
- **Sidebar**: Configuration, server status, and system checks
- **Main Area**: Stock selection, agent cards, and analysis controls
- **Results Section**: Comprehensive analysis reports with formatting
- **Download Feature**: Save analysis results as text files

## 🔗 Project Files

### Core CrewAI Demo Files:
- `streamlit_crewai_app.py` - **Streamlit web interface for CrewAI demo**
- `run_streamlit_crewai.py` - **Streamlit demo runner with auto-setup**
- `crewai_stocks_demo.py` - Main CrewAI implementation (command-line)

### MCP Server & Backend:
- `mcp_server.py` - MCP server with stock analysis tools
- `api.py` - FastAPI bridge for MCP tools
- `datasource.py` - Yahoo Finance data source

### Configuration & Documentation:
- `requirements.txt` - Python dependencies including CrewAI
- `README_CrewAI.md` - This documentation file

## 📄 License

This demo is part of the MCP Stocks Analyzer project. See the main project for licensing information.
