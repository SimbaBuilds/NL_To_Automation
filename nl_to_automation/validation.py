"""
Automation Validation

Validates automation JSON before deployment:
1. Schema validation (JSON structure)
2. Tool validation (all tools exist in registry)
3. Condition validation (proper structure)
4. Preflight validation for polling (paths resolve against real data)
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple

from .interfaces import ToolRegistry
from .templates import get_nested_value

logger = logging.getLogger(__name__)


# ============================================================================
# Schema Validation Helpers
# ============================================================================

def _check_handlebars_syntax(value: Any, path: str = "") -> List[str]:
    """
    Recursively check for Handlebars block syntax in a value.

    Handlebars blocks like {{#if}}, {{#each}}, {{/if}} are NOT supported.
    Returns list of error messages for any such patterns found.
    """
    errors = []
    handlebars_pattern = re.compile(r'\{\{[#/][^}]+\}\}')

    if isinstance(value, str):
        matches = handlebars_pattern.findall(value)
        if matches:
            errors.append(
                f"Handlebars block syntax not supported at '{path}': {matches}. "
                f"Use action conditions for conditional logic."
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
    Check for {{event_data.}} usage which should be {{trigger_data.}}.

    This is a common mistake where the agent uses 'event_data' instead of 'trigger_data'.
    Returns list of error messages for any {{event_data.}} patterns found.
    """
    errors = []
    pattern = re.compile(r'\{\{event_data\.[^}]+\}\}')

    if isinstance(value, str):
        matches = pattern.findall(value)
        if matches:
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
    """
    errors = []

    # Only check webhook automations
    if trigger_type != 'webhook':
        return errors

    # Pattern to match array syntax: {{trigger_data.0.field}} or {{0.field}}
    array_pattern = re.compile(r'\{\{(?:trigger_data\.)?(\d+)\.[^}]+\}\}')

    if isinstance(value, str):
        matches = array_pattern.findall(value)
        if matches:
            errors.append(
                f"Webhook automation at '{path}' uses array syntax {{{{trigger_data.{matches[0]}.field}}}}. "
                f"Webhooks provide trigger_data as an OBJECT. Use {{{{field}}}} instead."
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
            field = match.strip().split('.')[0]
            if field != 'trigger_data':
                fields.add(field)
    elif isinstance(value, dict):
        for v in value.values():
            fields.update(_extract_template_fields(v))
    elif isinstance(value, list):
        for item in value:
            fields.update(_extract_template_fields(item))

    return fields


# ============================================================================
# Condition Validation
# ============================================================================

def validate_condition_structure(condition: Dict[str, Any], action_id: str) -> List[str]:
    """
    Validate condition structure.

    Single clause format:
        {"path": "score", "op": "<", "value": 70}

    Multi-clause format:
        {"operator": "AND", "clauses": [...]}
    """
    errors = []

    if not isinstance(condition, dict):
        errors.append(f"{action_id}: condition must be an object")
        return errors

    # Single clause format
    if 'path' in condition:
        if 'op' not in condition:
            errors.append(f"{action_id}: condition clause missing 'op'")
        # 'value' required except for exists/not_exists
        op = condition.get('op', '')
        if 'value' not in condition and op not in ('exists', 'not_exists'):
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
                # 'value' required except for exists/not_exists
                clause_op = clause.get('op', '')
                if 'value' not in clause and clause_op not in ('exists', 'not_exists'):
                    errors.append(f"{action_id}: clause {j} missing 'value'")

    return errors


# ============================================================================
# Main Validation Functions
# ============================================================================

async def validate_automation_actions(
    actions: List[Dict[str, Any]],
    tool_registry: ToolRegistry,
    trigger_type: str = None,
    trigger_config: Dict[str, Any] = None
) -> Tuple[bool, List[str]]:
    """
    Validate automation actions before deployment.

    Checks:
    1. Actions is a non-empty array
    2. No Handlebars block syntax
    3. No {{event_data.}} usage
    4. No array syntax in webhook automations
    5. All tools exist in registry
    6. Condition structure is valid

    Args:
        actions: List of action definitions
        tool_registry: Registry to validate tool existence
        trigger_type: Automation trigger type (for format validation)
        trigger_config: Trigger configuration (for format validation)

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    if not actions or not isinstance(actions, list):
        errors.append("actions must be a non-empty array")
        return False, errors

    # Check for Handlebars block syntax
    handlebars_errors = _check_handlebars_syntax(actions, "actions")
    errors.extend(handlebars_errors)

    # Check for {{event_data.}} usage
    event_data_errors = _check_event_data_template(actions, "actions")
    errors.extend(event_data_errors)

    # Check for webhook array syntax errors
    if trigger_type:
        webhook_errors = _check_webhook_array_syntax(actions, trigger_type, "actions")
        errors.extend(webhook_errors)

        if trigger_config and trigger_config.get('filters'):
            filter_errors = _check_webhook_array_syntax(
                trigger_config['filters'],
                trigger_type,
                "trigger_config.filters"
            )
            errors.extend(filter_errors)

    # Validate each action
    for i, action in enumerate(actions):
        action_id = action.get('id', f'action_{i}')

        # Check required fields
        if 'tool' not in action:
            errors.append(f"{action_id}: missing 'tool' field")
            continue

        tool_name = action['tool']

        # Check tool exists in registry
        tool = await tool_registry.get_tool_by_name(tool_name)
        if not tool:
            errors.append(f"{action_id}: unknown tool '{tool_name}'")
            continue

        # Validate condition structure if present
        if 'condition' in action:
            cond_errors = validate_condition_structure(action['condition'], action_id)
            errors.extend(cond_errors)

    return len(errors) == 0, errors


def validate_agent_fetched_schemas(
    actions: List[Dict[str, Any]],
    fetched_tool_schemas: Dict[str, Dict[str, Any]]
) -> Tuple[bool, List[str]]:
    """
    Validate that the agent fetched tool schemas before using them.

    This prevents the agent from guessing parameter names.

    Args:
        actions: List of action definitions
        fetched_tool_schemas: Dict of tool_name -> schema from agent's fetch_tool_data calls

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    unfetched_tools = []

    for action in actions:
        tool_name = action.get('tool')
        if not tool_name:
            continue

        # Check if agent fetched this tool's schema
        if tool_name not in fetched_tool_schemas:
            unfetched_tools.append(tool_name)
        else:
            # Validate parameter names match schema
            schema_params = fetched_tool_schemas[tool_name].get('parameters', {})
            action_params = action.get('parameters', {})
            unknown_params = set(action_params.keys()) - set(schema_params.keys())

            if unknown_params:
                errors.append(
                    f"Tool '{tool_name}' has unknown parameters: {list(unknown_params)}. "
                    f"Valid parameters: {list(schema_params.keys())}"
                )

    if unfetched_tools:
        errors.insert(0,
            f"Agent must call fetch_tool_data for these tools before using them: {unfetched_tools}"
        )

    return len(errors) == 0, errors


# ============================================================================
# Preflight Validation for Polling
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
    template_pattern = re.compile(r'\{\{(trigger_data\.[^}]+)\}\}')

    def extract_from_value(value: Any) -> None:
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
        if not condition:
            return

        if 'path' in condition:
            path = condition['path']
            if path.startswith('trigger_data.'):
                paths.add(path)

        if 'clauses' in condition:
            for clause in condition.get('clauses', []):
                path = clause.get('path', '')
                if path.startswith('trigger_data.'):
                    paths.add(path)

    for action in actions:
        if 'condition' in action:
            extract_from_condition(action['condition'])
        if 'parameters' in action:
            extract_from_value(action['parameters'])

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
        # Remove trigger_data. prefix for lookup
        lookup_path = path.replace('trigger_data.', '')
        value = get_nested_value(sample_output, lookup_path)

        if value is None:
            # Provide helpful context about the actual structure
            if isinstance(sample_output, dict):
                available_keys = list(sample_output.keys())[:5]
                hint = f"Available top-level keys: {available_keys}"
            elif isinstance(sample_output, list) and sample_output:
                if isinstance(sample_output[0], dict):
                    available_keys = list(sample_output[0].keys())[:5]
                    hint = f"Output is an array. First item keys: {available_keys}. Use '0.' prefix."
                else:
                    hint = f"Output is an array of {type(sample_output[0]).__name__}"
            else:
                hint = f"Output type: {type(sample_output).__name__}"

            errors.append(f"Path '{path}' not found in source tool output. {hint}")

    return errors


async def preflight_validate_polling_automation(
    trigger_config: Dict[str, Any],
    actions: List[Dict[str, Any]],
    tool_registry: ToolRegistry,
    user_id: str
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """
    Perform pre-flight validation for a polling automation.

    1. Validates source_tool exists
    2. Executes source_tool with resolved params to get sample data
    3. Validates all trigger_data.* paths resolve against sample data

    Args:
        trigger_config: The automation's trigger_config
        actions: The automation's actions
        tool_registry: Registry to execute the source tool
        user_id: User ID for tool execution

    Returns:
        Tuple of (is_valid, errors/warnings, sample_output)
    """
    errors = []

    source_tool = trigger_config.get('source_tool')
    if not source_tool:
        errors.append("Polling automation missing 'source_tool' in trigger_config")
        return False, errors, None

    # Validate source_tool exists
    tool = await tool_registry.get_tool_by_name(source_tool)
    if not tool:
        errors.append(f"source_tool '{source_tool}' not found in registry")
        return False, errors, None

    # Extract all trigger_data paths from actions
    trigger_data_paths = extract_trigger_data_paths(actions, trigger_config)

    if not trigger_data_paths:
        # No trigger_data references - skip the API call
        logger.info(f"No trigger_data paths found - skipping pre-flight test for {source_tool}")
        return True, [], None

    logger.info(f"Pre-flight validation: testing {source_tool} with {len(trigger_data_paths)} trigger_data paths")

    # Execute source tool to get sample data
    tool_params = trigger_config.get('tool_params', {})
    resolved_params = resolve_tool_params(tool_params)

    try:
        sample_output = await tool_registry.execute_tool(
            source_tool,
            resolved_params,
            user_id
        )
    except Exception as e:
        # Tool execution failed - warn but allow creation
        logger.warning(f"Pre-flight test for {source_tool} failed: {e}")
        errors.append(
            f"Warning: Could not validate trigger_data paths - source_tool test failed: {e}. "
            f"Paths to validate: {list(trigger_data_paths)}"
        )
        return True, errors, None

    # Handle string responses
    if isinstance(sample_output, str):
        try:
            sample_output = json.loads(sample_output)
        except json.JSONDecodeError:
            logger.warning(f"Pre-flight test for {source_tool} returned string: {sample_output[:100]}")
            errors.append(
                f"Warning: source_tool returned a message instead of data: '{sample_output[:100]}'. "
                f"Cannot validate paths: {list(trigger_data_paths)}"
            )
            return True, errors, None

    # Validate paths against actual output
    path_errors = validate_paths_against_output(trigger_data_paths, sample_output)

    if path_errors:
        for err in path_errors:
            errors.append(f"Path validation error: {err}")
        return False, errors, sample_output

    logger.info(f"Pre-flight validation passed for {source_tool} - all {len(trigger_data_paths)} paths validated")
    return True, [], sample_output


# ============================================================================
# Utility Functions
# ============================================================================

def sanitize_action_strings(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sanitize action parameters to fix double-escaped characters.

    When the agent generates JSON with apostrophes (e.g., "You're"),
    they can get double-escaped during serialization.
    """
    if not actions:
        return actions

    actions_str = json.dumps(actions)
    actions_str = actions_str.replace("\\\\'", "'")
    actions_str = actions_str.replace('\\\\"', '"')
    actions_str = actions_str.replace("\\\\n", "\\n")

    return json.loads(actions_str)
