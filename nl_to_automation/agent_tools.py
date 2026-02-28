"""
Agent Tools for Automation Building

These tools are used by an LLM agent (EDA Agent) to discover available tools
and build declarative automations. They implement a 3-step progressive disclosure
pattern for efficient context management.

Usage:
    1. initial_md_fetch - Get tool names/descriptions for a service
    2. fetch_tool_data - Get full schemas for specific tools
    3. deploy_automation - Validate and save the automation

All tools are designed to work with the ToolRegistry and AutomationDatabase interfaces.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from .interfaces import ToolRegistry, AutomationDatabase, Tool

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """
    Tracks state during automation building.

    The agent maintains this context to track which tools have been fetched,
    enabling validation that the agent fetched schemas before using tools.
    """
    fetched_tool_schemas: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def record_fetched_tool(self, tool_name: str, parameters: Dict, returns: Optional[str] = None):
        """Record that a tool's schema was fetched."""
        self.fetched_tool_schemas[tool_name] = {
            'parameters': parameters or {},
            'returns': returns
        }

    def has_fetched_tool(self, tool_name: str) -> bool:
        """Check if a tool's schema has been fetched."""
        return tool_name in self.fetched_tool_schemas


async def initial_md_fetch(
    service_name: str,
    tool_registry: ToolRegistry,
    automation_db: Optional[AutomationDatabase] = None,
    include_capabilities: bool = True
) -> str:
    """
    Step 1: Fetch tool names and descriptions for a service.

    This is the first step in tool discovery. Returns a lightweight list of
    available tools so the agent can decide which ones to fetch full schemas for.

    Args:
        service_name: Name of the service (e.g., "Oura", "Gmail", "Slack")
        tool_registry: Registry to fetch tools from
        automation_db: Optional database for fetching service capabilities
        include_capabilities: Whether to include webhook/polling capabilities

    Returns:
        Formatted string with tool names, descriptions, and optionally capabilities
    """
    if not service_name:
        return "Error: service_name is required"

    try:
        # Get tools for this service
        tools = await tool_registry.list_tools(service=service_name)

        if not tools:
            return f"No tools found for service '{service_name}'"

        # Format tool list
        response = f"Available {service_name} tools:\n"
        for tool in tools:
            category = getattr(tool, 'category', 'General') or 'General'
            response += f"- {tool.name}: {tool.description} (Category: {category})\n"

        # Append service capabilities if available
        if include_capabilities and automation_db:
            try:
                capabilities = await automation_db.get_service_capabilities(service_name)
                if capabilities:
                    response += "\n\nService Capabilities:"
                    response += f"\n- Supports Webhooks: {capabilities.get('supports_webhooks', False)}"
                    response += f"\n- Supports Polling: {capabilities.get('supports_polling', True)}"

                    if capabilities.get('notes'):
                        response += f"\n- Notes: {capabilities['notes']}"

                    # Include webhook event types
                    if capabilities.get('supports_webhooks') and capabilities.get('webhook_events'):
                        response += "\n\nWebhook Event Types:"
                        for event_type in capabilities['webhook_events']:
                            response += f"\n  - {event_type}"

                        # Include payload schemas for template variable reference
                        schemas = capabilities.get('webhook_payload_schemas', {})
                        if schemas:
                            response += "\n\nWebhook Payload Schemas (available trigger_data fields):"
                            for event_type, schema in schemas.items():
                                response += f"\n  {event_type}:"
                                if schema.get('description'):
                                    response += f"\n    Description: {schema['description']}"
                                fields = schema.get('trigger_data_fields', {})
                                if fields:
                                    response += "\n    Fields:"
                                    for field_name, desc in fields.items():
                                        response += f"\n      - {field_name}: {desc}"
            except Exception as e:
                logger.warning(f"Failed to fetch service capabilities for '{service_name}': {e}")

        return response

    except Exception as e:
        logger.exception(f"Error in initial_md_fetch for '{service_name}'")
        return f"Error fetching tools for '{service_name}': {str(e)}"


