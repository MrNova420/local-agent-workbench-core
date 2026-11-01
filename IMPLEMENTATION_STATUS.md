# Implementation and verification status

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
