"""
Declarative Automation Executor

Executes automations defined as declarative JSON actions using a ToolRegistry.
"""

import asyncio
import inspect
import json
import logging
import re
import time
from typing import Dict, Any, List, Optional, Tuple

from .types import ExecutionStatus, ExecutionResult, ActionResult, USAGE_LIMIT_ERROR
from .templates import resolve_parameters
from .conditions import evaluate_condition
from .interfaces import ToolRegistry, NotificationHandler, UserInfo

logger = logging.getLogger(__name__)


# ============================================================================
# JSON Extraction from LLM Responses
# ============================================================================

def extract_json_from_string(text: str) -> Any:
    """
    Extract and parse JSON from a string that may contain embedded JSON.

    Handles common LLM response patterns:
    - "Here's the JSON: ```json {...} ```"
    - "The result is: {...}"
    - Pure JSON strings

    Args:
        text: String that may contain JSON

    Returns:
        Parsed JSON object/array if found, otherwise original string
    """
    if not isinstance(text, str):
        return text

    text = text.strip()

    # Try direct JSON parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract from markdown code blocks: ```json ... ``` or ``` ... ```
    code_block_pattern = r'```(?:json)?\s*([\s\S]*?)```'
    matches = re.findall(code_block_pattern, text)
    for match in matches:
        try:
            return json.loads(match.strip())
        except json.JSONDecodeError:
            continue

    # Try to find JSON object or array in the text
    # Look for {...} or [...]
    json_patterns = [
        r'(\{[\s\S]*\})',  # Object
        r'(\[[\s\S]*\])',  # Array
    ]

    for pattern in json_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue

    # No JSON found, return original string
    return text


# ============================================================================
# Output Normalization (matches polling-manager.ts normalizeForTriggerData)
# ============================================================================

def normalize_for_context(item: Any) -> Dict[str, Any]:
    """
    Normalize tool output for consistent template access.

    Matches the normalizeForTriggerData() function in polling-manager.ts
    so all automation types use the same flattened paths.

    Rules:
    - Wrapper keys (data, summary, result, response, output): Contents moved to root
    - Flatten-and-keep keys (contributors, user, author, goals): Primitives copied to root, original kept
    - Only primitive values are flattened, not nested objects/arrays
    """
    if not item or not isinstance(item, dict):
        return {"value": item} if item is not None else {}

    normalized = {}

    # Wrapper keys that should be completely flattened
    wrapper_keys = ['data', 'summary', 'result', 'response', 'output']

    # Nested object keys that should be flattened BUT also kept as original
    flatten_and_keep_keys = ['contributors', 'user', 'author', 'goals']

    def flatten_nested_object(key: str, value: dict):
        """Flatten a nested object, keeping original and adding primitives to root"""
        normalized[key] = value
        for nested_key, nested_value in value.items():
            if nested_key not in normalized and not isinstance(nested_value, (dict, list)):
                normalized[nested_key] = nested_value
            # Special case: flatten user.profile fields
            if key == 'user' and nested_key == 'profile' and isinstance(nested_value, dict):
                for profile_key, profile_value in nested_value.items():
                    if profile_key not in normalized:
                        normalized[profile_key] = profile_value

    for key, value in item.items():
        if key in wrapper_keys and isinstance(value, dict) and value is not None:
            # Flatten wrapper objects - spread contents to root BUT ALSO keep original
            # This allows both {{var.summary.steps}} and {{var.steps}} to work
            normalized[key] = value  # Keep original for backwards compatibility
            for inner_key, inner_value in value.items():
                # Check if the inner key needs flatten-and-keep treatment
                if inner_key in flatten_and_keep_keys and isinstance(inner_value, dict):
                    flatten_nested_object(inner_key, inner_value)
                elif inner_key not in normalized:  # Don't overwrite the wrapper key itself
                    normalized[inner_key] = inner_value
        elif key in wrapper_keys and isinstance(value, list) and value:
            # Special case: wrapper contains array (e.g., data: [{score: 85}])
            # Flatten first item's fields to root for convenience
            normalized[key] = value  # Keep original array
            if isinstance(value[0], dict):
                for inner_key, inner_value in value[0].items():
                    if inner_key not in normalized and not isinstance(inner_value, (dict, list)):
                        normalized[inner_key] = inner_value
        elif key in flatten_and_keep_keys and isinstance(value, dict) and value is not None:
            flatten_nested_object(key, value)
        else:
            normalized[key] = value

    return normalized


# ============================================================================
# Tool Execution
# ============================================================================

