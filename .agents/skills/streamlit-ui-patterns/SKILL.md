---
name: streamlit-ui-patterns
description: |
  Patterns and rules for the FastAPI + HTML/Tailwind frontend in server.py and static/index.html.
  Covers the phase state machine, session state management, SSE streaming,
  rendering helpers, API endpoints, and common pitfalls.
  Use this skill when modifying the UI, adding new phases, or fixing display issues.
  (Note: skill directory still named "streamlit-ui-patterns" for git continuity;
  the actual UI is now FastAPI + HTML/Tailwind.)
---

# Frontend UI Patterns (FastAPI + HTML/Tailwind)

## Architecture Overview

```
static/index.html (SPA)
    │  Phase state machine in vanilla JS
    │  SSE consumer via EventSource
    │  Tailwind CSS v3 (CDN) + custom glassmorphism styles
    ▼
server.py (FastAPI backend)
    │  REST endpoints: /api/config, /api/clarify, /api/merge, /api/reset, /api/history
    │  SSE endpoint: GET /api/run (streams agent execution events)
    │  Serves static files from static/ directory
    ▼
graph.py / agent.py (unchanged agent pipeline)
```

## Phase State Machine

The UI operates as a 4-phase state machine managed via the JS `state.phase` variable:

```
"input" → "clarify" → "running" → "done"
                ↑           │
                └───────────┘  (multi-round clarification, max 3 rounds)
```

### Phase Transitions
- **input → clarify:** Product is ambiguous, `/api/clarify` returned questions.
- **input → running:** Product is specific enough, skip clarification.
- **clarify → clarify:** Refined product still ambiguous (round < 3).
- **clarify → running:** Product is now specific, or user clicked "Skip".
- **running → done:** SSE stream sends `done` event.
- **done → input:** User clicks "Search another product".

Every transition calls `setPhase('new_phase')` which toggles `.phase-hidden` CSS class and re-triggers entry animations.

### setPhase() Implementation
```javascript
function setPhase(phase) {
    state.phase = phase;
    // Hide all phase containers
    ['phaseInput', 'phaseClarify', 'phaseRunning', 'phaseDone', 'errorPanel'].forEach(id => {
        document.getElementById(id).classList.add('phase-hidden');
    });
    // Show the target phase container
    const target = { input: 'phaseInput', clarify: 'phaseClarify', ... }[phase];
    document.getElementById(target).classList.remove('phase-hidden');
    updateDebugPanel();
}
```

## Session State

### Backend (server.py — Python dict)
```python
session_state = {
    "phase": "input",
    "product": "",
    "original_product": "",
    "clarification_questions": [],
    "clarification_answers": [],
    "clarify_round": 0,
    "final_state": None,
    "attempts": [],
    "recent_runs": [],          # Persisted across resets
    "pre_graph_tokens": [],
}
```

### Frontend (static/index.html — JS object)
```javascript
const state = {
    phase: 'input',
    product: '',
    originalProduct: '',
    clarificationQuestions: [],
    clarificationAnswers: [],
    clarifyRound: 0,
    maxAttempts: 3,
    preGraphTokens: [],
    finalState: null,
    attempts: [],
    eventSource: null,          // SSE connection reference
};
```

## SSE Streaming During Execution

### Backend: `GET /api/run` endpoint (server.py)
- Uses `sse-starlette` `EventSourceResponse` for Server-Sent Events.
- Runs `graph.stream()` in a thread via `loop.run_in_executor()`.
- Yields events for each graph step:

```
event: status  → Live activity feed update
event: tokens  → Token counter update
event: attempt → Attempt snapshot card
event: done    → Final result + all attempts
event: error   → Crash details with traceback
```

### Backend: `_get_activity_status(state)` (server.py)
- Builds a status payload from the current graph state.
- Determines the current step by checking which state fields are populated:

