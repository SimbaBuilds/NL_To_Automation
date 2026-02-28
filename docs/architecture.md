# Architecture

This document explains the architecture of nl_to_automation and how it achieves deterministic automation execution without runtime LLM inference.

## Overview

nl_to_automation separates the **intelligence phase** (building the automation) from the **execution phase** (running the automation). This is fundamentally different from agent-in-the-loop systems that require LLM inference on every execution.

```
Traditional Agent-in-the-Loop:
User Request → LLM → Execute → LLM → Execute → ... (every time)
LLM tokens: Used on every execution

nl_to_automation:
User Request → LLM (once) → Declarative JSON → Execute deterministically (forever)
LLM tokens: Only used during automation creation
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Intelligence Phase                        │
│               (Happens once, during creation)                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  User: "Alert me if my sleep score is below 70"             │
│         ↓                                                    │
│  EDA Agent (LLM) analyzes:                                  │
│    - Available tools (via ToolRegistry)                     │
│    - User's services and capabilities                       │
│    - Natural language intent                                │
│         ↓                                                    │
│  Generates Declarative JSON:                                │
│  {                                                           │
│    "trigger_type": "polling",                               │
│    "actions": [{                                            │
│      "tool": "oura_get_sleep",                              │
│      "output_as": "sleep_data",                             │
│      "condition": {"path": "sleep_data.score", ...}         │
│    }]                                                        │
│  }                                                           │
│         ↓                                                    │
│  Validation & Pre-flight checks                             │
│         ↓                                                    │
│  Store in Database                                          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Execution Phase                           │
│          (Happens repeatedly, NO LLM required)               │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Trigger fires (webhook / poll / schedule)                  │
│         ↓                                                    │
│  Load automation JSON from database                         │
│         ↓                                                    │
│  For each action:                                           │
│    1. Evaluate condition (templates + operators)            │
│    2. Resolve template variables                            │
│    3. Execute tool via ToolRegistry                         │
│    4. Store output for next action                          │
│         ↓                                                    │
│  Return ExecutionResult                                     │
│         ↓                                                    │
│  Log to database                                            │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Types (`types.py`)

Defines the data structures for execution:

- **ExecutionStatus**: Enum for execution states (COMPLETED, FAILED, PARTIAL_FAILURE, etc.)
- **ActionResult**: Result of a single action with timing, output, and error info
- **ExecutionResult**: Complete automation run result

### 2. Templates (`templates.py`)

Handles variable resolution in parameters:

- **get_nested_value()**: Extract values from nested dicts/lists using dot notation
- **resolve_template()**: Replace `{{variable}}` placeholders with actual values
- **resolve_parameters()**: Recursively resolve templates in parameter dicts

**Built-in variables:**
- Date/time: `{{today}}`, `{{yesterday}}`, `{{now}}`, etc. (timezone-aware)
- User info: `{{user.email}}`, `{{user.timezone}}`, `{{user.name}}`
- Trigger data: `{{trigger_data.field}}` or directly `{{field}}`
- Action outputs: `{{action_output.field}}`

### 3. Conditions (`conditions.py`)

Deterministic condition evaluation:

- **compare_values()**: Compare two values using an operator
- **evaluate_clause()**: Evaluate a single condition clause
- **evaluate_condition()**: Evaluate multi-clause conditions with AND/OR logic

**Operators:**
- Numeric: `<`, `>`, `<=`, `>=`, `==`, `!=`
- String: `contains`, `not_contains`, `starts_with`, `ends_with`
- Existence: `exists`, `not_exists`

### 4. Executor (`executor.py`)

Main execution engine:

- **execute_automation()**: Main entry point for running automations
- **execute_tool()**: Execute a single tool with timeout and error handling
- **normalize_for_context()**: Flatten tool outputs for consistent template access
- **extract_json_from_string()**: Parse JSON from LLM responses

**Execution flow:**
1. Build context (user info + trigger data + variables)
2. For each action:
   - Evaluate condition → skip if false
   - Resolve parameters → replace {{variables}}
   - Execute tool → get output
   - Store output → available for next action
3. Return ExecutionResult with status and timing

### 5. Interfaces (`interfaces/`)

Abstract base classes for extension:

- **ToolRegistry**: Tool discovery and execution
- **AutomationDatabase**: Storage and logging
- **UserProvider**: User information retrieval
- **LLMProvider**: Optional LLM inference (for opt-in intelligence)
- **WebSearchProvider**: Optional web search
- **NotificationHandler**: Usage limits and alerts

## Execution Model

### Context Building

The executor builds a context dict that contains all available data:

```python
context = {
    # Trigger data spread at root for convenience
    'score': 85,
    'subject': 'Low Sleep Alert',

    # Reserved keys
    'user': {
        'id': 'user_123',
        'email': 'alice@example.com',
        'timezone': 'America/New_York',
        'name': 'Alice'
    },
    'trigger_data': {...},  # Full trigger data

    # Action outputs (added during execution)
    'sleep_data': {'score': 85, 'date': '2026-02-27'},
    'calculated_result': {...},

    # User variables
    'threshold': 70
}
```

### Template Resolution

Templates are resolved recursively before tool execution:

```python
# Before:
parameters = {
    'to': '{{user.email}}',
    'body': 'Your score on {{today}} is {{sleep_data.score}}'
}

# After resolution:
parameters = {
    'to': 'alice@example.com',
    'body': 'Your score on 2026-02-27 is 85'
}
```

### Condition Evaluation

Conditions are evaluated before action execution:

```python
condition = {
    'operator': 'AND',
    'clauses': [
        {'path': 'sleep_data.score', 'op': '<', 'value': 70},
        {'path': 'user.email', 'op': 'exists'}
    ]
}

