"""
Agent roles — Actor, Evaluator, Reflector.

Each function is a LangGraph node: takes AgentState, returns a partial
state update dict.  All LLM calls go through a shared Gemini instance.
"""

from __future__ import annotations

import json
import re
import time

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

from config import GROQ_API_KEY, MODEL_NAME
from search import web_search

import logging
logger = logging.getLogger("agent")

# ── Retry config ─────────────────────────────────────────────────────
MAX_RETRIES = 3
BASE_WAIT   = 40  # seconds — Gemini free tier typically asks for ~35s


# ── Shared LLM instance ─────────────────────────────────────────────

def _get_llm() -> ChatGroq:
    """Return a configured Groq chat model."""
    return ChatGroq(
        model=MODEL_NAME,
        groq_api_key=GROQ_API_KEY,
        temperature=0.3,
    )


def _invoke_with_retry(llm, messages) -> tuple[str, dict]:
    """Call the LLM with automatic retry on rate-limit (429) errors.

    Returns:
        (content, usage_dict) where usage_dict has input_tokens,
        output_tokens, total_tokens.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = llm.invoke(messages)
            usage = dict(response.usage_metadata) if response.usage_metadata else {}
            return response.content, usage
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "resource_exhausted" in err_str or "rate" in err_str:
                wait = BASE_WAIT * attempt
                print(f"  ⏳ Rate limited — waiting {wait}s before retry ({attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
            else:
                raise  # non-rate-limit error, let it bubble up
    # Final attempt — let any error propagate
    response = llm.invoke(messages)
    usage = dict(response.usage_metadata) if response.usage_metadata else {}
    return response.content, usage


# ╔══════════════════════════════════════════════════════════════════╗
# ║  NODE 0 — Clarifier: detect ambiguity, ask user if needed       ║
# ╚══════════════════════════════════════════════════════════════════╝

def clarifier_node(state: dict) -> dict:
    """
    Check if the product name is ambiguous. Skip if the caller has
    already handled clarification (indicated by `_clarification_done`).
    """
    # ── Skip if caller already collected clarifications ──────────────
    if state.get("_clarification_done"):
        logger.info(f"🤔 Clarifier SKIPPED (already done): {state.get('product')!r}")
        return {"clarification_questions": []}

    product = state.get("product", "").strip()
    logger.info(f"🤔 Clarifier examining: {product!r}")

    # If somehow re-entered with questions still populated, clear them
    if state.get("clarification_questions"):
        return {"clarification_questions": []}

    llm = _get_llm()

    prompt = (
        f"You are a strict product-specificity checker for a price-research agent "
        f"operating in the INDIAN market.\n\n"
        f"Product name: \"{product}\"\n\n"
        f"Your job: determine if this product name refers to ONE SPECIFIC, "
        f"PURCHASABLE product (a single SKU that a store would list). "
        f"If it is vague, ambiguous, or refers to a product line/series/family, "
        f"you MUST flag it as not specific and ask clarifying questions.\n\n"
        f"A product name is TOO VAGUE if:\n"
        f"- It is just a brand name (e.g. 'boAt headphones', 'Samsung phone')\n"
        f"- It is a product series/family without a model number "
        f"(e.g. 'boAt Rockerz', 'iPhone', 'Galaxy S', 'Noise ColorFit')\n"
        f"- It is missing a key variant that changes price by >15% "
        f"(e.g. 'iPhone 15' without storage, 'MacBook Air' without screen size)\n"
        f"- It could refer to multiple generations "
        f"(e.g. 'AirPods' without specifying 2nd/3rd gen/Pro)\n\n"
        f"A product name IS SPECIFIC ENOUGH if:\n"
        f"- It includes a clear model number or name "
        f"(e.g. 'boAt Rockerz 450', 'iPhone 15 128GB', 'Sony WH-1000XM5')\n"
        f"- It is a product with only one variant on the market "
        f"(e.g. 'Kindle Paperwhite 2024')\n\n"
        f"STRICT RULES — DO NOT VIOLATE:\n"
        f"1. If the product is a brand + generic category (like 'boAt headphones'), "
        f"ask which SPECIFIC model they want. Suggest popular models if you know them.\n"
        f"2. If the product is a series name (like 'boAt Rockerz'), "
        f"ask which EXACT model number (e.g. Rockerz 400, 450, 510, 550).\n"
        f"3. Ask at most 2 questions. Prefer 1 unless 2 independent dimensions "
        f"are needed (e.g. iPad needs both size AND connectivity).\n"
        f"4. NEVER ask about color, warranty, or retailer preference.\n"
        f"5. NEVER ask a question whose answer is already in the product name.\n"
        f"6. When asking, ALWAYS provide specific examples or options the user "
        f"can pick from.\n"
        f"7. If the product is already specific enough, return "
        f"is_specific=true and an empty questions list.\n\n"
        f"Respond in EXACTLY this JSON format (no markdown fences):\n"
        f'{{\n'
        f'  "is_specific": true or false,\n'
        f'  "questions": ["Short specific question with examples"]\n'
        f'}}\n\n'
        f"Examples:\n"
        f"- 'boat headphones' → is_specific=false, "
        f"questions=[\"Which boAt headphone model? (e.g. Rockerz 450, "
        f"Rockerz 510, Airdopes 141, BassHeads 100)\"]\n"
        f"- 'boAt Rockerz' → is_specific=false, "
        f"questions=[\"Which Rockerz model? (e.g. Rockerz 400, 450, 510, "
        f"550, 255 Pro+)\"]\n"
        f"- 'boAt Rockerz 450' → is_specific=true, questions=[]\n"
        f"- 'iPhone 15' → is_specific=false, "
        f"questions=[\"Which storage: 128GB, 256GB, or 512GB?\"]\n"
        f"- 'iPhone 15 128GB' → is_specific=true, questions=[]\n"
        f"- 'Sony WH-1000XM5' → is_specific=true, questions=[]"
    )

    content, usage = _invoke_with_retry(llm, [HumanMessage(content=prompt)])
    parsed = _parse_json(content)

    is_specific = bool(parsed.get("is_specific", True))
    questions = parsed.get("questions", []) if not is_specific else []
    questions = [str(q).strip() for q in questions if str(q).strip()]
    questions = questions[:2]  # hard cap at 2

    logger.info(f"   → is_specific={is_specific}, questions={len(questions)}")

    return {
        "original_product": state.get("original_product", product),
        "clarification_questions": questions,
        "token_usage": [{
            "node": "clarifier",
            "attempt": state.get("attempt", 0),
            **usage,
        }],
    }


# ╔══════════════════════════════════════════════════════════════════╗
# ║  NODE 1 — Actor: decide search query                           ║
# ╚══════════════════════════════════════════════════════════════════╝

def actor_query_node(state: dict) -> dict:
    """Generate a web-search query for the current attempt."""
    llm = _get_llm()
    reflections = state.get("reflections", [])
    attempt = state.get("attempt", 0) + 1  # bump attempt counter

    reflection_block = ""
    if reflections:
        numbered = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(reflections))
        reflection_block = (
            f"\n\nPrevious attempts failed. Here are the critiques — "
            f"use them to craft a BETTER query this time:\n{numbered}"
        )

    prompt = (
        f"You are a price-research agent for the INDIAN market.\n"
        f"Product: {state['product']}\n"
        f"{reflection_block}\n\n"
        f"Your job: output a single web-search query (just the query string, "
        f"nothing else) that will find current retail prices for this EXACT "
        f"product from Indian online retailers.\n\n"
        f"IMPORTANT RULES:\n"
        f"- Preserve the product name EXACTLY as given — do NOT substitute it "
        f"with a different product, model, or brand.\n"
        f"- Do NOT invent specific model numbers that weren't in the original.\n"
        f"- Do NOT use search operators like 'site:', 'inurl:', 'OR', quotes, "
        f"or Boolean logic — the search backend does not support them. "
        f"Just write plain natural language.\n"
        f"- Do NOT list retailer names in the query — the search backend "
        f"already filters by Indian retailers automatically.\n"
        f"- Include 'price India' or 'price in India' to target Indian pricing.\n"
        f"- Keep the query concise: product name + 'price India' is usually "
        f"sufficient.\n\n"
        f"Output ONLY the search query text, no quotes, no explanation."
    )

    content, usage = _invoke_with_retry(llm, [HumanMessage(content=prompt)])
    query = content.strip().strip('"').strip("'")

    logger.info(f"🎯 Actor query (attempt {attempt}): {query!r}")
    return {
        "search_query": query,
        "attempt": attempt,
        "token_usage": [{
            "node": "actor_query",
            "attempt": attempt,
            **usage,
        }],
    }


# ╔══════════════════════════════════════════════════════════════════╗
# ║  NODE 2 — Search: run the web search                           ║
# ╚══════════════════════════════════════════════════════════════════╝

def search_node(state: dict) -> dict:
    """Execute the web search using the Actor's chosen query."""
    results = web_search(state["search_query"])
    return {"search_results": results}


