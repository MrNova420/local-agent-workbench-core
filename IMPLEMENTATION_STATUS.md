# Implementation and verification status

Date: 2026-10-30
Git baseline: `2e10b37`; post-audit work is committed in small changes.
Current status: active development; **not approved for unrestricted real-project work**.

## Implemented in source

- Windows-first Tkinter coding workbench with a project/session rail, central conversation/composer, and Plan/Activity/Changes inspector. Generation controls are in Preferences.
- Default Ollama model remains `hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M`. Reasoning remains at the model default, and context is sent as selected.
- Immutable task snapshots; project/session/model controls are locked during a task; resume clears the current transcript and asks before switching projects.
- Run settings (context, maximum output, temperature, access mode, and approval mode) are validated and retained on session resume; late model metadata cannot overwrite a saved or manually selected context. Reasoning remains unset at the model default.
- Project-specific instructions are refreshed for resumed history; shared project traversal skips symlinks, junctions, and common generated folders.
- Persistent MCP event-loop ownership and per-turn cancellation for MCP operations.
- Streaming has no fixed socket inactivity deadline; approved direct child commands can be cancelled and have a one-hour maximum.
- Context budget includes serialized conversation and tool schemas plus output reserve, using a conservative UTF-8-byte estimate. It does not rewrite the configured context.
- Tool responses are retained for each call; over-limit batches report which actions were not executed.

## Verification performed in this work period

- `python -m unittest discover -s tests -v`: **48 passed**.
- `python -m py_compile` over application and support modules: passed.
- `git diff --check`: passed after the latest source and documentation updates.
- Regression tests cover turn snapshots, resumed project instructions, link traversal, persistent/cancelled MCP operations, request timeout configuration, cancellable commands, tool batches, bounded retrieval/search, full-request budgeting, patch transaction/deletion/rollback, session recovery validation, run-setting validation, and context-setting precedence.
- Read-only runtime snapshot while inspecting the existing app: `hf.co/ressl/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M`, allocated context 262,144, reported 87% CPU / 13% GPU. No request, reload, or unload was issued by this audit.

## Not verified here

- The visible existing window was the older UI, idle at READY, showing the `LocalAgentDesk-StableTest-...` workspace and its matching session. The redesigned source was not visually inspected or exercised end-to-end. No model request or project write was submitted.
- No live Ollama coding request or hardware benchmark was performed. Earlier numbers and model state in the original prototype report are historical records, not fresh evidence for this source revision.
- The prior `Tk window instantiated` claim belongs to the earlier prototype and does not verify the current layout.

## Remaining release gates

- Visually inspect the rebuilt UI at realistic Windows sizes/scaling and complete a disposable-project flow: choose root, hello, task, exact per-action approval, write, test, cancel, save, resume, and verify root isolation.
- Port the existing `copy` access mode into the desktop app, including disposable workspace creation and explicit reviewed application of changes back to the source. The app currently offers only `plan`, `ask`, and `edit`.
- Complete event/UI lifecycle testing for startup, cancel, approval-dialog close, save/resume, and shutdown races.
- Add Windows child-process tree containment and resource limits; direct command approval still runs with the user's Windows account and is not an OS sandbox.
- Make project traversal respect `.gitignore` and nested ignore rules while keeping link and file/byte limits.
- Measure actual Ollama first-token latency, generation speed, allocated context, and peak RAM/VRAM under the approved model-preservation protocol.
- Revisit packaging, diagnostic exports, and installation/updating before calling the app production-ready.

See [the source audit and redesign plan](docs/AUDIT_AND_REDESIGN_PLAN.md) for findings, architecture direction, and gates.
