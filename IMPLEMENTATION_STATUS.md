# Implementation and verification status

## 2026-11-02 low-context checkpoint retention regression

- Strengthened the oversized-checkpoint compaction regression to prove that, under context pressure, the current goal and open implementation-plan step remain in the inference summary, while the canonical 200-operation checkpoint is unchanged. The selected context is asserted unchanged as well.
- Focused test `tests/test_context.py::ContextTests::test_compaction_budget_summarizes_oversized_checkpoint_without_changing_context`: **1 passed**. The full engine suite later passed: **345 passed** with one upstream `python-multipart` pending-deprecation warning.

## 2026-11-02 full engine suite and Windows contract assertions

- Updated two stale test expectations to match current contracts: provider HTTP failures are surfaced as `InferenceStreamError` with the selected context preserved in the captured request; approved Windows commands include `CREATE_SUSPENDED` before Job Object assignment as well as `CREATE_NO_WINDOW`.
- Focused failures: **6 passed**. Full engine suite: `python -m pytest -q` → **345 passed**, one upstream pending-deprecation warning, **37.30s**. No model was loaded by this suite.

## 2026-11-02 Continuation progress uses observed read results and argument fingerprints

- Read-only search/read/checkpoint/history/Git operations count as new continuation progress when their observed result changes; changing only a query while receiving the same empty result no longer disguises a no-progress loop. Distinct file paths remain distinguishable even when contents match. Other operations retain their argument-based identity.
- The engine now passes tool arguments separately to checkpoint recording, which stores only a SHA-256 fingerprint. Raw arguments are not copied into the progress record. Existing older rows without a result fingerprint retain the argument-fingerprint fallback.
- Focused `tests/test_workflow_outcome.py`, `tests/test_operation_stream.py`, and `tests/test_task_checkpoint.py`: **100 passed**. `py_compile` and `git diff --check` passed. Full suites, packaged app, and live model acceptance remain queued.

## 2026-11-02 continuation recognizes distinct model responses

- An implementation continuation no longer treats two different assistant responses as identical merely because plan, operation, and verification state did not change. The no-progress signature for a tool-free implementation response now includes a local SHA-256 fingerprint of emitted content/reasoning; it stores no duplicate response text in that signature. New responses may continue the same task toward a structured action; any repeated response/state fingerprint, including a cycle separated by other reasoning, becomes an explicit `implementation_incomplete` outcome. Plan-only tool batches retain their separate no-progress behavior. No attempt, runtime, context, output, or reasoning cap was added.
- TDD regression first failed by ending after three model calls before the expected write, then passed after the change. Focused `tests/test_operation_stream.py` plus `tests/test_workflow_outcome.py`: **72 passed**; `py_compile` and `git diff --check` passed. The integration regression reaches write, inspection, plan completion, and an `inspected` terminal outcome. Full suites and packaged/visible long-run acceptance remain queued.

## 2026-11-02 case-aware regular-expression project search

- `search_files` keeps case-insensitive literal matching as the default and now accepts `regex`, `case_sensitive`, and `match_timeout_seconds` options. Invalid expressions return a correctable error. A regex timeout preserves any matches already found and returns an explicit incomplete result; the activity layer classifies it as partial. Regex matching uses the `regex` package's per-operation timeout support, and the runtime dependency is included in both engine and packaged-sidecar requirements.
- Focused evidence: `tests/test_tools.py` **41 passed**, including case behavior, pagination-compatible schema options, invalid patterns, pathological-pattern timeout, and partial status; `tests/test_context.py` plus `tests/test_context_budget.py` **21 passed**; Python compilation and `git diff --check` passed. App pin/build-config check and packaged sidecar installation remain pending; full suites remain queued. No model or inference settings changed.

## 2026-11-02 Windows shell selection for project workflows

- `run_shell` now accepts an explicit `shell` choice: PowerShell remains the default and CMD uses the Windows `COMSPEC` command processor with `/d /s /c`; requests for CMD on non-Windows hosts and unknown shell names return clear errors. The selected shell is part of the structured tool arguments shown in activity and approvals.
- Both shells stay non-interactive, run in the captured workspace, stream stdout/stderr, receive end-of-input on stdin, inherit the existing approval/permission policy, retain request cancellation and optional explicit timeout behavior, and remain host-user execution rather than OS containment.
- A same-task recovery regression now sends a rejected file path back as tool evidence, corrects the path, writes and inspects the requested file, and ends as `inspected`; it does not restart the task or lose the first failure.
- Focused evidence: tool schema and shell argv tests **4 passed**; `tests/test_tool_validation.py` **21 passed**, including a real Windows CMD `echo` through the approval-aware command runner; selected command/continuation checks **5 passed**; same-task failure recovery **1 passed**; updated prompt-contract tests **10 passed**; Python compilation and `git diff --check` passed. App pin/build checks and packaged CMD acceptance remain pending; no model was started and no inference settings changed.

## 2026-11-02 automatic task progress visibility and plan-loop balance

- Automatic runs now publish one generic, host-owned in-progress checklist item before the first model request, so the Live Work Sheet is not empty while the model reasons. It does not infer discussion versus implementation from prompt wording. A model-authored plan replaces it and remains freely expandable; the generic item completes with the task and does not turn an ordinary Auto answer into `plan_only`.
- The no-progress detector deliberately does not treat plan text/expansion or agent-authored decision notes alone as project implementation progress. Tool-free implementation responses also compare an exact response fingerprint so a new model response can continue the same task; identical repeated responses with unchanged host state stop with resumable incomplete status. Plan-only tool loops remain separately detected. Host decisions, user updates, actual operations, and verification remain observable evidence. No step/runtime/token cap was added.
- Focused evidence: `tests/test_workflow_outcome.py` **23 passed**; `tests/test_operation_stream.py -k "automatic or repeated_identical_plan or pending_plan_expansion"` **7 passed**; `py_compile` and `git diff --check` passed. Full engine/app suites and packaged worksheet acceptance remain queued. Model and inference settings unchanged.

## 2026-11-02 same-task live steering during model responses

