# Implementation and verification status

## 2026-11-01 unpublished continuity source work

- Added a factual task checkpoint module for continuing the same user task across turns and compaction, retaining plan, captured settings, completed operation references, changed files, and verification. Older flat checkpoints migrate forward; terminal tasks remain in local checkpoint history.
- Compaction injects the checkpoint alongside the configured system prompt and recent complete-turn tail, and emits omitted/retained turns plus checkpoint inclusion and estimated size. Context/output/reasoning values remain unmodified.
- The Ollama stream adapter now retains prompt_eval_cached_count when the provider returns it.
- Added queued checkpoint migration/continuation and cache-counter regression cases. No tests, typecheck, package, or live model request were run for this source work.
- The paired Electron sidecar must pass session-owned checkpoint state into AgentEngine and durably mirror its context-compaction metadata. Update app core pins only after this engine milestone is committed. See the paired app's docs/SESSION_CONTINUITY_IMPLEMENTATION.md for the full fault-injection and visible acceptance queue.

Date: 2026-10-30
Git baseline: `2e10b37`; post-audit work is committed in small changes.
Current status: active development; **not approved for unrestricted real-project work**.

## Implemented in source

- Windows-first Tkinter coding workbench with a project/session rail, central conversation/composer, and Plan/Activity/Changes inspector. Generation controls are in Preferences.
- Default Ollama model remains `hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M`. Reasoning remains at the model default, and context is sent as selected.
- Immutable task snapshots; project/session/model controls are locked during a task; resume clears the current transcript and asks before switching projects.
- Run settings (context, maximum output, temperature, access mode, and approval mode) are validated and retained on session resume; late model metadata cannot overwrite a saved or manually selected context. Reasoning remains unset at the model default.
- Project-specific instructions are refreshed for resumed history; shared project traversal respects root and nested `.gitignore` rules, skips symlinks/junctions and common generated folders, and retains explicit file/byte limits.
- Persistent MCP event-loop ownership and per-turn cancellation for MCP operations.
- Streaming has no fixed socket inactivity deadline; approved direct child commands can be cancelled and have a one-hour maximum.
- Context budget includes serialized conversation and tool schemas plus output reserve, using a conservative UTF-8-byte estimate. It does not rewrite the configured context.
- Tool responses are retained for each call; over-limit batches report which actions were not executed.
- Copy access mode creates a managed disposable session workspace. The model edits only that copy; a full diff, source-conflict check, and transactional apply-back require a separate explicit user action. Commands still require approval.
- Tasks cannot start until the user explicitly selects a project folder; the initial home-folder fallback is not treated as consent.

## Verification performed in this work period

- `python -m unittest discover -s tests -q`: **69 passed**.
- `python -m py_compile` over application and support modules: passed.
- `git diff --check`: passed after the latest source and documentation updates.
- Regression tests cover turn snapshots, resumed project instructions, link traversal, persistent/cancelled MCP operations, request timeout configuration, cancellable commands, tool batches, bounded retrieval/search, full-request budgeting, patch transaction/deletion/rollback, session recovery validation, run-setting validation, context-setting precedence, explicit project selection, disposable-copy isolation, apply-back conflicts, and rollback.
- Terminal engine errors and cancellation are held until the worker persists the resulting session, preventing the UI from accepting a new prompt against stale in-memory history. A regression test verifies the event ordering.
- Read-only runtime snapshot while inspecting the existing app: `hf.co/ressl/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M`, allocated context 262,144, reported 87% CPU / 13% GPU. No request, reload, or unload was issued by this audit.

## Not verified here

- Visually inspected the rebuilt source at 1,580×990: the three-column project/conversation/inspector layout rendered, with no project selected and the model reasoning left at model default. The folder picker opened. UI automation then returned a Codex screenshot while reporting the app HWND; interaction was stopped before folder selection. The test process was closed, the older StableTest window was left untouched, and no inference or project write was submitted. A fresh empty fixture remains at `%USERPROFILE%\Documents\Codex\2026-10-30\fi\work\local-agent-desk-userflow-empty`.
- No live Ollama coding request or hardware benchmark was performed. Earlier numbers and model state in the original prototype report are historical records, not fresh evidence for this source revision.
- The prior `Tk window instantiated` claim belongs to the earlier prototype and does not verify the current layout.

## Remaining release gates

- Complete a disposable-project UI flow: choose root, hello, task, exact per-action approval, write, test, cancel, save, resume, and verify root isolation. One layout screenshot was inspected; the interactive flow is still unverified because the Windows automation helper returned the wrong app screenshot.
- Complete remaining event/UI lifecycle testing for startup, approval-dialog close, save/resume, and shutdown races; terminal-event persistence ordering has a mocked regression test but has not been exercised in the visible app.
- Add Windows child-process tree containment and resource limits; direct command approval still runs with the user's Windows account and is not an OS sandbox.
- Measure actual Ollama first-token latency, generation speed, allocated context, and peak RAM/VRAM under the approved model-preservation protocol.
- Revisit packaging, diagnostic exports, and installation/updating before calling the app production-ready.

See [the source audit and redesign plan](docs/AUDIT_AND_REDESIGN_PLAN.md) for findings, architecture direction, and gates.

## 2026-10-31 shared-engine handoff

