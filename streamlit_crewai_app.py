#!/usr/bin/env python3
"""
Streamlit CrewAI + MCP Stocks Analysis Demo

This Streamlit app provides a web interface for running CrewAI stock analysis
using the MCP stocks server. Users can select stocks, configure agents, and
view comprehensive analysis results.
"""

import streamlit as st
import os
import sys
import time
import subprocess
import requests
from datetime import datetime

# Import agent functions from separate module
try:
    from agents import run_crewai_analysis, CREWAI_AVAILABLE
except ImportError:
    CREWAI_AVAILABLE = False
    st.error("Could not import agents module. Please ensure agents.py is in the same directory.")
    
    def run_crewai_analysis(*args, **kwargs):
        return {"error": "Agents module not available"}

# MCP API Configuration
MCP_API_URL = "http://127.0.0.1:8001"

# Page configuration
st.set_page_config(
    page_title="CrewAI + MCP Stocks Analysis",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 2rem;
    }
    .agent-card {
        border: 1px solid #ddd;
        border-radius: 10px;
        padding: 1rem;
        margin: 0.5rem 0;
        background-color: #f9f9f9;
        color: #333333;
    }
    .agent-card h4 {
        color: #1f77b4;
        margin-top: 0;
    }
    .agent-card p {
        color: #333333;
        margin: 0.5rem 0;
    }
    .agent-card strong {
        color: #1f77b4;
    }
    .status-success {
        color: #28a745;
        font-weight: bold;
    }
    .status-error {
        color: #dc3545;
        font-weight: bold;
    }
    .status-warning {
        color: #ffc107;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

def check_mcp_api() -> bool:
    """Check if MCP API server is running"""
    try:
        response = requests.get(f"{MCP_API_URL}/health", timeout=5)
        return response.status_code == 200
    except:
        return False

def start_mcp_api():
    """Start MCP API server in background"""
    if not check_mcp_api():
        try:
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", 
                "api:app", 
                "--host", "127.0.0.1", 
                "--port", "8001",
                "--log-level", "warning"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Wait for server to start
            for _ in range(20):
                time.sleep(0.5)
                if check_mcp_api():
                    return True
            return False
        except Exception:
            return False
    return True

def main():
    """Main Streamlit app"""
    
    # Header
    st.markdown('<h1 class="main-header">🤖 CrewAI + MCP Stocks Analysis</h1>', unsafe_allow_html=True)
    st.markdown("**Comprehensive stock analysis using specialized AI agents**")
    
    # Simple instructions
    st.info("💡 **Quick Start:** 1) Start MCP Server (sidebar) → 2) Enter Gemini API Key → 3) Enter stock symbol → 4) Click Start Analysis")
    
    # Sidebar configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # MCP API Server Status
        st.subheader("🔗 MCP API Server Status")
        if check_mcp_api():
            st.success("✅ MCP API Server Connected")
        else:
            st.error("❌ MCP API Server Not Running")
            if st.button("🚀 Start MCP API Server", use_container_width=True):
                with st.spinner("Starting MCP API server..."):
                    if start_mcp_api():
                        st.success("✅ MCP API server started successfully!")
                        st.rerun()
                    else:
                        st.error("❌ Failed to start MCP API server")
        
        st.markdown("---")
        
        # Gemini API Key
        st.subheader("🔑 Gemini API Key")
        gemini_api_key = st.text_input(
            "Enter your Gemini API Key",
            value=st.session_state.get("gemini_api_key", ""),
            type="password",
            help="Get a free key at https://aistudio.google.com/apikey"
        )
        if gemini_api_key:
            st.session_state["gemini_api_key"] = gemini_api_key
            st.success("✅ API Key Set")
        else:
            st.warning("⚠️ API Key Required")
        
    
    # Main content
    if not CREWAI_AVAILABLE:
        st.error("CrewAI is not installed. Please install it using the command in the sidebar.")
        return
    
    if not gemini_api_key:
        st.warning("Please enter your Gemini API key in the sidebar to enable LLM explanations.")
    
    # Stock selection
    st.header("📊 Stock Selection")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        symbol = st.text_input(
            "Stock Symbol",
            value=st.session_state.get("selected_symbol", "AAPL"),
            placeholder="Enter stock symbol (e.g., AAPL, NVDA, TSLA)",
            help="Enter a valid stock ticker symbol"
        ).upper()
        
        if symbol:
            st.session_state["selected_symbol"] = symbol
    
    with col2:
        st.metric("Selected Symbol", symbol if symbol else "None")
    
    # Agent information
    st.header("👥 Analysis Agents")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown("""
        <div class="agent-card">
            <h4>🔍 Research Agent</h4>
            <p><strong>Role:</strong> Stock Research Specialist</p>
            <p><strong>Tools:</strong> Search, Quote, Price Series</p>
            <p><strong>Output:</strong> Basic stock data and historical information</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown("""
        <div class="agent-card">
            <h4>📈 Technical Analyst</h4>
            <p><strong>Role:</strong> Technical Analysis Expert</p>
            <p><strong>Tools:</strong> Indicators, Events, AI Explanation</p>
            <p><strong>Output:</strong> Technical analysis and market insights</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown("""
        <div class="agent-card">
            <h4>🏢 Sector Analyst</h4>
            <p><strong>Role:</strong> Sector Comparison Specialist</p>
            <p><strong>Tools:</strong> Search, Quote, Indicators</p>
            <p><strong>Output:</strong> Sector peer comparison and positioning</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown("""
        <div class="agent-card">
            <h4>📝 Report Writer</h4>
            <p><strong>Role:</strong> Financial Report Writer</p>
            <p><strong>Tools:</strong> Synthesis only</p>
            <p><strong>Output:</strong> Comprehensive investment report</p>
        </div>
        """, unsafe_allow_html=True)
    
    # Analysis controls
    st.header("🚀 Run Analysis")
    
    if not symbol:
        st.warning("Please enter a stock symbol to begin analysis.")
        return
    
    if not check_mcp_api():
        st.error("MCP API server is not running. Please start it using the button in the sidebar.")
        return

    col1, col2, col3 = st.columns([1, 1, 2])

    with col1:
        run_analysis = st.button("🔍 Start Analysis", type="primary", use_container_width=True)
    
    with col2:
        clear_results = st.button("🗑️ Clear Results", use_container_width=True)
    
    with col3:
        # Show analysis status
        if st.session_state.get("analysis_running", False):
            st.markdown('<p class="status-warning">⏳ Analysis in progress...</p>', unsafe_allow_html=True)
        elif "analysis_result" in st.session_state:
            if st.session_state["analysis_result"].get("success", False):
                st.markdown('<p class="status-success">✅ Analysis completed!</p>', unsafe_allow_html=True)
            else:
                st.markdown('<p class="status-error">❌ Analysis failed</p>', unsafe_allow_html=True)
    
    
    # Clear results
    if clear_results:
        for key in ["analysis_result", "analysis_running", "analysis_progress", "debug_messages", "verbose_messages"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()
    
    # Run analysis
    if run_analysis and symbol and gemini_api_key and not st.session_state.get("analysis_running", False):
        # Set flag to prevent multiple executions
        st.session_state["analysis_running"] = True
        
        # Create progress container
        progress_container = st.container()
        
        with progress_container:
            st.subheader("📊 Analysis Progress")
            progress_bar = st.progress(0)
            status_text = st.empty()
            progress_percentage = st.empty()
        
        # Create Analysis Results section (empty initially)
        st.header("📋 Analysis Results")
        results_placeholder = st.empty()
        
        # Progress callback with percentage tracking
        def update_progress(message, percentage=None):
            status_text.text(message)
            if percentage is not None:
                progress_bar.progress(percentage)
                progress_percentage.text(f"Progress: {percentage:.0f}%")
        
        # Simplified verbose callback - just store messages, no real-time display
        def verbose_callback(message):
            if "verbose_messages" not in st.session_state:
                st.session_state["verbose_messages"] = []
            st.session_state["verbose_messages"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
            # Keep only last 50 messages
            if len(st.session_state["verbose_messages"]) > 50:
                st.session_state["verbose_messages"] = st.session_state["verbose_messages"][-50:]
            
        
        # Run analysis directly with progress updates
        try:
            result = run_crewai_analysis(symbol, gemini_api_key, update_progress, verbose_callback)
            
            # Store in session state
            st.session_state["analysis_result"] = result
            st.session_state["analysis_running"] = False  # Clear running flag
            
            # Display results immediately
            with results_placeholder.container():
                if result.get("success", False):
                    st.success(f"✅ Analysis completed for {result.get('symbol', 'Unknown')}")
                    
                    # Display the result
                    st.subheader("📊 Comprehensive Analysis Report")
                    
                    # Format the result nicely
                    analysis_text = result.get("result", "")
                    
                    # Try to parse and display structured content
                    if analysis_text:
                        # If it's a string, display it directly
                        if isinstance(analysis_text, str):
                            st.markdown("### Full Analysis Report")
                            st.markdown(analysis_text)
                        else:
                            # If it's an object, try to display it nicely
                            st.json(analysis_text)
                    else:
                        st.warning("No analysis text found in result")
                    
                    # Tool Trace Section
                    if result.get("tool_trace"):
                        st.subheader("🔍 Tool Call Trace (MCP Verification)")
                        tool_trace = result.get("tool_trace", [])
                        st.info(f"**Total Tool Calls:** {len(tool_trace)} | **Successful:** {result.get('tool_calls_successful', 0)}")
                        
                        with st.expander("📊 View Detailed Tool Trace", expanded=True):
                            for i, call in enumerate(tool_trace, 1):
                                status_icon = "✅" if call.get("success") else "❌"
                                st.markdown(f"**{i}. {status_icon} {call.get('tool_name', 'Unknown')}**")
                                st.markdown(f"   - **Arguments:** `{call.get('arguments', {})}`")
                                st.markdown(f"   - **Duration:** {call.get('duration_seconds', 0)}s")
                                st.markdown(f"   - **Time:** {call.get('timestamp', 'N/A')}")
                                if call.get("error"):
                                    st.error(f"   - **Error:** {call.get('error')}")
                                if call.get("result_preview"):
                                    st.text(f"   - **Result Preview:** {call.get('result_preview')[:150]}...")
                                st.markdown("---")
                    
                    # Download button
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"crewai_analysis_{result.get('symbol', 'stock')}_{timestamp}.txt"
                    
                    # Include tool trace in download
                    download_content = f"{analysis_text}\n\n{'='*60}\nTOOL CALL TRACE (MCP Verification)\n{'='*60}\n"
                    if result.get("tool_trace"):
                        for call in result.get("tool_trace", []):
                            download_content += f"\n[{call.get('timestamp')}] {call.get('tool_name')}\n"
                            download_content += f"  Arguments: {call.get('arguments')}\n"
                            download_content += f"  Success: {call.get('success')}\n"
                            download_content += f"  Duration: {call.get('duration_seconds')}s\n"
                            if call.get("error"):
                                download_content += f"  Error: {call.get('error')}\n"
                    
                    st.download_button(
                        label="📥 Download Report (with Tool Trace)",
                        data=download_content,
                        file_name=filename,
                        mime="text/plain"
                    )
                else:
                    st.error(f"❌ Analysis failed: {result.get('error', 'Unknown error')}")
            
        except Exception as e:
            st.session_state["analysis_result"] = {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol
            }
            st.session_state["analysis_running"] = False  # Clear running flag
            
            # Display error in results section
            with results_placeholder.container():
                st.error(f"❌ Analysis failed: {str(e)}")
        
        # Clear only the progress container after completion, keep verbose log
        with progress_container:
            st.empty()  # Clear progress elements
    
    # Show verbose messages only after analysis is complete
    if "analysis_result" in st.session_state and "verbose_messages" in st.session_state and st.session_state["verbose_messages"]:
        with st.expander("🤖 Agent Activity Log", expanded=False):
            for msg in st.session_state["verbose_messages"][-15:]:  # Show last 15 messages
                st.text(msg)
    
    # Footer
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #666;'>
        <p>🤖 CrewAI + MCP Stocks Analysis Demo | Powered by Google Gemini & Yahoo Finance</p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