async def execute_tool(
    tool_name: str,
    parameters: Dict[str, Any],
    user_id: str,
    tool_registry: ToolRegistry,
    request_id: Optional[str] = None,
    timeout: float = 30.0
) -> Tuple[bool, Any, Optional[str]]:
    """
    Execute a single tool from the ToolRegistry.

    Args:
        tool_name: Name of the tool to execute
        parameters: Parameters to pass to the tool
        user_id: User ID for authentication
        tool_registry: Tool registry instance
        request_id: Request ID for logging
        timeout: Execution timeout in seconds

    Returns:
        Tuple of (success, output, error_message)
    """
    # Look up tool
    tool = await tool_registry.get_tool_by_name(tool_name)
    if not tool:
        return False, None, f"Tool not found: {tool_name}"

    # Prepare tool input (tools expect JSON string)
    # Add user_id, request_id, and automation context to parameters
    parameters['user_id'] = user_id
    if request_id:
        parameters['request_id'] = request_id
    parameters['is_automation'] = True

    tool_input = json.dumps(parameters)

    try:
        # Execute with timeout
        if inspect.iscoroutinefunction(tool.handler):
            result = await asyncio.wait_for(
                tool.handler(tool_input),
                timeout=timeout
            )
        else:
            # Call the handler (may return a coroutine if wrapped async)
            result = tool.handler(tool_input)

            # If result is a coroutine (lambda wrapping async func), await it
            if inspect.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=timeout)
            elif callable(result):
                # Unlikely but handle callable results
                result = await asyncio.wait_for(
                    asyncio.to_thread(result),
                    timeout=timeout
                )

        # Check if result indicates an error
        if isinstance(result, str) and result.startswith('Error:'):
            return False, None, result

        # Try to parse result as JSON
        try:
            if isinstance(result, str):
                result = json.loads(result)
        except json.JSONDecodeError:
            pass  # Keep as string

        return True, result, None

    except asyncio.TimeoutError:
        return False, None, f"Tool execution timed out after {timeout}s"
    except Exception as e:
        logger.exception(f"Tool execution error: {tool_name}")
        return False, None, str(e)


# ============================================================================
# Usage Limit Handling
# ============================================================================

def is_usage_limit_error(output: Any) -> bool:
    """Check if tool output indicates a usage limit was exceeded."""
    if isinstance(output, dict):
        return output.get("error") == USAGE_LIMIT_ERROR
    return False


async def handle_usage_limit_exceeded(
    automation_id: str,
    user_id: str,
    automation_name: str,
    service: str,
    message: str,
    notification_handler: NotificationHandler
) -> None:
    """
    Handle usage limit exceeded during automation execution.

    Sends notification to user about the limit being reached.

    Args:
        automation_id: ID of the automation
        user_id: User ID
        automation_name: Human-readable automation name
        service: Service that hit the limit (e.g., "textbelt", "perplexity")
        message: Error message from the service
        notification_handler: Notification handler instance
    """
    try:
        await notification_handler.notify_usage_limit_exceeded(
            user_id=user_id,
            automation_id=automation_id,
            automation_name=automation_name
        )
        logger.info(f"Sent usage limit notification for automation {automation_id}")
    except Exception as e:
        logger.error(f"Failed to send usage limit notification: {e}")


# ============================================================================
# Main Executor
# ============================================================================

