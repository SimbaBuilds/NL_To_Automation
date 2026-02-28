# Getting Started with nl_to_automation

This guide will help you get started with nl_to_automation, a declarative automation engine that executes workflows without LLM inference at runtime.

## Installation

```bash
pip install nl_to_automation
```

Or install from source:

```bash
git clone https://github.com/your-username/nl-to-automation.git
cd nl-to-automation
pip install -e .
```

## Quick Example

Here's a minimal example that shows how to use the core features:

### 1. Template Resolution

```python
from nl_to_automation import resolve_template, UserInfo

# Create a context with user info and data
context = {
    'user': {
        'name': 'Alice',
        'email': 'alice@example.com',
        'timezone': 'America/New_York'
    },
    'score': 85,
    'status': 'active'
}

# Resolve template variables
message = resolve_template(
    'Hello {{user.name}}, your score on {{today}} is {{score}}',
    context
)
print(message)
# Output: "Hello Alice, your score on 2026-02-27 is 85"
```

### 2. Condition Evaluation

```python
from nl_to_automation import evaluate_condition

# Define a condition
condition = {
    'operator': 'AND',
    'clauses': [
        {'path': 'score', 'op': '>', 'value': 70},
        {'path': 'status', 'op': '==', 'value': 'active'}
    ]
}

# Evaluate against context
result = evaluate_condition(condition, context)
print(result)  # True (score is 85 and status is 'active')
```

### 3. Execute an Automation

To execute full automations, you need to implement the `ToolRegistry` interface:

```python
import asyncio
from nl_to_automation import execute_automation, UserInfo
from nl_to_automation.interfaces import ToolRegistry, Tool
from typing import Dict, Any, List, Optional

class MyToolRegistry(ToolRegistry):
    """Simple tool registry example."""

    async def get_tool_by_name(self, name: str) -> Optional[Tool]:
        # Look up tool by name
        if name == 'send_email':
            return Tool(
                name='send_email',
                description='Send an email',
                parameters={'to': 'string', 'subject': 'string', 'body': 'string'},
                returns='Email sent confirmation',
                handler=self._send_email_handler
            )
        return None

    async def _send_email_handler(self, input_str: str):
        import json
        params = json.loads(input_str)
        # Your email sending logic here
        print(f"Sending email to {params['to']}: {params['subject']}")
        return json.dumps({'sent': True, 'message_id': '12345'})

    async def list_tools(self, service: Optional[str] = None) -> List[Tool]:
        # Return list of available tools
        return [await self.get_tool_by_name('send_email')]

    async def execute_tool(self, tool_name: str, parameters: Dict[str, Any], user_id: str, **kwargs) -> Any:
        tool = await self.get_tool_by_name(tool_name)
        if not tool:
            raise Exception(f"Tool not found: {tool_name}")
        return await tool.handler(json.dumps(parameters))


async def main():
    # Create tool registry
    registry = MyToolRegistry()

    # Define automation actions
    actions = [
        {
            'id': 'send_notification',
            'tool': 'send_email',
            'parameters': {
                'to': '{{user.email}}',
                'subject': 'Score Alert',
                'body': 'Your score on {{today}} is {{score}}'
            },
            'condition': {
                'path': 'score',
                'op': '<',
                'value': 70
            }
        }
    ]

    # Create user info
    user_info = UserInfo(
        id='user_123',
        email='alice@example.com',
        timezone='America/New_York',
        name='Alice'
    )

    # Execute automation
    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={'score': 50},  # Score below threshold
        user_id='user_123',
        user_info=user_info,
        tool_registry=registry
    )

    print(f"Success: {result.success}")
    print(f"Status: {result.status}")
    print(f"Actions executed: {result.actions_executed}")

# Run
asyncio.run(main())
```

## Core Concepts

### 1. Declarative Automation Format

Automations are defined as JSON with:
- **Trigger data**: The event that started the automation
- **Variables**: User-defined values
- **Actions**: List of tools to execute
- **Conditions**: Optional filters on when actions run

### 2. Template Variables

Use `{{variable}}` syntax to reference:
- **Built-in dates**: `{{today}}`, `{{yesterday}}`, `{{tomorrow}}`
- **User info**: `{{user.email}}`, `{{user.name}}`, `{{user.timezone}}`
- **Trigger data**: `{{trigger_data.field}}` or just `{{field}}`
- **Action outputs**: `{{previous_action_output.field}}`

### 3. Conditions

Actions can have conditions that determine if they execute:

```python
{
    'id': 'notify',
    'tool': 'send_notification',
    'condition': {
        'path': 'score',
        'op': '<',
        'value': 70
    },
    'parameters': {...}
}
```

