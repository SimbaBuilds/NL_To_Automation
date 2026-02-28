# Declarative Automation Schema

You build automations as declarative JSON that references tools from the registry.

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

The system calls `source_tool` on the specified interval, compares results to previous poll, and executes actions when new data is found.

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

## CRITICAL: Trigger Data Format

**The trigger_data format is DIFFERENT for webhooks vs polling.**

### Webhook Trigger Data

Webhooks provide trigger_data as a **FLAT OBJECT** with top-level fields.

```json
// CORRECT for webhooks:
{"body": "Email from {{from}}: {{subject}}"}

// WRONG for webhooks - DO NOT use array syntax:
{"body": "{{trigger_data.0.subject}}"}
```

Check `webhook_payload_schemas` via initial_md_fetch for available fields.

### Polling Trigger Data

The source tool's return fields become `trigger_data`. Format depends on aggregation_mode:

- **latest** (health services default): `{{field}}` - single item promoted to top level
- **per_item** (non-health default): `{{trigger_data.0.field}}` - array item, needs index

Check the tool's `returns` field via fetch_tool_data to determine structure.

## Template Variables

### IMPORTANT: Only Simple Syntax Supported

**Only `{{variable}}` syntax works.** Do NOT use Handlebars blocks like `{{#if}}`, `{{#each}}`, `{{#unless}}`, or `{{/if}}`. These will NOT be processed and appear as raw text.

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

## Examples

### Example 1: Bad Sleep Alert (Scheduled)

```json
{
  "name": "Bad Sleep Alert",
  "trigger_type": "schedule_recurring",
  "trigger_config": {"interval": "daily", "time_of_day": "08:00"},
  "actions": [
    {
      "id": "get_sleep",
      "tool": "oura_get_daily_sleep",
      "parameters": {
        "start_date": "{{two_days_ago}}",
        "end_date": "{{today}}"
      },
      "output_as": "sleep_data"
    },
    {
      "id": "send_alert",
      "tool": "send_sms",
      "condition": {
        "operator": "AND",
        "clauses": [
          {"path": "sleep_data.data.0.score", "op": "<", "value": 70},
          {"path": "sleep_data.data.1.score", "op": "<", "value": 70}
        ]
      },
      "parameters": {
        "phone": "{{user.phone}}",
        "message": "Two rough nights of sleep. Prioritize rest tonight!"
      }
    }
  ]
}
```

### Example 2: Polling Automation (Low Heart Rate Alert)

```json
{
  "name": "Low Heart Rate Alert",
  "trigger_type": "polling",
  "trigger_config": {
    "service": "Oura",
    "source_tool": "oura_get_heart_rate",
    "event_type": "heart_rate_updated",
    "tool_params": {
      "start_date": "{{yesterday}}",
      "end_date": "{{today}}"
    },
    "polling_interval_minutes": 60
  },
  "actions": [
    {
      "id": "send_alert",
      "tool": "send_notification",
      "condition": {
        "path": "trigger_data.data.0.bpm",
        "op": "<",
        "value": 50
      },
      "parameters": {
        "title": "Low Heart Rate",
        "body": "Alert: {{trigger_data.data.0.bpm}} BPM detected"
      }
    }
  ]
}
```

### Example 3: Webhook with Filtering

```json
{
  "name": "Urgent Email to Slack",
  "trigger_type": "webhook",
  "trigger_config": {
    "service": "Gmail",
    "event_type": "message.created",
    "filters": {
      "operator": "OR",
      "clauses": [
        {"path": "subject", "op": "contains", "value": "urgent", "case_insensitive": true},
        {"path": "subject", "op": "contains", "value": "ASAP", "case_insensitive": true}
      ]
    }
  },
  "actions": [
    {
      "id": "post_slack",
      "tool": "slack_post_message",
      "parameters": {
        "channel": "#alerts",
        "text": "Urgent email from {{from}}: {{subject}}"
      }
    }
  ]
}
```

### Example 4: Daily Health Log (No Notification)

```json
{
  "name": "Daily Health Log",
  "trigger_type": "schedule_recurring",
  "trigger_config": {"interval": "daily", "time_of_day": "21:00"},
  "actions": [
    {
      "id": "get_sleep",
      "tool": "oura_get_daily_sleep",
      "parameters": {"start_date": "{{today}}", "end_date": "{{today}}"},
      "output_as": "sleep"
    },
    {
      "id": "get_activity",
      "tool": "oura_get_daily_activity",
      "parameters": {"start_date": "{{today}}", "end_date": "{{today}}"},
      "output_as": "activity"
    },
    {
      "id": "log_to_notion",
      "tool": "notion_append_block",
      "parameters": {
        "block_id": "{{user.health_log_page_id}}",
        "children": [{"paragraph": {"text": "{{today}}: Sleep {{sleep.data.0.score}}, Activity {{activity.data.0.score}}"}}]
      }
    }
  ]
}
```

## Optional: LLM Tools for Runtime Intelligence

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

## Validation Requirements

Before deployment, automations are validated for:

1. **JSON structure** - All required fields present
2. **Tool existence** - All tools exist in registry
3. **Agent fetched schemas** - You must call fetch_tool_data for every tool before using it
4. **Preflight check** (polling) - Source tool is executed to verify trigger_data paths resolve correctly
