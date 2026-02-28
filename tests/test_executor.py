"""Tests for automation executor."""

import pytest
from typing import Dict, Any, List, Optional

from nl_to_automation import (
    execute_automation,
    ExecutionStatus,
    UserInfo,
)
from nl_to_automation.interfaces import Tool, ToolRegistry, NotificationHandler


# Mock implementations for testing

class MockToolRegistry(ToolRegistry):
    """Mock tool registry for testing."""

    def __init__(self):
        self.tools = {}

    def add_tool(self, name: str, handler):
        """Add a mock tool."""
        self.tools[name] = Tool(
            name=name,
            description=f"Mock tool {name}",
            parameters={},
            returns="Mock result",
            handler=handler
        )

    async def get_tool_by_name(self, name: str) -> Optional[Tool]:
        return self.tools.get(name)

    async def list_tools(self, service: Optional[str] = None) -> List[Tool]:
        return list(self.tools.values())

    async def execute_tool(self, tool_name: str, parameters: Dict[str, Any], user_id: str, **kwargs) -> Any:
        tool = await self.get_tool_by_name(tool_name)
        if tool:
            import json
            return await tool.handler(json.dumps(parameters))
        raise Exception(f"Tool not found: {tool_name}")


class MockNotificationHandler(NotificationHandler):
    """Mock notification handler for testing."""

    def __init__(self):
        self.notifications = []

    async def notify_usage_limit_exceeded(self, user_id: str, automation_id: str, automation_name: str) -> None:
        self.notifications.append({
            'type': 'usage_limit',
            'user_id': user_id,
            'automation_id': automation_id,
            'automation_name': automation_name
        })

    async def notify_automation_failed(self, user_id: str, automation_id: str, automation_name: str, error_summary: Optional[str] = None) -> None:
        self.notifications.append({
            'type': 'failed',
            'user_id': user_id,
            'automation_id': automation_id,
            'automation_name': automation_name,
            'error': error_summary
        })

    async def notify_custom(self, user_id: str, title: str, body: str, **kwargs) -> None:
        self.notifications.append({
            'type': 'custom',
            'user_id': user_id,
            'title': title,
            'body': body
        })


# Tests

@pytest.mark.asyncio
async def test_simple_automation_execution():
    """Test executing a simple automation."""
    # Create mock tool that returns a score
    async def mock_get_score(input_str: str):
        return '{"score": 85}'

    registry = MockToolRegistry()
    registry.add_tool('get_score', mock_get_score)

    # Create automation
    actions = [
        {
            'id': 'get_data',
            'tool': 'get_score',
            'parameters': {},
            'output_as': 'score_data'
        }
    ]

    user_info = UserInfo(id='user123', email='test@example.com', timezone='UTC')

    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={},
        user_id='user123',
        user_info=user_info,
        tool_registry=registry
    )

    assert result.success is True
    assert result.status == ExecutionStatus.COMPLETED
    assert result.actions_executed == 1
    assert result.actions_failed == 0


@pytest.mark.asyncio
async def test_automation_with_condition():
    """Test automation with conditional action."""
    # Mock tool
    async def mock_notify(input_str: str):
        return '{"sent": true}'

    registry = MockToolRegistry()
    registry.add_tool('send_notification', mock_notify)

    # Automation with condition
    actions = [
        {
            'id': 'notify_if_low',
            'tool': 'send_notification',
            'condition': {
                'path': 'score',
                'op': '<',
                'value': 70
            },
            'parameters': {
                'body': 'Score is low: {{score}}'
            }
        }
    ]

    user_info = UserInfo(id='user123', email='test@example.com', timezone='UTC')

    # Test with failing condition (score = 85)
    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={'score': 85},
        user_id='user123',
        user_info=user_info,
        tool_registry=registry
    )

    assert result.success is True
    assert result.actions_executed == 0  # Action was skipped
    assert len(result.action_results) == 1
    assert result.action_results[0].skipped is True

    # Test with passing condition (score = 50)
    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={'score': 50},
        user_id='user123',
        user_info=user_info,
        tool_registry=registry
    )

    assert result.success is True
    assert result.actions_executed == 1  # Action was executed
    assert result.action_results[0].success is True


