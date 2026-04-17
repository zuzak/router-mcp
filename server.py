#!/usr/bin/env python3
"""
Router MCP server — three-tier access control over the ZyXEL DAL API.

Three independent MCP servers run on separate ports:

  Port 8080  read      — read-only DAL queries; auto-allow in Claude settings
  Port 8081  routine   — writes that don't disrupt other devices; ask permission
  Port 8082  dangerous — network-wide side effects (reboot, credentials); ask permission

Each port requires the matching X-API-Key header. Keys are loaded from
environment variables (see k8s/secret.example.yaml for the full list).

Usage:
    ROUTER_PASSWORD=... READ_API_KEY=... ROUTINE_API_KEY=... DANGEROUS_API_KEY=... \\
        python server.py
"""

import asyncio
import os

import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.models import InitializationOptions
from mcp.types import ServerCapabilities, ToolsCapability
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from router_client import RouterClient
import tools.read as read_tools
import tools.routine as routine_tools
import tools.dangerous as dangerous_tools


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return val


def _make_client(
    base_url: str,
    username_env: str,
    password_env: str,
    fallback_user: str,
    fallback_pass: str,
) -> RouterClient:
    """Build a RouterClient, falling back to admin credentials when tier-specific ones aren't set."""
    return RouterClient(
        base_url,
        os.environ.get(username_env, fallback_user),
        os.environ.get(password_env, fallback_pass),
    )


def build_tier_app(
    server_name: str,
    tier_module,
    client: RouterClient,
    api_key: str | None,
) -> Starlette:
    """
    Build an authenticated Starlette ASGI app for one MCP tier.

    The SSE endpoint is at /sse; the JSON-RPC message endpoint at /messages/.
    Both require the X-API-Key header.
    """
    server = Server(server_name)
    tier_module.register(server, client)

    sse = SseServerTransport("/messages/")

    init_options = InitializationOptions(
        server_name=server_name,
        server_version="0.1.0",
        capabilities=ServerCapabilities(tools=ToolsCapability()),
    )

    async def handle_sse(request: Request):
        if api_key is not None and request.headers.get("x-api-key") != api_key:
            return Response("Unauthorized", status_code=401)
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(streams[0], streams[1], init_options)

    async def handle_messages(request: Request):
        if api_key is not None and request.headers.get("x-api-key") != api_key:
            return Response("Unauthorized", status_code=401)
        await sse.handle_post_message(request.scope, request.receive, request._send)

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["POST"]),
        ]
    )


async def main() -> None:
    base_url = os.environ.get("ROUTER_BASE_URL", "https://192.168.1.1")
    admin_user = _require_env("ROUTER_USERNAME")
    admin_pass = _require_env("ROUTER_PASSWORD")

    read_client = _make_client(
        base_url,
        "ROUTER_READ_USERNAME",
        "ROUTER_READ_PASSWORD",
        admin_user,
        admin_pass,
    )
    routine_client = _make_client(
        base_url,
        "ROUTER_ROUTINE_USERNAME",
        "ROUTER_ROUTINE_PASSWORD",
        admin_user,
        admin_pass,
    )
    dangerous_client = RouterClient(base_url, admin_user, admin_pass)

    tiers = [
        (
            build_tier_app(
                "router-read",
                read_tools,
                read_client,
                os.environ.get("READ_API_KEY"),
            ),
            8080,
        ),
        (
            build_tier_app(
                "router-routine",
                routine_tools,
                routine_client,
                os.environ.get("ROUTINE_API_KEY"),
            ),
            8081,
        ),
        (
            build_tier_app(
                "router-dangerous",
                dangerous_tools,
                dangerous_client,
                os.environ.get("DANGEROUS_API_KEY"),
            ),
            8082,
        ),
    ]

    servers = [
        uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        )
        for app, port in tiers
    ]
    await asyncio.gather(*[s.serve() for s in servers])


if __name__ == "__main__":
    asyncio.run(main())