- The engine accepts a separate steering interrupt signal alongside user cancellation. A persisted same-session update interrupts only the active provider request, saves the text/reasoning already received as an explicitly partial assistant message, excludes incomplete tool-call arguments, and resumes the same task with the update at its next safe boundary. Ordinary Cancel still takes precedence and ends the task; completed tools and approval decisions are not interrupted or bypassed.
- The interruption is a request-level HTTP stream close only; it does not stop or unload the provider/model. `tests/test_operation_stream.py -k steering`: **3 passed**. Paired app `63b2238` pins this engine and passes worker steering **3**, journal-recovery redirect **1**, renderer run-state **22**, and TypeScript typecheck. App activity recovery distinguishes redirect from user cancel. Full suites, crash/fault matrix, packaging, visible workflow, and live Flint behavior remain pending. No model settings changed.

## 2026-11-02 evidence-based tool failure recovery guidance

- The model now receives general recovery guidance after ordinary tool failures: inspect the error and current project state, correct the cause before retrying, preserve successful work, and continue after recoverable errors. Potentially partial/uncertain effects must be inspected before a retry; denials must not be retried or routed around. The prompt sets no fixed retry/iteration cap and requires each retry to respond to observed evidence.
- `tests/test_agent_instructions.py`: **10 passed**, including a regression for failure recovery, uncertainty, denial respect, and no fixed retry cap. This changes model guidance only; it does not change host approval behavior, project access, runtime settings, or model inference. Paired app pin and packaged behavior remain to be synced/verified.

## 2026-11-02 continuation and checkpoint focused verification

- Engine source commit `dcdf7fc2c3b1ca61bfd44cb2449e4e77b7e69c59` passes `tests/test_operation_stream.py` and `tests/test_task_checkpoint.py`: **61 passed**.
- This focused pass covers observable-state implementation continuation, no-progress outcomes, tool activity/results, and checkpoint plan continuity. It does not replace the full engine/sidecar/renderer suites or packaged/visible long-run acceptance.

## 2026-11-02 preserve concrete next action after tool results

- `record_operation` no longer overwrites a concrete plan-derived `next_action` with a generic reminder after every tool result. This keeps compaction and same-task continuation pointed at the saved open step. Unplanned tasks still receive a concise operation-review fallback.
- Regression tests passed: `tests/test_task_checkpoint.py` **25 passed**. Full engine suite, packaged app, and long-run model continuation remain pending.
- Under checkpoint budget pressure, the compacted `active_plan_action` now selects the first open step in plan order rather than the last, avoiding a jump ahead. The large-plan regression asserts the first workstream survives while the canonical plan remains unchanged.

## 2026-11-02 per-request reasoning budget for llama.cpp

- Added an optional `reasoning_budget_tokens` runtime value for llama.cpp requests. `null` remains unlimited; configured values are sent in `thinking_budget_tokens` without changing `max_tokens`. The accepted range follows llama.cpp's signed 32-bit request field. Provider telemetry records the requested value and field, but does not claim that a model template enforced it.
- The existing model-specific reasoning mode/effort control remains separate: it selects the runtime-advertised on/off/level behavior. The new token budget only constrains supported reasoning sections; it does not impose a time limit, cap total output, or guarantee model quality/behavior.
- Focused llama.cpp runtime, operation-stream, context, context-budget, and task-checkpoint suites passed: **90 passed**. This is not a full engine-suite or runtime acceptance result. Packaged UI and a live llama.cpp budget enforcement check remain pending.

## 2026-11-02 same-task steering boundary (verification queued)

- Engine commit `39ff5495256e12f5f7e43232f269e5ace8d1ff88` adds an optional host steering callback. The agent loop polls it only before a provider request and at the safe boundary before completing a tool-free response, after the current response and complete structured tool exchange have been resolved.
- `record_user_update` stores stable update IDs and delivery state idempotently in the active task checkpoint, preserving the task ID and prior plan/progress. Provider-bound message serialization removes internal turn/steering metadata while saved history retains it.
- Added queued regressions for safe-boundary delivery after final prose, checkpoint update idempotence/identity conflicts, and provider serialization. No tests or live model request were run; the paired app source/sidecar integrates the host callback in its pinned source revision.

## 2026-11-01 Ollama system-message ordering fix

- A visible request using the selected Ornith GGUF reached Ollama with the configured 65,536 context, 8,192 output allowance, model-default reasoning, and a ~15,628-token input estimate, then failed with HTTP 500 before its first token.
- The Ollama server log identified the root cause: the model's chat template rejects a second `system` message. Compaction had injected the durable task checkpoint as a separate system message after the configured agent prompt.
- Provider-bound compaction now folds existing system instructions and the task checkpoint into one leading system message while preserving every non-system conversation message and the canonical saved transcript. No context, output, reasoning, warm-model, or runtime placement setting was changed.
- Regression coverage first failed with two system messages, then passed after the fix. Full engine suite: **251 passed**, one upstream `python_multipart` pending-deprecation warning.
- The packaged app still contains the earlier engine revision; paired app pin, sidecar/package rebuild, and visible retry remain pending. The first live request produced no model response or workspace changes.

## 2026-11-01 full harness audit: verified engine stabilization milestone

- Committed as `f3809e82ed6266e25ac50722535d1d9bb3030ffe` on `workbench/approval-tool-continuation`; paired app pin is updated to this revision.
- Full engine suite: **251 passed**. The run covers tool schema validation, context/request construction, task checkpoint continuity, tool batching, operation streaming, MCP, cancellation, permission policy, and continuation outcomes.
- Fixed the shared JSON Schema traversal defect that treated arbitrary property names as unsupported schema keywords; restored small-model guidance that explicitly carries actionable plans into implementation; removed the oversized fixed required-tool bundle while retaining `load_tools` and deferred-schema discovery; made command-effect inspection count as structured progress; ensured direct `AgentEngine.run` creates its own durable checkpoint; fixed multi-file path permission matching; and made request-only Ollama cancellation return without waiting on the blocked response reader or stopping the runtime/model.
- No selected model, reasoning default, context/output values, warm-model setting, or runtime placement was changed. This is source-suite verification only; paired sidecar/renderer/package/visible acceptance is tracked in the app repository.

## 2026-11-01 prompt-wording-free Automatic implementation evidence (verification queued)

