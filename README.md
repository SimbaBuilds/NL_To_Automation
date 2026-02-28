# NL to Automation

An architecture for building AI agents that create event-driven automations from natural language.

**[Watch the demo](https://www.youtube.com/watch?v=tmmqHsehkQI)** | Powers [Juniper](https://juniper.app)

## The Problem

You want to let users say things like:

> "Alert me if my sleep score drops below 70 two nights in a row"

And have an AI agent build a working automation. But most approaches (like OpenClaw's "heartbeat") run an LLM on every trigger—checking that sleep score every hour burns tokens on simple integer comparisons.

## The Solution

**Build once with LLM, execute forever without.**

```
User: "Alert me when sleep score < 70"
              ↓
     Agent builds JSON (once)
              ↓
┌─────────────────────────────────────┐
│ {                                   │
│   "trigger_type": "polling",        │
│   "trigger_config": {               │
│     "source_tool": "oura_get_sleep",│
│     "interval": "1hr"               │
│   },                                │
│   "actions": [{                     │
│     "tool": "send_notification",    │
│     "condition": {                  │
│       "path": "score",              │
│       "op": "<",                    │
│       "value": 70                   │
│     }                               │
│   }]                                │
│ }                                   │
└─────────────────────────────────────┘
              ↓
     Executes deterministically
     (no LLM, every trigger)
```

The automation lives in a database. Webhooks and polling jobs populate an events table. Cron jobs check conditions deterministically. When you *do* need LLM intelligence at runtime (semantic classification, content generation), you opt in explicitly with `llm_classify` or `llm_transform` tools.

## What's Included

### Python Package (`nl_to_automation/`)

**Executor** - Runs declarative automations:
```python
from nl_to_automation import execute_automation

result = await execute_automation(
    actions=automation["actions"],
    trigger_data=event_data,
    tool_registry=my_registry,
    user_info=user
)
```

**Agent Tools** - For your automation-building agent:
```python
from nl_to_automation import create_agent_tools

tools = create_agent_tools(tool_registry, automation_db, user_id)
# tools['definitions'] → Use with Claude/GPT function calling
# tools['handlers'] → Execute the tools
```

The 3-step tool discovery flow:
1. `initial_md_fetch` - Get tool names/descriptions for a service
2. `fetch_tool_data` - Get full parameter schemas for tools you'll use
3. `deploy_automation` - Validate and save to database

**Validation** - Catch errors before deployment:
```python
from nl_to_automation import validate_automation_actions

is_valid, errors = await validate_automation_actions(
    actions, tool_registry, trigger_type
)
```

Checks include: JSON structure, all tools exist, no Handlebars blocks, preflight test for polling (does the source tool return data? do the paths resolve?).

**Schema Spec** - Inject into your agent's system prompt:
```python
schema = Path("spec/declarative-schema.md").read_text()
system_prompt = f"Build automations using this schema:\n{schema}"
```

### Database Schemas (`schemas/postgres/`)

SQL migrations for:
- `automation_records` - Stores automation JSON
- `automation_events` - Populated by webhooks/polling
- `automation_execution_logs` - Execution history
- `service_capabilities` - Webhook support, payload schemas

### Edge Functions (`edge_functions/`)

Supabase edge functions for:
- Webhook handling
- Scheduled job execution
- Polling management

## Installation

```bash
git clone https://github.com/SimbaBuilds/NL_To_Automation.git
cd NL_To_Automation
pip install -e .
```

## Quick Example

```python
from nl_to_automation import (
    resolve_template,
    evaluate_condition,
    execute_automation,
)

# Template resolution
context = {'user': {'name': 'Alice'}, 'score': 85}
msg = resolve_template('Hello {{user.name}}, your score is {{score}}', context)
# → "Hello Alice, your score is 85"

# Condition evaluation
condition = {'path': 'score', 'op': '<', 'value': 70}
should_alert = evaluate_condition(condition, context)
# → False (85 is not < 70)

# Full execution requires implementing ToolRegistry interface
# See docs/getting-started.md
```

## Documentation

- **[Getting Started](docs/getting-started.md)** - Installation and building your agent
- **[Architecture](docs/architecture.md)** - System design, agent workflow, database architecture
- **[Schema Spec](spec/declarative-schema.md)** - Full JSON format (inject into agent prompts)
- **[Validation](docs/validation.md)** - Pre-deployment checks
- **[Agent Tool Discovery](docs/agent-tool-discovery.md)** - How the agent finds tools

## Key Concepts

**Trigger Types**: `polling`, `webhook`, `schedule_recurring`, `schedule_once`, `manual`

**Template Variables**: `{{today}}`, `{{user.email}}`, `{{trigger_data.field}}`, `{{previous_action.output}}`

**Condition Operators**: `<`, `>`, `==`, `contains`, `starts_with`, `exists`, etc.

**Opt-in LLM Tools**: `llm_classify` (yes/no decisions), `llm_transform` (format/restructure), `call_agent` (full reasoning)

## License

MIT

---

Built by [Cameron Hightower](https://github.com/SimbaBuilds) for [Juniper](https://juniper.app).