Supported operators:
- Comparison: `<`, `>`, `<=`, `>=`, `==`, `!=`
- String: `contains`, `not_contains`, `starts_with`, `ends_with`
- Existence: `exists`, `not_exists`

Multi-clause conditions:

```python
{
    'operator': 'AND',  # or 'OR'
    'clauses': [
        {'path': 'score', 'op': '<', 'value': 70},
        {'path': 'user.active', 'op': '==', 'value': True}
    ]
}
```

### 4. Action Chaining

Actions can reference outputs from previous actions:

```python
actions = [
    {
        'id': 'get_data',
        'tool': 'fetch_score',
        'parameters': {},
        'output_as': 'score_data'  # Store output as 'score_data'
    },
    {
        'id': 'process',
        'tool': 'calculate',
        'parameters': {
            'value': '{{score_data.score}}'  # Reference previous output
        }
    }
]
```

## Implementing Interfaces

To use nl_to_automation in your application, implement these interfaces:

### ToolRegistry

Required for executing tools:

```python
from nl_to_automation.interfaces import ToolRegistry, Tool

class MyToolRegistry(ToolRegistry):
    async def get_tool_by_name(self, name: str) -> Optional[Tool]:
        # Return Tool instance or None
        pass

    async def list_tools(self, service: Optional[str] = None) -> List[Tool]:
        # Return list of available tools
        pass

    async def execute_tool(self, tool_name: str, parameters: Dict[str, Any], user_id: str, **kwargs) -> Any:
        # Execute and return result
        pass
```

### NotificationHandler (Optional)

For usage limit notifications:

```python
from nl_to_automation.interfaces import NotificationHandler

class MyNotificationHandler(NotificationHandler):
    async def notify_usage_limit_exceeded(self, user_id: str, automation_id: str, automation_name: str) -> None:
        # Send notification to user
        pass

    async def notify_automation_failed(self, user_id: str, automation_id: str, automation_name: str, error_summary: Optional[str] = None) -> None:
        # Notify about failure
        pass

    async def notify_custom(self, user_id: str, title: str, body: str, **kwargs) -> None:
        # Send custom notification
        pass
```

## Example Automations

See the `examples/automations/` directory for complete automation examples:

1. **polling_health_alert.json** - Monitor Oura sleep score with polling
2. **webhook_slack_notify.json** - Forward urgent emails to Slack
3. **scheduled_daily_digest.json** - Daily health summary with LLM transform
4. **conditional_multi_action.json** - Classify and route messages

## Building an Automation Agent

The core use case is having an LLM agent build automations from natural language. The `spec/declarative-schema.md` file is designed to be injected into your agent's system prompt.

### Basic Agent Setup

```python
from pathlib import Path
import anthropic  # or openai, etc.

# Load the schema as agent instructions
schema_content = Path("spec/declarative-schema.md").read_text()

# Create agent system prompt
system_prompt = f"""You are an automation-building agent.

When the user describes an automation in natural language, generate
declarative JSON that follows this schema:

{schema_content}

After generating the automation JSON, validate it and save to the database.
Always confirm the automation details with the user before activating.
"""

# Use with your LLM
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    system=system_prompt,
    messages=[{
        "role": "user",
        "content": "Alert me if my sleep score is below 70 two days in a row"
    }]
)
```

### Agent Tools

Your agent should have access to:

1. **Tool discovery** - List available tools from your ToolRegistry
2. **Tool details** - Get parameter schemas for specific tools
3. **Deploy automation** - Save the generated JSON to your database
4. **Search automations** - Find existing user automations
5. **Edit automation** - Modify existing automations

### Validation Before Deployment

Always validate agent-generated automations before saving:

```python
from nl_to_automation import validate_automation  # If implemented

errors, warnings = validate_automation(automation_json, tool_registry)
if errors:
    # Return errors to agent for correction
    pass
```

## Next Steps

- See [Declarative Schema Spec](../spec/declarative-schema.md) for agent prompt instructions
- Check out the example automations in `examples/automations/`

## Testing

Run tests to verify your installation:

```bash
pip install pytest pytest-asyncio
pytest tests/
```

## Troubleshooting

### Import errors

Make sure you're using Python 3.9+:

```bash
python --version  # Should be 3.9 or higher
```

### Timezone issues

Always specify a valid IANA timezone in UserInfo:

```python
user_info = UserInfo(
    id='user123',
    email='user@example.com',
    timezone='America/New_York'  # Use IANA timezone
)
```

### Tool execution timeouts

Adjust the timeout parameter:

```python
result = await execute_automation(
    ...,
    timeout_per_action=60.0  # 60 seconds instead of default 30
)
```
