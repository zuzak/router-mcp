"""
Read-only router tools.

All operations are DAL GET requests — no state is modified.
Safe to auto-allow in Claude Code settings.
"""

import asyncio
import json
from mcp.server import Server
from mcp.types import TextContent, Tool

from router_client import RouterClient

TOOLS = [
    Tool(
        name="get_status",
        description="General router status: uptime, firmware version, model.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_wan",
        description="WAN connection details: IP address, gateway, DNS, connection type.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_lan",
        description="LAN configuration: IP, subnet mask, DHCP pool range.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_dhcp_leases",
        description="Current DHCP leases and connected hosts with MAC addresses.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_wlan",
        description="Wireless network status and configuration for all SSIDs.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_dns",
        description="DNS settings (upstream servers, local overrides).",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_eth_status",
        description="Ethernet port link status (speed, duplex, connected).",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]

_OID_MAP = {
    "get_status": "status",
    "get_wan": "wan",
    "get_lan": "lan",
    "get_dhcp_leases": "lanhosts",
    "get_wlan": "wlan",
    "get_dns": "dns",
    "get_eth_status": "ethctl",
}


def register(server: Server, client: RouterClient) -> None:
    """Register read tools on a Server instance."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        oid = _OID_MAP.get(name)
        if oid is None:
            raise ValueError(f"Unknown read tool: {name!r}")
        result = await asyncio.to_thread(client.dal_get, oid)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