- Removed keyword/regex classification of user prompts as implementation requests. Automatic mode recognizes implementation from an agent-owned open structured plan or observed mutation operations; explicit Implement remains authoritative, while Discussion and Plan remain non-implementing.
- Automatic mode no longer fabricates a host checklist based on selected words. Agent-authored plans are live in the UI and drive continuation; observed writes/patches activate post-change inspection and continuation requirements.
- Added regression assertions for structured evidence and the absence of wording classification. Tests, typecheck, live model, desktop app, and packaging checks remain queued.

## 2026-11-01 Automatic checklist from first structured write (verification queued)

- If Automatic mode reaches a structured file mutation without an existing agent plan, the host now emits and saves the implementation checklist before the operation is executed. The trigger is the actual requested tool operation, not guessed prompt vocabulary.
- This gives the user a visible plan even when the model skips `update_plan`, and the resulting write still enters the post-change inspection/verification flow. A regression checks plan-event ordering before the write event; it remains unrun.

## 2026-11-01 Structured Automatic task mode (verification queued)

- `update_plan` now accepts a structured `task_mode` (`discussion`, `plan`, or `implement`) for Automatic runs. The effective mode is stored in the task checkpoint and durable plan activity, shown in the Activity Trace, and restored with the task. Explicit UI intent remains authoritative over any model-supplied mode.
- An open plan-only checklist does not trigger implementation continuation. A structured write in Automatic mode still creates implementation evidence and activates review, even if the model had previously selected plan-only mode.
- A new user follow-up retains the plan, decisions, and completed work but resets an earlier Automatic mode selection to `auto`; the engine can classify the follow-up afresh. This prevents a previous plan-only choice from suppressing later requested implementation.
- Verification remains queued; these are source changes only.

## 2026-11-01 Automatic plan-to-implementation continuation (verification queued)

- In Automatic intent, an agent-authored nonempty `task_mode=plan` represents the planning phase, not plan-only completion. The implementation obligation remains until host-observed project work occurs; marking checklist items complete without a project change cannot produce a successful plan-only terminal result.
- Added a separate structured `plan_only` task mode for an explicit no-implementation request in Auto. Explicit Workbench Plan-only and Discuss intents remain read-only and authoritative. An implementation plan that cannot make progress is reported as implementation incomplete, not successful plan-only completion.
- Updated the Automatic-mode settings explanation to make the distinction visible. Added a queued engine regression for plan → write → inspect → completed plan → final response. No tests, typecheck, build, packaged app, or model run were performed.
- Auto now has a distinct structured `plan_only` state for a user-requested no-edit plan. A nonempty agent `plan` phase remains implementation work even if the model marks its items complete; explicit UI intent still overrides the model state. The paired sidecar persists the mode and plan provenance into the session activity/checkpoint. Regressions are authored and queued across engine and sidecar; none have run.

## 2026-11-01 Sticky implementation obligation (verification queued)

- Task checkpoints now persist whether implementation was requested. Auto follow-ups retain this state across compaction/session recovery; explicit Discuss/Plan follow-ups clear it, and explicit Implement sets it. Continuation progress signatures and the compacted checkpoint include the field so a completed-looking plan cannot erase an interrupted implementation request.
- Queued regressions cover checkpoint persistence, explicit read-only reset, and state-based continuation. No tests, typecheck, build, UI, package, or model run was performed.
- Legacy checkpoints without plan provenance migrate using saved item markers first and the saved `plan`/`implement` mode when markers are absent. Explicit Workbench-owned rows remain Workbench plans.
- In Auto, a structured `plan_only` classification can clear a sticky implementation obligation only before any mutation, observed file change, or command side effect. After effects begin, the task remains obligated to inspect and report them safely.
- Plan status interpretation is shared by continuation, Workbench checklist advancement, and checkpoint `next_action`. Unknown states remain open across compaction/recovery rather than being kept active with no resumable action.
- New saved decisions count as structured continuation progress alongside plan, acceptance criteria, operation, and verification changes; repeating an unchanged decision does not create a new checkpoint entry.

## 2026-11-01 Saved plan provenance and resume continuation (verification queued)

- Task checkpoints now persist whether their current plan came from the agent or the Workbench. Session restoration restores that source, and open agent plans or Workbench implementation checklists in Automatic mode re-establish their implementation obligation before the first resumed inference request.
- Compaction includes plan provenance, and continuation progress signatures include it. Explicit Discuss/Plan UI intents still override resumed implementation state. Added queued checkpoint provenance and restored-plan continuation regressions. No tests, typecheck, build, packaged app, or model run were performed.
- Continuation treats only explicit terminal plan states as closed. New or model-specific status labels therefore stay open instead of silently ending implementation; an explicit `deferred` state remains closed.
- Observable-progress comparison now includes stable plan fields and acceptance criteria. Repeating an identical `update_plan` is still a no-op, while adding work, advancing a status, or changing acceptance criteria permits another continuation without a fixed pass limit.

## 2026-11-01 Missing final response recovery (verification queued)

- The engine now treats empty assistant content as incomplete after a tool-free model response. It requests a user-visible terminal summary and checks for observable progress; if the same state repeats, it fails with `response_missing`, preserving completed operations and verification instead of reporting generic success.
- A blank assistant bubble is not added to the transcript. The complete response, blocker, and terminal status remain recoverable; this adds no overall deadline and does not change model settings.
- The continuation progress signature includes durable task mode and host-observed non-plan tool results, file changes, and verification. `update_plan` calls themselves do not count as implementation progress, so repeatedly rewriting a plan cannot keep a no-work loop alive; plans remain fully expandable and are not capped.
- Regression coverage is authored but not run.
- Added helper, schema, and sidecar-persistence regressions. All tests remain queued and unrun.

## 2026-11-01 budget-safe checkpoint projection (verification queued)

- Oversized checkpoints now compact only their inference projection against the requested budget. Long expandable plans preserve a usable outline where possible, with omitted plan entries/details recorded explicitly and the complete canonical plan unchanged for paged retrieval through `read_task_checkpoint`.
- Oversized goal/status prose and low-priority history are reduced only in the inference copy, with omission metadata. Tiny-budget fallback preserves task identity and omission status when possible; the engine does not change the selected model context or output limit.
- Added regressions for large evolving plans, strict projected size, explicit omissions, and canonical checkpoint preservation. Tests remain queued and were not run.

## 2026-11-01 structured user clarification (verification queued)

