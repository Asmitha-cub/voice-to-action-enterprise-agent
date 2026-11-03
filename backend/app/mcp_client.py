"""Persistent MCP client.

The previous implementation spawned a fresh MCP server subprocess for *every*
tool call and did not pass the parent environment to it. MCP's stdio client
gives the child a clean environment, so DATABASE_URL / GROQ_API_KEY / SMTP
settings never reached the tool server and every call failed with an opaque
"unhandled errors in a TaskGroup" error.

This version:
  * starts ONE long-lived MCP server subprocess, lazily, on first use;
  * passes the full environment and the correct working directory to it;
  * owns the async session inside a single worker task (no cross-task cancel
    scope issues) and serializes calls through a queue;
  * respawns the worker if it dies, and surfaces clear error messages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import settings

log = logging.getLogger(__name__)

# backend/ directory (parent of the app package) so `-m app.mcp_server` resolves
# regardless of the current working directory.
_BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)


def _parse_response(response) -> dict | list:
    if getattr(response, 'isError', False):
        text = ''
        for part in getattr(response, 'content', None) or []:
            text += getattr(part, 'text', '') or ''
        return {'error': text or 'MCP tool returned an error.'}

    structured = getattr(response, 'structuredContent', None)
    if structured is not None:
        if isinstance(structured, dict) and 'result' in structured:
            return structured['result']
        return structured

    for part in getattr(response, 'content', None) or []:
        text = getattr(part, 'text', None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {'result': text}
    return {'error': 'MCP returned no result.'}


class MCPClient:
    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None
        self._ready: asyncio.Event | None = None
        self._start_error: str | None = None
        self._start_lock: asyncio.Lock | None = None

    def _server_params(self) -> StdioServerParameters:
        env = dict(os.environ)
        env.setdefault('PYTHONPATH', _BACKEND_ROOT)
        env.setdefault('PYTHONUNBUFFERED', '1')
        return StdioServerParameters(
            command=sys.executable,
            args=['-m', 'app.mcp_server'],
            cwd=_BACKEND_ROOT,
            env=env,
        )

    async def _run_worker(self) -> None:
        assert self._queue is not None and self._ready is not None
        try:
            async with stdio_client(self._server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._start_error = None
                    self._ready.set()
                    log.info('MCP session established.')
                    while True:
                        name, args, fut = await self._queue.get()
                        if name is None:  # shutdown sentinel
                            if not fut.done():
                                fut.set_result(None)
                            return
                        try:
                            resp = await session.call_tool(name, args)
                            if not fut.done():
                                fut.set_result(_parse_response(resp))
                        except Exception as exc:  # noqa: BLE001
                            if not fut.done():
                                fut.set_result({'error': f'MCP call failed: {exc}'})
        except Exception as exc:  # noqa: BLE001
            self._start_error = f'MCP server failed to start: {exc}'
            log.error(self._start_error)
            if self._ready is not None:
                self._ready.set()
        finally:
            # Fail any queued requests so callers are not left hanging.
            if self._queue is not None:
                while not self._queue.empty():
                    _, _, fut = self._queue.get_nowait()
                    if not fut.done():
                        fut.set_result({'error': self._start_error or 'MCP worker stopped.'})

    async def ensure_started(self) -> None:
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._worker is not None and not self._worker.done() and self._start_error is None:
                return
            self._queue = asyncio.Queue()
            self._ready = asyncio.Event()
            self._start_error = None
            self._worker = asyncio.create_task(self._run_worker())
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=settings().tool_timeout_seconds)
            except asyncio.TimeoutError:
                self._start_error = 'Timed out establishing MCP session.'

    async def call_tool(self, name: str, arguments: dict) -> dict | list:
        await self.ensure_started()
        if self._start_error:
            return {'error': self._start_error}
        assert self._queue is not None
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._queue.put((name, arguments, fut))
        try:
            return await asyncio.wait_for(fut, timeout=settings().tool_timeout_seconds)
        except asyncio.TimeoutError:
            return {'error': f"MCP tool '{name}' timed out after {settings().tool_timeout_seconds}s."}

    async def shutdown(self) -> None:
        if self._worker is None or self._worker.done():
            return
        try:
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            assert self._queue is not None
            await self._queue.put((None, {}, fut))
            await asyncio.wait_for(self._worker, timeout=5)
        except Exception:  # noqa: BLE001
            self._worker.cancel()


_client = MCPClient()


async def call_tool(name: str, arguments: dict) -> dict | list:
    return await _client.call_tool(name, arguments)


async def start_mcp() -> None:
    await _client.ensure_started()


async def stop_mcp() -> None:
    await _client.shutdown()
