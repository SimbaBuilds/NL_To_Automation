# Development Status

## Completed

- [x] Project structure
- [x] Core Python modules extracted (types.py, templates.py, conditions.py)
- [x] Interfaces created (ToolRegistry, AutomationDatabase, etc.)
- [x] Executor refactored to use interfaces
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

## Validation Checklist

See [docs/validation.md](docs/validation.md) for full details. Key checks:

1. **Schema validation** - JSON structure is valid
2. **Tool validation** - All tools exist in registry
3. **Agent schema verification** - Agent fetched tool defs before using them
4. **Preflight for polling** - Source tool returns data, paths resolve

## Optional Future Work

- Refactor validation.py to use interfaces (remove Supabase deps)
- Refactor llm_tools.py to use LLMProvider interface
- Clean up edge functions to remove Juniper-specific code
- Add more example tool registry implementations