- The desktop engine exposes `ask_user` only when the host supplies an answer handler; standalone/CLI registries do not advertise an unsupported tool. Plan-only sessions can use it because asking a requirement does not mutate the workspace.
- The tool is treated as a read-only clarification operation, still respects persistent hard-deny rules, and is explicitly excluded from file/command approval. The prompt asks only for materially consequential missing requirements and tells the model to continue from reasonable defaults otherwise.
- Engine prompt/catalog regressions are queued. No test was run.

## 2026-11-01 living plan progression and batch safety

- Starter checklist stages now carry stable phase identifiers instead of depending on display-text matching. Legacy title-only plans remain readable, the captured task brief starts complete rather than appearing as permanently pending, and plan-only presentation can complete from the host terminal event. The model can still replace, extend, and repeatedly revise its own plan; workbench progress will not overwrite an agent-owned plan. A queued regression covers phase metadata; tests remain intentionally unrun.
- The engine's current batch behavior supports multiple calls in one provider response and concurrent execution for validated, independent, read-only tools. Mutating file actions, commands, MCP, and permission-sensitive work remain sequential so approval and side-effect order stay inspectable. This boundary is explicit in the agent guidance and is not being broadened without a safe approval/rollback design.
- Finish-after-operation now drains and records a safe read batch that was already launched concurrently before stopping at the next model request. This prevents completed reads from being mislabeled as unexecuted when the user chooses to finish active work. A queued regression covers the race; tests remain unrun.
- Small-model guidance now explicitly encourages one-response batches for independent calls whose inputs are known, while warning against batching dependent actions and explaining the host's concurrent-read versus ordered-side-effect behavior. Prompt regression coverage is queued and unrun.

## 2026-11-01 unpublished continuity source work

- Added an app-wide persistent permission-rule evaluator for exact/wildcard tools, project-relative path globs, and command prefixes. Deny wins over allow and run auto-approval; ask rules force the ordinary approval UI; absent rules preserve existing behavior. Rules are captured with the run and checkpointed; the paired Electron source adds a Settings editor. Regression cases are queued and unrun; Windows process containment remains a separate pending gap.
- Seeded implementation checklists now advance from host-observed reads, writes, commands, and post-change inspection. Model-authored plans take ownership when `update_plan` is called and are never overwritten by automatic progress. Acceptance criteria remain attached to host progress events. Queued regression covers progression and agent-plan ownership; it has not been run.

- The inference copy of an oversized task checkpoint is now budgeted independently from the configured model context. It preserves the full saved checkpoint, active goal/plan/settings, newest operation references and recent updates, and reports each omitted history category. The compaction event exposes these counts to the desktop so the user can see what was summarized. Context/output/reasoning settings are unchanged. Regressions are queued and unrun.

- Verification outcomes now retain an explicit `command_incomplete` state for cancelled, partial, uncertain, or otherwise incomplete command runs, even when the project also has observed file changes. The command result is included in the verification record. Queued regression coverage was added; no test was run.

- Tool-batch events now include the inference request index so the desktop activity layer can settle streamed per-call preparation rows against the exact request. A regression is queued; no verification was run.

- Added a factual task checkpoint module for continuing the same user task across turns and compaction, retaining plan, captured settings, completed operation references, changed files, and verification. Older flat checkpoints migrate forward; terminal tasks remain in local checkpoint history.
- Compaction injects the checkpoint alongside the configured system prompt and recent complete-turn tail, and emits omitted/retained turns plus checkpoint inclusion and estimated size. Context/output/reasoning values remain unmodified.
- The Ollama stream adapter now retains prompt_eval_cached_count when the provider returns it.
- Added queued checkpoint migration/continuation and cache-counter regression cases. No tests, typecheck, package, or live model request were run for this source work.
- The paired Electron sidecar must pass session-owned checkpoint state into AgentEngine and durably mirror its context-compaction metadata. Update app core pins only after this engine milestone is committed. See the paired app's docs/SESSION_CONTINUITY_IMPLEMENTATION.md for the full fault-injection and visible acceptance queue.

## 2026-11-01 task intent and read-only planning

- Added persisted `auto`, `discuss`, `plan`, and `implement` task intents to the shared engine contract. Explicit Plan and Implement tasks receive a Workbench-authored checklist before inference; Automatic no longer relies on prompt keyword matching and uses agent-authored structured plans and observed project operations to identify implementation work. The requested goal is preserved in the explicit checklist, and model `update_plan` calls refine the live plan.
- The starter checklist refers to the captured task brief instead of copying its full text a second time into the prompt checkpoint, preserving input budget for project context and work.
- Plan and Discussion intents run with the engine's read-only access policy. Read-only access now omits write, patch, and command schemas from the model catalog and hides MCP tools for that run; runtime enforcement remains in place if the model submits an out-of-catalog tool call.
- The shared engine also suppresses passed MCP schemas whenever the effective workspace access is `plan`, so the read-only boundary applies to the legacy engine entry point as well as the Electron sidecar.
- The existing implementation-request classifier now lives in the checkpoint/workflow module so host plan seeding and engine continuation decisions use the same wording rules. Task intent is added to the assembled prompt without modifying model reasoning or token settings.
- Added queued coverage for intent guidance, automatic plan seeding, explicit planning, and the read-only tool catalog. No test suite or typecheck was run. The Electron sidecar integration and app revision pin remain pending until the shared engine commit lands.

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

The linked [source audit and redesign plan](docs/AUDIT_AND_REDESIGN_PLAN.md) is a historical review of the Tkinter prototype at `2e10b37`, not the current defect list or release gate. For current paired-source status, evidence, backlog, and release gates, see the Electron app repository's `docs/MASTER_ROADMAP.md`, `docs/CORE_HARNESS_READINESS.md`, `docs/PRODUCT_BACKLOG.md`, and `docs/ACCEPTANCE_LOG.md`.

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

## 2026-11-01 explicit skill access controls in progress

- `ProjectKnowledge.skills` can filter by project/user source. A disabled skill tool is omitted from the model request and direct engine invocation is rejected; selected source lists are captured with each immutable turn.
- Standalone engine defaults preserve the prior skill behavior for existing CLI callers. The paired desktop app now adds Workbench-owned enable/source controls and persists them per app/session. It does not read or modify Codex/OpenCode skill settings.
- Regression cases cover source filtering, disabled-tool exclusion, and turn capture. They are queued with the broader final gate and have not been executed.

