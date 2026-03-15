# Declarative Automation Schema

You build automations as declarative JSON that references tools from a service tool registry.

## Automation Structure

```json
{
  "name": "Automation Name",
  "description": "What this automation does",
  "trigger_type": "schedule_recurring",
  "trigger_config": { ... },
  "actions": [
    {
      "id": "unique_action_id",
      "tool": "tool_name_from_registry",
      "parameters": {
        "param1": "value or {{template}}",
        "param2": "{{trigger_data.field}}"
      },
      "output_as": "variable_name",
      "condition": {
        "path": "previous_output.field",
        "op": "<",
        "value": 70
      }
    }
  ],
  "variables": {
    "threshold": 70
  }
}
```

## Trigger Types

| Type | Description | trigger_config |
|------|-------------|----------------|
| `schedule_recurring` | Runs on a repeating schedule | `{"interval": "daily", "time_of_day": "14:00"}` |
| `schedule_once` | Runs exactly once at a specific time | `{"interval": "once", "run_at": "2025-12-10T15:00:00Z"}` |
| `webhook` | Triggered by external webhook events | `{"service": "Gmail", "event_type": "message.created"}` |
| `polling` | Periodically polls a source tool for new data | See polling config below |
| `manual` | Triggered manually by user | `{}` |

### Schedule Config

**Intervals:** `5min`, `15min`, `30min`, `1hr`, `6hr`, `daily`, `weekly`

**time_of_day:** Use user's local time in HH:MM format (e.g., "09:00" for 9am). The backend converts to UTC automatically.

**run_at:** Use user's local time as ISO datetime (e.g., "2025-12-10T09:00:00"). The backend converts to UTC automatically.

### Polling Config

```json
{
  "service": "Oura",
  "source_tool": "oura_get_daily_sleep",
  "event_type": "sleep_data_updated",
  "tool_params": {
    "start_date": "{{yesterday}}",
    "end_date": "{{today}}"
  },
  "polling_interval_minutes": 60
}
```

The system calls `source_tool` on the specified interval, compares results to previous poll, and conditionally executes actions when new data is found.

### Webhook Config

```json
{
  "service": "Gmail",
  "event_type": "message.created",
  "filters": {
    "operator": "OR",
    "clauses": [
      {"path": "subject", "op": "contains", "value": "urgent", "case_insensitive": true},
      {"path": "from", "op": "contains", "value": "boss@company.com", "case_insensitive": true}
    ]
  }
}
```


## Template Variables

For conditional content, use separate actions with `condition` fields:

```json
// WRONG - Handlebars will NOT work:
{"body": "{{#if invoice.due_date}}Due: {{invoice.due_date}}{{/if}}"}

// CORRECT - Use separate conditional actions:
[
  {
    "id": "notify_with_due_date",
    "tool": "send_notification",
    "condition": {"path": "invoice.due_date", "op": "exists"},
    "parameters": {"body": "Due: {{invoice.due_date}}"}
  },
  {
    "id": "notify_without_due_date",
    "tool": "send_notification",
    "condition": {"path": "invoice.due_date", "op": "not_exists"},
    "parameters": {"body": "Invoice received"}
  }
]
```

### Built-in Variables

**Dates:**
- `{{today}}`, `{{yesterday}}`, `{{tomorrow}}`, `{{two_days_ago}}`
- `{{this_week_start}}`, `{{this_week_end}}`
- `{{now}}` (current ISO datetime UTC)

**Time offsets (for intraday health data):**
- `{{now_minus_1h}}`, `{{now_minus_6h}}`, `{{now_minus_12h}}`, `{{now_minus_24h}}`

**User:**
- `{{user.phone}}`, `{{user.email}}`, `{{user.timezone}}`, `{{user.id}}`

**Dynamic:**
- `{{trigger_data.field}}` - data from trigger
- `{{output_name.field}}` - output from previous action
- `{{output_name.0.field}}` - array access
- `{{output_name.data.-1.field}}` - negative indexing (latest item)

### Template Safety

When accessing array data, add an existence condition to prevent literal `{{...}}` in output if data is missing:

```json
{
  "id": "notify",
  "tool": "send_notification",
  "condition": {"path": "output_name.data.-1", "op": "exists"},
  "parameters": {
    "body": "Latest score: {{output_name.data.-1.score}}"
  }
}
```

## Condition Operators

### Comparison
`<`, `>`, `<=`, `>=`, `==`, `!=`

### String (always add `case_insensitive: true`)
- `contains` - string contains substring
- `contains_any` - string contains any of provided substrings (array value)
- `not_contains` - string does not contain substring
- `starts_with`, `ends_with`

### Existence
`exists`, `not_exists`

### Multi-clause Conditions

```json
{
  "operator": "AND",
  "clauses": [
    {"path": "sleep_data.data.0.score", "op": "<", "value": 70},
    {"path": "sleep_data.data.1.score", "op": "<", "value": 70}
  ]
}
```

## LLM Tools for Runtime Intelligence

Most automations execute deterministically without LLM calls. For cases requiring runtime intelligence, you can use LLM tools:

| Tool | Use Case | Cost |
|------|----------|------|
| `search_web` | Factual lookups, current info | Free (API only) |
| `llm_classify` | Yes/No decisions, categorization | 1 fast call |
| `llm_transform` | Text formatting, restructuring | 1 fast call |
| `llm_agent` | Complex reasoning, content generation | Full agent call |

### Filtering Strategy for Semantic Matching

For semantic filtering (e.g., "mentions client approval"):

1. **Pre-filter** with broad keyword conditions in trigger_config:
```json
{
  "path": "body",
  "op": "contains_any",
  "value": ["approved", "approval", "confirmed", "accepted"],
  "case_insensitive": true
}
```

2. **Then confirm intent** with `llm_classify` in actions:
```json
{
  "id": "confirm_approval",
  "tool": "llm_classify",
  "parameters": {
    "text": "{{body}}",
    "question": "Does this message indicate client approval?",
    "options": ["YES", "NO"]
  },
  "output_as": "is_approval"
}
```

This hybrid approach reduces LLM calls while maintaining accuracy.

