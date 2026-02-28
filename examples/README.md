# Examples

Example automations demonstrating the declarative JSON format.

## Automation Examples

### 1. polling_health_alert.json

**Use case:** Monitor Oura sleep score and send notification if it drops below 70

**Key features:**
- Polling trigger (checks every 60 minutes)
- Filter condition in trigger_config
- Template variables ({{yesterday}}, {{today}})
- Simple notification action

**LLM calls:** 0 (deterministic)

---

### 2. webhook_slack_notify.json

**Use case:** Forward urgent emails to Slack #alerts channel in real-time

**Key features:**
- Webhook trigger (real-time Gmail events)
- Multi-clause OR filter
- Case-insensitive string matching
- Structured Slack blocks

**LLM calls:** 0 (deterministic)

---

### 3. scheduled_daily_digest.json

**Use case:** Daily health summary at 9 AM using LLM transformation

**Key features:**
- Scheduled recurring trigger (daily at 9:00)
- Multiple tool executions
- LLM transformation (opt-in intelligence)
- Data aggregation from multiple sources

**LLM calls:** 1 per execution (uses llm_transform)

---

### 4. conditional_multi_action.json

**Use case:** Classify and route Slack messages based on urgency and topic

**Key features:**
- LLM classification (urgency + topic)
- Multi-level conditional routing
- AND/OR condition operators
- Multiple potential actions (only matching ones execute)

**LLM calls:** 2 per execution (uses llm_classify twice)

---

## Running Examples

These are complete automation definitions. To use them:

### Option 1: Validate

```python
from nl_to_automation import validate_automation
import json

with open('examples/automations/polling_health_alert.json') as f:
    automation = json.load(f)

errors, warnings = validate_automation(automation, tool_registry)
print(f"Errors: {errors}")
print(f"Warnings: {warnings}")
```

### Option 2: Execute

```python
from nl_to_automation import execute_automation
import json

with open('examples/automations/polling_health_alert.json') as f:
    automation = json.load(f)

result = await execute_automation(
    actions=automation["actions"],
    variables=automation.get("variables", {}),
    trigger_data=sample_trigger_data,
    tool_registry=my_registry,
    user_info=user
)
```

### Option 3: Deploy to Database

```python
import json
from your_database import insert_automation

with open('examples/automations/polling_health_alert.json') as f:
    automation = json.load(f)

automation_id = insert_automation(
    user_id="your-user-id",
    automation=automation
)
```

## Template Variables

All examples use template variables for dynamic data:

- `{{today}}` - Current date (YYYY-MM-DD)
- `{{yesterday}}` - Yesterday's date
- `{{trigger_data.field}}` - Data from trigger event
- `{{output_name.field}}` - Output from previous action
- `{{user.email}}` - User information

See [spec/declarative-schema.md](../spec/declarative-schema.md) for full template documentation.

## Condition Operators

Examples demonstrate various condition operators:

- `<`, `>`, `<=`, `>=` - Numeric comparison
- `==`, `!=` - Equality
- `contains`, `not_contains` - String matching
- `starts_with`, `ends_with` - String patterns
- `exists`, `not_exists` - Null checks

## Adding Your Own Examples

1. Create a new JSON file in `automations/`
2. Follow the schema in `../spec/declarative-schema.md`
3. Test with validation before execution
4. Document the use case and cost in this README