## 2026-11-01 observed workflow outcomes in progress

- Terminal coding turns now record an outcome derived from host-observed tool results: discussion, plan-only, blocked, changed, inspected, command-completed, or command-failed. A successful command is deliberately not called a passing test or verified project.
- The outcome is stored in the task checkpoint and emitted with the terminal event. Checkpoint completion records the outcome separately from the transport/run status.
- Regression cases cover each classification and checkpoint persistence. They remain queued and have not been run.

## 2026-11-01 implementation completion evidence in progress

- An implementation-intent turn that returns without a host-observed project change now ends as `implementation_incomplete`, rather than being mislabeled as discussion. Explicit Implement with read-only access reports blocked; Auto with read-only access remains plan-only.
- Verification records carry a compact evidence contract: whether a project change was observed, whether a post-change inspection completed, and the results of approved commands. A successful command is explicitly not a test-pass claim.
- After a successful change, the engine's bounded continuation now asks for a post-change inspection even when a command already ran. If the model still does not inspect, the terminal record remains unverified and names the missing evidence.
- Regression tests cover outcome precedence and evidence records. They are queued; no tests or model calls were run for this change.

## 2026-11-01 prompt provenance in progress

- The engine now emits a compact provenance manifest immediately before each provider request, describing that exact request's messages, conservative input estimate/budget, compaction notice and task-checkpoint inclusion, project instruction paths/sizes, retrieved project-relative file paths, enabled/available/catalog-included skills, retained skill-load results, and exact tool/MCP names.
- The manifest contains source metadata and sizes, not duplicated prompt text or MCP credentials. It is stored as a durable activity event so the same inspection can be reopened with the session.
- Pure engine cases cover prompt-source accounting, exact skill catalog inclusion, retained skill results, and omission of prompt content. These are queued and unrun.

## 2026-11-01 approved-command side-effect evidence in progress

- Implementation commands can change project files without using the structured file tools. The engine now treats such effects as possible, records the exact approved command arguments and later inspection target, and requests one bounded post-command workspace inspection before it completes when no later inspection exists.
- A command-only implementation remains `implementation_unverified` even if a later inspection ran, because the host has no structured write evidence identifying exactly what changed. A failed command remains `command_failed`; the UI warns that partial side effects may remain. Successful command exit is still not represented as a test pass.
- Engine and renderer regressions cover this path and are queued for the final verification gate. No test, model, package, or UI run was performed for this source change.

## 2026-11-01 task-specific acceptance criteria in progress

- `update_plan` now accepts optional concrete acceptance criteria alongside checklist items. Criteria are retained in the active task checkpoint and its compaction rendering, while legacy calls that only send checklist items remain valid.
- The source distinguishes agent-defined criteria from the workbench's empty starter state. A new task publishes an explicit empty criteria snapshot so restoring a session cannot reuse criteria from an older task.
- Code review also found that `AgentEngine.run` reads `ToolRegistry.plan` before any plan update, but the registry had not initialized that field. It is now initialized to an empty list; a queued regression asserts the pre-update state.
- Engine checkpoint, schema, sidecar journal replay, worker, renderer restore, and Plan-panel regressions have been added. All are queued; no tests, typecheck, package, app launch, or inference was run.

## 2026-11-01 uncertain command termination

- An approved command canceled after launch or terminated on timeout may already have written files. These results now use an `uncertain` operation state rather than implying a clean cancellation or definite failure; they trigger the existing bounded post-effect inspection path. Commands denied or canceled before launch remain non-side-effecting.
- The renderer warns on uncertain command results, and a queued engine regression distinguishes in-flight cancellation/timeout from cancellation before execution. No tests or runtime commands were run.
- The compaction checkpoint now explicitly requires inspecting uncertain operation output and current workspace state before requesting or repeating that operation; it says uncertain work is never automatically replayed. A queued continuity regression covers this instruction and the durable result reference.

## 2026-11-01 provider request and returned usage attribution

- Each runtime now attaches a monotonically increasing request index and emits provider/API route, selected model versus API model ID, stream/tool count, exact context/output/reasoning/keep-alive fields, runtime request options, and input-estimate method immediately before dispatch. llama.cpp also reports its already-loaded runtime context and reasoning mode when available. No request setting is modified.
- The request evidence also includes non-option body fields that affect behavior (Ollama's optional `think` override and llama.cpp's `stream_options`), keeping their absence distinct from their presence.
- The Ollama sidecar wrapper attributes provider-returned token and timing counters to that same request index. Mocked Ollama and llama.cpp coverage was added; it is queued and has not been run.

## 2026-11-01 request-only cancellation while awaiting headers

- Both Ollama and llama.cpp inference now use a direct HTTP(S) stream transport that registers the active connection before request dispatch. Cancel shuts down only that socket, including while blocked waiting for response headers; it does not stop or unload the provider model.
- DNS/TCP/TLS establishment has a 10-second connection-establishment bound. After connection establishment, request upload, response headers, and model generation have no deadline, preserving long local-model waits. This is not an overall inference timeout.
- Request activity records prepared, dispatched/waiting-for-headers, headers received/status and wait time, response stream completion, failure phase, or cancellation. A local delayed-header regression was added; it is queued and has not been run.
- llama.cpp stream events now keep reasoning and user-facing generation as separate phases, matching Ollama: reasoning-only deltas do not show the task as generating, and the phase changes when visible answer text or a tool call arrives. A queued regression covers the transition.

## 2026-11-01 uncertain MCP completion safety

- Once an MCP tool call is dispatched, cancellation or a lost/failed response is now recorded as `uncertain`; the remote server may have applied side effects even though the app did not receive a result. User-visible errors omit raw exception text that might contain endpoint credentials.
- The engine stops the remaining calls from that same model tool batch after an uncertain MCP result. Those calls are persisted as `blocked` with an explicit not-executed result; no later call or automatic retry is made.
- The uncertain operation and its exact arguments/result are retained in the task checkpoint and sidecar run state, and journal replay reconstructs the warning. Added queued engine, status, and persistence regressions. No tests or runtime checks were run.
- The MCP adapter regression additionally injects a post-dispatch timeout containing credential-like text and requires an uncertain result that omits the raw exception message. It is queued and unrun.