async def fetch_tool_data(
    tool_names: List[str],
    tool_registry: ToolRegistry,
    agent_context: Optional[AgentContext] = None,
    max_tools: int = 5
) -> str:
    """
    Step 2: Fetch full tool definitions including parameter schemas.

    Call this after initial_md_fetch to get complete schemas for tools
    you plan to use in the automation.

    Args:
        tool_names: List of tool names to fetch
        tool_registry: Registry to fetch tools from
        agent_context: Optional context to track fetched schemas for validation
        max_tools: Maximum number of tools to fetch (default 5)

    Returns:
        Formatted string with full tool definitions
    """
    if not tool_names:
        return "Error: tool_names is required"

    # Limit number of tools
    tool_names = tool_names[:max_tools]

    try:
        responses = []
        found_tools = []
        not_found = []

        for name in tool_names:
            tool = await tool_registry.get_tool_by_name(name)

            if not tool:
                not_found.append(name)
                continue

            found_tools.append(name)

            # Record in agent context for validation
            if agent_context:
                agent_context.record_fetched_tool(
                    name,
                    tool.parameters,
                    tool.returns
                )

            # Build tool definition response
            response = f"\n## {tool.name}\n"
            response += f"Description: {tool.description}\n"

            if tool.parameters:
                response += f"Parameters:\n```json\n{json.dumps(tool.parameters, indent=2)}\n```\n"

            if tool.returns:
                response += f"Returns: {tool.returns}\n"

            responses.append(response)

        # Build final response
        result = ""

        if not_found:
            result += f"Warning: Tools not found: {', '.join(not_found)}\n"

        if responses:
            result += "\n".join(responses)
        else:
            result = "Error: No tools found"

        return result

    except Exception as e:
        logger.exception(f"Error in fetch_tool_data")
        return f"Error fetching tool data: {str(e)}"


async def deploy_automation(
    automation: Dict[str, Any],
    user_id: str,
    automation_db: AutomationDatabase,
    tool_registry: ToolRegistry,
    agent_context: Optional[AgentContext] = None,
    run_preflight: bool = True
) -> Tuple[bool, str, Optional[str]]:
    """
    Step 3: Validate and deploy an automation to the database.

    Performs validation checks before saving:
    1. Schema validation (JSON structure)
    2. Tool validation (all tools exist)
    3. Agent schema verification (tools were fetched)
    4. Preflight validation for polling (optional)

    Args:
        automation: The automation definition dict
        user_id: User ID to deploy for
        automation_db: Database to save to
        tool_registry: Registry for tool validation
        agent_context: Context with fetched tool schemas for validation
        run_preflight: Whether to run preflight checks for polling

    Returns:
        Tuple of (success, message, automation_id)
    """
    from .validation import (
        validate_automation_actions,
        validate_agent_fetched_schemas,
        preflight_validate_polling_automation
    )

    errors = []

    # Extract automation components
    name = automation.get('name', 'Unnamed Automation')
    actions = automation.get('actions', [])
    trigger_type = automation.get('trigger_type')
    trigger_config = automation.get('trigger_config', {})
    variables = automation.get('variables', {})

    # 1. Validate automation structure
    is_valid, structure_errors = await validate_automation_actions(
        actions=actions,
        tool_registry=tool_registry,
        trigger_type=trigger_type,
        trigger_config=trigger_config
    )

    if not is_valid:
        errors.extend(structure_errors)

    # 2. Verify agent fetched tool schemas (if context provided)
    if agent_context:
        schema_valid, schema_errors = validate_agent_fetched_schemas(
            actions=actions,
            fetched_tool_schemas=agent_context.fetched_tool_schemas
        )
        if not schema_valid:
            errors.extend(schema_errors)

    # 3. Preflight validation for polling automations
    if run_preflight and trigger_type == 'polling':
        preflight_valid, preflight_errors, sample_output = await preflight_validate_polling_automation(
            trigger_config=trigger_config,
            actions=actions,
            tool_registry=tool_registry,
            user_id=user_id
        )
        if not preflight_valid:
            errors.extend(preflight_errors)

    # Return errors if any
    if errors:
        error_msg = "Validation failed:\n" + "\n".join(f"- {e}" for e in errors)
        return False, error_msg, None

    # Save to database
    try:
        # Add status for user review
        automation_to_save = {
            **automation,
            'status': 'pending_review'  # Requires user confirmation
        }

        automation_id = await automation_db.create_automation(
            user_id=user_id,
            automation=automation_to_save
        )

        return True, f"Automation '{name}' created successfully (pending review)", automation_id

    except Exception as e:
        logger.exception(f"Failed to save automation")
        return False, f"Failed to save automation: {str(e)}", None


