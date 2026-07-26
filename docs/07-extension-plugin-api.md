# Extension Plugin API — Python

This document describes the Python extension/plugin API for pycortex, the mechanical port of hoocode's extension system. In the TS source, extensions are TypeScript modules loaded via dynamic `import()`. In Python, we use `importlib` with a redesigned plugin interface.

## Overview

Extensions are Python modules that:

- Subscribe to agent lifecycle events
- Register LLM-callable tools
- Register commands, keyboard shortcuts, and CLI flags
- Interact with the user via UI primitives

## Extension Structure

An extension is a Python module (or package) with:

```
my_extension/
├── __init__.py       # Extension entry point
├── types.py          # Extension-specific types
└── ...
```

### Entry Point

The extension module must define:

```python
from cortex.code.extensions import ExtensionMetadata

EXTENSION_METADATA = ExtensionMetadata(
    name="my-extension",
    version="1.0.0",
    description="A sample extension",
    author="Author Name",
    homepage="https://example.com",
    keywords=["sample", "demo"],
)
```

### Lifecycle Hooks

```python
from cortex.code.extensions import ExtensionContext


def activate(context: ExtensionContext) -> None:
    """Called when the extension is loaded."""
    # Register tools, commands, event handlers, etc.
    context.state["my_data"] = {}


def deactivate() -> None:
    """Called when the extension is unloaded."""
    # Cleanup resources
    pass
```

## Extension Context

The `ExtensionContext` provides access to the agent and extension state:

```python
@dataclass
class ExtensionContext:
    metadata: ExtensionMetadata
    config: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
```

### Accessing Agent

Extensions can access the agent via the context:

```python
def activate(context: ExtensionContext) -> None:
    # Access agent state
    agent = context.state.get("agent")
    if agent:
        # Register a tool
        agent.register_tool("my_tool", my_tool_handler)
```

## Registering Tools

Extensions can register custom tools that the agent can call:

```python
from cortex.ai.types import TextContent, AgentToolResult


async def my_tool_handler(tool_call_id: str, args: dict) -> AgentToolResult:
    """Handle a tool call."""
    return AgentToolResult(
        content=[TextContent(text=f"Result: {args.get('input', '')}")],
        details=None,
    )


def activate(context: ExtensionContext) -> None:
    context.state["tools"] = {
        "my_tool": my_tool_handler,
    }
```

## Registering Commands

Extensions can register slash commands:

```python
def activate(context: ExtensionContext) -> None:
    context.state["commands"] = {
        "my-command": {
            "description": "A custom command",
            "handler": my_command_handler,
        },
    }
```

## Registering Event Handlers

Extensions can subscribe to agent events:

```python
def activate(context: ExtensionContext) -> None:
    # Subscribe to events
    context.state["event_handlers"] = {
        "message": [on_message],
        "tool_result": [on_tool_result],
        "error": [on_error],
    }
```

## Extension Loader

The `ExtensionLoader` class manages loading and activating extensions:

```python
from cortex.code.extensions import ExtensionLoader

# Create a loader
loader = ExtensionLoader(extension_paths=["/path/to/extensions"])

# Load an extension
info = loader.load_extension("/path/to/extensions/my_extension.py")

# Activate it
loader.activate_extension("my-extension")

# Deactivate it
loader.deactivate_extension("my-extension")

# List all extensions
extensions = loader.list_extensions()
```

## Extension Registry

The `ExtensionRegistry` manages multiple loaders:

```python
from cortex.code.extensions import ExtensionRegistry

# Create a registry
registry = ExtensionRegistry()

# Register a loader
registry.register_loader("user", loader)

# Load all extensions
loaded = registry.load_all_extensions(["/path/to/extensions"])

# Activate all
count = registry.activate_all()

# Deactivate all
count = registry.deactivate_all()
```

## Differences from TypeScript

### Loading Mechanism

- **TypeScript**: Uses dynamic `import()` and `require()`
- **Python**: Uses `importlib.util.spec_from_file_location()` and `module_from_spec()`

### Module Discovery

- **TypeScript**: Discovers `.js`/`.ts` files in extension directories
- **Python**: Discovers `.py` files in extension directories

### State Management

- **TypeScript**: Extensions can access the agent directly
- **Python**: Extensions receive an `ExtensionContext` with state

### Event System

- **TypeScript**: Uses `EventEmitter` pattern
- **Python**: Uses callback lists stored in extension state

## Security Considerations

- Extensions are loaded from the filesystem, not from remote sources
- Extension modules have access to the Python runtime
- Extensions should be trusted code

## Testing Extensions

Extensions can be tested using the same `pytest` framework:

```python
from cortex.code.extensions import ExtensionLoader, ExtensionContext


def test_my_extension():
    loader = ExtensionLoader()
    info = loader.load_extension("/path/to/my_extension.py")
    assert info is not None
    assert info.metadata.name == "my-extension"
    assert info.is_active is False

    # Activate
    loader.activate_extension("my-extension")
    assert info.is_active is True
```
