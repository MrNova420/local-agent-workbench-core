# Implementation and verification status

Date: 2026-10-30

## Implemented

- Fresh standalone Tkinter app under `outputs/local-9b-dev-app`; no old harness source copied into it.
- Default Ollama tag is `hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M`; any installed Ollama model may be selected.
- Context is taken from model metadata and remains user editable; runtime request carries the selected `num_ctx` verbatim. No reasoning parameter is set.
- Worker-thread generation, streamed text/tool events, request cancellation, model/session/project settings, approvals, relevant-file retrieval, compaction, project guidance/skills, stdio MCP, sessions and read-only telemetry.
- Glossy dark Tkinter interface, startup launcher, build helper, README, and an architecture diagram.

## Verification

- `python -m unittest discover -s tests -v`: 24 passed.
- `python -m py_compile ...`: passed for app and support modules.
- Tk window instantiated, ran event loop and destroyed cleanly.
- Ollama read-only metadata: exact tag advertises 262,144 context.
- Ollama loaded snapshot after cleanup: exact tag remained loaded at the original 65,536 context, `size_vram=1,633,492,663` bytes.

## Live check limitation

An authorized live check was started, but did not produce a trustworthy coding result and was interrupted. It briefly changed the already-loaded model's requested context to 32,768. The original 65,536 allocation was restored through the approved local check pathway, verified via `/api/ps`. No first-token, tokens/sec, or coding-quality baseline is reported. The model remains running and was not unloaded.

## Remaining work

- Review the app visually for polish on the user's desktop (the window lifecycle was verified programmatically).
- Complete a controlled live coding run only when the exact model's prior loaded state can be preserved or restored reliably, and record first-token latency, tokens/sec, effective context and peak resource use.
- Add broader tool loop/session recovery and user-configurable runtime tuning controls; keep Ollama KV cache tuning opt-in and benchmarked.
- Keep llama.cpp and subagents on the roadmap until this V1 is reliable.
