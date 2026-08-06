---
name: prompt-engineering
description: |
  Reference for all LLM prompts used in the agent pipeline.
  Covers the clarifier, actor query, actor verdict, evaluator, and reflector prompts.
  Documents the rubric, strict rules, JSON schemas, and known prompt pitfalls.
  Use this skill when modifying agent behavior, tuning evaluation criteria,
  or debugging why the agent makes certain decisions.
---

# Prompt Engineering Guide

## Prompt Locations

All prompts are embedded as f-strings inside their respective node functions in `agent.py`:

| Node | Function | Lines (approx) | Purpose |
|------|----------|-----------------|---------|
| Clarifier | `clarifier_node()` | ~85-150 | Detect ambiguous product names, ask clarification questions |
| Actor Query | `actor_query_node()` | ~187-206 | Generate a web search query string |
| Actor Verdict | `actor_verdict_node()` | ~253-285 | Extract prices from search results |
| Evaluator | `evaluator_node()` | ~367-394 | Judge evidence quality against rubric |
| Reflector | `reflector_node()` | ~429-454 | Generate critique for failed attempts |
| Smart Merge | `_smart_merge_product()` in `server.py` | ~111-125 | Merge product name + clarification answers |

## Clarifier Prompt

**Goal:** Determine if a product name is specific enough to search for, or if it needs clarification.

**Key rules in the prompt:**
- Generic categories without brand/model → ask (e.g., "power bank", "laptop", "earbuds")
- Brand + generic category → ask which model (e.g., "boAt headphones")
- Series name → ask which model number (e.g., "boAt Rockerz")
- Missing price-affecting variants → ask (e.g., "iPhone 15" without storage)
- Max 2 questions, prefer 1
- Never ask about color, warranty, or retailer

**Output JSON schema:**
```json
{
  "is_specific": true/false,
  "questions": ["Short specific question with examples"]
}
```

**Common pitfall:** If you add examples, make sure they demonstrate `is_specific=false` for truly generic inputs. The LLM tends to be too permissive without explicit negative examples.

## Actor Query Prompt

**Goal:** Generate a plain-language web search query.

**Key rules:**
- Preserve the product name EXACTLY (no substitution)
- No search operators (`site:`, `OR`, quotes, Boolean)
- No retailer names in the query (Tavily handles Indian scoping)
- Include "price India" or "price in India"
- Output ONLY the query text

**Common pitfall:** The LLM may add retailer names ("Flipkart Amazon") or search operators. The `_sanitize_query()` function in `mcp_server.py` strips these as a safety net, but the prompt should discourage them.

## Actor Verdict Prompt

**Goal:** Extract prices from search result snippets.

**Key rules:**
- Only INR prices (₹ format)
- Sources must have actual numeric prices (not "N/A" or "No price available")
- Distinguish new vs refurbished/open-box condition
- Empty sources list if no valid prices found

**Output JSON schema:**
```json
{
  "best_price": "₹XX,XXX at RetailerName (new/refurbished)",
  "price_summary": "brief summary",
  "reasoning": "detailed analysis",
  "sources": [
    {"name": "retailer", "price": "₹XX,XXX", "url": "https://..."}
  ]
}
```

**Defensive filter:** `_is_valid_source()` in `actor_verdict_node()` programmatically drops sources with placeholder prices, even if the LLM includes them.

## Evaluator Prompt

**Goal:** Judge if the actor's findings meet the evidence-quality bar.

**7-point rubric:**
1. **Source requirement:** Either 2+ independent Indian retailers with INR prices, OR 1 verified authoritative source (official brand .in site, authorized distributor)
2. Sources are for the EXACT same product (not similar/variant)
3. All prices in INR
4. Sources are Indian retailers or official brand sites
5. Condition distinguished (new vs refurbished)
6. `best_price` claim supported by numbers
7. **Product drift check:** refined product must be a valid refinement of original query (not a different product)

**MCP Verification Block:** When `len(sources) == 1`, the evaluator calls `verify_merchant()` and injects an `[🔍 ALGORITHMIC MERCHANT VERIFICATION REPORT]` into the prompt if the source scores `>= 0.8`. This allows the LLM judge to PASS on a single verified authoritative source.

**Output JSON schema:**
```json
{
  "result": "PASS" or "FAIL",
  "reason": "specific evidence-based explanation"
}
```

## Reflector Prompt

**Goal:** Generate a 1-2 sentence critique suggesting a specific improvement for the next attempt.

**Allowed suggestions:**
- (a) Reword the search query
- (b) Alternative interpretation of the product name
- (c) Search for authorized distributors / specialty stores

**Strict rules:**
- No suggesting to call anyone, read reports, or consult studies
- No inventing model numbers
- No switching product categories
- Must be different from past critiques

**Deduplication:** `_is_similar()` checks word-overlap (>70% threshold) against past reflections. If duplicate, a fallback message is used.

## Prompt Modification Checklist

When modifying any prompt:
1. ☐ Does the new prompt maintain the JSON output schema exactly?
2. ☐ Did you test with both specific ("Sony WH-1000XM5") and ambiguous ("power bank") inputs?
3. ☐ Does the change affect the evaluator rubric? If so, update both the evaluator AND reflector prompts.
4. ☐ Run `python -m py_compile agent.py` — f-strings with braces are fragile.
5. ☐ Check that `{{` and `}}` are used for literal braces in f-strings (not `{` and `}`).