# ╔══════════════════════════════════════════════════════════════════╗
# ║  NODE 3 — Actor: extract prices from search results             ║
# ╚══════════════════════════════════════════════════════════════════╝

def actor_verdict_node(state: dict) -> dict:
    """Analyse search results and find the best price."""
    llm = _get_llm()

    # Format search results for the prompt
    results_text = _format_results(state.get("search_results", []))
    reflections = state.get("reflections", [])

    reflection_block = ""
    if reflections:
        numbered = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(reflections))
        reflection_block = (
            f"\n\nPast reflections (address these in your analysis):\n{numbered}"
        )

    prompt = (
        f"You are a price-research agent for the INDIAN market.\n"
        f"Product: {state['product']}\n"
        f"{reflection_block}\n\n"
        f"Here are the web search results (Indian retailers only):\n{results_text}\n\n"
        f"Your task: Extract ALL prices in INDIAN RUPEES (₹ / INR) you can find "
        f"for this EXACT product. Identify the BEST (lowest) price and where "
        f"to buy it in India.\n\n"
        # ... existing text ...
        f"STRICT RULES:\n"
        f"- ONLY report prices in Indian Rupees (₹).\n"
        f"- Format prices as '₹XX,XXX'.\n"
        f"- **NEVER include a source in the 'sources' list unless it has an "
        f"actual numeric INR price FOR THE EXACT PRODUCT REQUESTED.**\n"
        f"  Do NOT include:\n"
        f"    ✗ Sources with 'No price available' or 'N/A' as the price\n"
        f"    ✗ Sources listing DIFFERENT products (e.g. Rockerz 550 when "
        f"      the user asked for Rockerz 500)\n"
        f"    ✗ Empty or placeholder prices\n"
        f"- If NO valid sources exist, return an EMPTY sources list [].\n"
        f"- If NO valid sources exist, set best_price to 'No price available'.\n"
        f"'Renewed', 'Certified Pre-Owned' as refurbished.\n"
        f"Respond in EXACTLY this JSON format (no markdown fences):\n"
        f'{{\n'
        f'  "best_price": "lowest INR price with retailer AND condition (e.g. \'₹4,999 at Flipkart (new)\' or \'₹36,490 at Croma Unboxed (refurbished/open-box)\')",\n'
        f'  "price_summary": "brief summary of INR prices across all Indian sources found",\n'
        f'  "reasoning": "detailed analysis including caveats (new vs refurbished, sale vs regular price)",\n'
        f'  "sources": [\n'
        f'    {{"name": "retailer name", "price": "₹price", "url": "URL if available"}},\n'
        f'    ...\n'
        f'  ]\n'
        f'}}\n\n'
        f"Be specific — cite actual INR prices and Indian retailers only."
    )

    content, usage = _invoke_with_retry(llm, [HumanMessage(content=prompt)])
    parsed = _parse_json(content)

    raw_sources = parsed.get("sources", [])

    # ── Defensive filter: drop garbage sources ─────────────────────
    def _is_valid_source(s: dict) -> bool:
        if not isinstance(s, dict):
            return False
        price = str(s.get("price", "")).strip()
        if not price:
            return False
        # Reject placeholder strings
        placeholder_markers = [
            "no price", "n/a", "not available", "unavailable",
            "no listing", "-", "?",
        ]
        if any(m in price.lower() for m in placeholder_markers):
            return False
        # Must contain a digit
        if not any(c.isdigit() for c in price):
            return False
        return True

    clean_sources = [s for s in raw_sources if _is_valid_source(s)]

    dropped = len(raw_sources) - len(clean_sources)
    if dropped:
        logger.info(f"   🧹 Filtered {dropped} invalid source(s)")

    logger.info(
        f"💰 Actor verdict: best_price={parsed.get('best_price', 'N/A')!r}, "
        f"n_sources={len(clean_sources)} (raw={len(raw_sources)})"
    )

    return {
        "best_price": parsed.get("best_price", "Could not determine"),
        "price_summary": parsed.get("price_summary", ""),
        "reasoning": parsed.get("reasoning", content),
        "sources": clean_sources,   # ← use filtered list
        "token_usage": [{
            "node": "actor_verdict",
            "attempt": state.get("attempt", 0),
            **usage,
        }],
    }


