# 🔌 Model Context Protocol (MCP) — Deep-Dive Architecture & Guide

This document is a comprehensive, end-to-end technical guide to the **Model Context Protocol (MCP)** integration in the **Price Check Reflexion Agent**. It explains what MCP is, why we migrated from a monolithic script to an MCP client-server architecture, how the protocol communicates, and the exact input/output schemas used in this project.

---

## 1. 🌐 What is the Model Context Protocol (MCP)?

The **Model Context Protocol (MCP)** is an open standard developed by Anthropic and the open-source community that standardizes how AI models and agentic applications interact with external data sources, tools, and environments.

Think of MCP as **"USB-C for AI applications."**
- Before USB-C, every device had a proprietary charger or custom connector.
- Before MCP, every AI agent had its own ad-hoc, hardcoded functions for calling APIs, reading files, or searching the web.

With MCP, capabilities are packaged into standalone **MCP Servers**. Any compatible **MCP Client** (such as our LangGraph agent, Claude Desktop, Cursor IDE, or VS Code) can connect to an MCP Server over a standard protocol and invoke its tools without needing to know how the underlying APIs are implemented.

```
       +-------------------------------------------------------+
       |                  Compatible Clients                   |
       |  (LangGraph Agent, Claude Desktop, Cursor IDE, etc.)  |
       +-------------------------------------------------------+
                |                   |                   |
                | (MCP / stdio)     | (MCP / stdio)     | (MCP / stdio)
                v                   v                   v
       +-------------------------------------------------------+
       |                      MCP Server                       |
       |     (Exposes standardized 'search_prices' tool)       |
       +-------------------------------------------------------+
                                    |
                                    v
                       +------------------------+
                       |   External APIs        |
                       |  (Tavily / Retailers)  |
                       +------------------------+
```

---

## 2. 💡 Why We Used MCP in This Project (Problem vs. Solution)

### The Old Approach (`search.py` — Monolithic)
Previously, the search logic was hardcoded inside `search.py`.
- **Tightly Coupled:** The LangGraph agent nodes directly imported and executed Tavily API client calls.
- **Not Reusable:** If you wanted to use our custom Indian retail search logic inside Claude Desktop or another agent, you would have to copy-paste the Python code.
- **No Isolation:** Any crash or dependency conflict in the search library directly impacted the core agent application.

### The MCP Approach (`mcp_server.py` + `mcp_client.py` — Modular)
We decoupled search into a standalone **MCP Server** (`mcp_server.py`) and connected our LangGraph nodes via an **MCP Client** (`mcp_client.py`).
- **Standardized Tool Exposure:** The server exposes a single, strongly typed tool: `search_prices`.
- **Universal Compatibility:** You can now connect `mcp_server.py` directly to Claude Desktop, Cursor, or any MCP-enabled AI app without changing a line of code.
- **Process Isolation:** The MCP server runs in its own process, communicating over standard input/output (`stdio`).

---

## 3. ⚙️ Core Concepts & The `stdio` Transport Layer

MCP supports two primary transport mechanisms:
1. **`stdio` (Standard Input / Output):** The client spawns the server as a local subprocess and communicates by sending JSON-RPC 2.0 messages over standard input (`stdin`) and receiving replies over standard output (`stdout`).
2. **`SSE / HTTP` (Server-Sent Events):** Used for remote servers over a network.

### Why We Use `stdio`
In this project, we use **`stdio` transport** because:
- **Zero Configuration:** No port conflicts, no firewall rules, and no background services to manage.
- **Automatic Lifecycle Management:** When the client starts a search, it spawns `mcp_server.py`. When the search finishes, the subprocess cleans up automatically.
- **Security:** All communication stays 100% local on your machine.

---

## 4. 🏛️ Architecture & Communication Flow in Our Project

Here is the exact step-by-step architecture flow when our agent performs a search:

