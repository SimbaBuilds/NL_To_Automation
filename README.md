# Natural Langauge to Automation Architecture

**Natural language to deterministic automation. Execute workflows with or without LLM inference at runtime.**

Powers [Juniper](https://juniper.app) - an AI wellness companion on the App Store.

**See it in action:** [Watch demo video](https://www.youtube.com/watch?v=tmmqHsehkQI)

---

## Why nl_to_automation?

Most automation agents (like OpenClaw) use an **agent-in-the-loop** approach: an LLM decides what to do on every execution. This works but uses LLM tokens on every trigger.

nl_to_automation takes a different approach: use LLM **once** to build a declarative automation, then execute it **deterministically** with or without any LLM calls.

| Feature | nl_to_automation | Agent-in-the-loop |
|---------|------------------|-------------------|
| LLM calls per execution | 0 (unless explicit) | 1+ per trigger |
| Determinism | 100% repeatable | Varies by LLM response |
| Pre-deployment validation | Yes | No |
| LLM token usage at scale | Minimal | Grows with executions |

---

## How It Works

**Agent-in-the-loop approach:**
```
Trigger fires → LLM decides what to do → Execute → (repeat every time)
```

**nl_to_automation approach:**
```
User: "Alert me when sleep score < 70"
       ↓
LLM builds declarative JSON (once)
       ↓
{
  "trigger_type": "polling",
  "actions": [
    {"tool": "oura_get_sleep", "output_as": "sleep"},
    {
      "tool": "send_notification",
      "condition": {"path": "sleep.score", "op": "<", "value": 70},
      "parameters": {"body": "Sleep score: {{sleep.score}}"}
    }
  ]
}
       ↓
Execute deterministically (no LLM) - every trigger
```

---

## Key Features

### Declarative JSON Format
Define automations as structured JSON with:
- Trigger types: polling, webhooks, schedules, manual
- Tool-based actions with parameters
- Conditional execution
- Template variables (`{{today}}`, `{{user.email}}`, etc.)

### Pre-Deployment Validation
Catch errors before deployment:
- Verify tools exist
- Validate parameter schemas
- Check template syntax
- Pre-flight polling checks

### Opt-In LLM Intelligence
Use LLM only when needed via special tools:
- `llm_classify` - YES/NO or category decisions
- `llm_transform` - Format/restructure data
- `call_agent` - Full agent reasoning for complex tasks

Most automations need zero LLM at runtime. When you do need intelligence, you choose exactly where.

---

## Building an Automation Agent

The schema spec (`spec/declarative-schema.md`) is designed to be injected into an LLM agent's system prompt:

```python
from pathlib import Path

schema = Path("spec/declarative-schema.md").read_text()

system_prompt = f"""You are an automation agent. Build declarative JSON
automations from natural language using this schema:

{schema}
"""

# Use with Claude, GPT-4, etc.
```

See [Getting Started](docs/getting-started.md#building-an-automation-agent) for full agent implementation details.

---

## Quick Start

### Installation

```bash
# From source
git clone https://github.com/chightower/nl-to-automation.git
cd nl-to-automation
pip install -e .
```

### Basic Usage

```python
from nl_to_automation import (
    execute_automation,
    resolve_template,
    evaluate_condition,
    UserInfo,
)
from nl_to_automation.interfaces import ToolRegistry

# Template resolution works immediately
context = {'user': {'name': 'Alice'}, 'score': 85}
msg = resolve_template('Hello {{user.name}}, score: {{score}}', context)
# "Hello Alice, score: 85"

# Condition evaluation
condition = {'path': 'score', 'op': '<', 'value': 70}
passes = evaluate_condition(condition, context)  # False

# For full automation execution, implement ToolRegistry interface
# See docs/getting-started.md for details
```

---

## Project Structure

```
nl_to_automation/
├── nl_to_automation/          # Python package
│   ├── executor.py            # Core automation executor
│   ├── templates.py           # Template resolution
│   ├── conditions.py          # Condition evaluation
│   ├── types.py               # Data types
│   └── interfaces/            # Extension interfaces
├── edge_functions/            # Supabase edge functions
├── schemas/postgres/          # Database schemas
├── examples/                  # Example automations
├── tests/                     # Test suite (47 tests)
└── docs/                      # Documentation
```

---

## Documentation

- [Getting Started](docs/getting-started.md) - Installation, examples, and building an automation agent
- [Architecture](docs/architecture.md) - System design and execution model
- [Declarative Schema](spec/declarative-schema.md) - JSON format spec (designed to be injected into agent prompts)

---

## Examples

See `examples/automations/` for complete automation JSON files:
- `polling_health_alert.json` - Monitor health data with polling
- `webhook_slack_notify.json` - Forward events to Slack
- `scheduled_daily_digest.json` - Daily summary with LLM transform
- `conditional_multi_action.json` - Multi-step conditional workflow

---

## License

MIT License - see [LICENSE](LICENSE)

---

## Credits

Built by [Cameron Hightower](https://github.com/chightower) for [Juniper](https://juniper.app).
