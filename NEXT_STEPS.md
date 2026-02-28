# Development Status

## Completed

- [x] Project structure
- [x] Core Python modules extracted (types.py, templates.py, conditions.py)
- [x] Interfaces created (ToolRegistry, AutomationDatabase, etc.)
- [x] Executor refactored to use interfaces
- [x] Validation module refactored (no external dependencies)
- [x] Agent tools extracted (initial_md_fetch, fetch_tool_data, deploy_automation)
- [x] Test suite (47 tests passing)
- [x] Documentation:
  - getting-started.md - Installation and agent setup
  - architecture.md - System design
  - agent-tool-discovery.md - How agent discovers tools and capabilities
  - validation.md - Validation checks (schema, tools, preflight)
  - spec/declarative-schema.md - Full schema for agent prompts
- [x] Example automations (4 complete)
- [x] Database schemas (including webhook_payload_schemas)
- [x] Edge functions (copied from Juniper, still have some Juniper references)

## Agent Tools

The `agent_tools.py` module provides tools for an LLM agent to build automations:

```python
from nl_to_automation import (
    AgentContext,
    initial_md_fetch,
    fetch_tool_data,
    deploy_automation,
    create_agent_tools,
)

# Option 1: Use individual functions
context = AgentContext()
tools_list = await initial_md_fetch("Oura", tool_registry, automation_db)
tool_details = await fetch_tool_data(["oura_get_sleep"], tool_registry, context)
success, msg, id = await deploy_automation(automation, user_id, db, registry, context)

# Option 2: Use create_agent_tools for LLM function calling
tools = create_agent_tools(tool_registry, automation_db, user_id)
# tools['definitions'] - Tool schemas for Claude/GPT
# tools['handlers'] - Async handlers to execute tools
```

## Implementing LLM Tools

The `llm_tools.py` file was removed (had Juniper-specific dependencies).

To add LLM capabilities to your automations, implement the `LLMProvider` interface:

```python
from nl_to_automation.interfaces import LLMProvider

class MyLLMProvider(LLMProvider):
    async def generate(self, prompt: str, **kwargs) -> str:
        # Your LLM implementation
        pass
```

Then create tools like `llm_classify`, `llm_transform` that use your provider.

## Optional Future Work

- Clean up edge functions to remove Juniper-specific code
- Add more example tool registry implementations
- Add tests for agent_tools.py
