"""
CLI entry point — runs the reflexion graph and prints the attempt trail.

Usage:
    python main.py
    python main.py --product "Sony WH-1000XM5"
"""

from __future__ import annotations

import argparse
import json
import sys

from config import GROQ_API_KEY, MAX_ATTEMPTS
from graph import build_graph
from state import AgentState


# ── Pretty-printing helpers ──────────────────────────────────────────

CYAN    = "\033[96m"
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"


def _header(product: str) -> None:
    print(f"\n{BOLD}{CYAN}{'='*55}{RESET}")
    print(f"{BOLD}{CYAN}  💰 Price Check Reflexion Agent{RESET}")
    print(f"{BOLD}{CYAN}{'='*55}{RESET}")
    print(f"  Product : {BOLD}{product}{RESET}")
    print(f"  Max attempts : {MAX_ATTEMPTS}")
    print(f"{CYAN}{'─'*55}{RESET}\n")


def _print_attempt(state: dict) -> None:
    attempt = state.get("attempt", "?")
    print(f"{BOLD}{YELLOW}--- Attempt {attempt} ---{RESET}")
    print(f"  Search query  : {state.get('search_query', 'N/A')}")

    n_results = len(state.get("search_results", []))
    print(f"  Results found : {n_results}")

    print(f"  Best price    : {BOLD}{state.get('best_price', 'N/A')}{RESET}")
    print(f"  Price summary : {state.get('price_summary', 'N/A')}")

    sources = state.get("sources", [])
    if sources:
        print(f"  Sources       :")
        for s in sources:
            if isinstance(s, dict):
                name = s.get("name", "Unknown")
                price = s.get("price", "N/A")
                url = s.get("url", "")
                line = f"    • {name}: {price}"
                if url:
                    line += f"  ({url})"
                print(line)
            else:
                print(f"    • {s}")

    eval_result = state.get("eval_result", "")
    if eval_result.upper() == "PASS":
        print(f"  Evaluator     : {GREEN}{BOLD}PASS{RESET} — {state.get('eval_reason', '')}")
    else:
        print(f"  Evaluator     : {RED}{BOLD}FAIL{RESET} — {state.get('eval_reason', '')}")

    reflections = state.get("reflections", [])
    if reflections:
        latest = reflections[-1]
        print(f"  Reflection    : {DIM}{latest}{RESET}")

    print()


def _print_final(state: dict) -> None:
    print(f"{BOLD}{CYAN}{'='*55}{RESET}")

    eval_result = state.get("eval_result", "")
    if eval_result.upper() == "PASS":
        print(f"{GREEN}{BOLD}  ✅ BEST PRICE FOUND (verified on attempt {state.get('attempt', '?')}){RESET}")
    else:
        print(f"{YELLOW}{BOLD}  ⚠️  BEST-EFFORT RESULT (max attempts reached){RESET}")

    print(f"\n  {BOLD}Product: {state.get('product', 'N/A')}{RESET}")
    print(f"  {GREEN}{BOLD}Best Price: {state.get('best_price', 'Could not determine')}{RESET}")
    print(f"\n  {state.get('price_summary', '')}")
    print(f"\n  {DIM}{state.get('reasoning', '')}{RESET}")

    sources = state.get("sources", [])
    if sources:
        print(f"\n  {BOLD}Price Comparison:{RESET}")
        for s in sources:
            if isinstance(s, dict):
                name = s.get("name", "Unknown")
                price = s.get("price", "N/A")
                url = s.get("url", "")
                line = f"    • {name}: {BOLD}{price}{RESET}"
                if url:
                    line += f"  {DIM}({url}){RESET}"
                print(line)
            else:
                print(f"    • {s}")

    reflections = state.get("reflections", [])
    if reflections:
        print(f"\n  {BOLD}Reflection trail:{RESET}")
        for i, r in enumerate(reflections, 1):
            print(f"    {i}. {DIM}{r}{RESET}")

    print(f"\n{CYAN}{'='*55}{RESET}\n")


def _ask_clarifications(product: str, questions: list[str]) -> str:
    """
    Interactively collect answers to clarifying questions, then return
    a refined product name that merges the original with the answers.
    """
    print(f"\n{BOLD}{YELLOW}🤔 The product name is a bit ambiguous.{RESET}")
    print(f"   To find the most accurate price, please clarify:\n")

    answers: list[str] = []
    for i, q in enumerate(questions, 1):
        print(f"   {BOLD}{i}. {q}{RESET}")
        ans = input(f"      > ").strip()
        if ans:
            answers.append(ans)
        print()

    if not answers:
        # User skipped everything — proceed with original
        return product

    # Merge into a refined product string
    refined = f"{product} ({', '.join(answers)})"
    print(f"{DIM}   → Searching for: {refined}{RESET}\n")
    return refined


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Price Check Reflexion Agent")
    parser.add_argument("--product", type=str, help="Product name to find the best price for")
    args = parser.parse_args()

    product = args.product or input("Enter product name: ").strip()

    if not product:
        print("Error: product name is required.")
        sys.exit(1)

    if not GROQ_API_KEY or GROQ_API_KEY == "your-groq-api-key-here":
        print(f"{RED}Error: GROQ_API_KEY not set.{RESET}")
        print(f"  1. Open the .env file in this directory")
        print(f"  2. Replace 'your-groq-api-key-here' with your actual Groq API key")
        print(f"  3. Get a key at: https://console.groq.com/keys")
        sys.exit(1)

    _header(product)

    graph = build_graph()

    initial_state: AgentState = {
        "product": product,
        "original_product": product,
        "attempt": 0,
        "reflections": [],
        "search_results": [],
        "sources": [],
        "clarification_questions": [],
    }

    # ── Run the graph, handling clarification if requested ──────────
    final_state = _run_with_clarification(graph, initial_state)

    _print_final(final_state)


def _run_with_clarification(graph, initial_state: dict) -> dict:
    """
    Run the graph. If clarifier requests clarifications, collect answers
    from the user, refine the product, and re-invoke the graph.
    Returns the final state.
    """
    state = initial_state
    max_clarify_rounds = 1  # only allow ONE round of clarification to avoid loops

    for clarify_round in range(max_clarify_rounds + 1):
        final_state = state.copy()
        last_printed_state = None
        last_printed_attempt = 0

        for event in graph.stream(state, stream_mode="values"):
            final_state.update(event)

            # Check for clarification request (only meaningful on first round)
            if final_state.get("clarification_questions"):
                # Graph exited early to request clarification
                break

            current_attempt = final_state.get("attempt", 0)
            has_eval = bool(final_state.get("eval_result"))
            if has_eval and current_attempt > last_printed_attempt:
                _print_attempt(final_state)
                last_printed_attempt = current_attempt
                last_printed_state = final_state.copy()

        # If clarifier asked questions AND we haven't already clarified once,
        # collect answers and loop.
        questions = final_state.get("clarification_questions", [])
        if questions and clarify_round < max_clarify_rounds:
            refined = _ask_clarifications(final_state.get("product", ""), questions)
            state = {
                **initial_state,
                "product": refined,
                "original_product": initial_state.get("product", ""),
                "clarification_questions": [],   # clear so clarifier passes through
            }
            continue

        # Either no questions, or we've already clarified once — we're done
        return last_printed_state or final_state

    return last_printed_state or final_state


if __name__ == "__main__":
    main()