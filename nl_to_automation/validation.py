"""
Declarative Automation Tools for Automations Agent

Tools for creating declarative JSON-based automations that reference
service tools instead of generating Python scripts.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple

from supabase import Client as SupabaseClient

from app.agents.integrations.service_tools.registry import get_user_registry
from app.config import MAX_USER_AUTOMATIONS
from app.utils.logging.component_loggers import get_agent_logger, log_agent_event
from app.utils.timezone import convert_trigger_config_to_utc

logger = get_agent_logger("EDA Declarative", __name__)


def sanitize_action_strings(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sanitize action parameters to fix double-escaped characters.

    When the EDA agent generates JSON with apostrophes (e.g., "You're"),
    they can get double-escaped during serialization. This function
    cleans up common escape issues.

    Args:
        actions: List of action definitions

    Returns:
        Sanitized actions with clean strings
    """
    if not actions:
        return actions

    # Convert to string, fix escapes, convert back
    actions_str = json.dumps(actions)

    # Fix common double-escape patterns
    actions_str = actions_str.replace("\\\\'", "'")      # \\' -> '
    actions_str = actions_str.replace('\\\\"', '"')      # \\" -> "
    actions_str = actions_str.replace("\\\\n", "\\n")    # \\n -> \n (preserve newlines)

    return json.loads(actions_str)


