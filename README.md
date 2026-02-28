# NL to Automation

**[Watch the demo](https://www.youtube.com/watch?v=tmmqHsehkQI)** | Powers [Juniper](https://juniper.app)

## Overview

Useful for anyone building agents or assistants for non-technical users who want something like the "heartbeat" feature of OpenClaw.

Rather than using a markdown file that an agent checks on set time intervals, automation records live in a database. Polling and webhook jobs populate an events table. Cron jobs run against the events table and deterministically check conditions, and they run against the automation records table to trigger any scheduled jobs.

The agent can specify LLM tools if the task requires LLM intelligence at runtime, giving you the flexibility of OpenClaw's heartbeat feature but the determinism and speed of traditional automation flows. This flexibility ensures that LLM tokens are not spent for something like checking a conditional on an integer value every 5 minutes.

The architecture can support business contexts where users would want dozens of automations.

## How It Works

It allows an LLM agent to intelligently handle user requests like "Let me know when my HRV drops below 95". The agent:

0. Asks any clarifying questions if the request is ambiguous
1. Executes tool discovery flow:
   - Initial tool metadata fetch: tool names and descriptions for relevant services
   - Fetches full tool data for relevant tools, along with any tagged resources
   - Executes tools if actual runtime data is needed to build the automation
   - Writes the JSON declarative script
2. Validation checks are run:
   - JSON is executable
   - All actions specified are valid tools
   - Preflight check for polling automations (ensuring proper output parsing)
   - Full tool definitions for all actions were fetched by the agent
3. A concise description of the automation is presented to the user for confirmation and activation

The automation now lives as a record and is fully mutable by the agent, with a limited edit/disable UI for the human user.

## Architecture

```
┌─────────────────────┐
│ automation_records  │  ← Stores automation JSON, status, trigger config
└──────────┬──────────┘
           │
           │ Triggers populate
           ↓
┌─────────────────────┐
│  automation_events  │  ← Webhooks and polling create events here
└──────────┬──────────┘
           │
           │ Cron job (1 min) checks conditions
           ↓
┌─────────────────────┐
│ Declarative Executor│  ← Runs actions deterministically
└──────────┬──────────┘
           │
           │ Logs results
           ↓
┌─────────────────────────┐
│ automation_execution_logs│
└─────────────────────────┘
```

- **Webhook/Polling automations**: Populate the events table. A 1-minute cron job processes events and checks conditions before executing.
- **Scheduled automations**: Run directly against automation_records on a 5-minute cron job (less conditional checking needed).

## Tool Discovery (3-Step Progressive Disclosure)

The tool discovery flow uses progressive disclosure for context management and performance:

1. **Initial Metadata**: Fetch tool names and descriptions for a service (~100 tokens per service)
2. **Full Tool Data**: Fetch complete schemas only for tools the agent plans to use (~500 tokens per tool)
3. **Runtime Data** (optional): Execute tools to get real data if needed for building the automation

This approach was borrowed from another agent that has access to 200+ tools across 15+ services. The database-backed tool registry enables easy joining with a memories/resources table based on tags.

## What's Included

### Python Package (`nl_to_automation/`)

```python
# Executor - runs declarative automations
from nl_to_automation import execute_automation

result = await execute_automation(
    actions=automation["actions"],
    trigger_data=event_data,
    tool_registry=my_registry,
    user_info=user
)

# Agent tools - for your automation-building agent
from nl_to_automation import create_agent_tools

tools = create_agent_tools(tool_registry, automation_db, user_id)
# tools['definitions'] → Use with Claude/GPT function calling
# tools['handlers'] → Execute the tools

# Validation - catch errors before deployment
from nl_to_automation import validate_automation_actions

is_valid, errors = await validate_automation_actions(
    actions, tool_registry, trigger_type
)
```

### Schema Spec (`spec/declarative-schema.md`)

Designed to be injected into your agent's system prompt:

```python
schema = Path("spec/declarative-schema.md").read_text()
system_prompt = f"Build automations using this schema:\n{schema}"
```

### Database Schemas (`schemas/postgres/`)

SQL migrations for automation_records, automation_events, execution_logs, and service_capabilities.

### Edge Functions (`edge_functions/`)

Supabase edge functions for webhook handling, scheduled job execution, and polling management.

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

## License

MIT

---

Built by [Cameron Hightower](https://github.com/SimbaBuilds) for [Juniper](https://juniper.app).
