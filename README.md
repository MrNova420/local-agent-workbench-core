# Shared Local Agent Engine

This repository contains the Python agent loop and local runtime/tooling services used by [Local Agent Workbench](https://github.com/MrNova420/local-agent-workbench). The Electron repository is the desktop product; this checkout is its versioned engine dependency. Its earlier Tkinter/CLI surfaces are retained for compatibility and are not the new product UI.

## Scope

- Iterative model/tool/model orchestration with Ollama and optional llama.cpp adapters.
- Project context assembly, instruction/skill discovery, request-only compaction, and workspace tools. When the selected input budget is tight, the request keeps core project operations available and discovers additional built-in/MCP schemas on demand; selected context and output values are not lowered.
- Large-file construction through ordered append chunks, resumable reads/results, and progress-based implementation continuation with durable incomplete status when the model stops advancing.
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

The current in-progress source adds context-adaptive per-request tool schemas. `load_tools` pages the project-visible catalog and can include exact built-in or MCP schemas in the next request. This makes schemas discoverable under smaller contexts; it does not guarantee a model can complete every task within a 4K–8K request budget. If required schemas plus the configured output reserve do not fit, the engine reports the constraint without changing either setting. Regression tests are queued and this source has not been verified.

## License

Licensed under the MIT License; see [`LICENSE`](LICENSE).