# ╔══════════════════════════════════════════════════════════════════╗
# ║  NODE 4 — Evaluator: judge evidence quality                      ║
# ╚══════════════════════════════════════════════════════════════════╝

def evaluator_node(state: dict) -> dict:
    """Check the Actor's price findings against the evidence-quality rubric."""
    llm = _get_llm()

    sources_str = json.dumps(state.get("sources", []), indent=2, default=str)

    prompt = (
        f"You are a strict evaluator for a price-finding agent in the INDIAN market.\n\n"
        f"ORIGINAL user query: {state.get('original_product', state['product'])!r}\n"
        f"Refined product (after clarification): {state['product']!r}\n"
        f"Best price found: {state.get('best_price', 'N/A')}\n"
        f"Price summary: {state.get('price_summary', 'N/A')}\n"
        f"Reasoning: {state.get('reasoning', 'N/A')}\n"
        f"Sources cited:\n{sources_str}\n"
        f"Raw search results:\n{_format_results(state.get('search_results', []))}\n\n"
        f"Evaluate against ALL of these criteria:\n"
        f"1. At least 2 independent Indian sources with actual INR prices\n"
        f"2. Sources are for the EXACT same product (not similar/variant/model)\n"
        f"3. All prices are in Indian Rupees (₹ / INR)\n"
        f"4. Sources are Indian retailers (amazon.in, flipkart.com, croma.com, etc.)\n"
        f"5. Prices distinguish condition (new vs refurbished) if relevant\n"
        f"6. The 'best_price' claim is supported by the numbers\n"
        f"7. **PRODUCT DRIFT CHECK**: The 'refined product' must be a valid, "
        f"more-specific version of the 'original query' — NOT a different product. "
        f"For example, 'iPhone 15 128GB' is valid refinement of 'iPhone 15'. "
        f"But 'iPhone 15 Pro' is NOT — it's a different product. If drift is "
        f"detected, FAIL with reason 'product drift'.\n\n"
        f"Respond in EXACTLY this JSON format (no markdown fences):\n"
        f'{{\n'
        f'  "result": "PASS" or "FAIL",\n'
        f'  "reason": "specific evidence-based explanation"\n'
        f'}}'
    )

    content, usage = _invoke_with_retry(llm, [HumanMessage(content=prompt)])
    parsed = _parse_json(content)

    logger.info(
        f"⚖️  Evaluator: {parsed.get('result', 'FAIL')} — "
        f"{parsed.get('reason', '')[:100]}"
    )
    return {
        "eval_result": parsed.get("result", "FAIL"),
        "eval_reason": parsed.get("reason", content.strip()),
        "token_usage": [{
            "node": "evaluator",
            "attempt": state.get("attempt", 0),
            **usage,
        }],
    }


