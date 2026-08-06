---
name: mcp-integration
description: |
  How MCP (Model Context Protocol) is integrated in this project.
  Covers the FastMCP server, stdio transport, client wrappers,
  adding new MCP tools, the merchant verification heuristic,
  and testing with MCP Inspector.
  Use this skill when adding new MCP tools or modifying the search/verification pipeline.
---

# MCP Integration Guide

## Architecture Overview

```
agent.py (node functions)
    │  Calls: web_search(), verify_merchant()
    ▼
mcp_client.py (sync wrappers over async MCP protocol)
    │  Spawns subprocess, manages stdio streams
    ▼
mcp_server.py (FastMCP server, transport=stdio)
    │  Registered tools: search_prices, verify_merchant_authority
    ▼
External APIs (Tavily with country="india")
```

**Transport:** stdio (not SSE, not HTTP). The client spawns the server as a subprocess on every call. The server is stateless.

## Current MCP Tools

### 1. `search_prices(query, max_results?)`
- **Purpose:** Search for product prices across Indian e-commerce.
- **Backend:** Tavily API with `country="india"` scoping.
- **Input:** `query` (str), `max_results` (int, default 6).
- **Output:** JSON array of `{title, url, content}` objects.
- **Called by:** `search_node()` in `agent.py` via `mcp_client.web_search()`.

### 2. `verify_merchant_authority(domain_or_url)`
- **Purpose:** Algorithmically verify if a domain is an authoritative Indian retailer.
- **Backend:** Local heuristic scoring (no external API call).
- **Input:** `domain_or_url` (str) — full URL or bare domain.
- **Output:** JSON object with `{domain, trust_score, status, authoritative, reason}`.
- **Called by:** `evaluator_node()` in `agent.py` via `mcp_client.verify_merchant()` — **only when `len(sources) == 1`**.

### Trust Score Algorithm (verify_merchant_authority)

```
Base score: 0.50

Layer 1 — AUTHORITATIVE_MARKETPLACES dict (amazon.in, flipkart.com, ajio.com, etc.)
  → Instant trust_score: 1.0, authoritative: True

Layer 2 — Dynamic Algorithmic Scoring:
  +0.25  Indian domain TLD (.in, .co.in, .org.in, -india, /in/)
  +0.15  Retail URL path structure (/product, /p/, /item/, /shop, /dp/)
  +0.35  E-commerce hostname keywords (store, shop, kart, audio, tech, etc.)

Final: score >= 0.80 → authoritative: True
       score < 0.80  → authoritative: False
```

## Adding a New MCP Tool

### Step 1: Define the tool in `mcp_server.py`

```python
@mcp.tool()
def my_new_tool(param1: str, param2: int = 10) -> str:
    """Docstring becomes the tool description for MCP clients.

    Args:
        param1: Description of param1.
        param2: Description of param2 (default: 10).

    Returns:
        A JSON string with the result.
    """
    # Your logic here
    result = {"key": "value"}
    return json.dumps(result)
```

**Rules:**
- Decorate with `@mcp.tool()`.
- Return a **JSON string** (not a dict).
- Write a comprehensive docstring — it's exposed to MCP clients.
- Type hints on all parameters.

### Step 2: Add the async client call in `mcp_client.py`

```python
async def _call_my_new_tool(param1: str, param2: int = 10) -> dict:
    """Spawn the MCP server, call `my_new_tool`, and return result dict."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[_SERVER_SCRIPT],
        env={**os.environ},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            arguments = {"param1": param1, "param2": param2}
            logger.info(f"🔌 MCP calling my_new_tool({param1!r})")

            result = await session.call_tool("my_new_tool", arguments=arguments)

            if result.isError or not result.content:
                return {"error": "MCP tool call failed"}

            text = result.content[0].text
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"error": "Failed to parse MCP result"}
```

### Step 3: Add the sync wrapper in `mcp_client.py`

```python
def my_new_tool_sync(param1: str, param2: int = 10) -> dict:
    """Synchronous wrapper for my_new_tool."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _call_my_new_tool(param1, param2))
            return future.result()
    else:
        return asyncio.run(_call_my_new_tool(param1, param2))
```

**Key pattern:** Uses asyncio + ThreadPoolExecutor for event loop compatibility, which is required because the calling context (FastAPI or any async framework) may already have a running event loop. In `server.py`, synchronous graph calls are run via `loop.run_in_executor()`, and MCP client calls within those threads use this same pattern.

### Step 4: Import and use in `agent.py`

```python
from mcp_client import my_new_tool_sync

def some_node(state: dict) -> dict:
    result = my_new_tool_sync(state["some_field"])
    return {"result_field": result}
```

## Testing MCP Tools

### MCP Inspector (GUI)
```bash
npx -y @modelcontextprotocol/inspector@latest python mcp_server.py
```
Opens a web UI at `http://localhost:6274` where you can call tools interactively.

### Quick CLI test
```bash
python -c "from mcp_server import search_prices; print(search_prices('iPhone 15 price India'))"
python -c "from mcp_server import verify_merchant_authority; print(verify_merchant_authority('https://www.ajio.com/'))"
```

### Full MCP round-trip test
```bash
python -c "from mcp_client import web_search; print(web_search('Sony WH-1000XM5 price India'))"
python -c "from mcp_client import verify_merchant; print(verify_merchant('headphonezone.in'))"
```
