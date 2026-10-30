"""Local stdio MCP clients owned by one long-lived asyncio event loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
import os
import threading
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
            if not isinstance(cfg, dict) or not cfg.get("command"):
                self.errors[name] = "Invalid server configuration: command is required."
                continue
            manager = stdio_client(StdioServerParameters(
                command=cfg["command"], args=cfg.get("args", []),
                env={**os.environ, **cfg.get("env", {})},
            ))
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
        return "\n".join(c.text for c in result.content if getattr(c, "text", None)) or json.dumps([
            getattr(c, "model_dump", lambda: {})() for c in result.content
        ])

    async def close(self):
        clients, self.clients = list(self.clients.values()), {}
        self.tools.clear()
        for session, manager in clients:
            try:
                await session.__aexit__(None, None, None)
            finally:
                await manager.__aexit__(None, None, None)

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
