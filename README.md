# Shared Local Agent Engine

This repository contains the Python agent loop and local runtime/tooling services used by [Local Agent Workbench](https://github.com/MrNova420/local-agent-workbench). The Electron repository is the desktop product; this checkout is its versioned engine dependency. Its earlier Tkinter/CLI surfaces are retained for compatibility and are not the new product UI.

## Scope

- Iterative model/tool/model orchestration with Ollama and optional llama.cpp adapters.
- Project context assembly, instruction/skill discovery, request-only compaction, and workspace tools.
- Approval-aware file and command operations, MCP support, run events, and session persistence.
- Shared desktop-agent protocols and regression tests.

## Development

Python 3.12 is the verified environment. Install requirements, then run:

```powershell
python -m pip install -r requirements.txt -r requirements-mcp.txt pytest
python -m pytest -q
```

The engine is tested independently and also through the sidecar tests in the desktop repository. Test results do not establish live-provider or packaged-app readiness; see the desktop repository's acceptance log and parity register.

## Safety boundary

Project-scoped file operations and approval states are application-level controls. Approved shell commands and MCP processes use the current Windows account and are not contained by the selected project directory. A real Windows process/filesystem sandbox remains open work.

## Status

Unstable beta development dependency. The desktop repository pins the compatible engine commit in `core-revision.txt`. Do not update that pin without running the app, sidecar, and engine source gates.

## License

License selection is pending. Until a license file is added, all rights are reserved despite public source visibility.
