---
name: debugging-and-testing
description: |
  Common debugging techniques, testing commands, and troubleshooting patterns
  for the Price Check Reflexion Agent. Covers log interpretation, MCP debugging,
  Streamlit session issues, LLM response parsing failures, and evaluation rubric tuning.
  Use this skill when something breaks or behaves unexpectedly.
---

# Debugging & Testing Guide

## Quick Health Checks

```bash
# Syntax check all Python files
python -m py_compile config.py state.py agent.py graph.py mcp_server.py mcp_client.py app.py memory.py

# Test MCP server tool directly (no client, no subprocess)
python -c "from mcp_server import search_prices; print(search_prices('iPhone 15 price India'))"
python -c "from mcp_server import verify_merchant_authority; print(verify_merchant_authority('https://www.ajio.com/'))"

# Test MCP client round-trip (spawns server as subprocess)
python -c "from mcp_client import web_search; print(web_search('Sony WH-1000XM5 price India'))"
python -c "from mcp_client import verify_merchant; print(verify_merchant('headphonezone.in'))"

# Test MCP with GUI inspector
npx -y @modelcontextprotocol/inspector@latest python mcp_server.py

# Run Streamlit
streamlit run app.py
```

## Log Interpretation

All modules use Python's `logging` module. Logs go to stderr (visible in the terminal when running Streamlit).

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
| 📍 | app.py | Current UI phase |
| ➡️ | app.py | Phase transition |
| 🎬 | app.py | Graph execution starting |
| 🏁 | app.py | Graph execution complete |
| ⏳ | agent.py | Rate limit retry |
| 🧹 | agent.py | Invalid sources filtered |
| ⚠️ | various | Warning condition |
| ❌ | various | Error condition |

## Common Issues & Solutions

### 1. Clarifier Not Asking Questions for Generic Products
**Symptom:** Typing "power bank" or "laptop" goes straight to search.
**Cause:** The clarifier prompt lacks examples for that category.
**Fix:** Add the category to the "TOO VAGUE" rules and examples in `clarifier_node()` in `agent.py`.

### 2. Raw HTML Showing in Streamlit
**Symptom:** `<div style="...">` text visible in the UI.
**Cause:** Streamlit's markdown renderer treats indented HTML as code blocks.
**Fix:** Use native Streamlit components only. Never use `unsafe_allow_html=True` with indented strings.

### 3. "Attempt 1/4" When Slider is Set to 3
**Symptom:** Live feed shows wrong max attempts.
**Cause:** Using a hardcoded value instead of `config.MAX_ATTEMPTS`.
**Fix:** Always read from `getattr(config, "MAX_ATTEMPTS", 4)` dynamically.

### 4. MCP "Event loop already running" Error
**Symptom:** `RuntimeError: This event loop is already running` when calling MCP from Streamlit.
**Cause:** Streamlit runs its own asyncio event loop.
**Fix:** The `ThreadPoolExecutor` pattern in `mcp_client.py` handles this. Never call `asyncio.run()` directly in Streamlit context.

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
**Debug:** Check the `reflections` list in the final state.
**Fix:** Lower the `threshold` in `_is_similar()` (currently 0.7), or add more variety prompting in the reflector.

### 8. Rate Limiting (429 Errors)
**Symptom:** `⏳ Rate limited — waiting Xs before retry` in logs.
**Cause:** Groq API free tier rate limits.
**Config:** `MAX_RETRIES=3`, `BASE_WAIT=40` seconds in `agent.py`.
**Fix:** Wait, upgrade API tier, or reduce `MAX_ATTEMPTS`.

## Debugging Session State (Streamlit)

The sidebar has a built-in debug panel:
```
🐛 Debug: session state
├── phase: "running"
├── product: "Sony WH-1000XM5"
├── original_product: "Sony headphones"
├── clarification_questions: []
├── clarification_answers: ["WH-1000XM5"]
└── n_attempts: 2
```

If state gets corrupted, click "🔄 Reset session" in the sidebar.

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