- Hardened structured tool-argument validation in `desktop_engine.py`: malformed or unknown schema types/keywords now fail closed; common JSON Schema composition, JSON-correct constants/enums/numeric equality, non-finite number rejection, numeric/string/array constraints, object patterns, schema-valued `additionalProperties`, and local JSON Pointer references are checked. External, missing, or non-object references return an explicit unsupported-schema error instead of being treated as valid.
- Added regression cases for nested MCP-like schemas, constraints, composition, local and unresolved references, JSON type edge cases, non-finite numbers, unsupported keywords, and unknown types. These checks are intentionally queued for the final verification phase; no test suite or package build was run for this handoff.
- Paired Electron source must update `core-revision.txt` to the resulting commit before packaging. The package has not yet incorporated or verified this change.

### Operation outcome reporting

- Tool result mapping now distinguishes incomplete file/search scans as `partial`, treats invalid line-range reads and Git inspection failures as `failed`, and no longer counts those failed reads as post-change verification evidence.
- Added engine and renderer regression cases for partial scans, failed inspections, and successful empty search. These remain queued for final verification; the packaged UI has not yet incorporated this status styling.

## 2026-10-31 public beta handoff

- Published as the public MIT-licensed `MrNova420/local-agent-workbench-core` repository. The Electron product lives in the paired `MrNova420/local-agent-workbench` repository and pins this engine at commit `ecbf2914740fdd2d2b4f04a49d9d22dab0ccdc34`.
- The paired product's current local gates passed: 132 engine tests, 43 renderer tests, 38 sidecar tests, and TypeScript typecheck. Its Windows x64 `0.1.0-beta.1` installer/ZIP build and bundled-worker JSONL startup/ping/shutdown check also passed. This does not establish live model coding quality or full visible workflow acceptance.
- GitHub Actions was triggered by publication but could not start a runner. GitHub's check annotation says the account is locked due to a billing issue; this is an account-level CI blocker, not a test failure. Local engine pytest passed on Python 3.12.10.
- The historical `model-inspection.json` remains in prior commits to preserve history; it has been removed from the current tree and ignored. A targeted scan of reachable Git blobs found no high-confidence credential patterns. See the paired product acceptance log for scan scope and the disclosed machine-local model path.

## 2026-10-31 post-beta core work

- The paired Electron beta.2 release pins shared engine commit `ff67bcad93e72fad7add4eb8fc9e8aac1358e633`; its public beta release and local verification evidence are recorded in the desktop repository.
- In implementation tasks, a final prose-only `May I run ...` / `Can I execute ...` response can now trigger one bounded continuation asking the model to issue the exact structured `run_command` or `run_shell` call. This does not authorize execution: the existing host approval dialog remains mandatory, and no action runs from the prose itself.
- Completion evidence now distinguishes a post-change file/diff inspection, a command that completed, a failed command, and no evidence. A completed command is not represented as a passing test, and the paired UI warns when recorded command verification failed. Regression checks remain queued.
- Updated the existing build-task regression expectation to assert an inspection outcome and structured evidence. It was not run in this development phase.
- Automated tests, typecheck, sidecar integration, and visible application acceptance for this continuation are deliberately queued for the next final verification gate. This engine commit is not yet included in an app package.

## 2026-10-31 paired app roadmap reconciliation

- The active Electron app worktree now pins this repository's commit `e5158359cca2e780ca506cfd98e8b8015c63852a` in `core-revision.txt` and both app workflows. This supersedes the earlier note above that said the app did not yet pin the continuation; that note remains historical for its original point in time.
- The engine commit is not yet in the published app package, and the latest paired app/engine regression batch has not yet passed its final verification gate. Do not call it packaged or release-ready.
- For the current product-wide source promotion, test, package, visible acceptance, and release sequence, see the paired app repository's `docs/MASTER_ROADMAP.md`. Keep this engine repo as the engine implementation/status authority.
- The engine is currently consumed by the desktop product as an immutable commit SHA. Do not create an independent engine version or release tag for app-only updates. Revisit separate engine releases only if a separately supported distributable/API contract is approved.

## 2026-11-01 beta.3 paired release

- Engine `main` now contains commit `742ddb55a0fcdd7d0d205b29c04228cef5b9c0ff`; the desktop beta.3 package pins tested engine implementation commit `e5158359cca2e780ca506cfd98e8b8015c63852a`.
- The paired local verification passed: **140 engine pytest tests**; app release evidence includes **53 renderer tests, 45 sidecar tests, typecheck, Windows package build, and bundled-worker protocol smoke**. The engine does not have a separate release tag; it is shipped as a pinned dependency of the desktop app.
- GitHub Actions failed before any steps started because no hosted runner was allocated. Local suite results passed; hosted CI remains blocked, not passing.
- The beta.3 desktop release is public. Its packaged UI was not opened because the previous app instance had an active run and was intentionally left untouched. Visible UI/update-cycle acceptance remains pending in the paired desktop `docs/MASTER_ROADMAP.md`.

## 2026-11-01 continuity stabilization in progress

- The engine now estimates serialized request input size for transparent UI reporting without changing configured context, output, reasoning, or runtime placement.
- Task checkpoints record explicit blockers and include the immediately preceding archived task summary when a new task starts, while full prior history and tool results remain in session storage.
- Added regression coverage for checkpoint carry-forward and exact request settings; these tests are queued and have not been run.
- Paired desktop work is in progress. The app must pin the resulting engine commit and both workflows must use the same SHA before any package is built. No release is authorized by this source work.