def format_automation_summary(automation: Dict[str, Any]) -> str:
    """
    Format an automation summary for user confirmation.

    This should be shown to the user before activation.
    """
    name = automation.get('name', 'Unnamed')
    trigger_type = automation.get('trigger_type', 'unknown')
    actions = automation.get('actions', [])

    summary = f"**{name}**\n\n"

    # Trigger description
    if trigger_type == 'polling':
        config = automation.get('trigger_config', {})
        source = config.get('source_tool', 'unknown')
        interval = config.get('polling_interval_minutes', 60)
        summary += f"**Trigger:** Poll {source} every {interval} minutes\n"
    elif trigger_type == 'webhook':
        config = automation.get('trigger_config', {})
        service = config.get('service', 'unknown')
        event = config.get('event_type', 'any event')
        summary += f"**Trigger:** When {service} sends {event}\n"
    elif trigger_type == 'schedule_recurring':
        config = automation.get('trigger_config', {})
        interval = config.get('interval', 'daily')
        time = config.get('time_of_day', '')
        summary += f"**Trigger:** {interval}" + (f" at {time}" if time else "") + "\n"
    elif trigger_type == 'schedule_once':
        config = automation.get('trigger_config', {})
        run_at = config.get('run_at', 'unknown time')
        summary += f"**Trigger:** Once at {run_at}\n"
    else:
        summary += f"**Trigger:** {trigger_type}\n"

    # Actions summary
    summary += f"\n**Actions:** ({len(actions)} steps)\n"
    for i, action in enumerate(actions, 1):
        tool = action.get('tool', 'unknown')
        action_id = action.get('id', f'step_{i}')
        has_condition = 'condition' in action

        summary += f"  {i}. {tool}"
        if has_condition:
            summary += " (conditional)"
        summary += "\n"

    return summary


# Convenience function to create tools for an agent
def create_agent_tools(
    tool_registry: ToolRegistry,
    automation_db: AutomationDatabase,
    user_id: str,
    agent_context: Optional[AgentContext] = None
) -> Dict[str, Any]:
    """
    Create tool definitions for use with an LLM agent.

    Returns a dict of tool definitions that can be used with Claude/GPT function calling.

    Example:
        tools = create_agent_tools(registry, db, user_id)
        # Use tools['definitions'] with your LLM
        # Call tools['handlers'][tool_name](input) to execute
    """
    if agent_context is None:
        agent_context = AgentContext()

    async def handle_initial_md_fetch(input_str: str) -> str:
        params = json.loads(input_str) if input_str.startswith('{') else {'service_name': input_str}
        return await initial_md_fetch(
            service_name=params.get('service_name', input_str),
            tool_registry=tool_registry,
            automation_db=automation_db,
            include_capabilities=True
        )

    async def handle_fetch_tool_data(input_str: str) -> str:
        params = json.loads(input_str) if input_str.startswith('{') else {'tool_names': [input_str]}
        tool_names = params.get('tool_names', [params.get('tool_name')])
        return await fetch_tool_data(
            tool_names=tool_names,
            tool_registry=tool_registry,
            agent_context=agent_context
        )

    async def handle_deploy_automation(input_str: str) -> str:
        automation = json.loads(input_str)
        success, message, automation_id = await deploy_automation(
            automation=automation,
            user_id=user_id,
            automation_db=automation_db,
            tool_registry=tool_registry,
            agent_context=agent_context
        )
        if success:
            return f"{message}\nAutomation ID: {automation_id}"
        return message

    return {
        'context': agent_context,
        'handlers': {
            'initial_md_fetch': handle_initial_md_fetch,
            'fetch_tool_data': handle_fetch_tool_data,
            'deploy_automation': handle_deploy_automation,
        },
        'definitions': [
            {
                'name': 'initial_md_fetch',
                'description': 'Step 1: Fetch available tools for a service. Call this first to see what tools are available.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'service_name': {
                            'type': 'string',
                            'description': 'Service name (e.g., "Oura", "Gmail", "Slack")'
                        }
                    },
                    'required': ['service_name']
                }
            },
            {
                'name': 'fetch_tool_data',
                'description': 'Step 2: Fetch full tool definitions. Call this for tools you plan to use in the automation.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'tool_names': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'List of tool names to fetch (max 5)'
                        }
                    },
                    'required': ['tool_names']
                }
            },
            {
                'name': 'deploy_automation',
                'description': 'Step 3: Validate and deploy the automation. The automation will be created in pending_review status.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Automation name'},
                        'trigger_type': {
                            'type': 'string',
                            'enum': ['polling', 'webhook', 'schedule_once', 'schedule_recurring', 'manual']
                        },
                        'trigger_config': {'type': 'object'},
                        'actions': {'type': 'array'},
                        'variables': {'type': 'object'}
                    },
                    'required': ['name', 'trigger_type', 'actions']
                }
            }
        ]
    }