## 2026-11-01 streamed structured-tool preparation

- Ollama and llama.cpp now emit per-call progress while streamed tool names and arguments are assembling. The event carries only provider, request/call indexes, current tool name, and argument character count; it never includes partial argument contents and cannot trigger a tool.
- The app renders a live “Preparing structured tool request · not dispatched” cue in chat, then replaces it with normal structured activity when the provider completes the call. Progress is transient and excluded from session journals; only complete structured calls and executed/blocked operation results are persisted.
- Queued mocked runtime and renderer-state regressions cover both adapters, preview updates/multiple calls, and the absence of partial argument content. They were not run.

## 2026-11-01 MCP schema capability visibility

- MCP discovery now performs a bounded recursive scan of each advertised input schema and records unsupported keywords, types, and non-local references before the model attempts the tool.
- When a schema uses features outside the engine validator, its MCP tool description explains which features are not validated and that calls using them fail closed. The raw schema remains intact, and runtime validation still rejects unsupported features rather than executing an unchecked call.
- The capability walk is limited by nesting depth and node count because MCP schemas are server-provided input. New unit and mocked-discovery regressions cover nested unsupported keywords, unsupported types/references, and the warning attached to a discovered tool. They are queued and unrun.
- This is explicit capability reporting, not expanded JSON Schema support. Add actual keyword semantics only with dedicated validation cases and preserve fail-closed behavior until then.
- JSON Schema `format` is treated as its default annotation vocabulary, not as an assertion. Common MCP schemas containing `format: date-time` or similar metadata remain usable, but the harness does not claim to validate the formatted value.
- Expanded validation for common 2020-era keywords: `if`/`then`/`else`, `dependentRequired`, `dependentSchemas`, `propertyNames`, `prefixItems`, `contains`, and its match-count bounds. A root schema preflight rejects unsupported keywords even when they sit in a conditional branch that would not otherwise be evaluated; this closes a fail-closed bypass in conditional schemas.
- Added queued cases for each conditional/dependency/array behavior and for nested unsupported-feature preflight. They remain unrun with the rest of the requested final verification batch.

## 2026-11-01 active-task compaction and saved tool-result recovery

- Compaction can now split an oversized active user turn only at complete assistant/tool exchange boundaries. It retains the original task prompt and the newest complete exchanges that fit, while the checkpoint preserves operation IDs and outcomes. Omitted exchange counts are emitted as compaction metadata; canonical session messages and full results are not modified.
- Added a read-only `read_operation_result` tool. It retrieves a complete result by checkpoint operation ID, or a user-selected character slice for large command/tool output. In the desktop runtime, lookup is scoped to the session captured by the active run. It is included in Plan/Discussion read-only catalogs.
- The system/checkpoint instructions tell the model to retrieve omitted output and inspect uncertain effects before retrying. The app stores and displays the number of omitted in-task exchange groups alongside omitted turns and the configured context/input budget.
- Queued regressions cover compaction of a single long task, complete call/result boundaries, full/ranged result recovery, captured-session scope, and persisted compaction metadata. They remain unrun.
- Standalone AgentEngine/CLI sessions now also retain completed operation results in engine memory for `read_operation_result`; the desktop's captured-session store remains the durable source and takes precedence. Cancelled, invalid, uncertain, blocked, and successful operation outcomes are eligible for lookup during the engine lifetime. This closes the advertised-tool gap when no app storage callback exists. Regression added and queued; not run.

## 2026-11-01 local runtime process telemetry

- Resource snapshots now report Ollama/llama.cpp worker process count, summed CPU, and resident memory separately from the Agent Workbench sidecar and whole-system readings. NVIDIA compute-process VRAM is matched by worker PID where available; unavailable attribution stays unavailable rather than appearing as zero.
- System GPU utilization, memory, and power remain device-wide; runtime worker VRAM is labeled as shared runtime process data and is not attributed to the selected model. Added mocked telemetry coverage; tests and hardware acceptance are queued and have not been run.

## 2026-11-01 approved command timeout behavior

- `run_command` and `run_shell` no longer impose a 120-second default or a one-hour ceiling. Omit `timeout_seconds` to wait for command completion or user cancellation; a caller can still request any positive explicit timeout. Tool descriptions and agent guidance explain this behavior.
- The retained CLI approval preview now displays the actual command and says whether the operation has no deadline or an explicitly requested timeout; it no longer shows a false 120-second default.

## 2026-11-01 selected Ollama token controls are authoritative

- Ollama request construction now prevents generic `runtime_options` from overriding the selected temperature, context (`num_ctx`), or output cap (`num_predict`). Non-conflicting runtime options still pass through.
- Any conflicting persisted option values are recorded in request provenance as ignored overrides so the user can see why the exact payload differs from stale configuration. A queued request-payload regression covers context/output/temperature collisions and safe pass-through of `num_thread`; no tests or model requests were run.
- Added queued validation coverage for no implicit timeout, large explicit values, and invalid values. No command or long-running task was launched.

## 2026-11-01 continuity retrieval and responsive project reads

- Added `read_task_checkpoint`, a read-only paged tool for the complete saved current plan, decisions, user updates, operation references, changed files, verification, and prior task history. The model is instructed to retrieve checkpoint sections when compaction reports omissions; canonical checkpoint content remains complete on disk.
- Project listing, text search, file reads, and relevance retrieval now observe the shared cancellation event. Text search reads lines incrementally instead of materializing each full file, and cancellation produces an explicit cancelled result; already active reads can finish cooperatively without stopping Ollama.
- Added `read_conversation_history` for paged recovery of omitted saved user/assistant/tool transcript, including emitted assistant reasoning; the system prompt is excluded. The engine points to this and `read_task_checkpoint` when compaction has omissions.
- Added queued regressions for checkpoint paging, transcript/reasoning recovery, and cancellation during read operations. They have not been run. The app still requires an updated engine pin before this revision is consumable.

## 2026-11-01 interrupted Ollama stream handling (source only)

- The streaming adapter no longer treats EOF as successful completion unless Ollama supplied its terminal `done: true` record. A truncated stream raises a provider error with the `streaming_response` phase; text and reasoning already emitted remain in the desktop transcript and run checkpoint for recovery. This prevents partial reasoning from being mistaken for a complete answer and fed back into the continuation loop as a completed response.
- Added a regression for EOF after partial reasoning. The test is queued and unrun; no live request, selected setting, timeout, or model process was changed.

