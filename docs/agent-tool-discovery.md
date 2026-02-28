# Agent Tool Discovery

This document explains how an automation-building agent discovers available tools and service capabilities.

**Implementation:** See `nl_to_automation/agent_tools.py` for the actual tool implementations:
- `initial_md_fetch()` - Step 1: Fetch tool names/descriptions
- `fetch_tool_data()` - Step 2: Fetch full tool schemas
- `deploy_automation()` - Step 3: Validate and save
- `create_agent_tools()` - Helper to create tools for LLM function calling

## Overview

The agent needs to know:
1. **What tools exist** - Names, descriptions, parameters
2. **Service capabilities** - Does a service support webhooks? Polling? What events?
3. **Webhook payload schemas** - What fields are available in `trigger_data` for each event type?

## Database Tables

### service_tools (public schema)

Stores tool definitions that the agent can use in automations:

```sql
CREATE TABLE public.service_tools (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,        -- e.g., "gmail_get_messages"
  description TEXT,                  -- Human-readable description
  service_id UUID REFERENCES services(id),
  category TEXT,                     -- e.g., "read", "write", "search"
  parameters JSONB,                  -- Parameter schema
  returns TEXT,                      -- Return value description
  is_active BOOLEAN DEFAULT true
);
```

### service_capabilities (automations schema)

Stores per-service metadata for automation building:

```sql
CREATE TABLE automations.service_capabilities (
  service_name TEXT PRIMARY KEY,
  supports_webhooks BOOLEAN DEFAULT false,
  webhook_events TEXT[],                    -- e.g., ['message', 'reaction_added']
  webhook_payload_schemas JSONB,            -- Schema for each event type
  supports_polling BOOLEAN DEFAULT true,
  polling_endpoints JSONB,
  notes TEXT,                               -- Agent guidance (sync delays, quirks)
  auth_types TEXT[] DEFAULT ARRAY['oauth2']
);
```

## Tool Discovery Flow

### 1. initial_md_fetch

The agent's first step is to fetch available tools for a service:

```python
def initial_md_fetch(service_name: str, supabase) -> str:
    """
    Fetch tool names and descriptions for a service.
    Returns a formatted list for the agent's context.
    """
    # Get tools for this service
    tools = supabase.from_('service_tools').select(
        'name, description, category'
    ).eq('service_id', service_id).eq('is_active', True).execute()
    
    # Format for agent
    result = f"Available {service_name} tools:\n"
    for tool in tools.data:
        result += f"- {tool['name']}: {tool['description']}\n"
    
    return result
```

### 2. Append Service Capabilities

For automation building, wrap `initial_md_fetch` to include capabilities:

```python
def initial_md_fetch_with_capabilities(service_name: str, supabase) -> str:
    """
    Fetch tools AND service capabilities for automation building.
    """
    # Get base tool list
    result = initial_md_fetch(service_name, supabase)
    
    # Append capabilities
    caps = supabase.schema('automations').from_('service_capabilities').select(
        'supports_webhooks, supports_polling, webhook_events, webhook_payload_schemas, notes'
    ).eq('service_name', service_name).execute()
    
    if caps.data:
        cap = caps.data[0]
        result += "\n\nService Capabilities:\n"
        result += f"- Supports Webhooks: {cap['supports_webhooks']}\n"
        result += f"- Supports Polling: {cap['supports_polling']}\n"
        
        if cap.get('notes'):
            result += f"- Notes: {cap['notes']}\n"
        
        # Include webhook event schemas
        if cap['supports_webhooks'] and cap.get('webhook_payload_schemas'):
            result += "\nWebhook Payload Schemas:\n"
            for event_type, schema in cap['webhook_payload_schemas'].items():
                result += f"\n  {event_type}:\n"
                result += f"    Description: {schema.get('description')}\n"
                result += "    Available fields:\n"
                for field, desc in schema.get('trigger_data_fields', {}).items():
                    result += f"      - {field}: {desc}\n"
    
    return result
```

### 3. fetch_tool_data

Once the agent knows which tools it needs, fetch full parameter schemas:

```python
def fetch_tool_data(tool_names: list, supabase) -> str:
    """
    Fetch complete tool definitions including parameter schemas.
    """
    tools = supabase.from_('service_tools').select(
        'name, description, parameters, returns'
    ).in_('name', tool_names).execute()
    
    result = ""
    for tool in tools.data:
        result += f"\n## {tool['name']}\n"
        result += f"{tool['description']}\n"
        result += f"Parameters: {json.dumps(tool['parameters'], indent=2)}\n"
        result += f"Returns: {tool['returns']}\n"
    
    return result
```

## Webhook Payload Schemas

The `webhook_payload_schemas` column tells the agent exactly what fields are available in `trigger_data` for webhook automations:

```json
{
  "message": {
    "description": "A message was posted to a channel",
    "trigger_data_fields": {
      "event.type": "message",
      "event.user": "User ID who sent the message",
      "event.channel": "Channel ID",
      "event.text": "The message text content",
      "event.ts": "Message timestamp"
    },
    "example_condition": {
      "op": "contains",
      "path": "event.text",
      "value": "urgent",
      "case_insensitive": true
    }
  }
}
```

This allows the agent to correctly write:
```json
{
  "condition": {"path": "event.text", "op": "contains", "value": "urgent"},
  "parameters": {"message": "Alert: {{event.text}} from {{event.user}}"}
}
```

## Agent Workflow

1. User: "Alert me when someone posts 'urgent' in Slack"
2. Agent calls `initial_md_fetch_with_capabilities("Slack")`
3. Agent sees:
   - Slack supports webhooks
   - "message" event has `event.text`, `event.user`, etc.
4. Agent calls `fetch_tool_data(["slack_post_message", "push_notifications_send"])`
5. Agent builds automation JSON with correct field references

## Notes Field

The `notes` column provides agent guidance:

```sql
UPDATE automations.service_capabilities
SET notes = 'Oura data syncs to cloud 5-6 hours after device sync. 
For real-time alerts, polling may show stale data. 
Consider using schedule_recurring at end of day instead.'
WHERE service_name = 'oura';
```

This helps the agent make better recommendations when building automations.