def _check_handlebars_syntax(value: Any, path: str = "") -> List[str]:
    """
    Recursively check for Handlebars block syntax in a value.

    Returns list of error messages for any {{#...}} or {{/...}} patterns found.
    """
    errors = []
    handlebars_pattern = re.compile(r'\{\{[#/][^}]+\}\}')

    if isinstance(value, str):
        matches = handlebars_pattern.findall(value)
        if matches:
            errors.append(
                f"Handlebars block syntax not supported at '{path}': {matches}. "
                f"Use action conditions or juniper_call_agent for conditional content."
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            errors.extend(_check_handlebars_syntax(v, f"{path}.{k}" if path else k))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            errors.extend(_check_handlebars_syntax(item, f"{path}[{i}]"))

    return errors


def _check_event_data_template(value: Any, path: str = "") -> List[str]:
    """
    Check for {{event_data. usage which should be {{trigger_data. or just {{field}}.

    This is a common mistake where the agent uses 'event_data' instead of 'trigger_data'.
    The correct patterns are:
    - {{trigger_data.field}} for explicit access
    - {{field}} for webhook fields (promoted to top level)

    Returns list of error messages for any {{event_data. patterns found.
    """
    errors = []
    pattern = re.compile(r'\{\{event_data\.[^}]+\}\}')

    if isinstance(value, str):
        matches = pattern.findall(value)
        if matches:
            # Suggest the correct replacement
            suggestions = [m.replace('{{event_data.', '{{trigger_data.') for m in matches]
            errors.append(
                f"Invalid template at '{path}': Found '{{{{event_data.' which is not supported. "
                f"Use '{{{{trigger_data.' instead. "
                f"Found: {matches}. Suggested fix: {suggestions}"
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            errors.extend(_check_event_data_template(v, f"{path}.{k}" if path else k))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            errors.extend(_check_event_data_template(item, f"{path}[{i}]"))

    return errors


def _check_webhook_array_syntax(
    value: Any,
    trigger_type: str,
    path: str = ""
) -> List[str]:
    """
    Check for incorrect array syntax in webhook automations.

    Webhooks provide trigger_data as a flat object, not an array.
    Templates should use {{field}} not {{trigger_data.0.field}}.

    Args:
        value: Value to check (can be dict, list, str)
        trigger_type: Automation trigger type
        path: Current path for error reporting

    Returns:
        List of error messages
    """
    errors = []

    # Only check webhook automations
    if trigger_type != 'webhook':
        return errors

    # Pattern to match array syntax in templates: {{trigger_data.0.field}} or {{0.field}}
    array_pattern = re.compile(r'\{\{(?:trigger_data\.)?(\d+)\.[^}]+\}\}')

    if isinstance(value, str):
        matches = array_pattern.findall(value)
        if matches:
            errors.append(
                f"Webhook automation at '{path}' uses array syntax {{{{trigger_data.{matches[0]}.field}}}}. "
                f"Webhooks provide trigger_data as an OBJECT. Use {{{{field}}}} or {{{{message_id}}}} instead."
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            errors.extend(_check_webhook_array_syntax(v, trigger_type, f"{path}.{k}" if path else k))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            errors.extend(_check_webhook_array_syntax(item, trigger_type, f"{path}[{i}]"))

    return errors


def _extract_template_fields(value: Any) -> Set[str]:
    """
    Extract all field names from template variables in a value.

    Example: "{{subject}} from {{from}}" → {"subject", "from"}
    """
    fields = set()
    template_pattern = re.compile(r'\{\{([^}]+)\}\}')

    if isinstance(value, str):
        matches = template_pattern.findall(value)
        for match in matches:
            # Get the first part of dotted paths (e.g., "trigger_data.0.field" → "trigger_data")
            field = match.strip().split('.')[0]
            # Remove "trigger_data" prefix if present
            if field != 'trigger_data':
                fields.add(field)
    elif isinstance(value, dict):
        for v in value.values():
            fields.update(_extract_template_fields(v))
    elif isinstance(value, list):
        for item in value:
            fields.update(_extract_template_fields(item))

    return fields


def _extract_filter_fields(filters: Any) -> Set[str]:
    """
    Extract all field names referenced in filters.

    Example: {"path": "subject", "op": "contains", "value": "x"} → {"subject"}
    """
    fields = set()

    if not filters:
        return fields

    if isinstance(filters, dict):
        # Single clause with path
        if 'path' in filters:
            # Get the first part of dotted paths
            path = filters['path'].split('.')[0]
            fields.add(path)

        # Multi-clause format
        if 'clauses' in filters and isinstance(filters['clauses'], list):
            for clause in filters['clauses']:
                fields.update(_extract_filter_fields(clause))

    return fields


def _check_filter_triggered_metadata(
    actions: List[Dict[str, Any]],
    trigger_config: Dict[str, Any],
    service_name: str,
    supabase: SupabaseClient
) -> List[str]:
    """
    For filter_triggered services, check if action templates reference fields
    that aren't in trigger_config filters.

    This prevents the common mistake where agent puts condition in action
    instead of trigger_config for Gmail webhooks.
    """
    errors = []

    # Get service capabilities
    try:
        result = supabase.schema('automations').table('service_capabilities').select(
            'webhook_payload_schemas'
        ).eq('service_name', service_name).execute()

        if not result.data:
            return errors  # Can't validate without service capabilities

        schemas = result.data[0].get('webhook_payload_schemas', {})
        event_type = trigger_config.get('event_type')

        if not event_type or event_type not in schemas:
            return errors

        event_schema = schemas[event_type]
        strategy = event_schema.get('metadata_fetching_strategy')

        # Only check filter_triggered services
        if strategy != 'filter_triggered':
            return errors

        # Get conditionally available fields from schema
        conditional_fields = set()
        fields_info = event_schema.get('trigger_data_fields', {})

        for field_name, field_info in fields_info.items():
            if isinstance(field_info, dict):
                availability = field_info.get('availability', '')
            else:
                availability = field_info  # String format

            if 'conditional' in availability.lower():
                conditional_fields.add(field_name)

        # Check if service has grouped content fields (e.g., Gmail)
        # If filter references ANY field in the group, ALL fields become available
        content_fields_group = event_schema.get('metadata_fetching_explanation', {}).get('content_fields_group', [])

        # Extract fields used in action templates
        action_fields = _extract_template_fields(actions)

        # Extract fields referenced in filters
        filter_fields = _extract_filter_fields(trigger_config.get('filters', {}))

        # If service has content_fields_group and filter references ANY group member,
        # all group members become available
        if content_fields_group:
            group_fields_set = set(content_fields_group)
            if filter_fields & group_fields_set:
                # Filter references at least one group member, so all become available
                filter_fields.update(group_fields_set)

        # Find conditional fields used in actions but not in filters
        missing_fields = (action_fields & conditional_fields) - filter_fields

        if missing_fields:
            errors.append(
                f"{service_name} uses filter_triggered metadata strategy. "
                f"Actions reference {sorted(missing_fields)} but trigger_config filters don't. "
                f"These fields won't be available in trigger_data! "
                f"Add a filter in trigger_config that references these fields, "
                f"or use {{'path': '{list(missing_fields)[0]}', 'op': 'exists'}} to fetch metadata without filtering events."
            )

    except Exception as e:
        logger.warning(f"Could not validate filter_triggered metadata: {e}")

    return errors


def validate_automation_actions(
    actions: List[Dict[str, Any]],
    user_id: str,
    supabase: SupabaseClient,
    trigger_type: str = None,
    trigger_config: Dict[str, Any] = None
) -> tuple[bool, List[str]]:
    """
    Validate EDA agent output before saving.

    Args:
        actions: List of action definitions
        user_id: User ID for registry context
        supabase: Supabase client
        trigger_type: Automation trigger type (for format validation)
        trigger_config: Trigger configuration (for format validation)

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    if not actions or not isinstance(actions, list):
        errors.append("actions must be a non-empty array")
        return False, errors

    # Check for Handlebars block syntax in all actions
    handlebars_errors = _check_handlebars_syntax(actions, "actions")
    errors.extend(handlebars_errors)

    # Check for {{event_data. usage (should be {{trigger_data.)
    event_data_errors = _check_event_data_template(actions, "actions")
    errors.extend(event_data_errors)

    # Check for webhook array syntax errors
    if trigger_type:
        webhook_errors = _check_webhook_array_syntax(actions, trigger_type, "actions")
        errors.extend(webhook_errors)

        # Also check trigger_config filters for array syntax
        if trigger_config and trigger_config.get('filters'):
            filter_errors = _check_webhook_array_syntax(
                trigger_config['filters'],
                trigger_type,
                "trigger_config.filters"
            )
            errors.extend(filter_errors)

    # Check for filter_triggered metadata issues (e.g., Gmail without filters)
    if trigger_type == 'webhook' and trigger_config:
        service_name = trigger_config.get('service')
        if service_name:
            metadata_errors = _check_filter_triggered_metadata(
                actions,
                trigger_config,
                service_name,
                supabase
            )
            errors.extend(metadata_errors)

    # Get user-specific registry
    registry = get_user_registry(user_id, supabase)

    for i, action in enumerate(actions):
        action_id = action.get('id', f'action_{i}')

        # Check required fields
        if 'tool' not in action:
            errors.append(f"{action_id}: missing 'tool' field")
            continue

        tool_name = action['tool']

        # Check tool exists in registry
        tool = registry.get_tool_by_name(tool_name)
        if not tool:
            errors.append(f"{action_id}: unknown tool '{tool_name}'")
            continue

        # Validate parameters if tool has them defined
        if tool.parameters and 'parameters' in action:
            action_params = action['parameters']
            for param_name, param_def in tool.parameters.items():
                if param_def.get('required', False) and param_name not in action_params:
                    # Allow template variables for required params
                    pass  # Templates like {{trigger_data.x}} are resolved at runtime

        # Validate condition structure if present
        if 'condition' in action:
            cond_errors = validate_condition_structure(action['condition'], action_id)
            errors.extend(cond_errors)

    return len(errors) == 0, errors


def validate_condition_structure(condition: Dict[str, Any], action_id: str) -> List[str]:
    """Validate condition structure."""
    errors = []

    if not isinstance(condition, dict):
        errors.append(f"{action_id}: condition must be an object")
        return errors

    # Single clause format
    if 'path' in condition:
        if 'op' not in condition:
            errors.append(f"{action_id}: condition clause missing 'op'")
        if 'value' not in condition:
            errors.append(f"{action_id}: condition clause missing 'value'")
        return errors

    # Multi-clause format
    if 'clauses' in condition:
        if 'operator' not in condition:
            errors.append(f"{action_id}: multi-clause condition missing 'operator'")
        elif condition['operator'] not in ('AND', 'OR'):
            errors.append(f"{action_id}: condition operator must be 'AND' or 'OR'")

        clauses = condition.get('clauses', [])
        if not isinstance(clauses, list):
            errors.append(f"{action_id}: condition clauses must be an array")
        else:
            for j, clause in enumerate(clauses):
                if 'path' not in clause:
                    errors.append(f"{action_id}: clause {j} missing 'path'")
                if 'op' not in clause:
                    errors.append(f"{action_id}: clause {j} missing 'op'")
                if 'value' not in clause:
                    errors.append(f"{action_id}: clause {j} missing 'value'")

    return errors


# ============================================================================
# Pre-flight Validation for Polling Automations
# ============================================================================

def extract_trigger_data_paths(
    actions: List[Dict[str, Any]],
    trigger_config: Dict[str, Any]
) -> Set[str]:
    """
    Extract all paths that reference trigger_data from actions and trigger_config.

    Finds paths in:
    - Action conditions (path field)
    - Action parameters ({{trigger_data.x}} templates)
    - Trigger config filters

    Returns:
        Set of paths like {'trigger_data.score', 'trigger_data.day'}
    """
    paths = set()

    # Pattern to match {{trigger_data.something}}
    template_pattern = re.compile(r'\{\{(trigger_data\.[^}]+)\}\}')

    def extract_from_value(value: Any) -> None:
        """Recursively extract trigger_data paths from a value."""
        if isinstance(value, str):
            matches = template_pattern.findall(value)
            paths.update(matches)
        elif isinstance(value, dict):
            for v in value.values():
                extract_from_value(v)
        elif isinstance(value, list):
            for item in value:
                extract_from_value(item)

    def extract_from_condition(condition: Dict[str, Any]) -> None:
        """Extract paths from condition clauses."""
        if not condition:
            return

        # Single clause
        if 'path' in condition:
            path = condition['path']
            if path.startswith('trigger_data.'):
                paths.add(path)

        # Multi-clause
        if 'clauses' in condition:
            for clause in condition.get('clauses', []):
                path = clause.get('path', '')
                if path.startswith('trigger_data.'):
                    paths.add(path)

    # Extract from actions
    for action in actions:
        # From condition
        if 'condition' in action:
            extract_from_condition(action['condition'])

        # From parameters (templates)
        if 'parameters' in action:
            extract_from_value(action['parameters'])

    # Extract from trigger_config filter
    if 'filter' in trigger_config:
        extract_from_condition(trigger_config['filter'])
    if 'filters' in trigger_config:
        extract_from_condition(trigger_config['filters'])

    return paths


def resolve_template_dates(value: str) -> str:
    """Resolve date template variables like {{today}}, {{yesterday}}."""
    today = datetime.utcnow().date()

    replacements = {
        '{{today}}': today.isoformat(),
        '{{tomorrow}}': (today + timedelta(days=1)).isoformat(),
        '{{yesterday}}': (today - timedelta(days=1)).isoformat(),
        '{{two_days_ago}}': (today - timedelta(days=2)).isoformat(),
        '{{this_week_start}}': (today - timedelta(days=today.weekday())).isoformat(),
        '{{this_week_end}}': (today + timedelta(days=6 - today.weekday())).isoformat(),
    }

    result = value
    for template, replacement in replacements.items():
        result = result.replace(template, replacement)

    return result


def resolve_tool_params(tool_params: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve template variables in tool_params for pre-flight test."""
    resolved = {}
    for key, value in tool_params.items():
        if isinstance(value, str):
            resolved[key] = resolve_template_dates(value)
        else:
            resolved[key] = value
    return resolved


async def test_polling_source_tool(
    tool_name: str,
    tool_params: Dict[str, Any],
    user_id: str,
    supabase: SupabaseClient
) -> Dict[str, Any]:
    """
    Execute the source tool to get sample data for path validation.

    Args:
        tool_name: Name of the tool to test
        tool_params: Parameters for the tool (may contain templates)
        user_id: User ID
        supabase: Supabase client

    Returns:
        Dict with 'success', 'output', and optionally 'error'
    """
    import asyncio
    import inspect

    registry = get_user_registry(user_id, supabase)
    tool = registry.get_tool_by_name(tool_name)

    if not tool:
        return {'success': False, 'error': f"Tool '{tool_name}' not found"}

    # Resolve template variables in params
    resolved_params = resolve_tool_params(tool_params)
    resolved_params['user_id'] = user_id
    resolved_params['is_automation'] = True

    tool_input = json.dumps(resolved_params)

    try:
        if inspect.iscoroutinefunction(tool.handler):
            result = await asyncio.wait_for(tool.handler(tool_input), timeout=30.0)
        else:
            result = tool.handler(tool_input)
            if inspect.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=30.0)

        # Check for error response
        if isinstance(result, str) and result.startswith('Error:'):
            return {'success': False, 'error': result}

        # Parse JSON if string
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass

        return {'success': True, 'output': result}

    except asyncio.TimeoutError:
        return {'success': False, 'error': f"Tool '{tool_name}' timed out after 30s"}
    except Exception as e:
        return {'success': False, 'error': f"Tool execution failed: {str(e)}"}


def get_nested_value(data: Any, path: str) -> Tuple[bool, Any]:
    """
    Get a nested value from data using dot notation.
    Supports array indexing: 'data.0.score' or 'data[0].score'

    Returns:
        Tuple of (found: bool, value: Any)
    """
    if data is None:
        return False, None

    # Handle array notation like data[0].score -> data.0.score
    path = re.sub(r'\[(\d+)\]', r'.\1', path)

    # Remove trigger_data. prefix if present (we're already at that level)
    if path.startswith('trigger_data.'):
        path = path[len('trigger_data.'):]

    parts = path.split('.')
    current = data

    for part in parts:
        if current is None:
            return False, None

        # Try numeric index first (for lists)
        if part.isdigit():
            idx = int(part)
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return False, None
        # Then try dict key
        elif isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return False, None
        else:
            return False, None

    return True, current


def validate_paths_against_output(
    paths: Set[str],
    sample_output: Any
) -> List[str]:
    """
    Validate that all trigger_data paths exist in the sample output.

    Args:
        paths: Set of paths like {'trigger_data.score', 'trigger_data.day'}
        sample_output: Actual output from the source tool

    Returns:
        List of error messages for paths that don't resolve
    """
    errors = []

    for path in paths:
        found, _ = get_nested_value(sample_output, path)
        if not found:
            # Provide helpful context about the actual structure
            if isinstance(sample_output, dict):
                available_keys = list(sample_output.keys())[:5]
                hint = f"Available top-level keys: {available_keys}"
            elif isinstance(sample_output, list) and sample_output:
                if isinstance(sample_output[0], dict):
                    available_keys = list(sample_output[0].keys())[:5]
                    hint = f"Output is an array. First item keys: {available_keys}. Use '0.' prefix for array access."
                else:
                    hint = f"Output is an array of {type(sample_output[0]).__name__}"
            else:
                hint = f"Output type: {type(sample_output).__name__}"

            errors.append(f"Path '{path}' not found in source tool output. {hint}")

    return errors


async def preflight_validate_polling_automation(
    trigger_config: Dict[str, Any],
    actions: List[Dict[str, Any]],
    user_id: str,
    supabase: SupabaseClient
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """
    Perform pre-flight validation for a polling automation.

    1. Validates source_tool exists
    2. Executes source_tool with resolved params to get sample data
    3. Validates all trigger_data.* paths resolve against sample data

    Args:
        trigger_config: The automation's trigger_config
        actions: The automation's actions
        user_id: User ID
        supabase: Supabase client

    Returns:
        Tuple of (is_valid, errors, sample_output)
    """
    errors = []

    source_tool = trigger_config.get('source_tool')
    if not source_tool:
        errors.append("Polling automation missing 'source_tool' in trigger_config")
        return False, errors, None

    # Validate source_tool exists
    registry = get_user_registry(user_id, supabase)
    tool = registry.get_tool_by_name(source_tool)
    if not tool:
        errors.append(f"source_tool '{source_tool}' not found in registry")
        return False, errors, None

    # Extract all trigger_data paths from actions
    trigger_data_paths = extract_trigger_data_paths(actions, trigger_config)

    if not trigger_data_paths:
        # No trigger_data references - skip the expensive API call
        logger.info(f"No trigger_data paths found - skipping pre-flight API test for {source_tool}")
        return True, [], None

    logger.info(f"Pre-flight validation: testing {source_tool} with {len(trigger_data_paths)} trigger_data paths")

    # Execute source tool to get sample data
    tool_params = trigger_config.get('tool_params', {})
    test_result = await test_polling_source_tool(
        tool_name=source_tool,
        tool_params=tool_params,
        user_id=user_id,
        supabase=supabase
    )

    if not test_result['success']:
        # Tool execution failed - this is a warning, not a blocking error
        # The user might not have data yet, or there could be a transient issue
        logger.warning(f"Pre-flight test for {source_tool} failed: {test_result.get('error')}")
        errors.append(
            f"Warning: Could not validate trigger_data paths - source_tool test returned: {test_result.get('error')}. "
            f"Paths to validate: {list(trigger_data_paths)}"
        )
        # Return True to allow creation, but with warnings
        return True, errors, None

    sample_output = test_result['output']

    # Handle string responses (e.g., "No data found for date range")
    if isinstance(sample_output, str):
        logger.warning(f"Pre-flight test for {source_tool} returned string: {sample_output[:100]}")
        errors.append(
            f"Warning: source_tool returned a message instead of data: '{sample_output[:100]}'. "
            f"Cannot validate paths: {list(trigger_data_paths)}. "
            f"This may be because no data exists for the test date range."
        )
        return True, errors, None

    # Validate paths against actual output
    path_errors = validate_paths_against_output(trigger_data_paths, sample_output)

    if path_errors:
        # Path validation failed - this is a blocking error
        for err in path_errors:
            errors.append(f"Path validation error: {err}")
        return False, errors, sample_output

    logger.info(f"Pre-flight validation passed for {source_tool} - all {len(trigger_data_paths)} paths validated")
    return True, [], sample_output


async def populate_prod_db_declarative_async(
    input_str: str,
    user_id: str = None,
    supabase: SupabaseClient = None,
    request_id: str = None,
    user_timezone: str = None,
    agent_instance = None
) -> str:
    """
    Deploy declarative automation to production database (async version).

    Args:
        input_str: JSON with:
            - name: Automation name
            - description: Automation description
            - trigger_type: schedule, event, or manual
            - trigger_config: Trigger configuration (times in user's local timezone)
            - actions: List of action definitions (declarative JSON)
            - variables: Optional user-defined variables
            - active: Whether automation is active (default True)
        user_timezone: User's IANA timezone (e.g., "America/New_York") for UTC conversion
        agent_instance: Agent instance with fetched_tool_schemas for validation

    Returns:
        Deployment result with automation ID
    """
    if not supabase or not user_id:
        return "Error: Database connection and user_id required"

    try:
        params = json.loads(input_str)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON - {str(e)}"

    # Extract and validate fields
    name = params.get('name')
    description = params.get('description', '')
    trigger_type = params.get('trigger_type', 'manual')
    trigger_config = params.get('trigger_config', {})

    # Auto-correct common mistake: tool_parameters should be tool_params
    # The polling-manager edge function expects 'tool_params', not 'tool_parameters'
    if 'tool_parameters' in trigger_config and 'tool_params' not in trigger_config:
        trigger_config['tool_params'] = trigger_config.pop('tool_parameters')
        logger.warning("Auto-corrected 'tool_parameters' to 'tool_params' in trigger_config")

    actions = params.get('actions')
    variables = params.get('variables', {})
    active = params.get('active', True)

    # Convert times in trigger_config from user's local timezone to UTC
    if user_timezone and trigger_type in ('schedule_once', 'schedule_recurring'):
        trigger_config = convert_trigger_config_to_utc(trigger_config, user_timezone)

    if not name:
        return "Error: 'name' is required"

    if not actions:
        return "Error: 'actions' is required for declarative automations"

    # Validate that all tools have been fetched via fetch_tool_data
    if agent_instance and hasattr(agent_instance, 'fetched_tool_schemas'):
        unfetched_tools = []
        param_errors = []

        for action in actions:
            tool_name = action.get('tool')
            if not tool_name:
                continue

            # Check if tool schema was fetched
            if tool_name not in agent_instance.fetched_tool_schemas:
                unfetched_tools.append(tool_name)
            else:
                # Validate parameter names match schema
                schema_params = agent_instance.fetched_tool_schemas[tool_name].get('parameters', {})
                action_params = action.get('parameters', {})
                unknown_params = set(action_params.keys()) - set(schema_params.keys())
                if unknown_params:
                    param_errors.append(
                        f"Tool '{tool_name}' has unknown parameters: {list(unknown_params)}. "
                        f"Valid parameters are: {list(schema_params.keys())}"
                    )

        if unfetched_tools:
            return (
                f"Error: You must call fetch_tool_data for these tools before using them: {unfetched_tools}. "
                f"Use fetch_tool_data to get the correct parameter names."
            )

        if param_errors:
            return "Validation failed:\n" + "\n".join(f"- {e}" for e in param_errors)

    # Validate trigger type
    valid_trigger_types = ('webhook', 'polling', 'schedule_once', 'schedule_recurring', 'manual')
    if trigger_type not in valid_trigger_types:
        return f"Error: trigger_type must be one of: {', '.join(valid_trigger_types)}"

    # Validate actions (including webhook array syntax check)
    is_valid, validation_errors = validate_automation_actions(
        actions, user_id, supabase, trigger_type, trigger_config
    )

    if not is_valid:
        return f"Validation failed:\n" + "\n".join(f"- {e}" for e in validation_errors)

    # Pre-flight validation for polling automations
    preflight_warnings = []
    if trigger_type == 'polling':
        preflight_valid, preflight_errors, _ = await preflight_validate_polling_automation(
            trigger_config=trigger_config,
            actions=actions,
            user_id=user_id,
            supabase=supabase
        )

        if not preflight_valid:
            return f"Pre-flight validation failed:\n" + "\n".join(f"- {e}" for e in preflight_errors)

        # Collect warnings (non-blocking errors)
        preflight_warnings = [e for e in preflight_errors if e.startswith("Warning:")]

    # Check automation count limit
    try:
        count_result = supabase.schema("automations").table("automation_records") \
            .select("id", count="exact") \
            .eq("user_id", user_id) \
            .execute()

        current_count = count_result.count if count_result.count is not None else 0

        if current_count >= MAX_USER_AUTOMATIONS:
            return f"""Error: You have reached the maximum of {MAX_USER_AUTOMATIONS} automations.

Please remove unused automations before creating new ones. You can manage your automations in the automations page."""
    except Exception as e:
        logger.warning(f"Failed to check automation count for user {user_id}: {e}")
        # Continue with creation if count check fails (fail-open)

    # Generate automation ID
    automation_id = str(uuid.uuid4())

    # Determine status based on active flag
    # If active=False, set status to 'pending_review' (awaiting user confirmation)
    # If active=True, set status to 'active'
    status = 'active' if active else 'pending_review'
    confirmed_at = datetime.utcnow().isoformat() if active else None

    # Prepare record
    automation_data = {
        'id': automation_id,
        'user_id': user_id,
        'name': name,
        'description': description,
        'trigger_type': trigger_type,
        'trigger_config': trigger_config,
        'actions': sanitize_action_strings(actions),  # Declarative JSON (sanitized)
        'variables': variables,
        'active': active,
        'status': status,
        'confirmed_at': confirmed_at,
        'created_at': datetime.utcnow().isoformat(),
        'updated_at': datetime.utcnow().isoformat()
    }

    # For polling automations, set initial polling state
    if trigger_type == 'polling':
        # Set next_poll_at to now so it runs immediately on first poll cycle
        automation_data['next_poll_at'] = datetime.utcnow().isoformat()
        # Use interval from trigger_config or default
        automation_data['polling_interval_minutes'] = trigger_config.get('polling_interval_minutes', 60)
        # Initialize cursor to creation time - only fetch data AFTER automation was created
        # This prevents processing historical data on first poll
        automation_data['last_poll_cursor'] = datetime.utcnow().date().isoformat()

    try:
        # Insert into database
        result = supabase.schema("automations").table("automation_records").insert(automation_data).execute()

        if result.data:
            log_agent_event(
                logger,
                f"Deployed declarative automation: {name}",
                agent_name="Automations Agent",
                action="populate_prod_db_declarative",
                user_id=str(user_id),
                request_id=request_id,
                automation_id=automation_id,
                action_count=len(actions)
            )

            status_msg = "ACTIVE and ready to run" if active else "PENDING REVIEW - awaiting user confirmation"
            next_step = "The automation will execute according to its trigger configuration." if active else "Ask the user to confirm the automation to activate it."

            # Include pre-flight warnings if any
            warnings_section = ""
            if preflight_warnings:
                warnings_section = "\n\nPre-flight Validation Warnings:\n" + "\n".join(f"- {w}" for w in preflight_warnings)

            return f"""Automation deployed successfully!

Automation ID: {automation_id}
Name: {name}
Status: {status_msg}
Trigger: {trigger_type}
Actions: {len(actions)}

{next_step}{warnings_section}"""

        else:
            return "Error: Failed to insert automation record"

    except Exception as e:
        logger.error(f"Failed to deploy automation: {str(e)}")
        return f"Error deploying automation: {str(e)}"


def populate_prod_db_declarative(
    input_str: str,
    user_id: str = None,
    supabase: SupabaseClient = None,
    request_id: str = None,
    user_timezone: str = None,
    agent_instance = None
) -> str:
    """
    Deploy declarative automation to production database (sync wrapper).

    This is a sync wrapper around populate_prod_db_declarative_async for
    backward compatibility with existing code that calls this function synchronously.

    For polling automations, this performs pre-flight validation by:
    1. Executing the source_tool with resolved parameters
    2. Validating that all trigger_data.* paths in conditions/templates resolve
    3. Blocking creation if paths don't exist in actual tool output

    Args:
        input_str: JSON with automation definition
        user_id: User ID
        supabase: Supabase client
        request_id: Request ID for logging
        user_timezone: User's timezone for time conversion
        agent_instance: Agent instance with fetched_tool_schemas for validation

    Returns:
        Deployment result with automation ID
    """
    import asyncio

    try:
        # Check if we're already in an async context
        loop = asyncio.get_running_loop()
        # We're in an async context - create a task
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                populate_prod_db_declarative_async(input_str, user_id, supabase, request_id, user_timezone, agent_instance)
            )
            return future.result(timeout=60)
    except RuntimeError:
        # No running loop - we can use asyncio.run directly
        return asyncio.run(
            populate_prod_db_declarative_async(input_str, user_id, supabase, request_id, user_timezone, agent_instance)
        )


# Declarative actions JSON schema for system prompt
DECLARATIVE_ACTIONS_SCHEMA = """
## Declarative Actions Format

Create automations as declarative JSON actions that call tools that are mapped to this application's endpoints:

```json
{
  "name": "Automation Name",
  "description": "What this automation does",
  "trigger_type": "schedule_recurring",  // See trigger types below
  "trigger_config": { ... },  // See trigger config by type below
  "actions": [
    {
      "id": "unique_action_id",
      "tool": "tool_name_from_registry",
      "parameters": {
        "param1": "value or {{template}}",
        "param2": "{{trigger_data.field}}"
      },
      "output_as": "variable_name",  // Store output for later actions
      "condition": {  // Optional - skip action if condition is false
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

### Trigger Types

| Type | Description | trigger_config |
|------|-------------|----------------|
| `schedule_recurring` | Runs on a repeating schedule | `{"interval": "daily", "time_of_day": "14:00"}` |
| `schedule_once` | Runs exactly once at a specific time | `{"interval": "once", "run_at": "2025-12-10T15:00:00Z"}` |
| `webhook` | Triggered by external webhook events | `{"service": "gmail", "event_type": "message.created"}` |
| `polling` | Periodically polls a source tool for new data | See polling config below |
| `manual` | Triggered manually by user | `{}` |

### Polling trigger_config (IMPORTANT)
For polling automations, specify the EXACT tool to poll:
```json
{
  "service": "Oura",
  "source_tool": "oura_get_daily_sleep",  // REQUIRED: exact tool name from registry
  "event_type": "sleep_data_updated",      // Event type to emit when new data found
  "tool_params": {                         // Optional: parameters for the source tool
    "start_date": "{{yesterday}}",
    "end_date": "{{today}}"
  },
  "polling_interval_minutes": 60           // Optional: override default interval
}
```
The system will call `source_tool` on the specified interval, compare results to previous poll,
and create events + execute actions when new data is found.

**Schedule intervals:** `5min`, `15min`, `30min`, `1hr`, `6hr`, `daily`, `weekly`
**time_of_day:** Use user's local time in HH:MM format (e.g., "09:00" for 9am).
  The backend will automatically convert to UTC before storing.
**run_at:** Use user's local time as ISO datetime (e.g., "2025-12-10T09:00:00").
  The backend will automatically convert to UTC before storing.

### Template Variables
**Dates:** `{{today}}`, `{{yesterday}}`, `{{tomorrow}}`, `{{two_days_ago}}`, `{{now}}`, `{{this_week_start}}`, `{{this_week_end}}`
**Time offsets (for intraday health data):** `{{now_minus_1h}}`, `{{now_minus_6h}}`, `{{now_minus_12h}}`, `{{now_minus_24h}}`
  - Use for heart rate, HRV, stress queries. Avoid `{{now}}` alone as current moment often has no data due to sync delays.
**User:** `{{user.phone}}`, `{{user.email}}`, `{{user.timezone}}`
**Dynamic:** `{{trigger_data.field}}`, `{{output_name.field}}`, `{{output_name.0.field}}` (for arrays)
**Negative indexing:** `{{output_name.data.-1.field}}` gets the LATEST (most recent) item; `.0.` gets the oldest.
**Template safety:** When accessing array data, add an existence condition to prevent literal `{{...}}` in output:
  - `"condition": {"path": "output_name.data.-1", "op": "exists"}`

**⚠️ IMPORTANT: Only simple `{{variable}}` syntax is supported.**
Do NOT use Handlebars block syntax like `{{#if}}`, `{{#each}}`, `{{#unless}}`, or `{{/if}}`. These will NOT be processed and will appear as raw text.

For conditional content, use separate actions with different `condition` fields:

```json
// WRONG - Handlebars will NOT work:
{"body": "{{#if invoice.due_date}}Due: {{invoice.due_date}}{{/if}}"}

// CORRECT - Use separate conditional actions:
[
  {"tool": "push_notifications_send", "condition": {"path": "invoice.due_date", "op": "exists"}, "parameters": {"body": "Due: {{invoice.due_date}}"}},
  {"tool": "push_notifications_send", "condition": {"path": "invoice.due_date", "op": "not_exists"}, "parameters": {"body": "Invoice received"}}
]
```

### Condition Operators
- Comparison: `<`, `>`, `<=`, `>=`, `==`, `!=`
- String: `contains`, `contains_any`, `not_contains`, `starts_with`, `ends_with` (always add `case_insensitive: true`)
- Existence: `exists`, `not_exists`
- Logical: `AND`, `OR` (for combining clauses)

### Multi-clause conditions:
```json
{
  "operator": "AND",
  "clauses": [
    {"path": "sleep_data.data.0.score", "op": "<", "value": 70},
    {"path": "sleep_data.data.1.score", "op": "<", "value": 70}
  ]
}
```

### Example 1: Bad Sleep Alert (wrapped array - oura returns Object with data array)
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
      "tool": "textbelt_send_sms",
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

### Example 2: Search and Create (direct array - notion_search returns Array directly)
```json
{
  "name": "Create Page in Workspace",
  "trigger_type": "manual",
  "actions": [
    {
      "id": "find_parent",
      "tool": "notion_search",
      "parameters": {"query": "", "page_size": 1},
      "output_as": "search_results"
    },
    {
      "id": "create_page",
      "tool": "notion_create_page",
      "parameters": {
        "parent": {"page_id": "{{search_results.0.id}}"},
        "properties": {"title": [{"text": {"content": "New Page"}}]}
      }
    }
  ]
}
```

### Example 3: Daily Health Log to Notion (No Notification)
Use scheduled automations to passively log data without sending alerts:
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
This automation fetches and logs data daily without sending any notification.

### Example 4: Polling Automation (Low Heart Rate Alert)
Use `polling` to periodically check a data source and trigger actions when conditions are met:
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
      "tool": "push_notifications_send",
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
The polling system will:
1. Call `oura_get_heart_rate` every 60 minutes
2. Compare results to previous poll (using cursor)
3. For new data, create an event with `trigger_data` = the polled item
4. Execute actions if conditions match

### Example 5: Weekly Sleep Trend Tracker (Passive Data Sync)
Use scheduled automations to aggregate and store trend data without notifications:
```json
{
  "name": "Weekly Sleep Trend Tracker",
  "trigger_type": "schedule_recurring",
  "trigger_config": {"interval": "weekly", "day_of_week": "sunday", "time_of_day": "20:00"},
  "actions": [
    {
      "id": "get_week_sleep",
      "tool": "oura_get_daily_sleep",
      "parameters": {
        "start_date": "{{this_week_start}}",
        "end_date": "{{this_week_end}}"
      },
      "output_as": "week_data"
    },
    {
      "id": "analyze_trends",
      "tool": "juniper_call_agent",
      "parameters": {
        "message": "Calculate average sleep score and identify any patterns from this week's data: {{week_data}}. Return as JSON with fields: avg_score, best_day, worst_day, trend (improving/declining/stable)."
      },
      "output_as": "analysis"
    },
    {
      "id": "update_tracker",
      "tool": "notion_update_page",
      "parameters": {
        "page_id": "{{user.sleep_tracker_page_id}}",
        "properties": {
          "Week": {"date": {"start": "{{this_week_start}}"}},
          "Avg Score": {"number": "{{analysis.avg_score}}"},
          "Trend": {"select": {"name": "{{analysis.trend}}"}}
        }
      }
    }
  ]
}
```
This automation aggregates weekly data and updates a tracking database without sending any alerts.
"""