```
+-------------------+            +---------------------+            +--------------------+            +-------------------+
|     agent.py      |            |    mcp_client.py    |            |   mcp_server.py    |            |    Tavily API     |
|   (search_node)   |            |    (stdio client)   |            |  (FastMCP Server)  |            |  (Indian Retail)  |
+---------+---------+            +----------+----------+            +---------+----------+            +---------+---------+
          |                                 |                                 |                                 |
          |  1. web_search(query, max=6)    |                                 |                                 |
          |-------------------------------->|                                 |                                 |
          |                                 |  2. Spawns python mcp_server.py |                                 |
          |                                 |     (Subprocess via stdio)      |                                 |
          |                                 |-------------------------------->|                                 |
          |                                 |                                 |                                 |
          |                                 |  3. JSON-RPC tools/call         |                                 |
          |                                 |     {"name": "search_prices"}   |                                 |
          |                                 |-------------------------------->|                                 |
          |                                 |                                 |  4. Sanitize Query & Filter     |
          |                                 |                                 |     (amazon.in, flipkart, etc.) |
          |                                 |                                 |-------------------------------->|
          |                                 |                                 |                                 |
          |                                 |                                 |  5. Returns Indian retail items |
          |                                 |                                 |     (Title, URL, Snippet, INR)  |
          |                                 |                                 |<--------------------------------|
          |                                 |  6. JSON-RPC Response           |                                 |
          |                                 |     [{"title": "...", ...}]     |                                 |
          |                                 |<--------------------------------|                                 |
          |  7. Returns list[dict]          |                                 |                                 |
          |     to LangGraph state          |                                 |                                 |
          |<--------------------------------|                                 |                                 |
```

### Component Breakdown

#### A. `mcp_server.py` (The Server)
- **Framework:** Built using Anthropic's official Python SDK (`mcp.server.fastmcp.FastMCP`).
- **Tool Registration:** We register the function `search_prices(query: str, max_results: int = 6)` using the `@mcp.tool()` decorator.
- **Domain Filtering:** It hardcodes a curated list of trusted Indian e-commerce domains:
  - `amazon.in`, `flipkart.com`, `croma.com`, `reliancedigital.in`, `vijaysales.com`, `tatacliq.com`, `jiomart.com`, `nykaa.com`, `myntra.com`, `apple.com/in`, `samsung.com/in`, `store.google.com/in`.
- **JSON Serialization Guarantee:** To ensure clean compatibility across all clients, the server explicitly serializes its result into a valid **JSON string** (`json.dumps(...)`) rather than returning raw Python objects.

#### B. `mcp_client.py` (The Client)
- **Framework:** Built using `mcp.client.stdio.stdio_client` and `mcp.ClientSession`.
- **Async-to-Sync Bridge:** LangGraph nodes in `agent.py` are synchronous functions. `mcp_client.py` wraps the asynchronous MCP session inside an event loop (`asyncio.run()`), exposing a simple, synchronous `web_search(query, max_results)` function.
- **Subprocess Execution:** Automatically detects the current Python executable (`sys.executable`) to spawn `mcp_server.py` in the active virtual environment (`venv`).
- **Fault Tolerance:** Includes fallback handling—if the MCP server is unreachable or returns malformed JSON, it catches the exception and returns a structured empty list with an error log so the agent can gracefully reflect without crashing.

---

## 5. 📋 Detailed Tool Specification (Inputs & Outputs)

When any MCP client inspects `mcp_server.py`, it discovers the following JSON-RPC tool definition:

### Tool Identifier
- **Name:** `search_prices`
- **Description:** *"Search Indian e-commerce websites (Amazon.in, Flipkart, Croma, Reliance Digital, etc.) for product prices using Tavily."*

### Input Schema (Parameters)

| Parameter | Type | Required | Default | Description |
| :--- | :--- | :---: | :---: | :--- |
| **`query`** | `string` | **Yes** | — | The natural-language search string generated by `actor_query_node` (e.g., `"boAt Rockerz 450 price in India"`). |
| **`max_results`** | `integer` | No | `6` | The maximum number of search results to fetch from Tavily. |

#### Example JSON-RPC Input Payload (`tools/call`):
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "search_prices",
    "arguments": {
      "query": "Sony WH-1000XM5 price in India",
      "max_results": 5
    }
  },
  "id": 1
}
```

### Output Schema (Response)

The server returns a text content block containing a JSON-encoded array of search result objects. Each item in the array has the following structure:

| Field | Type | Description |
| :--- | :--- | :--- |
| **`title`** | `string` | The web page title (e.g., *"Sony WH-1000XM5 Wireless Headphones - Amazon.in"*). |
| **`url`** | `string` | The full Indian e-commerce URL. |
| **`content`** | `string` | The snippet/snippet text containing INR prices (₹) and product details. |
| **`score`** | `float` | Relevance score assigned by Tavily (between `0.0` and `1.0`). |

#### Example JSON-RPC Output Payload:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "[\n  {\n    \"title\": \"Sony WH-1000XM5 Noise Cancelling Headphones - Amazon.in\",\n    \"url\": \"https://www.amazon.in/Sony-WH-1000XM5-Cancelling-Headphones-Bluetooth/dp/B09XS7JWHH\",\n    \"content\": \"Buy Sony WH-1000XM5 ... Special Price ₹26,990. M.R.P.: ₹34,990 ... In stock.\",\n    \"score\": 0.9842\n  },\n  {\n    \"title\": \"Sony WH-1000XM5 Wireless Headphones - Flipkart\",\n    \"url\": \"https://www.flipkart.com/sony-wh-1000xm5-bluetooth-headset/p/itm...\",\n    \"content\": \"Sony WH-1000XM5 Price in India: ₹27,490 ... 10% instant discount available.\",\n    \"score\": 0.9411\n  }\n]"
      }
    ],
    "isError": false
  },
  "id": 1
}
```

