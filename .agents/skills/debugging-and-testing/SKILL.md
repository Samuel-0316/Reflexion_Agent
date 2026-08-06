---
name: debugging-and-testing
description: |
  Common debugging techniques, testing commands, and troubleshooting patterns
  for the Price Check Reflexion Agent. Covers log interpretation, MCP debugging,
  FastAPI/frontend issues, LLM response parsing failures, and evaluation rubric tuning.
  Use this skill when something breaks or behaves unexpectedly.
---

# Debugging & Testing Guide

## Quick Health Checks

```bash
# Syntax check all Python files
python -m py_compile config.py state.py agent.py graph.py mcp_server.py mcp_client.py server.py memory.py

# Test MCP server tool directly (no client, no subprocess)
python -c "from mcp_server import search_prices; print(search_prices('iPhone 15 price India'))"
python -c "from mcp_server import verify_merchant_authority; print(verify_merchant_authority('https://www.ajio.com/'))"

# Test MCP client round-trip (spawns server as subprocess)
python -c "from mcp_client import web_search; print(web_search('Sony WH-1000XM5 price India'))"
python -c "from mcp_client import verify_merchant; print(verify_merchant('headphonezone.in'))"

# Test MCP with GUI inspector
npx -y @modelcontextprotocol/inspector@latest python mcp_server.py

# Run FastAPI server
uvicorn server:app --reload --port 8000

# Test API endpoints
curl http://localhost:8000/api/config
curl -X POST http://localhost:8000/api/clarify -H "Content-Type: application/json" -d "{\"product\": \"power bank\"}"
curl -X POST http://localhost:8000/api/clarify -H "Content-Type: application/json" -d "{\"product\": \"Sony WH-1000XM5\"}"
```

## Log Interpretation

All modules use Python's `logging` module. Logs go to stderr (visible in the terminal when running uvicorn).

### Log Emoji Legend
| Emoji | Source | Meaning |
|-------|--------|---------|
| 🤔 | agent.py / clarifier | Clarifier examining product |
| 🎯 | agent.py / actor_query | Search query generated |
| 🔎 | mcp_server.py | MCP search_prices called |
| 🔌 | mcp_client.py | MCP client connecting to server |
| 💰 | agent.py / actor_verdict | Price extraction result |
| 🛡️ | agent.py / evaluator | Merchant verification called |
| ⚖️ | agent.py / evaluator | Evaluation result |
| 🔁 | agent.py / reflector | Reflection/critique generated |
| ⚙️ | server.py | Config update (MAX_ATTEMPTS) |
| 🎬 | server.py | SSE stream / graph execution starting |
| 🏁 | server.py | Graph execution complete |
| ⏳ | agent.py | Rate limit retry |
| 🧹 | agent.py | Invalid sources filtered |
| 🧠 | server.py | Smart merge result |
| ⚠️ | various | Warning condition |
| ❌ | various | Error condition |

## Common Issues & Solutions

### 1. Clarifier Not Asking Questions for Generic Products
**Symptom:** Typing "power bank" or "laptop" goes straight to search.
**Cause:** The clarifier prompt lacks examples for that category.
**Fix:** Add the category to the "TOO VAGUE" rules and examples in `clarifier_node()` in `agent.py`.

### 2. SSE Stream Disconnects Prematurely
**Symptom:** The live activity feed stops updating mid-run, or the frontend shows no result.
**Cause:** The SSE connection timed out or the server crashed during `graph.stream()`.
**Debug:** Check the uvicorn terminal for error logs. The frontend has a fallback — if it received attempt data before disconnect, it uses the last attempt as the final state.
**Fix:** Check for exceptions in the terminal. Common cause is rate limiting (429) or MCP subprocess failures.

### 3. "Attempt 1/4" When Slider is Set to 3
**Symptom:** Live feed shows wrong max attempts.
**Cause:** The `POST /api/config` call failed, or using a hardcoded value instead of `config.MAX_ATTEMPTS`.
**Fix:** Always read from `getattr(config, "MAX_ATTEMPTS", 4)` dynamically in `server.py`. Check browser console for failed config API calls.