async def execute_automation(
    actions: List[Dict[str, Any]],
    variables: Dict[str, Any],
    trigger_data: Dict[str, Any],
    user_id: str,
    user_info: UserInfo,
    tool_registry: ToolRegistry,
    notification_handler: Optional[NotificationHandler] = None,
    automation_id: Optional[str] = None,
    automation_name: Optional[str] = None,
    request_id: Optional[str] = None,
    timeout_per_action: float = 30.0
) -> ExecutionResult:
    """
    Execute a declarative automation.

    Args:
        actions: List of action definitions
        variables: User-defined variables
        trigger_data: Data from the trigger event
        user_id: User ID
        user_info: User profile data (UserInfo instance)
        tool_registry: Tool registry for executing actions
        notification_handler: Optional notification handler for alerts
        automation_id: Automation ID (for usage limit notifications)
        automation_name: Automation name (for usage limit notifications)
        request_id: Request ID for logging
        timeout_per_action: Timeout per action in seconds

    Returns:
        ExecutionResult with details of execution
    """
    start_time = time.time()
    action_results: List[ActionResult] = []

    # Convert UserInfo to dict for context
    user_dict = {
        'id': user_info.id,
        'email': user_info.email,
        'timezone': user_info.timezone,
    }
    if user_info.phone:
        user_dict['phone'] = user_info.phone
    if user_info.name:
        user_dict['name'] = user_info.name

    # Build initial context
    # Spread trigger_data at root level for direct access (e.g., {{subject}} instead of {{trigger_data.subject}})
    # Order matters: spread trigger_data first, then set reserved keys to avoid conflicts
    # (e.g., Slack events have 'user' field which shouldn't override user_info)
    context: Dict[str, Any] = {
        **trigger_data,  # Spread first: enables {{field}} access
        'user': user_dict,  # Override any 'user' from trigger_data
        'trigger_data': trigger_data,  # Keep nested for backwards compatibility
        **variables  # User-defined variables can override anything
    }

    actions_executed = 0
    actions_failed = 0
    errors: List[str] = []

    for action in actions:
        action_id = action.get('action_id') or action.get('id', f"action_{len(action_results)}")
        tool_name = action.get('tool')
        condition = action.get('condition')
        parameters = action.get('params') or action.get('parameters', {})
        output_as = action.get('output_as')

        action_start = time.time()

        # Evaluate condition
        if condition:
            condition_result = evaluate_condition(condition, context)
            if not condition_result:
                # Skip action - condition not met
                action_results.append(ActionResult(
                    action_id=action_id,
                    tool=tool_name,
                    success=True,  # Not a failure, just skipped
                    duration_ms=int((time.time() - action_start) * 1000),
                    skipped=True,
                    condition_result=False
                ))
                logger.info(f"Action {action_id} skipped - condition not met")
                continue

        # Resolve parameters
        resolved_params = resolve_parameters(parameters, context)

        logger.info(f"Executing action {action_id}: {tool_name}")

        # Execute tool
        success, output, error = await execute_tool(
            tool_name=tool_name,
            parameters=resolved_params,
            user_id=user_id,
            tool_registry=tool_registry,
            request_id=request_id,
            timeout=timeout_per_action
        )

        duration_ms = int((time.time() - action_start) * 1000)
        actions_executed += 1

        # Check for usage limit error (returned as structured JSON even on "success")
        if success and is_usage_limit_error(output):
            service = output.get("service", "unknown")
            message = output.get("message", "Usage limit reached")

            logger.warning(f"Usage limit exceeded for {service} in action {action_id}")

            # Handle the limit: notify user
            if notification_handler and automation_id:
                await handle_usage_limit_exceeded(
                    automation_id=automation_id,
                    user_id=user_id,
                    automation_name=automation_name or "Your automation",
                    service=service,
                    message=message,
                    notification_handler=notification_handler
                )

            # Record this action as failed due to limit
            action_results.append(ActionResult(
                action_id=action_id,
                tool=tool_name,
                success=False,
                duration_ms=duration_ms,
                error=f"Usage limit exceeded: {message}",
                condition_result=True if condition else None
            ))

            # Return early with usage limit status
            total_duration = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                status=ExecutionStatus.USAGE_LIMIT_EXCEEDED,
                actions_executed=actions_executed,
                actions_failed=1,
                action_results=action_results,
                duration_ms=total_duration,
                error_summary=f"Usage limit exceeded for {service}"
            )

        if success:
            # Store output in context for subsequent actions (normalized for consistent paths)
            if output_as:
                # First, try to extract JSON from string responses (e.g., LLM responses with embedded JSON)
                processed_output = output
                if isinstance(output, str):
                    processed_output = extract_json_from_string(output)
                    if processed_output != output:
                        logger.info(f"Extracted JSON from string output for {action_id}")

                # Normalize output so templates can use flattened paths (e.g., readiness_data.score)
                # instead of nested paths (e.g., readiness_data.data.0.score)
                normalized_output = normalize_for_context(processed_output) if isinstance(processed_output, dict) else processed_output
                context[output_as] = normalized_output

            action_results.append(ActionResult(
                action_id=action_id,
                tool=tool_name,
                success=True,
                duration_ms=duration_ms,
                output=output,
                condition_result=True if condition else None
            ))
            logger.info(f"Action {action_id} completed successfully")
        else:
            actions_failed += 1
            errors.append(f"{action_id}: {error}")

            action_results.append(ActionResult(
                action_id=action_id,
                tool=tool_name,
                success=False,
                duration_ms=duration_ms,
                error=error,
                condition_result=True if condition else None
            ))
            logger.warning(f"Action {action_id} failed: {error}")
            # Continue to next action - don't halt automation

    # Determine overall status
    total_duration = int((time.time() - start_time) * 1000)

    if actions_failed == 0:
        status = ExecutionStatus.COMPLETED
        success = True
    elif actions_failed < actions_executed:
        status = ExecutionStatus.PARTIAL_FAILURE
        success = True  # Partial success
    else:
        status = ExecutionStatus.FAILED
        success = False

    return ExecutionResult(
        success=success,
        status=status,
        actions_executed=actions_executed,
        actions_failed=actions_failed,
        action_results=action_results,
        duration_ms=total_duration,
        error_summary='; '.join(errors) if errors else None
    )