## 2026-11-01 authoritative plan tool results

- `update_plan` now returns the full authoritative current checklist, task mode, acceptance criteria, and durable decisions after each accepted revision. The model can verify current state instead of blindly repeating an update when the generic “Plan updated.” result provides no state. Planning remains expandable with no pass or item cap.
- Focused task-checkpoint and operation-stream suites: 48 passed.
- Full engine suite before the combined reasoning/tool-call regression: **252 passed** in 47.19 seconds with one existing Starlette deprecation warning. One earlier full run timed out in the response-header cancellation test under active model load; that test passed standalone, in its module, and in the full rerun. Keep the transient recorded for continued cancellation reliability review.
- Added a streaming regression for a default-reasoning response that first emits `thinking` and then returns a structured `write_file` call in the same assistant response. The focused streaming suite passes **7 tests**; the full engine suite now passes **253 tests** with one existing Starlette deprecation warning.

## 2026-11-01 implementation pacing follow-up

- User feedback from the interrupted Flint acceptance: the model spent too long revising/planning and should move into actual project development sooner, roughly 10–20% less planning as a directional preference. The request preserves full planning scope, evolving plans, acceptance checks, retries, tool use, and runtime; it does not authorize a fixed plan-pass or task-time cap.
- Added shared standard/small-model agent guidance: plan only enough to identify the next safe action, use the current authoritative plan/checkpoint rather than restating it, start tool-driven implementation once requirements and access are clear, and expand the plan when evidence changes the work. Added a regression assertion; it is queued and unrun while the user is using the PC.
- The current visible Flint task was intentionally cancelled by the user for a break. Its journal preserves the partial 23,477-character reasoning message and terminal `cancelled` status. The disposable project still contains only `.agent-workbench` session data; no website source files or writes were made. This run is interrupted acceptance evidence, not a model-quality verdict.

## 2026-11-01 plan-to-action handoff and provider finish reason

- Responding to the live acceptance evidence and user correction that the agent needs to implement more of what it plans, `update_plan` now returns whether authoritative state changed, the first open checklist action, and mode/access-aware execution guidance. An unchanged plan result points back to the current next step rather than inviting another plan read/revision. This is guidance from saved structured state, not a cap on planning, scope, iterations, tools, or runtime. Regression authored; queued unrun.
- Ollama `done_reason` and llama.cpp OpenAI `finish_reason` now pass through request completion so the app can distinguish provider-reported `length`, `stop`, `tool_calls`, or other reasons. This supports diagnosing output-limit exhaustion when reasoning and implementation share the selected budget; it does not alter any selected limit. Stream, persistence, worker, and activity-renderer regressions are authored and queued unrun.
- The visible Flint task was cancelled by the user during a game break. Its saved final assistant message contains 23,477 characters of partial reasoning and no tool calls or final content; no source files were written. This run therefore gives evidence of prolonged reasoning without implementation before cancellation, but cannot establish what the model would have done if allowed to finish.

## 2026-11-01 next-action implementation handoff

- The engine's continuation after a planning-only model response now carries the exact saved next open plan item into the next request and instructs the model to execute it through a suitable structured tool, inspecting only when needed. With no saved item, it directs the model to inspect the selected project and identify the first concrete implementation action instead of repeating the full user prompt.
- The shared agent guidance now explicitly asks for the first useful inspection/change after adequate scoping and the next open plan step immediately after plan publication, while preserving reasoning defaults, scope, planning freedom, approval requirements, context/output settings, and unbounded task runtime.
- Added regressions for the request-level next-step handoff and shared prompt guidance. They are queued and intentionally unrun until the agreed verification batch; no live model task was resumed.
- Root-cause evidence remains limited: the cancelled Flint run emitted 23,477 characters of reasoning and no tool call before the user cancelled it. It does not prove the model would never have implemented. This change targets the confirmed weak generic continuation handoff; verify with the queued mocked tests and a later user-approved visible run.

## 2026-11-02 automatic plan-to-implementation phase transition

- Root-cause review confirmed that an Automatic run labeled an agent-authored phase `plan` as active in its saved/UI state, even though write tools were still available. The engine depended on the model to change that state to `implement` itself, so there was no host-owned mode transition after planning.
- Automatic intent now promotes a non-empty agent-authored `plan` phase to `implement` when saving the plan update. The plan remains editable and expandable, and tools remain subject to the selected access and approval policy. Explicit Plan intent and `plan_only` remain read-only.
- Added a regression for the automatic phase promotion, including the saved checkpoint and emitted plan state. It is queued and unrun; no model request or app launch was started.

## 2026-11-02 same-task steering source audit

- Engine commit `39ff5495256e12f5f7e43232f269e5ace8d1ff88` adds an idempotent durable user-update checkpoint and a steering callback at provider-safe boundaries. A user update arriving during final prose is delivered before task completion and causes the same task to continue with another request. Workbench-only transcript metadata is removed from provider payloads while retained in saved history.
- The paired Electron app commit `d94f52af7d0e046186c8961f38ed0380f26e4ba2` persists steering against the active session and captured workspace, keeps queue-next separate, restores pending/delivered status, and refuses steering after finalization. Steering never grants approval or replays completed operations.
- Source review found that the sidecar reads steering commands while the model worker is active; state locking is around checkpoint/event updates, not the duration of inference or commands. Mirrored disk persistence remains synchronous inside the lock, so file contention could delay acknowledgement. Validate this with the queued storage fault tests and visible active-command workflow before calling the interaction reliable.
- Engine and app regression cases are committed but not run for these revisions. No inference, app launch, or test was run for this audit. The safe-boundary contract cannot change a generation already in progress; it applies the new direction before the next model request.

## 2026-11-02 scoped external-folder file access

