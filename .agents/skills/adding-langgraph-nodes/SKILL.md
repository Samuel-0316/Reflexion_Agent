---
name: adding-langgraph-nodes
description: |
  How to add a new LangGraph node to the reflexion loop.
  Covers the node function signature, state updates, token tracking,
  wiring in graph.py, and updating the Streamlit live feed.
  Use this skill when adding new capabilities to the agent pipeline.
---

# Adding a New LangGraph Node

## Step-by-Step Process

### 1. Define the State Fields (state.py)

If your node produces new output fields, add them to `AgentState`:

```python
class AgentState(TypedDict, total=False):
    # ... existing fields ...
    my_new_field: str  # Add your new field here
```

**Rules:**
- Use `total=False` so all fields are optional.
- If the field is append-only across attempts, use a reducer: `my_list: Annotated[list[str], operator.add]`

### 2. Write the Node Function (agent.py)

Every node follows this exact pattern:

```python
def my_new_node(state: dict) -> dict:
    """Docstring explaining what this node does."""
    llm = _get_llm()

    # Read from state
    product = state.get("product", "")

    # Build prompt
    prompt = f"You are a ... \n\nProduct: {product}\n\n..."

    # Call LLM with retry
    content, usage = _invoke_with_retry(llm, [HumanMessage(content=prompt)])
    parsed = _parse_json(content)

    logger.info(f"🆕 MyNode: {parsed.get('key', 'N/A')}")

    # Return PARTIAL state update (only fields this node owns)
    return {
        "my_new_field": parsed.get("key", ""),
        "token_usage": [{
            "node": "my_new_node",
            "attempt": state.get("attempt", 0),
            **usage,
        }],
    }
```

**Critical rules:**
- Always return `token_usage` as a single-element list `[{...}]` — the reducer appends it.
- Always use `_invoke_with_retry()` — never call `llm.invoke()` directly.
- Always use `_parse_json()` for JSON extraction from LLM output.
- Log with a distinctive emoji for easy identification in terminal output.

### 3. Wire the Node into the Graph (graph.py)

```python
from agent import my_new_node  # Add import

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Register the node
    graph.add_node("my_new_node", my_new_node)

    # Wire edges (example: insert between actor_verdict and evaluator)
    graph.add_edge("actor_verdict", "my_new_node")
    graph.add_edge("my_new_node", "evaluator")

    # Remove the old direct edge:
    # graph.add_edge("actor_verdict", "evaluator")  ← DELETE THIS

    return graph.compile()
```

### 4. Update the Live Feed (app.py)

Add a status line for your node in `_render_live_activity_feed()`:

```python
elif not state.get("my_new_field"):
    status_text = "🆕 **MyNode:** Processing..."
```

Insert it in the correct position in the if/elif chain to match the graph execution order.

### 5. Update the Token Metrics Label (app.py)

Add a display name in `_render_token_metrics()`:

```python
node_labels = {
    # ... existing labels ...
    "my_new_node": "🆕 My New Node",
}
```

## Common Patterns

### Non-LLM Node (Pure Logic)
If the node doesn't call an LLM (like `search_node`), skip the token tracking:

```python
def my_logic_node(state: dict) -> dict:
    result = some_computation(state["search_results"])
    return {"my_field": result}
```

### Node with MCP Tool Call
If the node calls an MCP tool:

```python
from mcp_client import web_search, verify_merchant

def my_mcp_node(state: dict) -> dict:
    result = verify_merchant(state["sources"][0]["url"])
    return {"verification_result": result}
```

### Conditional Routing After Your Node
If you need branching after your node:

```python
# In graph.py
def _route_after_my_node(state: dict) -> str:
    if state.get("my_new_field") == "SKIP":
        return "evaluator"  # Skip to evaluator
    return "next_node"

graph.add_conditional_edges("my_new_node", _route_after_my_node)
```
