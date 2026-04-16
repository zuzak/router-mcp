"""
Routine router tools.

Write operations that modify router configuration without disrupting
other devices on the network (no reboots, no credential changes).

OID values are guesses based on ZyXEL DAL naming conventions.
Verify against your router with dal_get() before relying on them —
see CLAUDE.md for the discovery procedure.
"""

import asyncio
import json
from mcp.server import Server
from mcp.types import TextContent, Tool

from router_client import RouterClient

# TODO: confirm OIDs by probing the router — see CLAUDE.md
_OID_DHCP_HOST = "dhcphost"
_OID_PORT_FWD = "portforward"

TOOLS = [
    Tool(
        name="add_dhcp_reservation",
        description=(
            "Reserve a static IP for a device by MAC address. "
            "The device will always receive the same IP from DHCP."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mac": {
                    "type": "string",
                    "description": "Device MAC address (e.g. aa:bb:cc:dd:ee:ff)",
                },
                "ip": {
                    "type": "string",
                    "description": "IP address to reserve (must be within LAN subnet)",
                },
                "hostname": {
                    "type": "string",
                    "description": "Optional label for the reservation",
                },
            },
            "required": ["mac", "ip"],
        },
    ),
    Tool(
        name="remove_dhcp_reservation",
        description="Remove a DHCP reservation by MAC address.",
        inputSchema={
            "type": "object",
            "properties": {
                "mac": {
                    "type": "string",
                    "description": "Device MAC address",
                },
            },
            "required": ["mac"],
        },
    ),
    Tool(
        name="add_port_forward",
        description=(
            "Add a port forwarding rule from the WAN to a LAN device. "
            "Does not affect existing connections."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Rule label"},
                "external_port": {
                    "type": "integer",
                    "description": "WAN-facing port number",
                },
                "internal_ip": {
                    "type": "string",
                    "description": "LAN device IP address",
                },
                "internal_port": {
                    "type": "integer",
                    "description": "Port on the internal device",
                },
                "protocol": {
                    "type": "string",
                    "enum": ["TCP", "UDP", "TCP/UDP"],
                    "description": "Protocol (default: TCP)",
                },
            },
            "required": ["name", "external_port", "internal_ip", "internal_port"],
        },
    ),
    Tool(
        name="remove_port_forward",
        description="Remove a port forwarding rule by name.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Rule name to remove"},
            },
            "required": ["name"],
        },
    ),
]


def register(server: Server, client: RouterClient) -> None:
    """Register routine tools on a Server instance."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        args = arguments or {}

        if name == "add_dhcp_reservation":
            result = await asyncio.to_thread(
                client.dal_post,
                _OID_DHCP_HOST,
                {
                    "action": "add",
                    "MACAddress": args["mac"],
                    "IPAddress": args["ip"],
                    "HostName": args.get("hostname", ""),
                },
            )
        elif name == "remove_dhcp_reservation":
            result = await asyncio.to_thread(
                client.dal_post,
                _OID_DHCP_HOST,
                {"action": "delete", "MACAddress": args["mac"]},
            )
        elif name == "add_port_forward":
            result = await asyncio.to_thread(
                client.dal_post,
                _OID_PORT_FWD,
                {
                    "action": "add",
                    "Name": args["name"],
                    "ExternalPort": args["external_port"],
                    "InternalClient": args["internal_ip"],
                    "InternalPort": args["internal_port"],
                    "Protocol": args.get("protocol", "TCP"),
                    "Enable": True,
                },
            )
        elif name == "remove_port_forward":
            result = await asyncio.to_thread(
                client.dal_post,
                _OID_PORT_FWD,
                {"action": "delete", "Name": args["name"]},
            )
        else:
            raise ValueError(f"Unknown routine tool: {name!r}")

        return [TextContent(type="text", text=json.dumps(result, indent=2))]
