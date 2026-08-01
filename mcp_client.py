"""
MCP Client — connects to the local MCP price-search server over stdio
and exposes a synchronous `web_search()` function matching the old
search.py interface so the rest of the codebase needs no changes.

The MCP server is spawned as a subprocess on first use and stays alive
for the duration of the process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("mcp_client")

# ── Path to the MCP server script ────────────────────────────────────
_SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "mcp_server.py")


async def _call_search_prices(
    query: str, max_results: int | None = None,
) -> list[dict]:
    """Spawn the MCP server, call `search_prices`, and return results."""

    server_params = StdioServerParameters(
        command=sys.executable,          # same Python interpreter
        args=[_SERVER_SCRIPT],
        env={**os.environ},              # pass through env (TAVILY_API_KEY etc.)
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # Build the tool arguments dict
            arguments: dict = {"query": query}
            if max_results is not None:
                arguments["max_results"] = max_results

            logger.info(
                f"🔌 MCP calling search_prices({query!r}, "
                f"max_results={max_results})"
            )

            result = await session.call_tool(
                "search_prices", arguments=arguments,
            )

            # Check for MCP-level errors
            if result.isError:
                error_text = (
                    result.content[0].text if result.content else "unknown error"
                )
                logger.error(f"   ❌ MCP tool error: {error_text}")
                return []

            # MCP tool results come back as a list of content blocks.
            # Our tool returns a JSON string, so the first block is text.
            if not result.content:
                logger.warning("   ⚠️ MCP returned no content blocks")
                return []

            text = result.content[0].text
            logger.debug(f"   📦 Raw MCP result (first 300 chars): {text[:300]}")

            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    logger.info(
                        f"   ← MCP returned {len(parsed)} results"
                    )
                    return parsed
                else:
                    logger.warning(
                        f"   ⚠️ MCP result is not a list: {type(parsed)}"
                    )
                    return []
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    f"   ⚠️ Could not parse MCP result as JSON: {e}\n"
                    f"      Raw text: {text[:300]}"
                )
                return []


# ── Synchronous wrapper (drop-in replacement for search.web_search) ──

def web_search(query: str, max_results: int | None = None) -> list[dict]:
    """Search for prices via the MCP server.

    This is a synchronous wrapper around the async MCP client call,
    matching the old `search.web_search()` signature so the rest of
    the codebase (agent.py, graph.py) needs no changes.
    """
    try:
        # If there's already a running event loop (e.g. inside Streamlit),
        # we need to handle that gracefully.
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an async context — use nest_asyncio or a thread.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run, _call_search_prices(query, max_results),
            )
            return future.result()
    else:
        return asyncio.run(_call_search_prices(query, max_results))