- Engine permission rules now accept concrete absolute folder scopes as `external_directory` rules ending in `/**`. Rule matching is case-insensitive and literal on Windows paths; nested `deny` wins over `ask` and `allow`. Globbed roots are not accepted, preventing accidental broadening through wildcard patterns.
- The selected folder's structured file tools (`list_files`, `search_files`, `read_file`, `write_file`, `append_file`, `edit_file`, and `apply_patch`) work when the external scope matches. `ask` prompts for each external path operation; `allow` is an explicit persistent grant. Missing/unavailable folders, ungranted paths, and symlink/junction traversal fail closed. Copy mode blocks external structured writes because they would bypass staged copy review.
- The agent is told about the external access policy, and tool descriptions identify supported external paths. Search/list display absolute external paths so results can be used in follow-up file calls.
- Focused permission/tool suites: **47 passed**. Full engine suite, worker integration, package, and live visible acceptance remain queued. This does not contain PowerShell/commands or MCP; those can still act with host-user rights. App-side folder-picker wiring and full packaged approval-flow acceptance are pending.
# 2026-11-02 runtime-reported llama.cpp reasoning capabilities

- llama.cpp status now reads `/props` once per status request and derives reasoning-effort choices only from `chat_template_caps.reasoning_effort_levels`. Missing capability metadata stays `unknown`; no generic effort list or guessed model default is exposed.
- This keeps reasoning mode/effort separate from the optional per-request `thinking_budget_tokens` control. Budget remains unlimited by default and does not change `max_tokens` or impose a deadline.
- Focused runtime tests: **13 passed**, including exact reported levels and unknown/omitted levels. The app-side worker tests for unloaded and loaded GGUF capability reporting: **2 passed**. Broader suites, packaged UI, runtime-template compatibility, and live enforcement remain pending.
- When the loaded template reports effort levels, the model capability contract also exposes the llama.cpp reasoning-off control. Unknown templates do not receive a fabricated toggle. This supports Default/Off/exact-level selection without confusing qualitative effort with a numeric token budget.

# 2026-11-02 tool-catalog pagination under small response budgets

- Explicit `load_tools.limit` values now respect the response budget supplied by the engine, just like automatically sized pages. The requested count is honored when it fits; otherwise the response ends at the largest fitting page and returns `next_offset` for continued discovery. No fixed catalog/page cap was introduced.
- Regression was first run red (the 12-schema explicit page exceeded the 1,500-byte budget), then green after the fix. Focused page-selection tests: **2 passed**. `git diff --check` and Python compilation passed.
- This is engine source evidence only. Broader schema/context suites, paired sidecar/package tests, visible UI acceptance, and live Flint work remain queued.

# 2026-11-02 tool-catalog single-schema overflow handling

- If the first matching tool schema alone exceeds the remaining response budget, `load_tools` now returns a direct, actionable size message instead of emitting an oversized page that can trigger another context failure. The message does not alter model context/output settings; it recommends narrowing discovery or changing settings explicitly.
- Focused page selection, explicit-size budget, and single-schema overflow cases: **3 passed**. Python compilation and `git diff --check` passed. Broader context/schema and paired app/package verification remain queued.

- **Context/tool batch follow-up:** the complete `tests/test_context.py tests/test_context_budget.py` pair passes (**21 passed**) against the current engine, covering 4K/8K context budgeting, compaction boundaries, deferred tool discovery, requested page sizes, oversized individual schemas, and result budgeting. `tests/test_engine_tool_batch.py` passes (**4 passed**) for batch dispatch and uncertainty handling. These remain focused source checks; full engine and packaged acceptance are still queued.
- **Steering during approval:** a new operation-stream regression pauses a model-requested write at per-action approval, injects a same-task user update while the decision is pending, denies the write, and verifies the update is delivered only after the denial result is in the next provider request. The project remains unchanged and approval is requested exactly once. Engine steering/checkpoint cases: **4 passed**; Python compilation and `git diff --check` passed. Sidecar crash/cancel/mirror-failure and visible approval/steering behavior remain open.
- **Approval-mode prompt accuracy:** the model prompt and `run_command`/`run_shell` tool schemas now describe the configured host mode (auto, per-action, per-task, or per-session) instead of claiming every command always needs manual approval. Persistent deny remains absolute, persistent ask still invokes the host decision, and shell host-user access is explicitly not OS-sandboxed. The engine passes the selected mode into the prompt; focused instruction/injection tests: **9 passed**. This does not alter host approval decisions or settings.

## 2026-11-02 dialect-aware MCP tool argument validation

- Replaced the hand-written JSON Schema subset validator with the maintained `jsonschema` implementation. Tool schemas select the declared Draft 3/4/6/7/2019-09/2020-12 validator; schemas without `$schema` use 2020-12. Unknown/custom dialects fail closed unless explicitly among the reviewed built-ins.
- Schema discovery and runtime validation reject remote `$ref`/`$dynamicRef` resolution, unresolved local references, unknown assertion keywords, and unknown required vocabularies. Validation inspects bounded argument trees, rejects non-finite numbers, and returns keyword/path errors without echoing argument values. `format` remains annotation-only. No shell or filesystem permission behavior changed.
- Added `jsonschema>=4.26,<5` to the engine dependency set; app-sidecar dependency synchronization and package integration are in progress.
- Focused validation and MCP discovery checks: **19 passed**. Python compilation and `git diff --check` passed. Coverage includes 2020-12 conditionals, dependencies, property names, arrays, unevaluated keywords, booleans, local refs, external ref rejection, Draft 7 tuple arrays, unknown vocabulary, malformed arguments, MCP capability reporting, and provider tool-result shape. Full suites, package verification, and visible model acceptance remain deferred.
- A follow-up discovery audit found Draft 2019-09 `$recursiveRef` was fail-closed by runtime resolution but not listed as an unsupported remote reference at discovery. It is now explicitly rejected/reported alongside `$ref` and `$dynamicRef`; the focused validation/MCP files pass **20 tests** with Python compilation and `git diff --check` clean. No remote schema fetching is enabled.

## 2026-11-02 progressive file scan activity

- Long `list_files` and `search_files` operations now emit compact `tool_progress` updates with inspected-file counts, matches, and bytes read where applicable. They retain the existing cancellable traversal, pagination, ignore handling, and optional explicit scan budgets; progress sampling is observational only and adds no scan cap.
- The paired desktop sidecar integration stores these updates on the existing operation activity row so chat and Activity Trace can show the same operation while it runs and after session recovery. No model output or partial tool arguments are exposed through these progress events.
- Focused engine file-tool tests pass (**37 passed**). Sidecar recovery/renderer checks are pending the paired app integration; full suites and package/visible acceptance remain deferred.
