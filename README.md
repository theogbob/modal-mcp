# modal-mcp-server

MCP server for [Modal](https://modal.com/) — deploy apps, manage volumes, run sandboxes from Claude Code.

## Install with Claude Code

```bash
claude mcp add modal --scope user -- uvx --from "git+https://github.com/theogbob/modal-mcp" modal-mcp-server
```

## Prerequisites

- Python 3.11+
- Modal CLI configured: `pip install modal && python3 -m modal setup`