---

## 6. 🔄 Complete Lifecycle: What Happens During a Search Step?

When you type a product in the Streamlit GUI (`app.py`) and the agent reaches Attempt 1:

1. **`actor_query_node`:** Generates a query like `"Oura Ring Generation 3 price India"`.
2. **`search_node`:** Invokes `mcp_client.web_search(query="Oura Ring Generation 3 price India", max_results=6)`.
3. **Pipe Opening:** `mcp_client.py` uses `asyncio` to spawn a subprocess:
   ```bash
   venv/Scripts/python.exe mcp_server.py
   ```
   It establishes two local pipes: `stdin` (sending to server) and `stdout` (reading from server).
4. **Handshake & Discovery:**
   - The client sends an MCP `initialize` request.
   - The server responds with its capabilities and tool list (`search_prices`).
5. **Tool Execution:**
   - The client sends `tools/call` for `search_prices`.
   - `mcp_server.py` strips boolean operators (`OR`, `AND`), attaches domain filters (`amazon.in`, `flipkart.com`, etc.), and calls `tavily_client.search()`.
6. **Result Formatting:**
   - The server dumps the results list to a JSON string and sends it over `stdout`.
7. **Process Termination:**
   - The MCP session closes, and the subprocess exits cleanly.
8. **State Synthesis:**
   - `mcp_client.py` parses the JSON string back into Python dictionaries and returns them to `search_node`.
   - `actor_verdict_node` examines the snippets for ₹ / INR pricing.

---

## 7. 🔌 How to Use This MCP Server in Other Applications

Because `mcp_server.py` is fully protocol-compliant, you can plug it into other popular AI interfaces:

### A. Using in Claude Desktop (Official GUI)
You can make Claude in your desktop app use this Indian retail search engine automatically:

1. Open **Claude Desktop** → **Settings** → **Developer** → **Edit Config**.
2. Add this block to your `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "indian-price-search": {
         "command": "C:/Users/Samuel Srujan B/Desktop/reflexion agent/price_check_agent/venv/Scripts/python.exe",
         "args": [
           "C:/Users/Samuel Srujan B/Desktop/reflexion agent/price_check_agent/mcp_server.py"
         ],
         "env": {
           "TAVILY_API_KEY": "your_tavily_api_key_here"
         }
       }
     }
   }
   ```
3. Restart Claude Desktop. A hammer icon (🔨) will appear in the bottom corner of your chat input, showing that `search_prices` is available!

### B. Using via the MCP Inspector CLI
To inspect schemas or run test tool calls visually in your browser:
```powershell
npx -y @modelcontextprotocol/inspector python mcp_server.py
```
*(Connects over stdio and opens a local web interface to inspect tool arguments and output).*

### C. Using Programmatically in Python
Any Python script can invoke the search client without running the whole LangGraph loop:
```python
from mcp_client import web_search

results = web_search("Sony WH-1000XM5 price in India", max_results=3)
for item in results:
    print(f"{item['title']} -> {item['url']}")
```

---

## 8. 📊 Summary Table: Old vs. New Architecture

| Feature | Old (`search.py`) | New (`mcp_server.py` + `mcp_client.py`) |
| :--- | :--- | :--- |
| **Architecture** | Monolithic function call | Decoupled Client-Server (MCP standard) |
| **Transport** | In-process Python import | Subprocess over `stdio` (JSON-RPC 2.0) |
| **Interoperability** | Limited to this project | Works with Claude Desktop, Cursor, IDEs |
| **Data Format** | Raw Python `list[dict]` | Explicit JSON string over text content blocks |
| **Fault Isolation** | API crashes affect app | Subprocess isolation with structured error recovery |