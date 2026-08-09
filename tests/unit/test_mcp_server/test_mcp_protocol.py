"""
A genuine MCP handshake against apps/mcp-server's mounted /mcp sub-app.

Teaching note:
  Unlike test_mcp_tools.py (which imports the calculation functions directly
  and never touches MCP at all), this test proves the server actually speaks
  MCP: it performs a real Streamable HTTP JSON-RPC list_tools()/call_tool()
  round-trip against the FastMCP app mounted by server.py
  (`app.mount("/mcp", mcp.streamable_http_app())`).

  No real network socket is opened — httpx's ASGITransport drives the FastAPI
  app in-process, so this runs the same way in CI as any other unit test.

Run: python -m pytest tests/unit/test_mcp_server/ -v
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../apps/mcp-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../packages/shared-config"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../packages/shared-observability"))

import httpx
import pytest

import server as mcp_server_module  # apps/mcp-server/server.py


def _asgi_client_factory(app):
    """Build the httpx_client_factory streamablehttp_client expects, bound to
    an in-process ASGI transport instead of a real socket."""

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mcp-server.test",
            headers=headers,
            timeout=timeout,
            auth=auth,
        )

    return factory


async def _list_tools_over_real_mcp():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    # httpx.ASGITransport never sends ASGI lifespan events (it only proxies
    # individual request/response cycles), so the streamable-http session
    # manager server.py wires into app.router.lifespan_context is never
    # started implicitly the way a real uvicorn deployment starts it. Drive
    # it directly here — this is exactly what that lifespan wiring does.
    async with mcp_server_module.mcp.session_manager.run():
        async with streamablehttp_client(
            "http://mcp-server.test/mcp/",
            httpx_client_factory=_asgi_client_factory(mcp_server_module.app),
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [t.name for t in result.tools]


async def _call_search_symbols_over_real_mcp(query: str):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with mcp_server_module.mcp.session_manager.run():
        async with streamablehttp_client(
            "http://mcp-server.test/mcp/",
            httpx_client_factory=_asgi_client_factory(mcp_server_module.app),
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("search_symbols", {"query": query})


class TestRealMCPHandshake:
    def test_mcp_sub_app_is_mounted(self):
        mount_paths = [
            route.path for route in mcp_server_module.app.routes
            if getattr(route, "path", None) == "/mcp"
        ]
        assert mount_paths, "expected app.mount(\"/mcp\", ...) in server.py"

    def test_server_advertises_all_five_data_tools(self):
        tool_names = asyncio.run(_list_tools_over_real_mcp())
        assert set(tool_names) == {
            "search_symbols",
            "latest_quote",
            "price_series",
            "indicators",
            "detect_events",
        }

    @pytest.mark.network
    def test_search_symbols_round_trips_real_json_rpc(self):
        """Hits the real datasource (network) — see the `network` marker."""
        result = asyncio.run(_call_search_symbols_over_real_mcp("apple"))
        assert result.isError is False
        assert result.content, "expected at least one content block"