@pytest.mark.asyncio
async def test_automation_with_template_variables():
    """Test that template variables are resolved in parameters."""
    # Mock tool that echoes input
    async def mock_echo(input_str: str):
        import json
        params = json.loads(input_str)
        return json.dumps({'message': params.get('message', '')})

    registry = MockToolRegistry()
    registry.add_tool('echo', mock_echo)

    actions = [
        {
            'id': 'send_message',
            'tool': 'echo',
            'parameters': {
                'message': 'Hello {{user.name}}, your score is {{score}}'
            },
            'output_as': 'result'
        }
    ]

    user_info = UserInfo(id='user123', email='test@example.com', timezone='UTC', name='Alice')

    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={'score': 85},
        user_id='user123',
        user_info=user_info,
        tool_registry=registry
    )

    assert result.success is True
    # The output should contain the resolved message
    output = result.action_results[0].output
    assert 'Alice' in str(output)
    assert '85' in str(output)


@pytest.mark.asyncio
async def test_automation_with_chained_actions():
    """Test automation with multiple actions where later actions use earlier outputs."""
    # Mock tools
    async def mock_get_data(input_str: str):
        return '{"value": 100}'

    async def mock_double(input_str: str):
        import json
        params = json.loads(input_str)
        value = int(params.get('value', 0))
        return json.dumps({'result': value * 2})

    registry = MockToolRegistry()
    registry.add_tool('get_data', mock_get_data)
    registry.add_tool('double_value', mock_double)

    actions = [
        {
            'id': 'fetch',
            'tool': 'get_data',
            'parameters': {},
            'output_as': 'data'
        },
        {
            'id': 'transform',
            'tool': 'double_value',
            'parameters': {
                'value': '{{data.value}}'
            },
            'output_as': 'doubled'
        }
    ]

    user_info = UserInfo(id='user123', email='test@example.com', timezone='UTC')

    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={},
        user_id='user123',
        user_info=user_info,
        tool_registry=registry
    )

    assert result.success is True
    assert result.actions_executed == 2
    assert result.actions_failed == 0


@pytest.mark.asyncio
async def test_automation_with_tool_failure():
    """Test automation handling when a tool fails."""
    # Mock tool that fails
    async def mock_failing_tool(input_str: str):
        raise Exception("Tool execution failed")

    registry = MockToolRegistry()
    registry.add_tool('failing_tool', mock_failing_tool)

    actions = [
        {
            'id': 'fail',
            'tool': 'failing_tool',
            'parameters': {}
        }
    ]

    user_info = UserInfo(id='user123', email='test@example.com', timezone='UTC')

    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={},
        user_id='user123',
        user_info=user_info,
        tool_registry=registry
    )

    assert result.success is False
    assert result.status == ExecutionStatus.FAILED
    assert result.actions_failed == 1
    assert result.error_summary is not None


@pytest.mark.asyncio
async def test_automation_continues_after_failure():
    """Test that automation continues to next action even if one fails."""
    # Mock tools
    async def mock_failing_tool(input_str: str):
        raise Exception("Tool failed")

    async def mock_success_tool(input_str: str):
        return '{"result": "ok"}'

    registry = MockToolRegistry()
    registry.add_tool('failing_tool', mock_failing_tool)
    registry.add_tool('success_tool', mock_success_tool)

    actions = [
        {
            'id': 'fail',
            'tool': 'failing_tool',
            'parameters': {}
        },
        {
            'id': 'succeed',
            'tool': 'success_tool',
            'parameters': {}
        }
    ]

    user_info = UserInfo(id='user123', email='test@example.com', timezone='UTC')

    result = await execute_automation(
        actions=actions,
        variables={},
        trigger_data={},
        user_id='user123',
        user_info=user_info,
        tool_registry=registry
    )

    # Should be partial failure (1 succeeded, 1 failed)
    assert result.status == ExecutionStatus.PARTIAL_FAILURE
    assert result.actions_executed == 2
    assert result.actions_failed == 1