# ╔══════════════════════════════════════════════════════════════════╗
# ║  NODE 5 — Reflector: critique on FAIL                            ║
# ╚══════════════════════════════════════════════════════════════════╝

def reflector_node(state: dict) -> dict:
    llm = _get_llm()
    past_reflections = state.get("reflections", [])
    history_block = ""
    if past_reflections:
        history_block = (
            "\nPast critiques (do NOT repeat these — try something different):\n"
            + "\n".join(f"  {i+1}. {r}" for i, r in enumerate(past_reflections))
        )

    prompt = (
        f"You are a reflection agent reviewing a failed price-finding attempt.\n\n"
        f"Product: {state['product']}\n"
        f"Search query used: {state.get('search_query', 'N/A')}\n"
        f"Best price found: {state.get('best_price', 'N/A')}\n"
        f"Evaluator failure reason: {state.get('eval_reason', 'N/A')}\n"
        f"{history_block}\n\n"
        f"The agent has ONLY these capabilities:\n"
        f"1. Formulate a text search query (given to Tavily search API)\n"
        f"2. Extract prices from search result snippets\n\n"
        f"It CANNOT:\n"
        f"- Call customer service or contact retailers\n"
        f"- Read historical price databases or market research reports\n"
        f"- Visit retailer websites directly (only sees search snippets)\n"
        f"- Access private APIs or paid data sources\n\n"
        f"Write a SHORT (1-2 sentence) NEW critique that suggests ONE of:\n"
        f"(a) A specific rewording of the search query "
        f"(e.g. 'try just <specific keywords>' — do NOT stuff more retailer names)\n"
        f"(b) A specific alternative interpretation of the product name\n"
        f"(c) Accepting the best available data if scarcity is the issue "
        f"(if 2+ attempts already found only 0-1 sources, the product may "
        f"genuinely be unavailable on the Indian open web).\n\n"
        f"STRICT RULES:\n"
        f"- Do NOT suggest calling anyone, reading reports, or consulting studies.\n"
        f"- Do NOT invent model numbers not in '{state['product']}'.\n"
        f"- Do NOT switch product categories.\n"
        f"- Must be materially different from past critiques.\n\n"
        f"Output ONLY the critique text."
    )

    content, usage = _invoke_with_retry(llm, [HumanMessage(content=prompt)])
    critique = content.strip()

    # ── Deduplication: reject near-identical reflections ──────────────
    past_reflections = state.get("reflections", [])
    if any(_is_similar(critique, prev) for prev in past_reflections):
        critique = (
            "Previous search strategies have been exhausted. "
            "The product name may be too generic to find an exact price match."
        )
        logger.info("   ⚠️ Duplicate reflection detected — using fallback")

    # ── Detect if failure is due to product ambiguity ────────────────
    eval_reason = state.get("eval_reason", "").lower()
    drift_signals = ["product drift", "too broad", "encompasses multiple",
                     "not a specific", "different product"]
    is_ambiguity = any(sig in eval_reason for sig in drift_signals)
    # +1 because we're about to add the current critique
    needs_reclarify = is_ambiguity and (len(past_reflections) + 1) >= 2

    logger.info(f"🔁 Reflection: {critique[:120]}")
    if needs_reclarify:
        logger.info("   🔄 Flagging for re-clarification")

    # Return ONLY the new critique — operator.add reducer handles accumulation
    return {
        "reflections": [critique],
        "needs_reclarification": needs_reclarify,
        "token_usage": [{
            "node": "reflector",
            "attempt": state.get("attempt", 0),
            **usage,
        }],
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _format_results(results: list[dict]) -> str:
    """Turn a list of search-result dicts into a readable string."""
    if not results:
        return "(no results found)"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"[{i}] {r.get('title', 'No title')}\n"
            f"    URL: {r.get('url', 'N/A')}\n"
            f"    Snippet: {r.get('content', 'N/A')}"
        )
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    """Best-effort JSON extraction from LLM output."""
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


def _is_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two strings have high word-overlap (used to dedup reflections)."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    return overlap / min(len(words_a), len(words_b)) > threshold