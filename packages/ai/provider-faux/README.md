# cortexcode-ai-provider-faux

Faux (mock) AI provider for testing.

## Overview

This package provides a mock AI provider for testing purposes, including:

- Fake completion responses
- Configurable delay and error simulation
- No external API dependencies

## Installation

```bash
pip install cortexcode-ai-provider-faux
```

## Usage

```python
from cortex.ai.providers.faux import create_faux_provider

provider = create_faux_provider()
```

## License

MIT
