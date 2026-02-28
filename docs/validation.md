# Automation Validation

Automations must be validated before deployment. This document describes the validation checks that should be performed.

## Validation Layers

### 1. Schema Validation (JSON is Executable)

Before saving, validate the automation JSON structure:

```python
def validate_automation_actions(actions: List[Dict], trigger_type: str = None) -> Tuple[bool, List[str]]:
    """
    Validate automation actions before deployment.
    
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Must be non-empty array
    if not actions or not isinstance(actions, list):
        errors.append("actions must be a non-empty array")
        return False, errors
    
    # Check for unsupported Handlebars syntax
    handlebars_errors = check_handlebars_syntax(actions)
    errors.extend(handlebars_errors)
    
    # Check for common mistakes
    event_data_errors = check_event_data_template(actions)  # {{event_data.x}} should be {{trigger_data.x}}
    errors.extend(event_data_errors)
    
    # Webhook-specific: no array syntax
    if trigger_type == 'webhook':
        array_errors = check_webhook_array_syntax(actions)
        errors.extend(array_errors)
    
    return len(errors) == 0, errors
```

#### Common Mistakes Caught

**Handlebars blocks not supported:**
```json
// INVALID - Handlebars blocks don't work
{"body": "{{#if score}}Score: {{score}}{{/if}}"}

// VALID - Use separate conditional actions
[
  {"condition": {"path": "score", "op": "exists"}, "parameters": {"body": "Score: {{score}}"}}
]
```

**Wrong template variable name:**
```json
// INVALID - event_data doesn't exist
{"body": "{{event_data.subject}}"}

// VALID - use trigger_data
{"body": "{{trigger_data.subject}}"}
// or just (for webhooks)
{"body": "{{subject}}"}
```

**Array syntax in webhook automations:**
```json
// INVALID - webhooks provide flat objects, not arrays
{"body": "{{trigger_data.0.subject}}"}

// VALID - direct access
{"body": "{{subject}}"}
```

### 2. Tool Validation (All Tools Exist)

Every tool referenced in actions must exist in the ToolRegistry:

```python
async def validate_tools_exist(actions: List[Dict], tool_registry: ToolRegistry) -> Tuple[bool, List[str]]:
    """Validate all referenced tools exist."""
    errors = []
    
    for action in actions:
        tool_name = action.get('tool')
        if not tool_name:
            errors.append(f"Action '{action.get('id')}': missing 'tool' field")
            continue
        
        tool = await tool_registry.get_tool_by_name(tool_name)
        if not tool:
            errors.append(f"Unknown tool: '{tool_name}'")
    
    return len(errors) == 0, errors
```

### 3. Condition Validation

Validate condition structure for each action:

```python
def validate_condition_structure(condition: Dict, action_id: str) -> List[str]:
    """Validate condition has required fields."""
    errors = []
    
    # Single clause format
    if 'path' in condition:
        if 'op' not in condition:
            errors.append(f"{action_id}: condition missing 'op'")
        # 'value' is required except for exists/not_exists
        if 'value' not in condition and condition.get('op') not in ('exists', 'not_exists'):
            errors.append(f"{action_id}: condition missing 'value'")
    
    # Multi-clause format
    elif 'clauses' in condition:
        if 'operator' not in condition:
            errors.append(f"{action_id}: multi-clause condition missing 'operator'")
        elif condition['operator'] not in ('AND', 'OR'):
            errors.append(f"{action_id}: operator must be 'AND' or 'OR'")
        
        for i, clause in enumerate(condition.get('clauses', [])):
            if 'path' not in clause:
                errors.append(f"{action_id}: clause {i} missing 'path'")
            if 'op' not in clause:
                errors.append(f"{action_id}: clause {i} missing 'op'")
    
    return errors
```

### 4. Agent Tool Schema Verification

**Critical:** Verify the agent fetched full tool definitions before building the automation.

This prevents the agent from guessing parameter names:

```python
def validate_agent_fetched_schemas(
    actions: List[Dict],
    fetched_tool_schemas: Dict[str, Dict]
) -> Tuple[bool, List[str]]:
    """
    Validate agent called fetch_tool_data for all tools used.
    
    Args:
        actions: Automation actions
        fetched_tool_schemas: Dict of tool_name -> schema from agent's fetch_tool_data calls
    
    Returns:
        Tuple of (is_valid, errors)
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
```

#### How It Works

The agent maintains a `fetched_tool_schemas` dict that gets populated when it calls `fetch_tool_data`:

