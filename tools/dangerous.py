"""
Dangerous router tools.

Operations with network-wide side effects: reboots and credential
changes will disconnect or break other devices. These are intentionally
in a separate tier requiring explicit authorisation.

OID values are guesses based on ZyXEL DAL naming conventions.
Verify against your router — see CLAUDE.md for the discovery procedure.
"""

import asyncio
import json
from mcp.server import Server
from mcp.types import TextContent, Tool

from router_client import RouterClient

# TODO: confirm OIDs by probing the router — see CLAUDE.md
_OID_REBOOT = "reboot"
_OID_WLAN_SECURITY = "wlansecurity"
_OID_WLAN_BASIC = "wlanbasic"

TOOLS = [
    Tool(
        name="reboot_router",
        description=(
            "Reboot the router. ALL network connections will be interrupted "
            "for approximately 60 seconds. Every device on the network is affected."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="change_wifi_password",
        description=(
            "Change the WPA2 passphrase for a given SSID. "
            "ALL devices connected to that SSID will be immediately disconnected "
            "and must reconnect with the new password."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ssid": {
                    "type": "string",
                    "description": "Target SSID name",
                },
                "new_password": {
                    "type": "string",
                    "description": "New WPA2 passphrase (8–63 characters)",
                },
            },
            "required": ["ssid", "new_password"],
        },
    ),
    Tool(
        name="change_wifi_ssid",
        description=(
            "Rename a wireless network. "
            "ALL devices connected to the old SSID will be disconnected "
            "and must reconnect to the new network name."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "old_ssid": {"type": "string", "description": "Current SSID name"},
                "new_ssid": {
                    "type": "string",
                    "description": "New SSID name (1–32 characters)",
                },
            },
            "required": ["old_ssid", "new_ssid"],
        },
    ),
]


def register(server: Server, client: RouterClient) -> None:
    """Register dangerous tools on a Server instance."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        args = arguments or {}

        if name == "reboot_router":
            result = await asyncio.to_thread(
                client.dal_post, _OID_REBOOT, {"action": "set"}
            )
        elif name == "change_wifi_password":
            pw = args["new_password"]
            if len(pw) < 8 or len(pw) > 63:
                raise ValueError("WiFi passphrase must be 8–63 characters")
            result = await asyncio.to_thread(
                client.dal_post,
                _OID_WLAN_SECURITY,
                {
                    "action": "set",
                    "SSID": args["ssid"],
                    "PreSharedKey": pw,
                },
            )
        elif name == "change_wifi_ssid":
            new = args["new_ssid"]
            if not 1 <= len(new) <= 32:
                raise ValueError("SSID must be 1–32 characters")
            result = await asyncio.to_thread(
                client.dal_post,
                _OID_WLAN_BASIC,
                {
                    "action": "set",
                    "SSID": args["old_ssid"],
                    "NewSSID": new,
                },
            )
        else:
            raise ValueError(f"Unknown dangerous tool: {name!r}")

        return [TextContent(type="text", text=json.dumps(result, indent=2))]