```
not query          → step: "actor_query", "🧠 Actor Query: Formulating search query..."
not results        → step: "search", "🔌 MCP Server: Calling search_prices..."
not best_price     → step: "actor_verdict", "💰 Actor Verdict: Extracting INR prices..."
not eval_result    → step: "evaluator", "⚖️ Evaluator Judge: Checking candidate..."
else               → step: "verdict", "🏁 Evaluator Verdict: PASS/FAIL — reason"
```

### Frontend: EventSource consumer (static/index.html)
```javascript
const eventSource = new EventSource(`/api/run?${params}`);

eventSource.addEventListener('status', (e) => { /* update activity bar */ });
eventSource.addEventListener('tokens', (e) => { /* update token counter */ });
eventSource.addEventListener('attempt', (e) => { /* append attempt card */ });
eventSource.addEventListener('done', (e) => { /* transition to done phase */ });
eventSource.addEventListener('error', (e) => { /* show error panel */ });
```

## Rendering Helpers (Frontend JS)

### `buildAttemptCardHtml(data, expanded)`
- Returns HTML string for one attempt card (collapsible accordion).
- Shows: search query, best price, summary, reasoning, sources, eval result.
- Uses `.badge-pass` / `.badge-fail` CSS classes for PASS/FAIL badges.

### `renderFinalResult(fs)`
- Renders the final result panel with gradient status banner, best price, source comparison grid.
- Includes collapsible reasoning and reflection trail sections.

### `renderTokenMetrics(fs)`
- Renders token usage summary cards + detailed per-node breakdown table.
- Uses collapsible accordion pattern.

### `renderAttemptHistory()`
- Renders all attempt cards in the done phase.

### `animateNumber(elementId, value)`
- Updates a number display with a CSS flash animation (`token-flash` class).

## API Endpoints Reference

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/api/config` | GET | — | `{max_attempts, has_api_key}` |
| `/api/config` | POST | `{max_attempts}` | `{max_attempts}` |
| `/api/clarify` | POST | `{product}` | `{questions, token_usage}` |
| `/api/merge` | POST | `{original, answers}` | `{merged, token_usage}` |
| `/api/run` | GET (SSE) | `?product=...&original_product=...&pre_graph_tokens=...` | SSE stream |
| `/api/reset` | POST | — | `{status: "ok"}` |
| `/api/history` | GET | — | `{recent_runs}` |
| `/api/session` | GET | — | debug state dict |

## Critical Rules

### Config Mutation via API
The sidebar slider sends `POST /api/config` which mutates `config.MAX_ATTEMPTS` at runtime:
```javascript
fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_attempts: parseInt(val) }),
});
```
This is picked up by `graph.py` routing and `_get_activity_status()` dynamically.

### Event Loop Compatibility
FastAPI runs its own asyncio event loop. The graph's synchronous `graph.stream()` is run in a thread via `loop.run_in_executor()` to avoid blocking the event loop. MCP client calls still use `ThreadPoolExecutor` in `mcp_client.py` for the same reason.

### Smart Merge Token Tracking
The `_smart_merge_product()` function in `server.py` makes an LLM call outside the graph. Its tokens are returned to the frontend, accumulated in `state.preGraphTokens`, and sent as a query parameter to `GET /api/run` where they are injected into `initial_state["token_usage"]` before graph execution starts.

### HTML Escaping
All user-provided text must be escaped before inserting into HTML. Use the `escapeHtml()` utility function:
```javascript
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}
```

### Design System
- **Dark mode** with deep navy palette (surface-900: `#0b1120`)
- **Glassmorphism** via `.glass` class (`backdrop-filter: blur(16px)`, `rgba(20, 27, 45, 0.7)`)
- **Gradient accents**: cyan `#06b6d4` → purple `#8b5cf6` → pink `#ec4899`
- **Tailwind CSS v3** via CDN (no build step)
- **Inter font** from Google Fonts
- All animations defined in Tailwind config `extend.animation` and `extend.keyframes`
