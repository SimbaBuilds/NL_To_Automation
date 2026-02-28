# Declarative Automation Schema

This document defines the JSON schema for declarative automations. It is designed to be **injected into an LLM agent's system prompt** so the agent can correctly build automations from natural language.

## Usage: Injecting into Agent Prompt

This schema serves as instructions for an automation-building agent (e.g., an "EDA Agent"). Inject it into your agent's system prompt:

```python
from pathlib import Path

# Load schema as agent instructions
schema_content = Path("spec/declarative-schema.md").read_text()

agent_system_prompt = f"""You are an automation-building agent.
When the user describes an automation in natural language,
generate declarative JSON following this schema:

{schema_content}
"""
```

The schema includes:
- JSON structure for triggers and actions
- Template variable syntax and built-in variables
- Condition operators and multi-clause logic
- Common mistakes to avoid (e.g., Handlebars blocks don't work)
- Trigger data format differences (webhook vs polling)

---

## Overview

Automations are defined as JSON objects with the following top-level structure:

```json
{
  "name": "Human-readable automation name",
  "description": "Optional description",
  "trigger_type": "webhook | polling | schedule_once | schedule_recurring | manual",
  "trigger_config": {...},
  "actions": [...],
  "variables": {...}
}
```

## Trigger Types

### 1. Webhook Trigger

Executes when a webhook is received from an external service.

```json
{
  "trigger_type": "webhook",
  "trigger_config": {
    "service": "Gmail",
    "event_type": "message.created",
    "filters": {
      "operator": "OR",
      "clauses": [
        {"path": "subject", "op": "contains", "value": "urgent"},
        {"path": "from", "op": "contains", "value": "boss@company.com"}
      ]
    }
  }
}
```

### 2. Polling Trigger

Periodically executes a source tool and processes the results.

```json
{
  "trigger_type": "polling",
  "trigger_config": {
    "source_tool": "oura_get_daily_sleep",
    "polling_interval_minutes": 60,
    "tool_params": {
      "start_date": "{{yesterday}}",
      "end_date": "{{today}}"
    },
    "filter": {
      "path": "score",
      "op": "<",
      "value": 70
    },
    "aggregation_mode": "latest"
  }
}
```

**Aggregation modes:**
- `latest`: Process only the most recent item
- `all`: Process all items as an array
- `per_item`: Create separate automation executions for each item

### 3. Schedule Once

Executes at a specific date/time (one-time).

```json
{
  "trigger_type": "schedule_once",
  "trigger_config": {
    "scheduled_at": "2026-03-01T09:00:00Z"
  }
}
```

### 4. Schedule Recurring

Executes on a recurring schedule.

```json
{
  "trigger_type": "schedule_recurring",
  "trigger_config": {
    "schedule": "daily",
    "time": "09:00",
    "timezone": "America/New_York",
    "days_of_week": ["monday", "wednesday", "friday"]
  }
}
```

**Schedule intervals:** `5min`, `15min`, `30min`, `1hr`, `6hr`, `daily`, `weekly`

**Time handling:**
- `time_of_day`: Use user's local time in HH:MM format (e.g., "09:00" for 9am). The backend converts to UTC automatically.
- `run_at`: Use user's local time as ISO datetime (e.g., "2025-12-10T09:00:00"). The backend converts to UTC automatically.

### 5. Manual Trigger

Executes only when manually triggered (no automatic trigger).

```json
{
  "trigger_type": "manual"
}
```

## Trigger Data Format

**Critical: The trigger_data format differs between webhook and polling triggers.**

### Webhook Trigger Data

Webhooks provide trigger_data as a **flat object** with top-level fields.

```json
// In your action parameters, access fields directly:
{
  "body": "Email from {{from}}: {{subject}}"
}
```

Do NOT use array syntax like `{{trigger_data.0.field}}` for webhooks.

### Polling Trigger Data

For polling triggers, the format depends on `aggregation_mode`:

- **`latest`** (default for health services): Single item promoted to top level
  ```json
  "{{field}}"  // Direct access
  ```

- **`per_item`** (default for non-health services): Array of items
  ```json
  "{{trigger_data.0.field}}"  // Array index required
  ```

- **`all`**: Full array available
  ```json
  "{{trigger_data}}"  // Entire array
  ```

### Template Safety

When accessing array data, add an existence condition to prevent literal `{{...}}` appearing in output if the data is missing:

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

## Actions

Actions are the core of the automation - they define what happens when the trigger fires.

### Basic Action Structure

```json
{
  "id": "unique_action_id",
  "tool": "tool_name_from_registry",
  "parameters": {
    "param1": "value",
    "param2": "{{template_variable}}"
  },
  "output_as": "variable_name",
  "condition": {...}
}
```

### Action Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier for this action |
| `tool` | string | Yes | Tool name from ToolRegistry |
| `parameters` | object | No | Parameters to pass to the tool |
| `output_as` | string | No | Store output under this variable name |
| `condition` | object | No | Condition that must be true to execute |

### Example Actions

#### Simple Action

```json
{
  "id": "send_notification",
  "tool": "send_push_notification",
  "parameters": {
    "title": "Alert",
    "body": "Your score is {{score}}"
  }
}
```

#### Conditional Action

```json
{
  "id": "notify_if_low",
  "tool": "send_email",
  "condition": {
    "path": "score",
    "op": "<",
    "value": 70
  },
  "parameters": {
    "to": "{{user.email}}",
    "subject": "Low Score Alert",
    "body": "Your score ({{score}}) is below the threshold."
  }
}
```

#### Chained Actions

```json
[
  {
    "id": "fetch_data",
    "tool": "oura_get_sleep",
    "parameters": {
      "date": "{{yesterday}}"
    },
    "output_as": "sleep_data"
  },
  {
    "id": "calculate",
    "tool": "compute_average",
    "parameters": {
      "value": "{{sleep_data.score}}"
    },
    "output_as": "average"
  },
  {
    "id": "notify",
    "tool": "send_notification",
    "parameters": {
      "body": "Average: {{average.result}}"
    }
  }
]
```

## Conditions

Conditions determine whether an action should execute.

### Single Clause Condition

```json
{
  "path": "score",
  "op": "<",
  "value": 70
}
```

### Multi-Clause Condition (AND)

```json
{
  "operator": "AND",
  "clauses": [
    {"path": "score", "op": "<", "value": 70},
    {"path": "status", "op": "==", "value": "active"}
  ]
}
```

### Multi-Clause Condition (OR)

```json
{
  "operator": "OR",
  "clauses": [
    {"path": "priority", "op": "==", "value": "high"},
    {"path": "subject", "op": "contains", "value": "urgent"}
  ]
}
```

### Condition Operators

#### Numeric Comparison

- `<`: Less than
- `>`: Greater than
- `<=`: Less than or equal
- `>=`: Greater than or equal
- `==`: Equal to
- `!=`: Not equal to

#### String Operators

- `contains`: String contains substring (case-insensitive)
- `contains_any`: String contains any of the provided substrings (array value)
- `not_contains`: String does not contain substring
- `starts_with`: String starts with prefix (case-insensitive)
- `ends_with`: String ends with suffix (case-insensitive)

**Important:** Always add `case_insensitive: true` for string operators:

```json
{
  "path": "subject",
  "op": "contains",
  "value": "urgent",
  "case_insensitive": true
}
```

#### Existence Operators

- `exists`: Value is not null/undefined
- `not_exists`: Value is null/undefined

### Nested Paths

Use dot notation to access nested values:

```json
{
  "path": "user.profile.score",
  "op": ">",
  "value": 70
}
```

Array indexing:

```json
{
  "path": "data.0.score",
  "op": ">",
  "value": 70
}
```

**Negative indexing:** Use `-1` to access the last (most recent) item:

```json
{
  "path": "data.-1.score",
  "op": ">",
  "value": 70
}
```

This is useful when API responses return data sorted oldest-first.

## Template Variables

Template variables are replaced at execution time using `{{variable}}` syntax.

### Important: Only Simple Syntax Supported

**Only simple `{{variable}}` syntax is supported.** Do NOT use Handlebars block syntax like `{{#if}}`, `{{#each}}`, `{{#unless}}`, or `{{/if}}`. These will NOT be processed and will appear as raw text in your output.

For conditional content, use separate actions with different `condition` fields:

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

#### Date/Time (Timezone-Aware)

- `{{today}}`: Today's date in user timezone (YYYY-MM-DD)
- `{{yesterday}}`: Yesterday's date
- `{{tomorrow}}`: Tomorrow's date
- `{{two_days_ago}}`: Two days ago
- `{{this_week_start}}`: Monday of current week
- `{{this_week_end}}`: Sunday of current week
- `{{now}}`: Current ISO datetime (UTC)

#### UTC Variants

- `{{today_utc}}`: Today's date in UTC
- `{{yesterday_utc}}`: Yesterday in UTC
- `{{tomorrow_utc}}`: Tomorrow in UTC

#### Time Offsets

- `{{now_minus_1h}}`: 1 hour ago
- `{{now_minus_6h}}`: 6 hours ago
- `{{now_minus_12h}}`: 12 hours ago
- `{{now_minus_24h}}`: 24 hours ago

#### User Information

- `{{user.id}}`: User ID
- `{{user.email}}`: User email
- `{{user.name}}`: User name
- `{{user.phone}}`: User phone number
- `{{user.timezone}}`: User timezone

#### Trigger Data

Access trigger event data:

```json
"{{trigger_data.field}}"
```

Or use shorthand (field spread at root):

```json
"{{field}}"
```

#### Action Outputs

Reference outputs from previous actions:

```json
"{{action_output_name.field}}"
```

#### User Variables

Custom variables defined in the `variables` object:

```json
{
  "variables": {
    "threshold": 70,
    "recipient": "admin@example.com"
  },
  "actions": [{
    "parameters": {
      "value": "{{threshold}}",
      "to": "{{recipient}}"
    }
  }]
}
```

### Template Examples

```json
{
  "parameters": {
    "date_range": "{{yesterday}} to {{today}}",
    "message": "Hello {{user.name}}, your score is {{sleep_data.score}}",
    "threshold": "{{threshold}}",
    "complex_object": "{{entire_object}}"
  }
}
```

Complex objects are converted to JSON strings:

```
"{{data}}" → "{\"score\": 85, \"date\": \"2026-02-27\"}"
```

## Variables

User-defined variables that can be referenced in templates.

```json
{
  "variables": {
    "min_score": 70,
    "notification_title": "Health Alert",
    "retry_count": 3,
    "enabled": true
  }
}
```

Variables can be any JSON type (string, number, boolean, object, array).

## Complete Examples

### Example 1: Simple Health Alert

```json
{
  "name": "Low Sleep Score Alert",
  "description": "Notify me if my sleep score drops below 70",
  "trigger_type": "polling",
  "trigger_config": {
    "source_tool": "oura_get_daily_sleep",
    "polling_interval_minutes": 60,
    "tool_params": {
      "start_date": "{{yesterday}}",
      "end_date": "{{today}}"
    },
    "filter": {
      "path": "score",
      "op": "<",
      "value": 70
    }
  },
  "actions": [
    {
      "id": "send_notification",
      "tool": "send_push_notification",
      "parameters": {
        "title": "Low Sleep Score",
        "body": "Your sleep score was {{score}} on {{date}}"
      }
    }
  ],
  "variables": {
    "min_acceptable_score": 70
  }
}
```

### Example 2: Multi-Action with LLM

```json
{
  "name": "Urgent Email Router",
  "description": "Classify emails and route urgent ones to Slack",
  "trigger_type": "webhook",
  "trigger_config": {
    "service": "Gmail",
    "event_type": "message.created"
  },
  "actions": [
    {
      "id": "classify",
      "tool": "llm_classify",
      "parameters": {
        "text": "{{subject}}: {{body}}",
        "question": "Is this email urgent?",
        "options": ["YES", "NO"]
      },
      "output_as": "urgency"
    },
    {
      "id": "post_to_slack",
      "tool": "slack_post_message",
      "condition": {
        "path": "urgency.answer",
        "op": "==",
        "value": "YES"
      },
      "parameters": {
        "channel": "#urgent",
        "text": "Urgent email from {{from}}: {{subject}}"
      }
    }
  ]
}
```

### Example 3: Scheduled Daily Digest

```json
{
  "name": "Daily Health Summary",
  "description": "Send daily health summary at 9 AM",
  "trigger_type": "schedule_recurring",
  "trigger_config": {
    "schedule": "daily",
    "time": "09:00",
    "timezone": "America/New_York"
  },
  "actions": [
    {
      "id": "fetch_sleep",
      "tool": "oura_get_sleep",
      "parameters": {
        "date": "{{yesterday}}"
      },
      "output_as": "sleep"
    },
    {
      "id": "fetch_activity",
      "tool": "oura_get_activity",
      "parameters": {
        "date": "{{yesterday}}"
      },
      "output_as": "activity"
    },
    {
      "id": "transform",
      "tool": "llm_transform",
      "parameters": {
        "data": {
          "sleep": "{{sleep}}",
          "activity": "{{activity}}"
        },
        "instructions": "Create a brief, friendly summary of sleep and activity data"
      },
      "output_as": "summary"
    },
    {
      "id": "send_email",
      "tool": "send_email",
      "parameters": {
        "to": "{{user.email}}",
        "subject": "Daily Health Summary - {{today}}",
        "body": "{{summary.result}}"
      }
    }
  ]
}
```

## Optional LLM Tools (Opt-in Intelligence)

Most automations execute deterministically without any LLM calls. For cases requiring runtime intelligence, you can use LLM tools. These require implementing the `LLMProvider` interface.

### Available LLM Tools

| Tool | Use Case | LLM Calls |
|------|----------|-----------|
| `search_web` | Factual lookups, current info | 0 (API only) |
| `llm_classify` | Yes/No decisions, categorization | 1 (fast, cheap) |
| `llm_transform` | Text formatting, restructuring | 1 (fast, cheap) |
| `llm_agent` | Complex reasoning, content generation | 1+ (full agent) |

### Decision Tree

1. **Simple factual lookup or current info** → `search_web`
2. **YES/NO or category decision** → `llm_classify`
3. **Format/restructure/convert text** → `llm_transform`
4. **Generate new content, complex analysis** → `llm_agent`

### Filtering Strategy

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

This hybrid approach reduces LLM calls while maintaining accuracy. Simple `contains` operators miss synonyms and can't detect negation.

## Validation

Before execution, automations should be validated for:

1. **Schema compliance**: All required fields present
2. **Tool existence**: All referenced tools exist in ToolRegistry
3. **Parameter schemas**: Tool parameters match expected types
4. **Template syntax**: Templates are well-formed
5. **Condition validity**: Operators and paths are valid
6. **Circular references**: No action references itself

## Best Practices

1. **Use descriptive IDs**: Make action IDs clear (`fetch_sleep` not `action1`)
2. **Store intermediate outputs**: Use `output_as` for debugging
3. **Fail gracefully**: Actions continue even if one fails
4. **Test conditions**: Verify condition logic before deployment
5. **Use variables**: Don't hardcode values that might change
6. **Document automations**: Use clear names and descriptions

## Schema Extensions

The schema is extensible. Custom fields can be added at any level without breaking compatibility:

```json
{
  "name": "My Automation",
  "custom_field": "custom_value",
  "actions": [
    {
      "id": "action1",
      "tool": "my_tool",
      "metadata": {
        "created_by": "user123",
        "version": "1.0"
      }
    }
  ]
}
```