```python
class EDAAgent:
    def __init__(self):
        self.fetched_tool_schemas = {}  # Populated by fetch_tool_data
    
    async def fetch_tool_data(self, tool_names: List[str]) -> str:
        """Fetch and cache tool schemas."""
        for name in tool_names:
            tool = await self.tool_registry.get_tool_by_name(name)
            if tool:
                self.fetched_tool_schemas[name] = {
                    'parameters': tool.parameters,
                    'returns': tool.returns
                }
        return format_tool_schemas(...)
```

### 5. Preflight Check for Polling Automations

Before deploying a polling automation, validate the data paths will resolve:

```python
async def preflight_validate_polling(
    trigger_config: Dict,
    actions: List[Dict],
    tool_registry: ToolRegistry,
    user_id: str
) -> Tuple[bool, List[str], Optional[Dict]]:
    """
    Pre-flight validation for polling automations.
    
    1. Validates source_tool exists
    2. Executes source_tool with sample params to get real data
    3. Validates all trigger_data.* paths resolve against the output
    
    Returns:
        Tuple of (is_valid, errors/warnings, sample_output)
    """
    errors = []
    
    # 1. Check source_tool exists
    source_tool = trigger_config.get('source_tool')
    if not source_tool:
        errors.append("Polling automation missing 'source_tool' in trigger_config")
        return False, errors, None
    
    tool = await tool_registry.get_tool_by_name(source_tool)
    if not tool:
        errors.append(f"source_tool '{source_tool}' not found")
        return False, errors, None
    
    # 2. Extract all trigger_data paths from automation
    trigger_data_paths = extract_trigger_data_paths(actions, trigger_config)
    
    if not trigger_data_paths:
        # No trigger_data references - no need for API call
        return True, [], None
    
    # 3. Execute source tool to get sample data
    tool_params = trigger_config.get('tool_params', {})
    try:
        sample_output = await tool_registry.execute_tool(
            source_tool, 
            resolve_template_params(tool_params),
            user_id
        )
    except Exception as e:
        # Tool execution failed - warn but allow creation
        errors.append(f"Warning: Could not test source_tool: {e}")
        return True, errors, None
    
    # 4. Validate paths against actual output
    for path in trigger_data_paths:
        # Remove 'trigger_data.' prefix for lookup
        lookup_path = path.replace('trigger_data.', '')
        value = get_nested_value(sample_output, lookup_path)
        
        if value is None:
            errors.append(
                f"Path '{path}' not found in source_tool output. "
                f"Available fields: {list(sample_output.keys()) if isinstance(sample_output, dict) else 'N/A'}"
            )
    
    if errors:
        return False, errors, sample_output
    
    return True, [], sample_output
```

## Validation Flow

```
User: "Alert me if sleep score < 70"
           ↓
Agent calls initial_md_fetch("Oura")
           ↓
Agent calls fetch_tool_data(["oura_get_daily_sleep", "push_notifications_send"])
           ↓                                    ↑
           ↓                    (schemas cached in fetched_tool_schemas)
Agent builds automation JSON
           ↓
validate_automation_actions()     → Check JSON structure
           ↓
validate_tools_exist()            → Check all tools in registry  
           ↓
validate_agent_fetched_schemas()  → Check agent fetched tool defs
           ↓
preflight_validate_polling()      → Test source_tool, validate paths
           ↓
Save to database (status: pending_review)
           ↓
User confirms in UI
           ↓
Set status: active
```

## Error Messages

Good validation errors help the agent self-correct:

```
❌ "Unknown tool 'oura_sleep'"
✓ "Unknown tool 'oura_sleep'. Did you mean 'oura_get_daily_sleep'? 
   Call fetch_tool_data to see available tools."

❌ "Invalid parameter"
✓ "Tool 'slack_post_message' has unknown parameter 'msg'. 
   Valid parameters: ['channel', 'text', 'blocks', 'thread_ts']"

❌ "Path not found"  
✓ "Path 'trigger_data.sleep_score' not found in oura_get_daily_sleep output.
   Available fields: ['score', 'day', 'timestamp', 'contributors']"
```

## Implementation Notes

The current `nl_to_automation/validation.py` file contains Juniper-specific imports and needs refactoring. Key functions to extract:

- `validate_automation_actions()` - Main validation entry point
- `validate_condition_structure()` - Condition validation
- `_check_handlebars_syntax()` - Detect unsupported Handlebars
- `_check_event_data_template()` - Detect wrong template names
- `_check_webhook_array_syntax()` - Detect array syntax in webhooks
- `preflight_validate_polling_automation()` - Pre-flight for polling
- `extract_trigger_data_paths()` - Find all trigger_data references
