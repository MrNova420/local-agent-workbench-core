"""Local stdio MCP clients owned by one long-lived asyncio event loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
import os
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any


class MCPRequestCancelled(Exception):
    """The caller cancelled one pending MCP operation."""


class MCPManager:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.servers: dict[str, Any] = {}
        self.clients: dict[str, tuple[Any, Any]] = {}
        self.tools: dict[str, tuple[str, str]] = {}
        self.errors: dict[str, str] = {}
        self.discovered = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()
        self._ready = threading.Event()

    def load(self):
        if self.path.exists():
            self.servers = json.loads(self.path.read_text(encoding="utf-8")).get("mcpServers", {})
        return self.servers

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"mcpServers": self.servers}, indent=2), encoding="utf-8")

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self.loop is not None and self.loop.is_running():
                return self.loop
            if self.loop_thread is None or not self.loop_thread.is_alive():
                self._ready.clear()

                def serve():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self.loop = loop
                    self._ready.set()
                    loop.run_forever()
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.close()

                self.loop_thread = threading.Thread(target=serve, name="mcp-event-loop", daemon=True)
                self.loop_thread.start()
            thread = self.loop_thread
        if not self._ready.wait(5):
            raise RuntimeError("MCP event loop did not start")
        if self.loop is None:
            raise RuntimeError("MCP event loop failed to initialize")
        if threading.current_thread() is thread:
            raise RuntimeError("MCP synchronous bridge cannot block its own event loop")
        return self.loop

    def run(self, awaitable, cancel_event: threading.Event | None = None):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(awaitable, loop)
        while True:
            try:
                return future.result(timeout=0.1)
            except FutureTimeout:
                if cancel_event is not None and cancel_event.is_set():
                    future.cancel()
                    raise MCPRequestCancelled("MCP operation cancelled")

    async def discover(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("Optional MCP SDK missing; install requirements-mcp.txt") from exc

        self.errors.clear()
        found = []
        for name, cfg in self.servers.items():
            if not isinstance(cfg, dict):
                self.errors[name] = "Invalid server configuration: expected an object."
                continue
            transport = cfg.get("transport", "stdio")
            if transport == "stdio":
                if not cfg.get("command"):
                    self.errors[name] = "Invalid stdio configuration: command is required."
                    continue
                server_env = cfg.get("env", {})
                if not isinstance(server_env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in server_env.items()):
                    self.errors[name] = "Invalid stdio configuration: env must map strings to strings."
                    continue
                try:
                    server_env = self._resolved_headers(server_env)
                except ValueError as exc:
                    self.errors[name] = f"Invalid stdio environment: {exc}"
                    continue
                manager = stdio_client(StdioServerParameters(
                    command=cfg["command"], args=cfg.get("args", []),
                    env=server_env,
                ))
            elif transport == "streamable-http":
                try:
                    url = self._validated_http_url(cfg.get("url"))
                    headers = self._resolved_headers(cfg.get("headers", {}))
                    timeout = float(cfg.get("timeoutSeconds", 30))
                    read_timeout = float(cfg.get("readTimeoutSeconds", 300))
                    if not 0 < timeout <= 3600 or not 0 < read_timeout <= 86400:
                        raise ValueError("MCP HTTP timeouts must be positive (connect up to 1 hour, response up to 24 hours).")
                    from mcp.client.streamable_http import streamable_http_client
                    import httpx
                    @asynccontextmanager
                    async def http_transport():
                        async with httpx.AsyncClient(headers=headers,
                                                     timeout=httpx.Timeout(timeout, read=read_timeout),
                                                     follow_redirects=False) as client:
                            async with streamable_http_client(url, http_client=client) as streams:
                                yield streams
                    manager = http_transport()
                except (ImportError, TypeError, ValueError) as exc:
                    self.errors[name] = f"Invalid Streamable HTTP configuration: {exc}"
                    continue
            else:
                self.errors[name] = f"Unsupported MCP transport: {transport}"
                continue
            session = None
            streams = None
            manager_entered = False
            session_entered = False
            try:
                streams = await manager.__aenter__()
                manager_entered = True
                session = ClientSession(*streams)
                await session.__aenter__()
                session_entered = True
                self.clients[name] = (session, manager)
                await session.initialize()
                response = await session.list_tools()
                for tool in response.tools:
                    key = f"mcp__{name}__{tool.name}"
                    self.tools[key] = (name, tool.name)
                    found.append({
                        "type": "function",
                        "function": {
                            "name": key,
                            "description": (tool.description or "") + " (local MCP tool; requires approval)",
                            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
                        },
                    })
            except BaseException as exc:
                self.errors[name] = f"{type(exc).__name__}: {exc}"
                self.clients.pop(name, None)
                if session_entered:
                    try:
                        await session.__aexit__(None, None, None)
                    except Exception:
                        pass
                if manager_entered:
                    try:
                        await manager.__aexit__(None, None, None)
                    except Exception:
                        pass
                if isinstance(exc, asyncio.CancelledError):
                    raise
        self.discovered = True
        return found

    async def call(self, name: str, args: dict[str, Any]):
        server, tool = self.tools[name]
        result = await self.clients[server][0].call_tool(tool, args)
        output = "\n".join(c.text for c in result.content if getattr(c, "text", None)) or json.dumps([
            getattr(c, "model_dump", lambda: {})() for c in result.content
        ])
        return ("Tool error: " if getattr(result, "isError", False) else "") + output

    async def close(self):
        clients, self.clients = list(self.clients.values()), {}
        self.tools.clear()
        for session, manager in clients:
            try:
                await session.__aexit__(None, None, None)
            finally:
                await manager.__aexit__(None, None, None)

    @staticmethod
    def _validated_http_url(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("url is required.")
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use an HTTPS endpoint without embedded credentials.")
        loopback = parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "http" and not loopback:
            raise ValueError("HTTPS is required for remote MCP servers; cleartext HTTP is allowed only on loopback.")
        if parsed.fragment:
            raise ValueError("Endpoint URLs must not contain fragments.")
        return value

    @staticmethod
    def _resolved_headers(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("headers must be an object.")
        import re
        result = {}
        sensitive = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key", "x-auth-token", "token"}
        pattern = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")
        for key, raw in value.items():
            if not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or not isinstance(raw, str):
                raise ValueError("header names and values must be strings with valid header names.")
            if "\r" in raw or "\n" in raw:
                raise ValueError("header values cannot contain line breaks.")
            matches = list(pattern.finditer(raw))
            if key.casefold() in sensitive and not matches:
                raise ValueError(f"Sensitive header '{key}' must use an environment reference like ${{ENV:WORKBENCH_MCP_TOKEN}}.")
            resolved = raw
            for match in matches:
                secret = os.environ.get(match.group(1))
                if secret is None:
                    raise ValueError(f"Environment variable '{match.group(1)}' is not set.")
                resolved = resolved.replace(match.group(0), secret)
            result[key] = resolved
        return result

    def shutdown(self):
        if self.loop is None or not self.loop.is_running():
            return
        loop, thread = self.loop, self.loop_thread
        try:
            self.run(self.close())
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread and threading.current_thread() is not thread:
                thread.join(timeout=5)
            self.loop = None
            self.loop_thread = None