### 4. MCP "Event loop already running" Error
**Symptom:** `RuntimeError: This event loop is already running` when calling MCP.
**Cause:** MCP client's `asyncio.run()` called inside an already-running event loop.
**Fix:** The `ThreadPoolExecutor` pattern in `mcp_client.py` handles this. In `server.py`, synchronous graph operations are run via `loop.run_in_executor()` to avoid blocking FastAPI's event loop.

### 5. LLM Returns Invalid JSON
**Symptom:** `_parse_json()` returns `{}`, node gets empty/default values.
**Cause:** LLM wrapped JSON in markdown fences, or included extra text.
**Fix:** `_parse_json()` already handles this with regex. If still failing, check if the prompt's JSON example has correct escaping (`{{` and `}}` in f-strings).

### 6. Evaluator Always Fails Single-Source Results
**Symptom:** Products found on one legitimate store always get FAIL.
**Cause:** `verify_merchant()` returned `authoritative: False` for that domain.
**Debug:** Run `python -c "from mcp_server import verify_merchant_authority; print(verify_merchant_authority('the-domain.com'))"` to check the trust score.
**Fix:** Either add the domain to `AUTHORITATIVE_MARKETPLACES` (if it's a major platform) or adjust the algorithmic scoring keywords.

### 7. Duplicate Reflections Causing Infinite Loop
**Symptom:** Agent keeps generating the same critique and query.
**Cause:** `_is_similar()` threshold too high, or LLM keeps suggesting similar strategies.
**Debug:** Check the `reflections` list in the final state (use the debug panel in the sidebar or `GET /api/session`).
**Fix:** Lower the `threshold` in `_is_similar()` (currently 0.7), or add more variety prompting in the reflector.

### 8. Rate Limiting (429 Errors)
**Symptom:** `⏳ Rate limited — waiting Xs before retry` in logs.
**Cause:** Groq API free tier rate limits.
**Config:** `MAX_RETRIES=3`, `BASE_WAIT=40` seconds in `agent.py`.
**Fix:** Wait, upgrade API tier, or reduce `MAX_ATTEMPTS`.

### 9. CORS or Static File Issues
**Symptom:** Frontend loads but API calls fail, or `index.html` returns 404.
**Cause:** Static files mount path incorrect, or CORS headers missing.
**Fix:** Ensure `server.py` mounts static files with `app.mount("/static", StaticFiles(directory="static"), name="static")`. The `/` route serves `index.html` directly, so no CORS issues for same-origin requests.

## Debugging Session State

### Backend Debug (API)
Call `GET /api/session` to see the current server-side state:
```json
{
    "phase": "running",
    "product": "Sony WH-1000XM5",
    "original_product": "Sony headphones",
    "clarification_questions": [],
    "clarification_answers": ["WH-1000XM5"],
    "n_attempts": 2
}
```

### Frontend Debug (Sidebar)
The sidebar has a built-in debug panel (🐛 Debug button) that shows the JS `state` object as formatted JSON. If state gets corrupted, click "🔄 Reset session" in the sidebar — this calls `POST /api/reset` and resets the JS state.

### Browser Console
Open DevTools (F12) to see:
- API request/response payloads in the Network tab
- SSE event stream in the Network tab (filter by EventSource)
- JavaScript errors in the Console tab

## Testing Specific Scenarios

### Clarification Flow
- "iPad" → Should ask about size/connectivity
- "power bank" → Should ask about brand/capacity
- "Sony WH-1000XM5" → Should skip clarification

### Single Source Verification
- Search for niche products like "Meze Empyrean II" → triggers `verify_merchant_authority`
- Check that specialty stores (.in domains, headphonezone.in) get `authoritative: True`

### Multi-Attempt Reflexion
- Search for "Oura Ring Gen 3" → likely no Indian sources, tests all attempts
- Check that reflections are unique and progressively different

### Product Drift Detection
- If the LLM changes "iPhone 15" to "iPhone 15 Pro" in actor_verdict, the evaluator should FAIL with "product drift"