# Evaluates to: sleep_data.score < 70 AND user.email exists
# If false → action is skipped (not an error)
```

### Action Chaining

Actions can reference outputs from previous actions:

```python
actions = [
    {
        'id': 'fetch_data',
        'tool': 'oura_get_sleep',
        'output_as': 'sleep_data'  # Stored in context
    },
    {
        'id': 'process',
        'tool': 'calculate_score',
        'parameters': {
            'value': '{{sleep_data.score}}'  # References previous output
        },
        'output_as': 'result'
    },
    {
        'id': 'notify',
        'tool': 'send_notification',
        'parameters': {
            'body': 'Result: {{result.final_score}}'  # Chain continues
        }
    }
]
```

## Output Normalization

Tool outputs are normalized to flatten nested structures for easier template access:

```python
# Raw tool output:
{
    "data": [
        {"score": 85, "date": "2026-02-27"}
    ]
}

# Normalized (via normalize_for_context):
{
    "data": [...],        # Original preserved
    "score": 85,          # Flattened from data[0]
    "date": "2026-02-27"  # Flattened from data[0]
}

# Now templates can use:
# {{sleep_data.score}} instead of {{sleep_data.data.0.score}}
```

## Error Handling

### Tool Failures

If a tool fails, the executor:
1. Records the error in ActionResult
2. Continues to the next action (doesn't halt)
3. Returns PARTIAL_FAILURE or FAILED status

### Usage Limits

If a tool returns a usage limit error:
1. Executor calls NotificationHandler
2. Returns USAGE_LIMIT_EXCEEDED status
3. Automation can be paused (via Database interface)

### Timeouts

Each action has a configurable timeout (default 30s):
- If exceeded, action fails with timeout error
- Execution continues to next action

## Extensibility

### Custom Tools

Implement ToolRegistry to add your own tools:

```python
class MyToolRegistry(ToolRegistry):
    async def get_tool_by_name(self, name: str) -> Optional[Tool]:
        if name == 'my_custom_tool':
            return Tool(
                name='my_custom_tool',
                description='Does something custom',
                parameters={...},
                returns='Result description',
                handler=my_tool_handler
            )
        return None
```

### Database Integration

Implement AutomationDatabase to use your database:

```python
class PostgresDatabase(AutomationDatabase):
    async def get_automation(self, automation_id: str, user_id: str):
        # Fetch from your Postgres instance
        pass

    async def log_execution(self, automation_id: str, user_id: str, log_entry: Dict):
        # Store execution logs
        pass
```

### Opt-in LLM Intelligence

For cases where runtime intelligence is needed, use the LLM tools (from llm_tools.py, if refactored):

```python
actions = [
    {
        'id': 'classify',
        'tool': 'llm_classify',  # Requires LLMProvider
        'parameters': {
            'text': '{{email.body}}',
            'question': 'Is this urgent?',
            'options': ['YES', 'NO']
        },
        'output_as': 'urgency'
    },
    {
        'id': 'route',
        'tool': 'send_to_slack',
        'condition': {
            'path': 'urgency.answer',
            'op': '==',
            'value': 'YES'
        },
        'parameters': {...}
    }
]
```

## Performance

### No LLM at Runtime

The key insight is that **conditions and templates can be evaluated deterministically**:

- Template resolution: Simple string replacement
- Condition evaluation: Numeric/string comparison
- Tool execution: Direct API calls

No need for LLM to "decide" what to do - the JSON already encodes the logic.

### Execution Speed

- Template resolution: ~1ms per template
- Condition evaluation: ~0.1ms per clause
- Total overhead: <10ms for typical automation
- Bottleneck is tool execution (API calls), not the executor

### LLM Token Usage

50 automations polling every 5 minutes:

| Approach | LLM Calls/Month |
|----------|----------------|
| nl_to_automation | 0 (deterministic execution) |
| Agent-in-the-loop | ~432,000 (LLM on every trigger) |

The key difference is when LLM tokens are used, not whether the software is free (both are).

## Production Deployment

### Recommended Architecture

```
┌──────────────┐
│   Clients    │ (Web app, mobile app)
└──────┬───────┘
       │
       ↓
┌──────────────────┐
│  Edge Functions  │ (Webhook handler, scheduler)
└──────┬───────────┘
       │
       ↓
┌──────────────────────────────┐
│  nl_to_automation executor   │
│  (FastAPI backend or Lambda) │
└──────┬───────────────────────┘
       │
       ↓
┌──────────────────┐
│    Database      │ (Postgres with automation_records,
└──────────────────┘  automation_events, execution_logs)
```

### Scaling Considerations

- **Horizontal scaling**: Executor is stateless, scale workers as needed
- **Queue-based execution**: Use task queue (Celery, RQ) for async execution
- **Rate limiting**: Implement per-user rate limits in ToolRegistry
- **Monitoring**: Log all executions to database for debugging

## Comparison with Agent-in-the-Loop Systems

| Feature | nl_to_automation | Agent-in-the-loop (e.g., OpenClaw) |
|---------|------------------|-----------------------------------|
| **Execution model** | Declarative JSON | LLM decides on each execution |
| **LLM at runtime** | Optional (opt-in) | Required (every trigger) |
| **Pre-flight validation** | Yes | No |
| **Determinism** | 100% repeatable | Varies by LLM response |
| **LLM token usage** | Minimal (creation only) | Grows with executions |
| **Debugging** | Inspect JSON | Re-run and check LLM output |

## Further Reading

- [Getting Started Guide](getting-started.md)
- [Declarative Schema Spec](../spec/declarative-schema.md)
- [Example Automations](../examples/README.md)
