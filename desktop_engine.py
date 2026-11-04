"""Minimal runtime, safe tool registry, event stream and project context for the desktop app."""
from __future__ import annotations
import codecs, copy, hashlib, json, os, queue, re, tempfile, threading, time, urllib.request, uuid
import stat
import weakref
from concurrent.futures import ThreadPoolExecutor, as_completed
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable
import math
from pathlib import Path
from urllib.parse import unquote
from project_knowledge import ProjectKnowledge
from project_walk import iter_project_files
from mcp_support import MCPRequestCancelled, MCPRequestUncertain
from prompt_manifest import build_prompt_manifest
from cancellable_http import interrupt_connection, open_http_stream
from task_checkpoint import begin_task, checkpoint_omissions, current_task, finish_task, initial_task_plan, plan_item_is_open, record_assistant_status, record_blocker, record_decision, record_implementation_requested, record_operation, record_plan, render_checkpoint
from tool_schema_capabilities import schema_validator_class, unsupported_schema_features
from permission_policy import _paths, external_directory_decision, permission_decision, permission_rules_for_workspace, validate_permission_rules
from windows_process_job import WindowsProcessJob
from typing import Any, Callable

MODEL = "hf.co/mradermacher/Ornith-1.5-9B-uncensored-GGUF:Q4_K_M"
CONTEXT_MAX = 262_144
IGNORED = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".agent-workbench", "dist", "build", "target", "Library", "Temp"}
TEXT_EXT = {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".txt", ".ts", ".tsx", ".js", ".jsx", ".cs", ".gd", ".rs", ".go", ".html", ".css", ".cpp", ".h", ".xml"}
PROJECT_LIST_SCAN_LIMIT = None
_APPEND_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_APPEND_LOCKS_GUARD = threading.Lock()
_CONTINUATION_GUIDANCE_MARKER = "\n\n[Current Workbench continuation]\n"

def approval_policy_guidance(mode: str) -> str:
    """Describe the host's actual approval behavior without implying every action is manual."""
    normalized = mode if mode in {"auto", "per-action", "per-task", "per-session"} else "per-action"
    mode_guidance = {
        "auto": "Approval mode: auto; eligible actions run automatically unless a persistent rule asks or denies.",
        "per-action": "Approval mode: per-action; a separate host decision is required for each eligible action.",
        "per-task": "Approval mode: per-task; the first eligible action in each task requires a host decision, and later actions are covered only after that decision is granted.",
        "per-session": "Approval mode: per-session; the first eligible action in each session requires a host decision, and later actions are covered only after that decision is granted.",
    }[normalized]
    return (
        mode_guidance + " Persistent deny rules always block; persistent ask rules still require a host decision; "
        "persistent allow rules apply only to matching actions. Access mode and project boundaries still apply. "
        "Submit actions through structured tools instead of asking permission in chat text. If the host requests a decision, "
        "wait for its result. Shell commands run with host-user permissions and are not OS-sandboxed."
    )

class RequestCancelled(Exception):
 def __init__(self,message='Cancelled',partial_message:dict|None=None):
  super().__init__(message);self.partial_message=partial_message

class UncertainFileOperationError(RuntimeError):
 """A failed file operation could not prove its partial effects were rolled back."""

class InferenceStreamError(RuntimeError):
 def __init__(self,message:str,partial_message:dict|None=None):
  super().__init__(message);self.partial_message=partial_message

def _project_directory_listing_state(path:Path)->tuple[Any,...]|None:
    """Cheaply detect path/ignore changes that would invalidate a paused walk."""
    try:
        if path.is_symlink() or getattr(os.path,"isjunction",lambda value:False)(path):return None
        directory=path.lstat()
        if not stat.S_ISDIR(directory.st_mode):return None
        ignore=path/".gitignore"
        if ignore.is_symlink() or getattr(os.path,"isjunction",lambda value:False)(ignore):
            ignore_state=("linked",)
        elif ignore.is_file():
            ignored=ignore.stat()
            ignore_state=(ignored.st_dev,ignored.st_ino,ignored.st_mtime_ns,ignored.st_ctime_ns,ignored.st_size)
        else:
            ignore_state=None
        return (directory.st_dev,directory.st_ino,directory.st_mtime_ns,directory.st_ctime_ns,directory.st_size,ignore_state)
    except (OSError,RuntimeError):
        return None

def _append_path_lock(path:Path)->threading.Lock:
    try:
        identity=path.stat()
        key=f"inode:{identity.st_dev}:{identity.st_ino}"
    except OSError:
        key=os.path.normcase(str(path.resolve(strict=True)))
    with _APPEND_LOCKS_GUARD:
        lock=_APPEND_LOCKS.get(key)
        if lock is None:
            lock=threading.Lock();_APPEND_LOCKS[key]=lock
        return lock

class _ProjectListCursor:
    """One lazy project listing continuation, scoped to one ToolRegistry run."""
    def __init__(self,base:Path,pattern:str,max_files:int|None=None):
        self.key=(os.path.normcase(str(base)),pattern.casefold())
        self.directory_states:dict[Path,tuple[Any,...]|None]={}
        self.match_index=0
        self.files_scanned=0
        self.pending:Path|None=None
        self.exhausted=False
        self.paths=iter_project_files(base,max_files=max_files,directory_observer=self.observe_directory)
    def observe_directory(self,path:Path)->None:
        self.directory_states[path]=_project_directory_listing_state(path)
    def is_current(self)->bool:
        return all(before is not None and _project_directory_listing_state(path)==before
                   for path,before in self.directory_states.items())

class _ProjectSearchCursor:
    """Resume adjacent search pages without reopening already-scanned files."""
    def __init__(self,base:Path,key:tuple[Any,...]):
        self.base=base
        self.key=key
        self._reset()
    def _reset(self):
        self.directory_states:dict[Path,tuple[Any,...]|None]={}
        self.file_states:dict[Path,tuple[Any,...]|None]={}
        self.paths=iter_project_files(self.base,directory_observer=self._observe_directory)
        self.current_path:Path|None=None
        self.current_offset=0
        self.current_line=0
        self.pending:tuple[Path,int,str]|None=None
        self.matches_seen=0
        self.next_offset=0
        self.exhausted=False
    def _observe_directory(self,path:Path)->None:
        self.directory_states[path]=_project_directory_listing_state(path)
    @staticmethod
    def _file_state(path:Path)->tuple[Any,...]|None:
        try:
            if path.is_symlink() or getattr(os.path,"isjunction",lambda value:False)(path):return None
            value=path.stat()
            return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
        except (OSError,RuntimeError):return None
    def is_current(self)->bool:
        return (all(before is not None and _project_directory_listing_state(path)==before
                    for path,before in self.directory_states.items()) and
                all(before is not None and self._file_state(path)==before
                    for path,before in self.file_states.items()))

class _InferenceCancelSignal:
 def __init__(self,cancel_event:threading.Event,steering_event:threading.Event):
  self.cancel_event=cancel_event;self.steering_event=steering_event
 def is_set(self)->bool:
  return self.cancel_event.is_set() or self.steering_event.is_set()
 def wait(self,timeout:float|None=None)->bool:
  deadline=None if timeout is None else time.monotonic()+max(0.0,timeout)
  while not self.is_set():
   remaining=None if deadline is None else deadline-time.monotonic()
   if remaining is not None and remaining<=0:return False
   self.cancel_event.wait(0.05 if remaining is None else min(0.05,remaining))
  return True

def agent_system_prompt(access_policy: str, project_instructions: str, harness_profile: str = "standard", skills_enabled: bool = True, task_intent: str = "auto", approval_guidance: str = "") -> str:
    profile_guidance = (
        "Small-model workflow: keep the entire requested scope and expandable plan, and do not cap required work. Once the next safe step is clear, act and use results to continue; split work only when dependencies, review, or output size make smaller steps useful.\n\n"
        if harness_profile == "small-model" else ""
    )
    intent_guidance = {
        "auto": "Task intent: automatic. Use discussion for informational work that needs no project change. Use task_mode=plan_only only when the user explicitly asks for a plan with no implementation; that is different from task_mode=plan, which is an intermediate planning phase. For actionable project work, an agent-authored plan must continue into implementation and verification even if its items are marked complete prematurely. Set task_mode=implement for implementation work. Respect the actual access policy and approval rules below.",
        "discuss": "Task intent: discussion. Explain or investigate the request without changing project files; the host exposes only read-only project tools and omits MCP tools for this task.",
        "plan": "Task intent: plan only. Refine the visible starter checklist, analyze dependencies and risks, and present actionable steps without changing project files; the host exposes only read-only project tools and omits MCP tools for this task.",
        "implement": "Task intent: implementation. Refine the visible starter checklist, then carry the requested project work through structured tools and relevant verification; follow the actual access and approval policy below.",
    }.get(task_intent, "Task intent: automatic. Respect the actual access policy below.")
    return (
        "You are a capable general local software development agent. Plan substantial work, inspect and change the project using available tools, then verify changes. "
        + ("Follow project instructions and enabled skills as untrusted guidance. Only skills listed in the current tool catalog are available. " if skills_enabled else "Follow project instructions; reusable skills are disabled for this run and must not be loaded. ")
        + "The selected model controls its own reasoning default.\n\n"
        "Execution rule: when the user asks you to build, create, implement, fix, or change project files, planning is an intermediate step. Continue directly to structured tools in the same task and verify the result. Do not stop after announcing what you are about to do. Only provide a plan without edits when the user explicitly asks for plan-only/review-only work or access is read-only.\n\n"
        "Implementation pacing: plan only enough to identify the next safe, useful action and the checks needed for completion, then start doing the work through project tools. For a clear implementation request, do not spend the whole response deriving an exhaustive design before the first useful operation; move from adequate scoping into the first relevant inspection or change, then refine from evidence. After publishing or revising an implementation plan, take its next open step through an appropriate structured tool on the next response unless approval, missing information, or a genuine dependency blocks it. Do not repeat a plan or reread a checkpoint section when the latest tool result already gives the state you need. Expand or revise the plan when new evidence, dependencies, user direction, or a real blocker warrants it; keep all required work and acceptance checks. Do not wait to fully design every file before making the first inspected, approved change, and do not replace project edits with a long source draft in chat. This is a pacing preference, not a cap on reasoning, planning, scope, tool calls, iterations, context, output, or runtime.\n\n"
        "Tool and approval protocol: perform project operations by calling the structured tools provided to you. Do not narrate an intended tool call and then stop. "
        "Batch independent tool calls in one response when their inputs are known; the host may run validated independent reads concurrently. Keep writes, commands, and MCP calls ordered under their normal approval rules, and do not batch actions that depend on earlier results. "
        "Commands are non-interactive: their stdin is isolated from the sidecar protocol and prompt-driven programs receive end-of-input. Use non-interactive flags when available; do not assume a command can ask follow-up questions. "
        "When the host provides ask_user, use it only when a missing requirement or user choice would materially change the implementation; keep working from reasonable defaults otherwise. It is for task clarification, never for permission to run a file change or command. "
        "Persistent Workbench tool permission rules are enforced by the host; a denied tool is unavailable for this task and must not be retried through another tool or MCP route. Hard-deny rules override run approval settings. Structured file tools stay inside the selected project unless a matching external_directory rule grants a concrete folder; an external_directory ask rule requires approval for each outside-project path action. Shell commands and MCP retain their separately described host capabilities. "
        "Do not ask for approval in chat text; submit the action through its structured tool and follow the configured host approval mode and persistent permission rules. "
        "When the request's tool set is context-adaptive, use load_tools to page through the available tool catalog and select any missing capability by exact name; selected schemas are added to the next model request. Deferred tools remain available through this mechanism and are not permission grants. "
        "Do not claim that an action ran unless its tool result confirms it. Put file contents in structured file-tool arguments, not in a chat message. For files too large for one response, create them with write_file and add ordered chunks using append_file; inspect the finished file. "
        "For focused changes to existing files, prefer edit_file with the exact old and new text; it refuses missing or ambiguous matches. Use apply_patch for structured or multi-file changes, and write_file for new or intentionally replaced whole files. "
        "When list_files or search_files says more results are available, continue with its exact next offset when needed; do not assume the first page is complete. "
        "Use run_command for a direct argv command without shell syntax. Use run_shell for Windows PowerShell or CMD workflows that require pipes, redirects, or script wrappers; it defaults to PowerShell and accepts an explicit shell choice. Both follow the configured host approval mode and persistent permission rules. "
        "For long builds, tests, installs, and other approved work, omit timeout_seconds unless a task-specific timeout is required; the host's Cancel control remains available. "
        "After a successful tool result, continue with the requested work and verification; finish only when complete or when a real blocker remains.\n\n"
        "Tool failure recovery: inspect the reported error and current project state, identify its cause, and correct the cause before retrying. A retry may adjust arguments, paths, command steps, or the implementation based on new evidence; do not repeat the same failed action without a reason. If an operation's outcome is uncertain, inspect for partial effects before retrying to avoid duplicate changes. Do not retry denied actions or route around a denial. Preserve completed work and continue the task after recoverable failures; report a concrete blocker when recovery is not possible. There is no fixed retry or iteration cap, and each retry must respond to observed evidence.\n\n"
        "For implementation work, after making edits inspect the resulting file or diff and run the relevant project check when one exists and is approved. A successful write alone is not verification. Report exactly which inspections/checks ran and whether they passed; do not claim unrun checks passed.\n\n"
        "For substantial or multi-file implementation work, create a visible update_plan checklist before editing and update it as phases finish. The plan is expandable: use add_items to add discovered tasks or independent workstreams without replacing existing items, and use items when intentionally revising the full checklist. Revise the plan when new evidence or user direction changes scope or priorities; do not keep reworking a plan for speculative improvements. Include task-specific acceptance_criteria as concrete, observable conditions for completion; do not claim them satisfied without evidence. Record concise user-relevant design decisions and assumptions in update_plan decisions when they should survive compaction; summarize conclusions and evidence, not private chain-of-thought. After compaction or a user follow-up, retain prior plans and progress, then continue or re-plan from that saved state rather than treating prior work as forgotten. When checkpoint omissions are reported, use read_task_checkpoint to recover omitted plan, decisions, updates, operation references, or verification; use read_conversation_history for omitted user/assistant transcript and emitted reasoning; use read_operation_result for full tool output and exact ranges. The complete transcript, emitted reasoning text, checkpoint, and operation results remain in saved session history.\n\n"
        + intent_guidance + "\n\n"
        "If the user denies an action, do not repeat or work around that denial; explain what was denied and stop dependent work unless the user asks to continue another way.\n\n"
        + profile_guidance + "Access policy: " + access_policy + "\n\n" + (approval_guidance or approval_policy_guidance("per-action")) + "\n\nProject instructions:\n" + project_instructions
    )

def _command_timeout(args:dict[str,Any])->int|None:
    """No implicit deadline: a command runs until exit or explicit user cancellation."""
    if "timeout_seconds" not in args:return None
    value=args["timeout_seconds"]
    if not isinstance(value,int) or isinstance(value,bool) or value<1:
        raise ValueError("timeout_seconds must be a positive integer when specified; omit it to wait for completion or cancellation.")
    return value

def _build_shell_argv(shell_type: str, command: str) -> list[str]:
    """Build an explicit non-interactive shell invocation without changing approval scope."""
    import shutil
    selected = shell_type or "powershell"
    if selected == "powershell":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise ValueError("PowerShell is unavailable; install PowerShell or use run_command with argv.")
        return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    if selected == "cmd":
        if os.name != "nt":
            raise ValueError("CMD is only available on Windows.")
        executable = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        if not executable:
            raise ValueError("CMD is unavailable; set COMSPEC or use run_command with argv.")
        return [executable, "/d", "/s", "/c", command]
    raise ValueError("shell must be 'powershell' or 'cmd'.")

def _validate_tool_arguments(value: Any, schema: dict, path: str = "$") -> str | None:
    """Validate tool arguments with the schema's declared dialect and no remote fetches."""
    if not isinstance(schema, dict):
        return f"{path} has an invalid tool schema."
    unsupported = unsupported_schema_features(schema)
    if unsupported:
        return f"{path} uses unsupported JSON Schema feature(s): {', '.join(unsupported[:4])}."

    # Bound both the untrusted schema (above) and model/provider-supplied
    # arguments before passing them to a recursive standards validator.
    stack = [(path, value, 0)]
    visited = 0
    while stack:
        value_path, current, depth = stack.pop()
        if depth >= 64:
            return f"{value_path} exceeds the supported tool-argument nesting depth."
        visited += 1
        if visited > 10_000:
            return f"{path} exceeds the supported tool-argument inspection size."
        if isinstance(current, float) and not math.isfinite(current):
            return f"{value_path} is not a finite JSON number."
        if isinstance(current, dict):
            stack.extend((f"{value_path}[{json.dumps(str(key), ensure_ascii=False)}]", item, depth + 1)
                         for key, item in current.items())
        elif isinstance(current, list):
            stack.extend((f"{value_path}[{index}]", item, depth + 1)
                         for index, item in enumerate(current))

    validator_class = schema_validator_class(schema)
    if validator_class is None:
        return f"{path} uses an unsupported JSON Schema dialect."
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        keyword = getattr(exc, "validator", None)
        return f"{path} has an invalid JSON Schema tool schema" + (f" ({keyword})." if keyword else ".")

    try:
        validator = validator_class(schema, registry=Registry())
        error = next(validator.iter_errors(value), None)
    except Unresolvable:
        return f"{path} references an unresolved local JSON Schema definition."
    except Exception:
        # Third-party schema implementations and MCP extensions can fail in
        # dialect-specific ways. Keep failures closed without reflecting
        # argument values or schema contents into the model conversation.
        return f"{path} could not be safely validated against the tool schema."
    if error is None:
        return None

    error_path = path + "".join(
        f"[{segment}]" if isinstance(segment, int) else f"[{json.dumps(str(segment), ensure_ascii=False)}]"
        for segment in error.absolute_path
    )
    keyword = str(error.validator or "false schema")
    return f"{error_path} violates the tool argument schema ({keyword})."

def _normalize_tool_history(messages: list[dict]) -> None:
    """Migrate persisted pre-protocol tool results to Ollama's native tool_name shape."""
    names_by_id={}
    for message in messages:
        if message.get("role")=="assistant":
            for call in message.get("tool_calls") or []:
                call_id=call.get("id")
                name=(call.get("function") or {}).get("name")
                if call_id and name:names_by_id[str(call_id)]=name
        elif message.get("role")=="tool":
            call_id=message.pop("tool_call_id",None)
            if not message.get("tool_name") and call_id:
                message["tool_name"]=names_by_id.get(str(call_id),"unknown_tool")

def _tool_activity_status(name: str, result: str) -> str:
    """Map known tool outcomes to truthful states without inspecting file contents."""
    text = str(result)
    folded = text.casefold().strip()
    if name in {"write_file", "append_file", "edit_file", "apply_patch"} and folded.startswith("uncertain:"):
        return "uncertain"
    if name.startswith("mcp__") and (folded.startswith(("uncertain:", "tool error:")) or "cancelled during mcp action" in folded or "canceled during mcp action" in folded):
        return "uncertain"
    if name in {"run_command", "run_shell"} and "[command cancelled by user]" in folded:
        return "uncertain"
    if folded.startswith("denied by user"):
        return "denied"
    if folded.startswith("blocked:"):
        return "blocked"
    if folded.startswith(("cancelled", "canceled")):
        return "cancelled"
    if folded.startswith(("tool error:", "command error:", "unknown tool:")):
        return "failed"
    if name == "read_file" and folded.startswith(("invalid start_line:", "invalid end_line:")):
        return "failed"
    if name in {"list_files", "search_files", "git_status", "git_diff", "write_file", "append_file", "edit_file"} and folded.startswith("invalid "):
        return "failed"
    if folded.startswith(("git diff unavailable:", "no git repository rooted", "requested lines ")):
        return "failed"
    if "results may be incomplete" in folded or "remaining output may be incomplete" in folded:
        return "partial"
    if name == "apply_patch":
        return "completed" if text.startswith("Updated: ") else "failed"
    if name == "write_file":
        return "completed" if text.startswith("Wrote ") else "failed"
    if name == "append_file":
        return "completed" if text.startswith(("Appended ", "No change: append content is empty.")) else "failed"
    if name == "edit_file":
        return "completed" if text.startswith("Edited ") else "failed"
    if name in {"run_command", "run_shell"}:
        if "[command timed out after " in folded:
            return "uncertain"
        match = re.search(r"\[exit (-?\d+)\]\s*$", folded)
        if match:
            return "completed" if int(match.group(1)) == 0 else "failed"
        return "failed"
    if name == "search_files" and folded.startswith("search query must not be empty"):
        return "failed"
    return "completed"

def _workflow_outcome(access: str, action_denied: bool, project_changed: bool, verification: dict[str, Any], plan: list[dict[str, Any]], task_intent: str = "auto", implementation_requested: bool = False, task_mode: str = "auto") -> str:
    """Describe only outcomes supported by host-observed tool activity."""
    if action_denied:
        return "blocked"
    status = str(verification.get("status", "not_verified"))
    if status == "implementation_incomplete":
        return status
    if status == "command_failed":
        return "command_failed"
    if status == "command_incomplete":
        return "command_incomplete"
    if task_intent == "discuss":
        return "discussion"
    if task_intent == "plan":
        return "plan_only"
    if task_intent == "implement" and access == "plan":
        return "blocked"
    if access == "plan":
        return "plan_only"
    if implementation_requested and not project_changed:
        contract = verification.get("contract", {})
        if status in {"command_completed", "command_failed"} or (isinstance(contract, dict) and contract.get("command_side_effects_possible")):
            return "implementation_unverified"
        return "implementation_incomplete"
    if status == "command_completed":
        return "command_completed"
    if status == "inspected":
        return "inspected"
    if task_intent == "auto" and not project_changed and task_mode == "discussion":
        return "discussion"
    if task_intent == "auto" and not project_changed and task_mode == "plan_only":
        return "plan_only"
    if project_changed:
        return "changed"
    if any(not (isinstance(item, dict) and item.get("phase") == "task_progress") for item in plan):
        return "plan_only"
    if task_intent == "auto" and task_mode == "auto" and not implementation_requested:
        return "no_action"
    return "discussion"

def _plan_has_open_work(plan: list[dict[str, Any]]) -> bool:
    return any(plan_item_is_open(item) for item in plan)

def _plan_requests_implementation(task_mode: str, plan_source: str, plan: list[dict[str, Any]]) -> bool:
    """Agent plan phases and open work drive Automatic implementation continuation."""
    if task_mode in {"discussion", "plan_only"}:
        return False
    if task_mode == "implement":
        return True
    if task_mode == "plan" and plan_source == "agent" and bool(plan):
        return True
    return plan_source == "agent" and _plan_has_open_work(plan)

def _restored_plan_requests_implementation(task_intent: str, task_mode: str, plan_source: str,
                                           plan: list[dict[str, Any]]) -> bool:
    """Restore implementation obligation from any saved open implementation plan."""
    return task_intent == "auto" and _plan_requests_implementation(task_mode, plan_source, plan)

def _saved_plan_source(task_mode: str, plan: list[dict[str, Any]], recorded_source: Any) -> str:
    """Recover plan ownership, including pre-provenance session checkpoints."""
    if isinstance(recorded_source,str) and recorded_source in {"agent", "workbench"}:
        return recorded_source
    sources = {item.get("source") for item in plan if isinstance(item, dict)
               and isinstance(item.get("source"),str)
               and item.get("source") in {"agent", "workbench"}}
    if "agent" in sources:
        return "agent"
    if sources == {"workbench"}:
        return "workbench"
    if plan and task_mode in {"plan", "implement"}:
        return "agent"
    return "workbench"

def _assistant_has_final_content(message: dict[str, Any]) -> bool:
    """Only user-visible assistant content counts as a completed response."""
    content = message.get("content")
    return isinstance(content, str) and bool(content.strip())


def _provider_message_history(messages: list[dict]) -> list[dict]:
    """Remove Workbench-only transcript metadata before sending provider JSON."""
    internal_keys = {"turn_id", "steering_id", "steering_status", "partial_response", "steering_interrupted"}
    return [
        {key: copy.deepcopy(value) for key, value in message.items() if key not in internal_keys}
        for message in messages if isinstance(message, dict)
    ]

def _implementation_evidence(task_intent: str, task_mode: str, plan_source: str, plan: list[dict[str, Any]],
                             project_changed: bool, command_side_effects_possible: bool,
                             mutation_attempted: bool = False) -> bool:
    """Infer implementation obligation from explicit UI intent and observed structured state only."""
    if task_intent == "implement":
        return True
    if task_intent != "auto":
        return False
    if task_mode in {"discussion", "plan_only"} and not (project_changed or command_side_effects_possible or mutation_attempted):
        return False
    return bool(project_changed or command_side_effects_possible or mutation_attempted
                or _plan_requests_implementation(task_mode, plan_source, plan))

def _continuation_progress_signature(task: dict[str, Any], verification_steps: list[dict[str, Any]],
                                     assistant_message: dict[str, Any] | None = None) -> str:
    """Compare observable progress, not elapsed time or a fixed continuation count."""
    result_driven_reads = {
        "list_files", "search_files", "read_file", "read_operation_result",
        "read_task_checkpoint", "read_conversation_history", "git_status", "git_diff", "git_log",
    }
    operations = task.get("operations", [])
    operation_state = []
    if isinstance(operations, list):
        for item in operations:
            if not isinstance(item, dict) or item.get("name") == "update_plan":
                continue
            name = item.get("name")
            fields = ["name", "status", "affected_files", "result_fingerprint"]
            # For successful/failed reads, a new result is evidence; a changed
            # search query that returns the same bytes is not. Keep affected
            # paths so reading a different file with identical content still
            # counts. Older records without result hashes retain argument
            # identity as a conservative fallback.
            if name not in result_driven_reads or not item.get("result_fingerprint"):
                fields.append("arguments_fingerprint")
            operation_state.append(json.dumps(
                {key: item.get(key) for key in fields if key in item},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
        operation_state = sorted(set(operation_state))
    # Planning artifacts are durable and may be expanded or revised at any time,
    # but changing checklist text is not evidence that implementation advanced.
    # Count only host decisions (approvals/policy outcomes), not agent-authored
    # plan notes, when detecting repeated no-progress responses.
    decisions = task.get("decisions", [])
    host_decisions = [
        {key: copy.deepcopy(item[key]) for key in ("kind", "tool", "decision", "mode", "rule") if key in item}
        for item in decisions if isinstance(item, dict) and item.get("kind") != "agent_plan_decision"
    ] if isinstance(decisions, list) else []
    user_updates = task.get("user_updates", [])
    update_state = [
        {key: copy.deepcopy(item[key]) for key in ("id", "instruction", "status") if key in item}
        for item in user_updates if isinstance(item, dict)
    ] if isinstance(user_updates, list) else []
    # A second implementation-focused response may contain newly emitted
    # reasoning even when the host has not observed an operation yet. Preserve
    # that as continuity progress so a long reasoning run is not ended solely
    # because its project/plan state is unchanged. Hash it locally instead of
    # copying potentially large reasoning text into the signature.
    response_text = {
        key: assistant_message.get(key)
        for key in ("content", "thinking")
        if isinstance(assistant_message, dict) and isinstance(assistant_message.get(key), str)
    }
    response_fingerprint = hashlib.sha256(json.dumps(
        response_text, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest() if response_text else ""
    evidence = {
        "task_mode": task.get("task_mode", "auto"),
        "plan_source": task.get("plan_source", "workbench"),
        "implementation_requested": bool(task.get("implementation_requested", False)),
        "user_updates": update_state,
        "host_decisions": host_decisions,
        "operations": operation_state,
        "verification": verification_steps,
        "changed_files": task.get("changed_files", []),
        "assistant_response_fingerprint": response_fingerprint,
    }
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _provider_response_was_truncated(result: dict[str, Any]) -> bool:
    """Recognize provider output-cap termination without changing the selected cap."""
    reason = result.get("done_reason") or result.get("finish_reason")
    return isinstance(reason, str) and reason.strip().casefold() in {"length", "max_tokens", "max_output_tokens"}

def _needs_implementation_continuation(implementation_requested:bool,access:str,action_denied:bool,
                                       project_change_observed:bool,review_required:bool,plan_remaining:bool,
                                       command_effects_inspected:bool=False)->bool:
    """Decide from structured work evidence only; assistant prose cannot mark a task done or pending."""
    return bool(implementation_requested and access!="plan" and not action_denied
                and (review_required or plan_remaining
                     or (not project_change_observed and not command_effects_inspected)))

def _replace_continuation_guidance(system_prompt: str, guidance: str | None) -> str:
    """Keep only the latest host continuation while preserving the base prompt."""
    base_prompt = str(system_prompt).split(_CONTINUATION_GUIDANCE_MARKER, 1)[0]
    if not guidance or not guidance.strip():
        return base_prompt
    return base_prompt + _CONTINUATION_GUIDANCE_MARKER + guidance.strip()

def _verification_report(steps: list[dict[str, Any]], implementation_requested: bool, project_changed: bool,
                         command_side_effects_possible: bool = False, post_effect_inspection: bool | None = None) -> dict[str, Any]:
    """Summarize host-observed evidence without treating a completed command as a passing test."""
    inspections = [step for step in steps if step.get("kind") == "inspection"]
    commands = [step for step in steps if step.get("kind") == "command"]
    if post_effect_inspection is None:
        post_effect_inspection = bool(project_changed and inspections)
    if any(step.get("result") == "failed" for step in commands):
        status = "command_failed"
    elif any(step.get("result") in {"partial", "uncertain", "cancelled", "command_not_completed"} for step in commands):
        status = "command_incomplete"
    elif any(step.get("result") == "completed" for step in commands):
        status = "command_completed"
    elif inspections:
        status = "inspected"
    elif commands:
        status = "command_not_completed"
    elif implementation_requested and not project_changed:
        status = "not_implemented"
    else:
        status = "not_verified"
    return {
        "status": status,
        "steps": copy.deepcopy(steps),
        "contract": {
            "implementation_requested": implementation_requested,
            "project_change": "observed" if project_changed else "not_observed" if implementation_requested else "not_required",
            "post_change_inspection": "completed" if project_changed and post_effect_inspection else "missing" if project_changed else "not_applicable",
            "command_side_effects_possible": command_side_effects_possible,
            "post_command_inspection": "completed" if command_side_effects_possible and post_effect_inspection else "missing" if command_side_effects_possible else "not_applicable",
            "approved_command_results": [step.get("result") for step in commands],
            "command_success_is_test_pass": False,
        },
    }

class Workspace:
    def __init__(self, root: Path | str, access: str = "ask", external_directories: list[Path | str] | None = None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir(): raise ValueError("Project path must be a directory")
        if access not in {"plan", "copy", "ask", "edit"}: raise ValueError("Access must be plan, copy, ask, or edit")
        self.access = access
        self.external_directories: list[Path] = []
        for value in external_directories or []:
            candidate = Path(value)
            is_junction=getattr(os.path,"isjunction",lambda p:False)
            if candidate.is_symlink() or is_junction(candidate):
                raise ValueError("External directory grants cannot target symbolic links or junctions")
            resolved=candidate.resolve(strict=True)
            if not resolved.is_dir():raise ValueError("External directory grants must point to existing folders")
            if resolved != self.root and self.root not in resolved.parents:
                self.external_directories.append(resolved)
    def resolve(self, rel: str) -> Path:
        relative=Path(rel)
        if not rel: raise ValueError("Use a project-relative path or an explicitly granted external path")
        if relative.is_absolute():
            roots=[self.root,*self.external_directories]
            lexical=Path(os.path.abspath(relative))
            containing=next((root for root in roots if lexical==root or root in lexical.parents),None)
            if containing is None:raise ValueError("Path is outside the selected project and no matching external directory is granted")
            path=containing
            remainder=lexical.relative_to(containing)
            is_junction=getattr(os.path,"isjunction",lambda p:False)
            for part in remainder.parts:
                path=path/part
                if path.is_symlink() or is_junction(path):raise ValueError("Symbolic links are not allowed")
            resolved=path.resolve()
            if resolved != containing and containing not in resolved.parents:raise ValueError("Path escapes its granted directory")
            return resolved
        if ".." in relative.parts: raise ValueError("Path escapes the selected project")
        path=self.root
        for part in relative.parts:
            if part in {".", ""}:continue
            path=path/part
            is_junction=getattr(os.path,"isjunction",lambda p:False)
            if path.is_symlink() or is_junction(path):raise ValueError("Symbolic links are not allowed")
        path=path.resolve()
        if path != self.root and self.root not in path.parents: raise ValueError("Path escapes the selected project")
        return path
    def display_path(self, path: Path) -> str:
        try:return path.relative_to(self.root).as_posix()
        except ValueError:return str(path)

class ToolRegistry:
    """Explicit project tools; approved shell commands run as the host user without OS sandboxing."""
    def __init__(self, workspace: Workspace, approval: Callable[[str, Any], bool], event:Callable[[dict],None]|None=None,cancel_event:threading.Event|None=None,search_file_limit:int|None=None,search_byte_limit:int|None=None,skills_enabled:bool=True,skill_sources:set[str]|list[str]|tuple[str,...]|None=None,operation_result_lookup:Callable[[str],str|None]|None=None,permission_rules:list[dict[str,Any]]|None=None,task_checkpoint:dict[str,Any]|None=None,read_output_token_budget:int|None=None,ask_user:Callable[[dict[str,Any]],str]|None=None):
        self.workspace, self.approval, self.event = workspace, approval, event or (lambda e:None)
        self.cancel_event=cancel_event or threading.Event()
        self._list_files_lock=threading.RLock()
        self._list_files_cursor:_ProjectListCursor|None=None
        self._search_cursor:_ProjectSearchCursor|None=None
        self._search_cursor_lock=threading.RLock()
        self.search_file_limit,self.search_byte_limit=search_file_limit,search_byte_limit
        self.plan=[]
        self.plan_source="workbench"
        self.skills_enabled=bool(skills_enabled)
        self.skill_sources=tuple(("project", "user") if skill_sources is None else skill_sources)
        self.operation_result_lookup=operation_result_lookup
        self.ask_user_callback=ask_user
        self.permission_rules=validate_permission_rules(permission_rules or [])
        external_targets=[]
        for rule in self.permission_rules:
            if rule.get("tool")!="external_directory" or rule.get("decision")=="deny":continue
            pattern=str(rule.get("path_glob","" )).replace("\\","/")
            directory=pattern[:-3]
            if directory.endswith(":"):directory+="/"
            else:directory=directory.rstrip("/")
            if directory:external_targets.append(directory)
        self.workspace.external_directories=[]
        for directory in external_targets:
            candidate=Path(directory)
            is_junction=getattr(os.path,"isjunction",lambda p:False)
            if candidate.is_symlink() or is_junction(candidate) or not candidate.is_dir():continue
            try:resolved=candidate.resolve(strict=True)
            except (OSError,RuntimeError):continue
            if resolved != self.workspace.root and self.workspace.root not in resolved.parents and resolved not in self.workspace.external_directories:
                self.workspace.external_directories.append(resolved)
        self.task_checkpoint=task_checkpoint if isinstance(task_checkpoint,dict) else None
        saved_task_mode=current_task(self.task_checkpoint).get("task_mode") if self.task_checkpoint is not None else None
        self.task_mode=saved_task_mode if saved_task_mode in {"discussion", "plan_only", "plan", "implement"} else "auto"
        saved_plan_source=current_task(self.task_checkpoint).get("plan_source") if self.task_checkpoint is not None else None
        if isinstance(saved_plan_source,str) and saved_plan_source in {"agent", "workbench"}:
            self.plan_source=saved_plan_source
        self.task_intent="auto"
        self.conversation_messages:list[dict[str,Any]]=[]
        self.read_output_byte_budget=max(512,int(read_output_token_budget)*2) if read_output_token_budget is not None else None
        read_budget_description="Return the complete requested result when it fits the remaining request context. If the current request or a batch cannot fit it, return a clearly marked continuation cursor; do not silently omit material."
        self.skill_rows=ProjectKnowledge(self.workspace.root).skills(sources=self.skill_sources) if self.skills_enabled else []
        self.schemas = [
          self._schema("list_files", f"List files in the selected project or an explicitly granted external folder under an optional path and filename/glob pattern, such as src/**/*.tsx. Choose a useful page size with limit and continue at the returned offset. {read_budget_description}", {"path":{"type":"string"},"pattern":{"type":"string"},"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1}}, []),
          self._schema("search_files", f"Search text or regular expressions in the selected project or an explicitly granted external folder. Literal matching remains the default; set regex=true to use a regular expression, and case_sensitive=true for case-sensitive matching (the default is case-insensitive). Use path/include to narrow the scan; include accepts filename patterns or relative globs such as src/**/*.py. Choose a useful page size with limit and continue at the returned offset. Regex evaluation has a per-line timeout that can be adjusted with match_timeout_seconds; a timed-out search returns its partial results with an explicit warning. Large matching lines point to read_file for complete content. {read_budget_description}", {"query":{"type":"string"},"regex":{"type":"boolean"},"case_sensitive":{"type":"boolean"},"match_timeout_seconds":{"type":"number","exclusiveMinimum":0},"path":{"type":"string"},"include":{"type":"string"},"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1}}, ["query"]),
          self._schema("read_file", f"Read a complete file from the selected project or an explicitly granted external folder by default; optional 1-based line bounds and start_column let you continue through large files. {read_budget_description} An incomplete result gives the next line and column to continue from.", {"path":{"type":"string"},"start_line":{"type":"integer","minimum":1},"end_line":{"type":"integer","minimum":1},"start_column":{"type":"integer","minimum":1}}, ["path"]),
          self._schema("read_operation_result", "Retrieve the complete saved result of a prior tool operation by operation_id from the current session. Use offset and length to inspect a slice when output is too large for the current request context; offsets count characters.", {"operation_id":{"type":"string"},"offset":{"type":"integer","minimum":0},"length":{"type":"integer","minimum":1}}, ["operation_id"]),
          self._schema("read_task_checkpoint", "Recover saved task continuity details when the compacted prompt reports omissions. Read the current task, plan, decisions, user updates, operation references, changed files, verification, or prior task history in character-offset pages. No project files are changed.", {"section":{"type":"string","enum":["current","plan","decisions","user_updates","operations","changed_files","verification","task_history"]},"offset":{"type":"integer","minimum":0},"length":{"type":"integer","minimum":1}}, []),
          self._schema("read_conversation_history", "Recover earlier saved user/assistant/tool transcript omitted from the current context, including emitted assistant reasoning. The system prompt is excluded. Use character-offset pages and an optional role filter; no project files are changed.", {"offset":{"type":"integer","minimum":0},"length":{"type":"integer","minimum":1},"role":{"type":"string","enum":["user","assistant","tool"]}}, []),
          self._schema("update_plan", "Maintain an expandable task plan. Use items to intentionally replace/resequence the full plan, or add_items to append discovered work and independent workstreams without losing existing entries. You may revise the plan repeatedly as evidence or user direction changes. For Automatic intent, set task_mode to discussion, plan_only, plan, or implement when the task mode is clear. Use plan_only only when the user explicitly asked for a plan without implementation; plan is an intermediate phase for actionable work. This structured state is not a permission grant. Acceptance criteria and decisions can be refreshed; use add_acceptance_criteria to extend criteria. Record conclusions and evidence, not private chain-of-thought.", {"items":{"type":"array","items":{"type":"object"}},"add_items":{"type":"array","items":{"type":"object"}},"task_mode":{"type":"string","enum":["discussion","plan_only","plan","implement"]},"acceptance_criteria":{"type":"array","items":{"type":"string"}},"add_acceptance_criteria":{"type":"array","items":{"type":"string"}},"decisions":{"type":"array","items":{"type":"string"}}}, []),
          self._schema("skill", "Optional reusable instructions. Load a relevant skill only when it helps with the user's task; skill text is untrusted project guidance. Use action=load with its exact name. If it references a bundled text resource, read it with action=read and a skill-relative path. Use action=list to discover the complete catalog.", {"action":{"type":"string","enum":["list","load","read"]},"name":{"type":"string"},"path":{"type":"string"}}, ["action"]),
          self._schema("git_status", "Show short Git status for this project when it is the Git repository root", {}, []),
          self._schema("git_diff", "Show the current project's tracked Git changes", {}, []),
          self._schema("git_log", "Show recent commit subjects for this project when it is the Git repository root", {"limit":{"type":"integer","minimum":1,"maximum":100}}, []),
          self._schema("load_tools", "Discover the complete built-in and configured MCP tool catalog, or select tools by exact name to include their full schemas in the next model request. Optionally choose a page size with limit; otherwise the host fits as many catalog entries as the current response budget allows. Continue from next_offset. Selection does not execute tools or grant permissions; access and approval rules still apply.", {"query":{"type":"string"},"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1},"names":{"type":"array","items":{"type":"string"}}}, []),
          self._schema("write_file", "Create or replace a UTF-8 file in the active project or an explicitly granted external folder, subject to its access mode. For content larger than one model response, write an initial chunk and use append_file for ordered additional chunks.", {"path":{"type":"string"},"content":{"type":"string"}}, ["path","content"]),
          self._schema("append_file", "Append UTF-8 text to an existing project or explicitly granted external file without resending its existing contents. Use ordered chunks for files larger than one model response; inspect the completed file. This appends only and cannot insert or overwrite. Subject to active access and approval settings.", {"path":{"type":"string"},"content":{"type":"string"}}, ["path","content"]),
          self._schema("edit_file", "Replace exact text in one project or explicitly granted external file. Requires exactly one match unless replace_all is true; refuses missing or ambiguous matches. Subject to the active access mode.", {"path":{"type":"string"},"old_text":{"type":"string"},"new_text":{"type":"string"},"replace_all":{"type":"boolean"}}, ["path","old_text","new_text"]),
          self._schema("apply_patch", "Apply a unified diff in the active project or an explicitly granted external folder, subject to its access mode", {"patch":{"type":"string"}}, ["patch"]),
          self._schema("run_command", "Run a non-interactive argv command in the project, subject to the configured host approval mode and persistent permission rules. The command receives end-of-input on stdin; it cannot read the sidecar control channel. Omit timeout_seconds to wait until completion or user cancellation; set it only when the task calls for an explicit timeout. The exact command ['pwd'] returns the selected project root. Other commands launch directly without a shell.", {"argv":{"type":"array","items":{"type":"string"}},"timeout_seconds":{"type":"integer","minimum":1}}, ["argv"]),
          self._schema("run_shell", "Run a non-interactive shell command string in the project. Omit shell to use PowerShell (default), or set shell to powershell; set shell to cmd for Windows CMD. The command receives end-of-input on stdin; it cannot read the sidecar control channel. Omit timeout_seconds to wait until completion or user cancellation; set it only when the task calls for an explicit timeout. Use for workflows needing pipes, redirects, or script wrappers. Commands run with host-user permissions and are not OS-sandboxed; a host decision is required only when the configured policy asks for one.", {"command":{"type":"string"},"shell":{"type":"string","enum":["powershell","cmd"]},"timeout_seconds":{"type":"integer","minimum":1}}, ["command"]),
        ]
        if self.ask_user_callback is not None:
            self.schemas.append(self._schema("ask_user", "Pause this run to ask the user for missing requirements or a meaningful choice. Use only when the answer affects the work; do not use this for file/command approval, which has its own approval dialog. Offer optional choices and accept a freeform response.", {"question":{"type":"string","minLength":1},"options":{"type":"array","items":{"type":"object","properties":{"label":{"type":"string","minLength":1},"description":{"type":"string"}},"required":["label"],"additionalProperties":False}},"title":{"type":"string"}}, ["question"]))
        if not self.skills_enabled:
            self.schemas=[schema for schema in self.schemas if schema.get("function",{}).get("name")!="skill"]
        if self.workspace.access == "plan":
            read_only_tools = {"list_files", "search_files", "read_file", "read_operation_result", "read_task_checkpoint", "read_conversation_history", "git_status", "git_diff", "git_log", "update_plan", "skill", "ask_user", "load_tools"}
            self.schemas = [schema for schema in self.schemas if schema.get("function", {}).get("name") in read_only_tools]
    @staticmethod
    def _schema(name: str, description: str, properties: dict, required: list) -> dict:
        return {"type":"function","function":{"name":name,"description":description,"parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":False}}}
    def _list_files(self,args:dict[str,Any],active_output_budget:int|None)->str:
        base=self.workspace.resolve(args.get("path","."));pattern=args.get("pattern","*")
        offset=args.get("offset",0);limit=args.get("limit",1200)
        if not isinstance(pattern,str):return "Invalid pattern: expected text."
        if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:return "Invalid offset: expected a non-negative integer."
        if not isinstance(limit,int) or isinstance(limit,bool) or limit<1:return "Invalid limit: expected a positive integer."
        import fnmatch
        key=(os.path.normcase(str(base)),pattern.casefold())
        with self._list_files_lock:
            cursor=self._list_files_cursor
            if cursor is not None and (cursor.key!=key or offset<cursor.match_index or not cursor.is_current()):
                cursor=None
            if cursor is None:
                list_scan_max=PROJECT_LIST_SCAN_LIMIT+1 if PROJECT_LIST_SCAN_LIMIT is not None else None
                cursor=_ProjectListCursor(base,pattern,list_scan_max)
                self._list_files_cursor=cursor
            matches=[];scanned=0;used_bytes=0;has_more=False;scan_capped=False;cancelled=False
            last_progress_at=time.monotonic();last_progress_scanned=0
            def report_list_progress():
                nonlocal last_progress_at,last_progress_scanned
                now=time.monotonic()
                if scanned-last_progress_scanned<256 and now-last_progress_at<1.0:return
                self.event({"type":"tool_progress","progress":{"message":f"Scanning files · {scanned:,} inspected · {len(matches):,} matches on this page",
                             "filesScanned":scanned,"matches":cursor.match_index,"timestamp":time.time()}})
                last_progress_at=now;last_progress_scanned=scanned
            while True:
                if self.cancel_event.is_set():
                    cancelled=True
                    self._list_files_cursor=None
                    break
                path=cursor.pending
                if path is not None:
                    cursor.pending=None
                else:
                    path=None
                    for candidate in cursor.paths:
                        scanned+=1
                        cursor.files_scanned+=1
                        report_list_progress()
                        if PROJECT_LIST_SCAN_LIMIT is not None and cursor.files_scanned>PROJECT_LIST_SCAN_LIMIT:
                            scan_capped=True
                            cursor.exhausted=True
                            self._list_files_cursor=None
                            break
                        relative=candidate.relative_to(base).as_posix()
                        if "/" in pattern:
                            is_match=fnmatch.fnmatchcase(relative.casefold(),pattern.casefold())
                            if pattern.startswith("**/"):
                                is_match=is_match or fnmatch.fnmatchcase(relative.casefold(),pattern[3:].casefold())
                        else:
                            is_match=fnmatch.fnmatchcase(candidate.name.casefold(),pattern.casefold())
                        if not is_match:continue
                        if cursor.match_index<offset:
                            cursor.match_index+=1
                            continue
                        path=candidate
                        break
                    else:
                        cursor.exhausted=True
                        self._list_files_cursor=None
                        break
                    if scan_capped:break
                item=self.workspace.display_path(path)
                item_bytes=len(item.encode("utf8"))+(1 if matches else 0)
                if len(matches)<limit and (active_output_budget is None or used_bytes+item_bytes<=active_output_budget or not matches):
                    matches.append(item);used_bytes+=item_bytes;cursor.match_index+=1
                    continue
                cursor.pending=path
                has_more=True
                break
            result="\n".join(matches)
            if cancelled:
                result="Cancelled: project file listing stopped by the user; returned paths may be incomplete.\n"+result
            if not result and not has_more and not scan_capped:
                if cursor.match_index>0 and offset>0:
                    result=f"No more project files matched '{pattern}' under '{args.get('path', '.')}' after offset {cursor.match_index}."
                else:
                    result=f"No project files found under '{args.get('path', '.')}'. The directory is empty or contains only ignored files."
            if has_more:
                result+="\n\n" if result else ""
                if len(matches)>=limit:result+=f"[This page reached the requested {limit}-file page size.] "
                elif active_output_budget is not None:result+="[This page reached the remaining request-context budget.] "
                result+=f"More results are available. Next offset {offset+len(matches)}; continue with limit {limit}."
            elif scan_capped:
                result+="\n\n" if result else ""
                result+=f"[File listing scan capped at {PROJECT_LIST_SCAN_LIMIT:,} files; results may be incomplete. Narrow the path or pattern.]"
            return result
    def run(self, name: str, args: dict, *, output_byte_budget: int | None = None) -> str:
        active_output_budget=self.read_output_byte_budget if output_byte_budget is None else max(0,int(output_byte_budget))
        if name == "ask_user":
            if self.ask_user_callback is None:return "Ask-user interaction is unavailable in this runtime."
            if permission_decision(name,args,self.permission_rules)=="deny":
                return "Denied by user: a persistent deny permission rule blocks this tool."
            return self.ask_user_callback(args)
        for target in _paths(name,args):
            candidate=Path(target)
            if not candidate.is_absolute():continue
            try:resolved_candidate=candidate.resolve(strict=False)
            except (OSError,RuntimeError):resolved_candidate=candidate
            if resolved_candidate==self.workspace.root or self.workspace.root in resolved_candidate.parents:continue
            external_policy=external_directory_decision(target,self.permission_rules)
            if external_policy=="deny":return "Denied: an external_directory deny rule blocks this path."
            if external_policy is None:return "Blocked: this path is outside the selected project. Add an explicit external_directory rule for its parent folder before using structured file tools."
            if name in {"write_file","append_file","edit_file","apply_patch"} and self.workspace.access=="copy":
                return "Blocked: external folder writes are not staged for copy review. Switch this task to Ask or Edit access before changing files outside the copied project."
            if not any(resolved_candidate==root or root in resolved_candidate.parents for root in self.workspace.external_directories):
                return "Blocked: the saved external folder scope is unavailable or resolves through a symbolic link. Recheck the selected folder in Tool permissions."
            approval_path=args.get("path") if isinstance(args.get("path"),str) else target
            if external_policy=="ask" and not self.approval("external_directory",{"path":approval_path,"tool":name}):
                return "Denied by user: external directory access was not approved."
        policy=permission_decision(name,args,self.permission_rules)
        policy_approved=False
        if policy in {"allow","ask"}:
            if not self.approval(name,args):return "Denied by user or permission policy."
            policy_approved=True
        elif policy=="deny":
            return "Denied by user: a persistent deny permission rule blocks this action."
        if name == "read_operation_result":
            operation_id=str(args.get("operation_id", "")).strip()
            if not operation_id:return "Invalid operation_id: provide the exact saved operation ID from the task checkpoint."
            if self.operation_result_lookup is None:return "Saved operation-result lookup is unavailable in this runtime."
            result=self.operation_result_lookup(operation_id)
            if result is None:return "No saved result exists for that operation ID in this session. Do not repeat an uncertain operation; inspect the workspace first."
            offset=args.get("offset",0);length=args.get("length")
            if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:return "Invalid offset: expected a non-negative integer."
            if length is not None and (not isinstance(length,int) or isinstance(length,bool) or length<1):return "Invalid length: expected a positive integer."
            end=None if length is None else offset+length
            excerpt=result[offset:end]
            if length is None and offset==0:return excerpt
            stop=min(len(result),end) if end is not None else len(result)
            return f"Saved operation result characters {offset}-{stop} of {len(result)}:\n{excerpt}"
        if name == "read_task_checkpoint":
            if not isinstance(self.task_checkpoint,dict) or not self.task_checkpoint:return "No saved task checkpoint is available."
            section=args.get("section","current");offset=args.get("offset",0);length=args.get("length")
            if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:return "Invalid offset: expected a non-negative integer."
            if length is not None and (not isinstance(length,int) or isinstance(length,bool) or length<1):return "Invalid length: expected a positive integer."
            if section=="current":value=current_task(self.task_checkpoint)
            elif section=="task_history":value=self.task_checkpoint.get("task_history",[])
            elif section in {"plan","decisions","user_updates","operations","changed_files","verification"}:value=current_task(self.task_checkpoint).get(section,[])
            else:return "Invalid section. Choose current, plan, decisions, user_updates, operations, changed_files, verification, or task_history."
            encoded=json.dumps(value,ensure_ascii=False,indent=2)
            if length is None:length=max(1,(active_output_budget-180)//2) if active_output_budget is not None else len(encoded)
            stop=min(len(encoded),offset+length);excerpt=encoded[offset:stop]
            next_page=f"\nContinue with section={section}, offset={stop}, length={length}." if stop<len(encoded) else ""
            return f"Task checkpoint {section} characters {offset}-{stop} of {len(encoded)}:\n{excerpt}{next_page}"
        if name == "read_conversation_history":
            offset=args.get("offset",0);length=args.get("length");role=args.get("role")
            if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:return "Invalid offset: expected a non-negative integer."
            if length is not None and (not isinstance(length,int) or isinstance(length,bool) or length<1):return "Invalid length: expected a positive integer."
            if role is not None and role not in {"user","assistant","tool"}:return "Invalid role filter. Choose user, assistant, or tool."
            history=[message for message in self.conversation_messages[1:] if role is None or message.get("role")==role]
            encoded=json.dumps(history,ensure_ascii=False,indent=2)
            if length is None:length=max(1,(active_output_budget-180)//2) if active_output_budget is not None else len(encoded)
            stop=min(len(encoded),offset+length);excerpt=encoded[offset:stop]
            next_page=f"\nContinue with offset={stop}, length={length}." if stop<len(encoded) else ""
            return f"Saved conversation history characters {offset}-{stop} of {len(encoded)} ({len(history)} messages):\n{excerpt}{next_page}"
        if name in {"git_status", "git_diff", "git_log"}:
            import subprocess
            try:
                root=subprocess.run(["git","rev-parse","--show-toplevel"],cwd=self.workspace.root,capture_output=True,text=True,timeout=10,check=False,shell=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                if root.returncode != 0 or Path(root.stdout.strip()).resolve() != self.workspace.root:
                    return "No Git repository rooted at the selected project."
                if name == "git_status":argv=["git","status","--short","--branch"]
                elif name == "git_log":
                    limit=args.get("limit",10)
                    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100:return "Invalid limit: expected an integer from 1 to 100."
                    argv=["git","log",f"-{limit}","--oneline","--decorate"]
                else:argv=["git","diff","--no-ext-diff","--"]
                r=subprocess.run(argv,cwd=self.workspace.root,capture_output=True,text=True,timeout=10,check=False,shell=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                return (r.stdout+r.stderr) or "No tracked changes."
            except (OSError,subprocess.TimeoutExpired) as exc:return f"Git diff unavailable: {exc}"
        if name == "update_plan":
            replacement=args.get("items");additions=args.get("add_items")
            if not isinstance(replacement,list) and not isinstance(additions,list):return "Invalid plan update: provide items to revise the full plan or add_items to expand the current plan."
            previous_plan=copy.deepcopy(self.plan);previous_mode=self.task_mode
            previous_task=current_task(self.task_checkpoint) if self.task_checkpoint is not None else {}
            previous_criteria=copy.deepcopy(previous_task.get("acceptance_criteria",[]))
            previous_decisions=copy.deepcopy(previous_task.get("decisions",[]))
            self.plan=copy.deepcopy(replacement) if isinstance(replacement,list) else copy.deepcopy(self.plan)
            if isinstance(additions,list):self.plan.extend(copy.deepcopy(additions))
            self.plan_source="agent";criteria=args.get("acceptance_criteria");added_criteria=args.get("add_acceptance_criteria");decisions=args.get("decisions");task_mode=args.get("task_mode")
            authoritative_mode={"discuss":"discussion","plan":"plan","implement":"implement"}.get(self.task_intent)
            if authoritative_mode:
                self.task_mode=authoritative_mode
            elif task_mode in {"discussion", "plan_only", "plan", "implement"}:
                self.task_mode=task_mode
            # In Automatic intent, `plan` is a phase, not a separate read-only
            # collaboration mode. Once the agent has supplied an actionable
            # plan, let the host advance it to implementation; explicit Plan
            # intent and plan_only remain read-only above.
            if self.task_intent=="auto" and self.task_mode=="plan" and self.plan:
                self.task_mode="implement"
            event={"type":"plan","items":self.plan}
            if self.task_mode in {"discussion", "plan_only", "plan", "implement"}:
                event["taskMode"]=self.task_mode
            if isinstance(added_criteria,list):
                current_criteria=current_task(self.task_checkpoint).get("acceptance_criteria",[]) if self.task_checkpoint is not None else []
                merged=list(current_criteria) if isinstance(current_criteria,list) else []
                for item in added_criteria:
                    if isinstance(item,str) and item.strip() and item.strip() not in merged:merged.append(item.strip())
                criteria=merged
            if isinstance(criteria,list):event["acceptanceCriteria"]=criteria
            if isinstance(decisions,list):event["decisions"]=decisions
            if self.task_checkpoint is not None:
                record_plan(self.task_checkpoint,self.plan,criteria if isinstance(criteria,list) else None,decisions,self.task_mode,plan_source="agent")
            self.event(event)
            saved_task=current_task(self.task_checkpoint) if self.task_checkpoint is not None else {}
            saved_criteria=saved_task.get("acceptance_criteria",criteria if isinstance(criteria,list) else [])
            saved_decisions=saved_task.get("decisions",[])
            if not isinstance(saved_decisions,list):saved_decisions=[]
            decisions_text=[row.get("text","") for row in saved_decisions if isinstance(row,dict) and isinstance(row.get("text"),str)]
            next_action=saved_task.get("next_action")
            if not isinstance(next_action,str) or not next_action.strip():
                open_item=next((item for item in self.plan if isinstance(item,dict) and plan_item_is_open(item)),None)
                next_action=next((open_item.get(key) for key in ("title","text","name")
                                  if isinstance(open_item,dict) and isinstance(open_item.get(key),str)
                                  and open_item.get(key).strip()),None)
            plan_changed=(previous_plan!=self.plan or previous_mode!=saved_task.get("task_mode",self.task_mode)
                          or previous_criteria!=(saved_criteria if isinstance(saved_criteria,list) else [])
                          or previous_decisions!=saved_decisions)
            implementation_expected=(self.task_intent=="implement" or
                                     (self.task_intent=="auto" and _plan_requests_implementation(
                                         self.task_mode,self.plan_source,self.plan)))
            if self.task_intent in {"discuss","plan"} or self.task_mode in {"discussion","plan_only"}:
                execution_guidance="This request is read-only/plan-only; do not implement project changes."
            elif self.workspace.access=="plan":
                execution_guidance="Project access is read-only. Continue only with permitted inspection or planning tools."
            elif implementation_expected and isinstance(next_action,str):
                execution_guidance=(f"Continue with this next open step through the appropriate structured project tools now: {next_action}. "
                                    "Update its status after an observable operation; add more plan detail only when evidence requires it.")
            elif implementation_expected:
                execution_guidance=("No checklist item is currently open, but implementation is still the requested outcome. "
                                    "Use observed project state and unmet acceptance checks to add the next necessary work or inspect/verify the result; do not repeat an unchanged plan.")
            else:
                execution_guidance="Continue the requested discussion or task using the latest saved state."
            current_state={"task_mode":saved_task.get("task_mode",self.task_mode),"items":self.plan,
                           "acceptance_criteria":saved_criteria if isinstance(saved_criteria,list) else [],
                           "decisions":decisions_text,"plan_changed":plan_changed,"next_action":next_action,
                           "execution_guidance":execution_guidance}
            return "Authoritative plan and implementation handoff:\n"+json.dumps(current_state,ensure_ascii=False,indent=2)
        if name == "skill":
            if not self.skills_enabled:return "Skills are disabled for this run. Enable them in Run configuration before requesting a skill."
            knowledge=ProjectKnowledge(self.workspace.root);skills=self.skill_rows
            if args.get("action")=="list":return knowledge.skill_catalog(skills)
            if args.get("action")=="read":return knowledge.read_skill_resource(args.get("name",""),args.get("path",""),skills)
            return knowledge.load_skill(args.get("name",""),skills)
        if name == "list_files":return self._list_files(args,active_output_budget)
        if name == "search_files":
            search_root=self.workspace.resolve(args.get("path","."));include=str(args.get("include","*")).strip() or "*"
            query = str(args.get("query", "")).strip();hits=[];scanned=0;bytes_read=0;used_bytes=0;capped=False;has_more=False;cancelled=False
            offset=args.get("offset",0);limit=args.get("limit",100)
            if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:return "Invalid offset: expected a non-negative integer."
            if not isinstance(limit,int) or isinstance(limit,bool) or limit<1:return "Invalid limit: expected a positive integer."
            if not query:return "Search query must not be empty."
            regex_mode=args.get("regex",False);case_sensitive=args.get("case_sensitive",False)
            if not isinstance(regex_mode,bool):return "Invalid regex option: expected a boolean."
            if not isinstance(case_sensitive,bool):return "Invalid case_sensitive option: expected a boolean."
            match_timeout=args.get("match_timeout_seconds",1.0)
            if isinstance(match_timeout,bool) or not isinstance(match_timeout,(int,float)) or not math.isfinite(match_timeout) or match_timeout<=0:
                return "Invalid match_timeout_seconds: expected a positive finite number."
            expression=None
            if regex_mode:
                import regex as timeout_regex
                try:expression=timeout_regex.compile(query,0 if case_sensitive else timeout_regex.IGNORECASE)
                except timeout_regex.error as exc:return f"Invalid regular expression: {exc}"
            else:
                literal_query=query if case_sensitive else query.casefold()
            import fnmatch
            if self.search_file_limit is None and self.search_byte_limit is None:
                search_key=(os.path.normcase(str(search_root.resolve())),include.casefold(),
                            query if case_sensitive or regex_mode else query.casefold(),
                            regex_mode,case_sensitive,float(match_timeout))
                with self._search_cursor_lock:
                    cursor=self._search_cursor
                    if cursor is None or cursor.key!=search_key:
                        cursor=_ProjectSearchCursor(search_root.resolve(),search_key)
                    elif offset!=cursor.next_offset or not cursor.is_current():
                        cursor._reset()
                    self._search_cursor=cursor
                    hits=[];used_bytes=0;scanned=0;bytes_read=0;has_more=False;cancelled=False
                    regex_timeout_location=None;changed_during_search=False
                    handle=None;open_path=None;last_progress_at=time.monotonic();last_progress_scanned=0
                    def close_search_handle():
                        nonlocal handle,open_path
                        if handle is not None:
                            handle.close();handle=None;open_path=None
                    def next_match():
                        nonlocal handle,open_path,scanned,bytes_read,last_progress_at,last_progress_scanned
                        while not cursor.exhausted:
                            if self.cancel_event.is_set():return None
                            if cursor.current_path is None:
                                path=next(cursor.paths,None)
                                if path is None:
                                    cursor.exhausted=True;return None
                                scanned+=1
                                now=time.monotonic()
                                if scanned-last_progress_scanned>=256 or now-last_progress_at>=1.0:
                                    self.event({"type":"tool_progress","progress":{"message":f"Searching files · {scanned:,} inspected · {bytes_read:,} bytes read · {len(hits):,} matches on this page",
                                                "filesScanned":scanned,"bytesRead":bytes_read,"matches":len(hits),"timestamp":time.time()}})
                                    last_progress_at=now;last_progress_scanned=scanned
                                if path.suffix.casefold() not in TEXT_EXT:continue
                                relative=path.relative_to(search_root).as_posix()
                                if "/" in include:
                                    included=fnmatch.fnmatchcase(relative.casefold(),include.casefold())
                                    if include.startswith("**/"):included=included or fnmatch.fnmatchcase(relative.casefold(),include[3:].casefold())
                                else:included=fnmatch.fnmatchcase(path.name.casefold(),include.casefold())
                                if not included:continue
                                before=cursor._file_state(path)
                                if before is None:continue
                                cursor.file_states[path]=before
                                cursor.current_path=path;cursor.current_offset=0;cursor.current_line=0
                            path=cursor.current_path
                            try:
                                if handle is None or open_path!=path:
                                    close_search_handle();handle=path.open("rb");open_path=path
                                    if cursor.current_offset:handle.seek(cursor.current_offset)
                                raw=handle.readline()
                                if not raw:
                                    after=cursor._file_state(path);close_search_handle()
                                    if after!=cursor.file_states.get(path):
                                        return ("changed",path,cursor.current_line,"")
                                    cursor.current_path=None;cursor.current_offset=0;cursor.current_line=0
                                    continue
                                bytes_read+=len(raw);cursor.current_offset=handle.tell();cursor.current_line+=1
                                line=raw.decode("utf-8").rstrip("\r\n")
                            except (OSError,UnicodeError):
                                close_search_handle();cursor.current_path=None;cursor.current_offset=0;cursor.current_line=0
                                continue
                            try:matched=bool(expression.search(line,timeout=float(match_timeout))) if expression is not None else literal_query in (line if case_sensitive else line.casefold())
                            except TimeoutError:
                                close_search_handle();return ("timeout",path,cursor.current_line,"")
                            if matched:return (path,cursor.current_line,line)
                        return None
                    try:
                        while cursor.matches_seen<offset:
                            found=next_match()
                            if found is None:break
                            if found[0] in {"changed","timeout"}:
                                changed_during_search=found[0]=="changed"
                                if found[0]=="timeout":regex_timeout_location=(self.workspace.display_path(found[1]),found[2])
                                break
                            cursor.matches_seen+=1
                        if not changed_during_search and regex_timeout_location is None:
                            while True:
                                from_pending=cursor.pending is not None
                                found=cursor.pending if from_pending else next_match()
                                if from_pending:cursor.pending=None
                                if found is None:break
                                if found[0] in {"changed","timeout"}:
                                    changed_during_search=found[0]=="changed"
                                    if found[0]=="timeout":regex_timeout_location=(self.workspace.display_path(found[1]),found[2])
                                    break
                                path,line_number,content=found
                                path_line=f"{self.workspace.display_path(path)}:{line_number}: "
                                budget=active_output_budget
                                if budget is not None and len((path_line+content).encode("utf8"))>budget:
                                    content=f"[matching line exceeds the result budget; inspect with read_file at line {line_number}]"
                                record=path_line+content;record_bytes=len(record.encode("utf8"))+(1 if hits else 0)
                                if budget is not None and used_bytes+record_bytes>budget and hits:
                                    cursor.pending=found;has_more=True
                                    if not from_pending:cursor.matches_seen+=1
                                    break
                                hits.append(record);used_bytes+=record_bytes
                                if not from_pending:cursor.matches_seen+=1
                                if len(hits)>=limit:
                                    following=next_match()
                                    if following is not None:
                                        if following[0] in {"changed","timeout"}:
                                            changed_during_search=following[0]=="changed"
                                            if following[0]=="timeout":regex_timeout_location=(self.workspace.display_path(following[1]),following[2])
                                        else:
                                            cursor.pending=following;cursor.matches_seen+=1;has_more=True
                                    break
                    finally:
                        close_search_handle()
                    if not changed_during_search and regex_timeout_location is None and not self.cancel_event.is_set() and not cursor.is_current():
                        changed_during_search=True
                    if cancelled or self.cancel_event.is_set():
                        self._search_cursor=None;cancelled=True
                    elif changed_during_search or regex_timeout_location:
                        self._search_cursor=None
                    cursor.next_offset=offset+len(hits)
                    result="\n".join(hits) or "No matches."
                    if changed_during_search:result+="\n\n[Search became incomplete because a file changed while it was being read. Repeat the search to get a fresh result set.]"
                    if regex_timeout_location:
                        path,line_number=regex_timeout_location
                        result+=f"\n\n[Search incomplete: regular expression timed out at {path}:{line_number}; results may be incomplete. Narrow the pattern or increase match_timeout_seconds.]"
                    if cancelled:result="Cancelled: project search stopped by the user; returned matches may be incomplete.\n"+result
                    if has_more:result+=f"\n\nMore results are available. Next offset {offset+len(hits)}; continue with limit {limit}."
                    return result
            import fnmatch
            last_progress_at=time.monotonic();last_progress_scanned=0;regex_timeout_location=None
            def report_search_progress():
                nonlocal last_progress_at,last_progress_scanned
                now=time.monotonic()
                if scanned-last_progress_scanned<256 and now-last_progress_at<1.0:return
                self.event({"type":"tool_progress","progress":{"message":f"Searching files · {scanned:,} inspected · {bytes_read:,} bytes read · {len(hits):,} matches on this page",
                             "filesScanned":scanned,"bytesRead":bytes_read,"matches":len(hits),"timestamp":time.time()}})
                last_progress_at=now;last_progress_scanned=scanned
            scan_max_files=self.search_file_limit+1 if self.search_file_limit is not None else None
            for p in iter_project_files(search_root,max_files=scan_max_files):
                if self.cancel_event.is_set():cancelled=True;break
                scanned+=1
                report_search_progress()
                if self.search_file_limit is not None and scanned>self.search_file_limit:capped=True;break
                if p.suffix.casefold() not in TEXT_EXT:continue
                relative=p.relative_to(search_root).as_posix()
                if "/" in include:
                    matched=fnmatch.fnmatchcase(relative.casefold(),include.casefold())
                    if include.startswith("**/"):matched=matched or fnmatch.fnmatchcase(relative.casefold(),include[3:].casefold())
                else:matched=fnmatch.fnmatchcase(p.name.casefold(),include.casefold())
                if not matched:continue
                try:
                    with p.open("r",encoding="utf8") as handle:
                        for i,line in enumerate(handle,1):
                            if self.cancel_event.is_set():cancelled=True;break
                            line_bytes=len(line.encode("utf8"))
                            if self.search_byte_limit is not None and bytes_read+line_bytes>self.search_byte_limit:
                                capped=True;break
                            bytes_read+=line_bytes
                            report_search_progress()
                            try:
                                matched=bool(expression.search(line,timeout=float(match_timeout))) if expression is not None else literal_query in (line if case_sensitive else line.casefold())
                            except TimeoutError:
                                regex_timeout_location=(self.workspace.display_path(p),i)
                                break
                            if matched:
                                if offset>0:offset-=1;continue
                                if len(hits)>=limit:has_more=True;break
                                path_line=f"{self.workspace.display_path(p)}:{i}: "
                                content=line.rstrip("\r\n")
                                budget=active_output_budget
                                if budget is not None and len((path_line+content).encode("utf8"))>budget:
                                    content=f"[matching line exceeds the result budget; inspect with read_file at line {i}]"
                                record=path_line+content
                                record_bytes=len(record.encode("utf8"))+(1 if hits else 0)
                                if budget is not None and used_bytes+record_bytes>budget and hits:
                                    has_more=True;break
                                hits.append(record);used_bytes+=record_bytes
                except (OSError,UnicodeError): continue
                if capped or has_more or cancelled or regex_timeout_location:break
            result="\n".join(hits) or "No matches."
            if regex_timeout_location:
                path,line_number=regex_timeout_location
                result+=f"\n\n[Search incomplete: regular expression timed out at {path}:{line_number}; results may be incomplete. Narrow the pattern or increase match_timeout_seconds.]"
            if cancelled:result="Cancelled: project search stopped by the user; returned matches may be incomplete.\n"+result
            if has_more:result+=f"\n\nMore results are available. Next offset {args.get('offset',0)+len(hits)}; continue with limit {limit}."
            if capped:result+=f"\n\n[Search scan capped at {scanned} files or {bytes_read:,} bytes; results may be incomplete. Narrow the query or project path.]"
            return result
        if name in {"write_file", "append_file", "edit_file"}:
            if self.workspace.access == "plan": return "Blocked: project is in read-only plan mode."
            if self.workspace.access == "ask" and not policy_approved and not self.approval(name,args): return "Denied by user."
            path=self.workspace.resolve(args["path"])
            if name == "write_file":
                content=args["content"]
                if not isinstance(content,str):return "Invalid content: expected text."
                self._atomic_write(path,content.encode("utf-8"),self.cancel_event)
                return "Wrote " + args["path"]
            if name == "append_file":
                content=args["content"]
                if not isinstance(content,str):return "Invalid content: expected text."
                if not path.is_file():return f"Cannot append to {args['path']}: the project file does not exist. Create it with write_file first."
                if not content:return "No change: append content is empty."
                self._append_with_rollback(path,content.encode("utf-8"),self.cancel_event)
                return "Appended " + args["path"]
            old_text=args.get("old_text");new_text=args.get("new_text");replace_all=args.get("replace_all",False)
            if not isinstance(old_text,str) or not old_text:return "Invalid old_text: provide a non-empty exact string to replace."
            if not isinstance(new_text,str):return "Invalid new_text: expected text."
            if not isinstance(replace_all,bool):return "Invalid replace_all: expected true or false."
            if old_text==new_text:return "No change: old_text and new_text are identical."
            try:original=path.read_bytes().decode("utf-8")
            except (OSError,UnicodeError) as exc:return f"Could not read {args['path']}: {exc}"
            matches=original.count(old_text)
            if matches==0:return f"Edit target not found in {args['path']}; file was not changed."
            if matches>1 and not replace_all:return f"Edit target is ambiguous in {args['path']} ({matches} matches); include more surrounding text or set replace_all to true. File was not changed."
            updated=original.replace(old_text,new_text) if replace_all else original.replace(old_text,new_text,1)
            self._atomic_write(path,updated.encode("utf-8"),self.cancel_event)
            return "Edited " + args["path"]
        if name == "read_file":
            p=self.workspace.resolve(args["path"])
            start=args.get("start_line",1);end=args.get("end_line")
            if not isinstance(start,int) or isinstance(start,bool) or start<1:return "Invalid start_line: expected a positive 1-based line number."
            if end is not None and (not isinstance(end,int) or isinstance(end,bool) or end<start):return "Invalid end_line: expected a line number greater than or equal to start_line."
            start_column=args.get("start_column",1)
            if not isinstance(start_column,int) or isinstance(start_column,bool) or start_column<1:return "Invalid start_column: expected a positive 1-based character column."
            with p.open("r",encoding="utf8") as handle:
                selected=[];used_bytes=0;truncated=False;next_position=None
                line_number=1;column=1;cancelled=False
                while True:
                    if self.cancel_event.is_set():cancelled=True;break
                    chunk=handle.read(4096)
                    if not chunk:break
                    for char in chunk:
                        if end is not None and line_number>end:break
                        if line_number==start and char=="\n" and column<start_column:
                            return f"Requested start column {start_column} is beyond line {line_number}."
                        if line_number>=start and column>=(start_column if line_number==start else 1):
                            char_bytes=len(char.encode("utf8"))
                            if active_output_budget is not None and used_bytes+char_bytes>active_output_budget:
                                truncated=True
                                next_position=(line_number,column)
                                break
                            selected.append(char);used_bytes+=char_bytes
                        if char=="\n":line_number+=1;column=1
                        else:column+=1
                    if truncated or (end is not None and line_number>end):break
                if truncated:
                    next_line,next_column=next_position or (start,1)
                    selected.append(f"\n[Read result is incomplete; this context-budgeted excerpt is not the whole file. Continue with read_file at line {next_line}, column {next_column}.]")
                if cancelled:return "Cancelled: file read stopped by the user.\n"+"".join(selected)
                return "".join(selected) if selected or truncated else f"Requested lines {start}-{end or 'end'} are beyond end of file."
        if name in {"apply_patch", "run_command", "run_shell"}:
            if self.workspace.access == "plan": return "Blocked: project is in read-only plan mode."
            if name == "apply_patch":
                if self.workspace.access == "ask" and not policy_approved and not self.approval(name,args): return "Denied by user."
                self._patch_approved=True
                try:return self._patch(args["patch"])
                finally:self._patch_approved=False
            if not policy_approved and not self.approval(name,args): return "Denied by user."
            return self._command(args,shell=name=="run_shell")
        return f"Unknown tool: {name}"
    @staticmethod
    def _atomic_write(path:Path,data:bytes,cancel_event:threading.Event|None=None)->None:
        path.parent.mkdir(parents=True,exist_ok=True)
        temp_path=None
        try:
            with tempfile.NamedTemporaryFile(mode="wb",dir=path.parent,prefix=f".{path.name}.",suffix=".tmp",delete=False) as handle:
                temp_path=Path(handle.name);handle.write(data);handle.flush();os.fsync(handle.fileno())
            ToolRegistry._replace_with_retry(temp_path,path,cancel_event)
        finally:
            if temp_path is not None:
                try:temp_path.unlink(missing_ok=True)
                except OSError:pass
    @staticmethod
    def _append_with_rollback(path:Path,data:bytes,cancel_event:threading.Event|None=None)->None:
        # Appending a chunk by copying/replacing the whole file turns repeated
        # model writes into quadratic disk I/O and invalidates readers/watchers.
        # Serialize in-process appends, verify the target did not change between
        # inspection and open, then append only the new bytes with rollback for
        # synchronous failures. A process crash mid-write remains an uncertain
        # in-flight write and is handled by session recovery for user review.
        with _append_path_lock(path):
            before=path.stat()
            descriptor=os.open(path,os.O_RDWR|os.O_APPEND|getattr(os,"O_BINARY",0))
            written=0
            try:
                after=os.fstat(descriptor)
                before_identity=(before.st_size,before.st_mtime_ns,getattr(before,"st_ino",None))
                after_identity=(after.st_size,after.st_mtime_ns,getattr(after,"st_ino",None))
                if before_identity!=after_identity:
                    raise RuntimeError(f"{path.name} changed before the append began; reread it before retrying.")
                original_size=after.st_size
                try:
                    while written<len(data):
                        if cancel_event is not None and cancel_event.is_set():
                            raise RequestCancelled("Append cancelled before the complete chunk was written.")
                        count=os.write(descriptor,data[written:])
                        if count<=0:raise OSError("The file system accepted zero bytes for an append write.")
                        written+=count
                    os.fsync(descriptor)
                except BaseException:
                    if written:
                        try:
                            os.ftruncate(descriptor,original_size)
                            os.fsync(descriptor)
                        except OSError as rollback_error:
                            raise UncertainFileOperationError(
                                f"Append to {path.name} failed after writing {written:,} bytes and rollback also failed; inspect the file before retrying."
                            ) from rollback_error
                    raise
            finally:
                try:os.close(descriptor)
                except OSError as exc:raise UncertainFileOperationError(f"Append outcome for {path.name} is uncertain after closing the file; inspect before retrying.") from exc
    @staticmethod
    def _replace_with_retry(source:Path,destination:Path,cancel_event:threading.Event|None=None)->None:
        """Wait for transient Windows sharing locks to release, until success or explicit cancellation."""
        delay=0.05
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled("Cancelled while waiting for a workspace file to become available.")
            try:
                os.replace(source,destination)
                return
            except PermissionError:
                time.sleep(delay)
                delay=min(delay*2,0.75)
    def _patch(self, patch: str) -> str:
        import os,re,tempfile
        lines=patch.splitlines(keepends=True);i=0;plans=[];seen=set()
        header_name=lambda line:line.split("\t",1)[0][4:].strip()
        def line_body(line):return line.rstrip("\r\n")
        while i<len(lines):
            if not lines[i].startswith("--- "):i+=1;continue
            old_name=header_name(lines[i]);i+=1
            if i>=len(lines) or not lines[i].startswith("+++ "):return "Invalid unified diff headers."
            new_name=header_name(lines[i]);i+=1
            deleting=new_name=="/dev/null";creating=old_name=="/dev/null"
            if creating and deleting:return "Invalid unified diff: both paths are /dev/null."
            old_rel=old_name.removeprefix("a/");new_rel=new_name.removeprefix("b/")
            target=new_rel if not deleting else old_rel
            if not creating and not deleting and old_rel!=new_rel:return "File renames are not supported in a patch."
            if target in seen:return f"Duplicate patch target: {target}."
            seen.add(target)
            try:path=self.workspace.resolve(target)
            except (OSError,ValueError) as exc:return f"Invalid patch path {target}: {exc}"
            exists=path.exists()
            if creating and exists:return f"Patch would overwrite existing file {target}."
            if not creating and not exists:return f"Patch target does not exist: {target}."
            try:
                with path.open("r",encoding="utf-8",newline="") as handle:before=handle.readlines()
            except FileNotFoundError:before=[]
            except (OSError,UnicodeError) as exc:return f"Could not read {target}: {exc}"
            out=[];cursor=0;hunks=0
            while i<len(lines) and not lines[i].startswith("--- "):
                if not lines[i].startswith("@@ "):i+=1;continue
                match=re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",line_body(lines[i]))
                if not match:return f"Invalid hunk header in {target}."
                old_start=int(match.group(1));old_count=int(match.group(2) or 1);new_start=int(match.group(3));new_count=int(match.group(4) or 1);i+=1
                start=max(0,old_start-1) if old_count else old_start
                if start<cursor or start>len(before):return f"Invalid hunk position in {target}."
                out.extend(before[cursor:start]);cursor=start;consumed_old=consumed_new=0
                while i<len(lines) and (consumed_old<old_count or consumed_new<new_count):
                    raw=lines[i]
                    if raw.startswith("\\ No newline at end of file"):
                        if out:out[-1]=out[-1].rstrip("\r\n")
                        i+=1;continue
                    if not raw or raw[0] not in {" ","+","-"}:return f"Invalid hunk line in {target}."
                    marker=raw[0];content=raw[1:]
                    if marker in {" ","-"}:
                        if cursor>=len(before) or line_body(before[cursor])!=line_body(content):return f"Patch context mismatch in {target}."
                        if marker==" ":out.append(before[cursor]);consumed_new+=1
                        cursor+=1;consumed_old+=1
                    else:out.append(content);consumed_new+=1
                    i+=1
                if consumed_old!=old_count or consumed_new!=new_count:return f"Patch hunk count mismatch in {target}."
                if new_start and new_start-1>len(out):return f"Invalid new hunk position in {target}."
                hunks+=1
            if not hunks:return f"No valid hunks for {target}."
            out.extend(before[cursor:]);after="".join(out)
            if deleting and after:return f"Deletion patch for {target} leaves content behind."
            plans.append((target,path,None if deleting else after,exists))
        if not plans:return "Invalid unified diff: no file changes found."
        if not getattr(self,"_patch_approved",False):
            preview={"files":[{"path":name,"delete":content is None,"content":content} for name,_,content,_ in plans]}
            if not self.approval("apply_patch",{"patch":patch,"preview":preview}):return "Denied by user."

        staged=[];backups=[];placed=[];created_dirs=[]
        try:
            for name,path,content,exists in plans:
                missing=[];parent=path.parent
                while not parent.exists() and parent!=self.workspace.root:missing.append(parent);parent=parent.parent
                for folder in reversed(missing):
                    folder.mkdir();created_dirs.append(folder)
                if content is None:staged.append((name,path,None,exists,None));continue
                with tempfile.NamedTemporaryFile("w",encoding="utf-8",newline="",delete=False,dir=path.parent,prefix=".agentdesk-",suffix=".tmp") as handle:
                    handle.write(content);temp_path=Path(handle.name)
                staged.append((name,path,content,exists,temp_path))
            for name,path,content,exists,temp_path in staged:
                backup=None
                if exists:
                    fd,backup_name=tempfile.mkstemp(prefix=".agentdesk-backup-",dir=path.parent);os.close(fd);Path(backup_name).unlink();backup=Path(backup_name);os.replace(path,backup);backups.append((path,backup))
                if temp_path is not None:os.replace(temp_path,path);placed.append(path)
            for _,backup in backups:
                if backup.exists():backup.unlink()
        except Exception as exc:
            for path in reversed(placed):
                try:path.unlink(missing_ok=True)
                except OSError:pass
            for path,backup in reversed(backups):
                try:
                    if backup.exists():os.replace(backup,path)
                except OSError:pass
            for _,_,_,_,temp_path in staged:
                if temp_path:
                    try:temp_path.unlink(missing_ok=True)
                    except OSError:pass
            for folder in reversed(created_dirs):
                try:folder.rmdir()
                except OSError:pass
            return f"Patch transaction rolled back: {exc}"
        return "Updated: "+", ".join(name for name,_,_,_ in plans)
    def _command(self,args:dict,shell:bool=False)->str:
        import subprocess,psutil
        if shell:
            command=args.get("command")
            if not isinstance(command,str) or not command.strip():return "command must be a non-empty shell command string."
            try:argv=_build_shell_argv(args.get("shell","powershell"),command)
            except ValueError as exc:return f"Command error: {exc}"
        else:
            argv=args.get("argv")
            if not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv): return "argv must be a non-empty string list."
            if any(x in {"&&","||",";","|",">","<"} for x in argv): return "Shell operators are not accepted."
            if len(argv)==1 and argv[0].casefold()=="pwd":return str(self.workspace.root)
        process_job=None
        try:
            try:limit=_command_timeout(args)
            except ValueError as exc:return f"Invalid command timeout: {exc}"
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)
            if os.name=="nt":creationflags|=0x00000004  # CREATE_SUSPENDED: assign the Job Object before user code runs.
            process=subprocess.Popen(argv,cwd=self.workspace.root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,shell=False,creationflags=creationflags)
            # Job Objects improve descendant cancellation. They do not restrict
            # filesystem, network, registry, or host-user permissions.
            process_job=WindowsProcessJob.attach(process.pid)
            if os.name=="nt" and not WindowsProcessJob.resume_primary_thread(process.pid):
                if process_job is not None:process_job.terminate()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                return "Command error: could not safely resume the process after assigning its cancellation Job Object."
            deadline=time.monotonic()+limit if limit is not None else None
            chunks={"stdout":[],"stderr":[]};output:queue.Queue=queue.Queue();readers=[];known_descendants={};last_tree_scan=0.0;drain_deadline=None;incomplete_output=False
            def scan_descendants():
                try:
                    for child in psutil.Process(process.pid).children(recursive=True):known_descendants[child.pid]=child
                except (psutil.Error,OSError):pass
            def terminate_tree():
                if process_job is not None:
                    process_job.terminate()
                scan_descendants()
                try:root_process=psutil.Process(process.pid)
                except (psutil.Error,OSError):root_process=None
                targets=list(known_descendants.values())
                if root_process is not None:targets.append(root_process)
                for child in reversed(targets):
                    try:
                        if child.is_running():child.terminate()
                    except (psutil.Error,OSError):pass
                try:
                    if process.poll() is None:process.terminate()
                except OSError:pass
                try:_,alive=psutil.wait_procs(targets,timeout=1.0)
                except (psutil.Error,OSError):alive=[]
                for child in alive:
                    try:child.kill()
                    except (psutil.Error,OSError):pass
                try:
                    if process.poll() is None:process.kill()
                except OSError:pass
            def pump(name,pipe):
                decoder=codecs.getincrementaldecoder("utf-8")("replace")
                try:
                    while True:
                        raw=os.read(pipe.fileno(),4096)
                        if not raw:break
                        text=decoder.decode(raw)
                        if text:output.put((name,text))
                    tail=decoder.decode(b"",final=True)
                    if tail:output.put((name,tail))
                finally:output.put((name,None))
            for name,pipe in (("stdout",process.stdout),("stderr",process.stderr)):
                reader=threading.Thread(target=pump,args=(name,pipe),daemon=True);reader.start();readers.append(reader)
            remaining=len(readers);stopped=False;timed_out=False;terminate_at=None
            while remaining:
                now=time.monotonic()
                if now-last_tree_scan>=0.05:
                    scan_descendants();last_tree_scan=now
                if not stopped and self.cancel_event.is_set():
                    stopped=True;terminate_at=now+1;drain_deadline=now+5;terminate_tree()
                elif not stopped and deadline is not None and now>=deadline:
                    stopped=True;timed_out=True;terminate_at=now+1;drain_deadline=now+5;terminate_tree()
                elif stopped and terminate_at is not None and now>=terminate_at and process.poll() is None:
                    terminate_tree()
                    terminate_at=None
                if stopped and drain_deadline is not None and now>=drain_deadline and remaining:
                    incomplete_output=True;break
                try:name,text=output.get(timeout=.05)
                except queue.Empty:continue
                if text is None:remaining-=1;continue
                chunks[name].append(text)
                self.event({"type":"operation_output","stream":name,"text":text,"at":time.time()})
            for reader in readers:reader.join(timeout=1)
            for pipe in (process.stdout,process.stderr):
                if pipe:pipe.close()
            if process.poll() is None:
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            result="".join(chunks["stdout"])+"\n"+"".join(chunks["stderr"])
            if incomplete_output:result+="\n[command process tree was stopped, but an output pipe did not close; remaining output may be incomplete]"
            if timed_out:return result+f"\n[command timed out after {limit}s]"
            if self.cancel_event.is_set():return result+"\n[command cancelled by user]"
            return result+f"\n[exit {process.returncode}]"
        except (OSError,ValueError) as exc: return f"Command error: {exc}"
        finally:
            if process_job is not None:
                process_job.close()

def _utf8_prefix(value:str,budget:int)->tuple[str,int]:
    used=0;index=0
    while index<len(value):
        size=len(value[index].encode("utf8"))
        if used+size>budget:break
        used+=size;index+=1
    return value[:index],index

def _utf8_suffix(value:str,budget:int)->tuple[str,int]:
    used=0;index=len(value)
    while index>0:
        size=len(value[index-1].encode("utf8"))
        if used+size>budget:break
        used+=size;index-=1
    return value[index:],index

def tool_result_for_context(value:str,byte_budget:int,operation_id:str)->str:
    """Keep large tool results complete on disk and provide a bounded resumable model excerpt."""
    byte_budget=max(192,int(byte_budget))
    if len(value.encode("utf8"))<=byte_budget:return value
    content_budget=max(0,(byte_budget-240)//2)
    head_budget=content_budget*2//3;tail_budget=content_budget-head_budget
    while True:
        head,head_end=_utf8_prefix(value,head_budget)
        tail,tail_start=_utf8_suffix(value,tail_budget)
        omitted=max(0,tail_start-head_end)
        marker=(f"[Tool result excerpt: full {len(value):,}-character result remains saved. "
                f"Omitted characters {head_end}-{tail_start}; continue with read_operation_result "
                f"operation_id={operation_id} offset={head_end} length={omitted}.]")
        excerpt=head+"\n\n"+marker+"\n\n"+tail
        if len(excerpt.encode("utf8"))<=byte_budget:return excerpt
        if head_budget==0 and tail_budget==0:return marker[:byte_budget]
        head_budget=head_budget*3//4;tail_budget=tail_budget*3//4

def _split_turn_for_compaction(turn:list[dict])->list[list[dict]]:
    """Keep the user's instruction separate from complete assistant/tool exchanges."""
    if not turn or turn[0].get("role")!="user":return [turn]
    groups=[[turn[0]]]
    index=1
    while index<len(turn):
        message=turn[index]
        calls=message.get("tool_calls") if message.get("role")=="assistant" else None
        if not isinstance(calls,list) or not calls:
            groups.append([message]);index+=1;continue
        exchange=[message];remaining=len(calls);index+=1
        while index<len(turn) and remaining:
            exchange.append(turn[index])
            if turn[index].get("role")=="tool":remaining-=1
            index+=1
        groups.append(exchange)
    return groups

class ContextManager:
    def __init__(self, context: int=CONTEXT_MAX, reserve:int=0,scan_file_limit:int|None=None,scan_bytes_limit:int|None=None): self.context,self.reserve=context,reserve;self.scan_file_limit,self.scan_bytes_limit=scan_file_limit,scan_bytes_limit
    @property
    def input_budget(self): return max(1024,self.context-self.reserve)
    def budget(self,output_tokens:int=0)->int:return max(0,self.context-max(self.reserve,output_tokens))
    @staticmethod
    def estimate(text:str)->int:
        # UTF-8 bytes are not tokens. Two bytes per token is deliberately
        # conservative for prose and compact JSON tool schemas.
        return max(1,(len(text.encode("utf8"))+1)//2)
    def retrieve(self,root:Path,prompt:str,limit:int|None=None,cancel_event:threading.Event|None=None)->str:
        limit=self.budget() if limit is None else max(0,limit)
        stop_words={"about","after","also","and","are","but","can","could","does","from","have","into","like","make","need","please","that","the","this","want","with","would"}
        terms={w.casefold() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}",prompt)}-stop_words; ranked=[];examined=0;bytes_read=0;capped=False
        if not terms:return ""
        retrieval_scan_max=self.scan_file_limit+1 if self.scan_file_limit is not None else None
        for p in iter_project_files(root,max_files=retrieval_scan_max):
            if cancel_event is not None and cancel_event.is_set():capped=True;break
            examined+=1
            if self.scan_file_limit is not None and examined>self.scan_file_limit:
                capped=True;break
            if p.suffix.casefold() not in TEXT_EXT:continue
            try:
                size=p.stat().st_size
                if self.scan_bytes_limit is not None and bytes_read+size>self.scan_bytes_limit:capped=True;break
                bytes_read+=size
                path_tokens={word.casefold() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}",p.relative_to(root).as_posix())}
                path_score=20*len(terms & path_tokens)
                matching_lines={};content_score=0;capturing_until=0;recent=[];match_count=0
                with p.open("r",encoding="utf8") as handle:
                    for line_number,line in enumerate(handle,1):
                        if cancel_event is not None and cancel_event.is_set():capped=True;break
                        folded=line.casefold();line_terms={term for term in terms if term in folded}
                        if line_terms:
                            match_count+=1;content_score+=min(8,len(line_terms)*2)
                            for previous_number,previous_line in recent:matching_lines[previous_number]=previous_line
                            matching_lines[line_number]=line.rstrip("\r\n");capturing_until=max(capturing_until,line_number+4)
                        elif line_number<=capturing_until:
                            matching_lines[line_number]=line.rstrip("\r\n")
                        recent.append((line_number,line.rstrip("\r\n")))
                        if len(recent)>4:recent.pop(0)
            except (OSError,UnicodeError):continue
            rel=p.relative_to(root).as_posix();score=path_score+content_score
            if score:
                excerpts=[];line_numbers=sorted(matching_lines)
                if line_numbers:
                    start=previous=line_numbers[0]
                    for number in line_numbers[1:]:
                        if number>previous+1:
                            excerpts.append((start,previous));start=number
                        previous=number
                    excerpts.append((start,previous))
                snippets="\n…\n".join("\n".join(f"{number}: {matching_lines[number]}" for number in range(start,end+1)) for start,end in excerpts)
                if match_count>len(excerpts):snippets+=f"\n[Showing {len(excerpts)} matching excerpt groups from {match_count} matching lines.]"
                ranked.append((score,rel,snippets))
        used=0; out=[];omitted_for_budget=False
        for score,rel,snippets in sorted(ranked,key=lambda x:(-x[0],x[1].casefold())):
            block=f"--- relevant file: {rel} (relevance {score}) ---\n{snippets or '[Path matched the task; inspect this file with read_file.]'}"; size=self.estimate(block)
            if used+size>limit:omitted_for_budget=True;continue
            out.append(block);used+=size
        result="\n\n".join(out)
        notice=(f"[Project retrieval scan capped after {examined} files / {bytes_read:,} text bytes; results may be incomplete.]" if capped else "")
        if omitted_for_budget:notice+=(" " if notice else "")+"[Additional relevant excerpts did not fit this request's remaining context budget; use search_files/read_file to inspect more.]"
        if notice and self.estimate((result+"\n\n" if result else "")+notice)<=limit:result+=("\n\n" if result else "")+notice
        return result
    def compact(self,messages:list[dict],tools:list[dict]|None=None,output_tokens:int=0,on_compact:Callable[[dict],None]|None=None,checkpoint:dict[str,Any]|None=None)->list[dict]:
        tools=tools or [];budget=self.budget(output_tokens)
        def request_size(rows):return self.estimate(json.dumps({"messages":rows,"tools":tools},ensure_ascii=False,separators=(",",":")))
        tool_size=self.estimate(json.dumps(tools,ensure_ascii=False,separators=(",",":"))) if tools else 0
        if tool_size+max(self.reserve,output_tokens)>=self.context:raise ValueError(f"Tool definitions and output reserve exceed the {self.context:,}-token context; context was not reduced.")
        # Ollama chat templates (including the selected Ornith GGUF template)
        # require every system instruction to be in the first message. Keep
        # saved history intact, but normalize provider-bound context into one
        # leading system message before adding the task checkpoint.
        system_messages=[message for message in messages if message.get("role")=="system"]
        conversation=[message for message in messages if message.get("role")!="system"]
        system_content="\n\n".join(str(message.get("content", "")) for message in system_messages if message.get("content"))
        full_checkpoint_text=render_checkpoint(checkpoint)
        checkpoint_text=full_checkpoint_text
        system_base=system_content
        def assemble_base(checkpoint_value:str)->list[dict]:
            combined=system_base
            if checkpoint_value:
                combined=(combined+"\n\n" if combined else "")+checkpoint_value
            return [{"role":"system","content":combined}] if combined else []
        base=assemble_base(checkpoint_text)
        request=base+conversation
        total=request_size(request)
        # Preserve the complete checkpoint whenever it fits with the current
        # conversation. Only spend its detail when actual context pressure
        # requires compaction, so a fixed share cannot discard useful history
        # from otherwise-fitting requests.
        if total>budget and checkpoint_text:
            checkpoint_target=max(64,budget//4)
            if self.estimate(checkpoint_text)>checkpoint_target:
                checkpoint_text=render_checkpoint(checkpoint,token_budget=checkpoint_target)
                base=assemble_base(checkpoint_text)
                request=base+conversation
                total=request_size(request)
        checkpoint_omitted=checkpoint_omissions(checkpoint_text)
        if total<=budget:
            if checkpoint_text!=full_checkpoint_text and on_compact:
                on_compact({"type":"context_compacted","omittedTurns":0,"retainedTurns":0,"omittedOperationGroups":0,
                            "configuredContext":self.context,"inputBudget":budget,"checkpointIncluded":bool(checkpoint_text),
                            "checkpointEstimate":self.estimate(checkpoint_text) if checkpoint_text else 0,
                            "checkpointCompacted":True,"checkpointOmissions":checkpoint_omitted,
                            "reason":"task checkpoint summarized to preserve input budget; canonical session checkpoint is unchanged"})
            return request
        if len(messages)<3:raise ValueError(f"Request exceeds input budget for {self.context:,}-token context; context was not reduced.")
        # Compact only at complete user-turn boundaries. Keep assistant tool-call messages
        # together with every corresponding tool result; partial pairs are invalid provider input.
        turns=[];current=[]
        for message in conversation:
            if message.get("role")=="user" and current:
                turns.append(current);current=[]
            current.append(message)
        if current:turns.append(current)
        if not turns:raise ValueError(f"Request exceeds input budget for configured {self.context:,}-token context; no complete turn boundary is available for safe compaction.")
        kept=[];omitted_groups=0
        for turn in reversed(turns):
            candidate=[turn]+kept
            dropped=len(turns)-len(candidate)
            notice=[]
            if dropped:
                notice=[{"role":"user","content":f"[Context notice: {dropped} earlier complete conversation turn(s) were omitted from this inference request. The full transcript, including emitted assistant reasoning, and tool results remain saved; use read_conversation_history for omitted user/assistant content and reasoning, read_task_checkpoint for saved decisions/plans/progress, and saved operation references for tool results. Do not claim omitted material was reviewed unless the checkpoint or a retrieval tool confirms it.]"}]
            proposed=base+notice+[m for group in candidate for m in group]
            if request_size(proposed)>budget:
                if not kept:
                    if turn is turns[-1]:
                        blocks=_split_turn_for_compaction(turn)
                        if len(blocks)>1:
                            retained=[]
                            for block in reversed(blocks[1:]):
                                trial=[block]+retained
                                missing=len(blocks)-1-len(trial)
                                within_notice=([{"role":"user","content":f"[Active task compaction: {missing} earlier assistant/tool exchange group(s) from this same task are omitted from this request. Their full transcript, including emitted assistant reasoning, and outputs remain saved. Use read_conversation_history for omitted user/assistant content and reasoning; use operation IDs in the task checkpoint with read_operation_result for full tool output; inspect the workspace before repeating any action.]"}] if missing else [])
                                candidate_turn=blocks[0]+within_notice+[message for group in trial for message in group]
                                if request_size(base+notice+candidate_turn)>budget:break
                                retained=trial
                            omitted_groups=len(blocks)-1-len(retained)
                            within_notice=([{"role":"user","content":f"[Active task compaction: {omitted_groups} earlier assistant/tool exchange group(s) from this same task are omitted from this request. Their full transcript, including emitted assistant reasoning, and outputs remain saved. Use read_conversation_history for omitted user/assistant content and reasoning; use operation IDs in the task checkpoint with read_operation_result for full tool output; inspect the workspace before repeating any action.]"}] if omitted_groups else [])
                            compact_turn=blocks[0]+within_notice+[message for group in retained for message in group]
                            if request_size(base+notice+compact_turn)<=budget:
                                turn=compact_turn
                                candidate=[turn]+kept
                                proposed=base+notice+[m for group in candidate for m in group]
                            else:
                                raise ValueError(f"The current user prompt and compaction notice exceed the {budget:,}-token input budget at configured context {self.context:,}; context/output settings were not changed.")
                        else:
                            raise ValueError(f"The current complete conversation turn exceeds the {budget:,}-token input budget at configured context {self.context:,}; context/output settings were not changed.")
                    else:
                        raise ValueError(f"The current complete conversation turn exceeds the {budget:,}-token input budget at configured context {self.context:,}; context/output settings were not changed.")
                else:break
            kept=candidate
        dropped=len(turns)-len(kept)
        notice=[]
        if dropped:
            notice=[{"role":"user","content":f"[Context notice: {dropped} earlier complete conversation turn(s) were omitted from this inference request. The full transcript, including emitted assistant reasoning, and tool results remain saved; use read_conversation_history for omitted user/assistant content and reasoning, read_task_checkpoint for saved decisions/plans/progress, and saved operation references for tool results. Do not claim omitted material was reviewed unless the checkpoint or a retrieval tool confirms it.]"}]
        compacted=base+notice+[m for group in kept for m in group]
        # If the notice itself pushes the request over budget, discard complete older turns,
        # never individual assistant/tool messages or fragments of user text.
        while request_size(compacted)>budget and len(kept)>1:
            kept=kept[1:];dropped=len(turns)-len(kept)
            notice=[{"role":"user","content":f"[Context notice: {dropped} earlier complete conversation turn(s) were omitted from this inference request. The full transcript, including emitted assistant reasoning, and tool results remain saved; use read_conversation_history for omitted user/assistant content and reasoning, read_task_checkpoint for saved decisions/plans/progress, and saved operation references for tool results. Do not claim omitted material was reviewed unless the checkpoint or a retrieval tool confirms it.]"}]
            compacted=base+notice+[m for group in kept for m in group]
        if request_size(compacted)>budget:
            raise ValueError(f"Current turn and context notice exceed the {budget:,}-token input budget at configured context {self.context:,}; context/output settings were not changed.")
        if on_compact:
            on_compact({"type":"context_compacted","omittedTurns":dropped,"retainedTurns":len(kept),"omittedOperationGroups":omitted_groups,"configuredContext":self.context,"inputBudget":budget,"checkpointIncluded":bool(checkpoint_text),"checkpointEstimate":self.estimate(checkpoint_text) if checkpoint_text else 0,"checkpointCompacted":checkpoint_text!=full_checkpoint_text,"checkpointOmissions":checkpoint_omitted,"reason":"budgeted task checkpoint plus recent complete-turn tail and complete current-task operation exchanges"})
        return compacted

class OllamaRuntime:
    def __init__(self,base_url:str="http://127.0.0.1:11434"):
        self.base_url=base_url.rstrip("/");self.response=None;self.connection=None;self.lock=threading.Lock();self.cancelled_waiting_headers=False
    def cancel(self):
        with self.lock:
            connection=self.connection;response=self.response
            if connection is not None and response is None:self.cancelled_waiting_headers=True
        # The cancel control must return immediately even if the HTTP reader
        # owns a buffered-response lock. Socket shutdown wakes that reader; its
        # own finally block performs definitive response/connection cleanup.
        def interrupt():
            interrupt_connection(connection,response)
        threading.Thread(target=interrupt,daemon=True).start()
    def get(self,path:str,payload:dict|None=None,timeout:int=5)->dict:
        req=urllib.request.Request(self.base_url+path,data=json.dumps(payload).encode() if payload is not None else None,headers={"Content-Type":"application/json"} if payload is not None else {})
        with urllib.request.urlopen(req,timeout=timeout) as response:return json.loads(response.read().decode())
    def models(self)->list[str]:return [x.get("name","") for x in self.get("/api/tags").get("models",[]) if x.get("name")]
    def model_info(self,model:str)->dict:return self.get("/api/show",{"model":model})
    def loaded(self)->list[dict]:return self.get("/api/ps").get("models",[])
    def preload(self,model:str,context:int,keep_alive:str)->dict:
        """Warm one Ollama model using its selected context, without generating a reply."""
        if not isinstance(context,int) or isinstance(context,bool) or context<1024:
            raise ValueError("Preload context must be an integer of at least 1,024 tokens.")
        payload={"model":model,"prompt":"","stream":False,"keep_alive":keep_alive,"options":{"num_ctx":context}}
        request=urllib.request.Request(self.base_url+"/api/generate",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST")
        with urllib.request.urlopen(request,timeout=None) as response:return json.loads(response.read().decode())
    def stream(self,state:dict,messages:list[dict],cancel:threading.Event,emit:Callable[[dict],None])->dict:
        configured_options=state.get("runtime_options")
        configured_options=configured_options if isinstance(configured_options,dict) else {}
        controlled_options={"temperature","num_ctx","num_predict"}
        ignored_overrides={key:copy.deepcopy(configured_options[key]) for key in controlled_options if key in configured_options}
        request_options={key:copy.deepcopy(value) for key,value in configured_options.items() if key not in controlled_options}
        request_options.update(temperature=state["temperature"],num_ctx=state["context"],num_predict=state["max_tokens"])
        request_tools=state.get("request_tools")
        request_tools=request_tools if isinstance(request_tools,list) else state["registry"].schemas+state.get("mcp_tools",[])
        provider_messages=_provider_message_history(messages)
        payload={"model":state["model"],"messages":provider_messages,"stream":True,"tools":request_tools,"keep_alive":state.get("keep_alive","30m"),
                 "options":request_options}
        if state.get("reasoning") is not None:payload["think"]=state["reasoning"]
        estimated_input=ContextManager.estimate(json.dumps({"messages":provider_messages,"tools":payload["tools"]},ensure_ascii=False,separators=(",",":")))
        additional_fields={"think":payload["think"]} if "think" in payload else {}
        if ignored_overrides:additional_fields["ignored_runtime_option_overrides"]=ignored_overrides
        emit({"type":"inference_settings","provider":"ollama","request_api":"/api/chat","request_index":state.get("request_index"),"model":state["model"],"api_model":payload["model"],"stream":payload["stream"],"tool_count":len(payload["tools"]),"tool_names":[(item.get("function") or {}).get("name","") for item in payload["tools"]],"deferred_tool_names":state.get("deferred_tool_names",[]),"requested_context":state["context"],"max_output_tokens":state["max_tokens"],"reasoning_override":state.get("reasoning"),"reasoning_field_sent":"think" in payload,"keep_alive":payload["keep_alive"],"request_options":payload["options"],"additional_fields":additional_fields,"estimated_input_tokens":estimated_input,"input_estimate_method":"conservative UTF-8 byte estimate; provider count replaces this after response"})
        req=urllib.request.Request(self.base_url+"/api/chat",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json","Accept":"application/x-ndjson"},method="POST")
        result={"message":{"role":"assistant","content":""}};calls={};call_ids={};request_started=time.monotonic();first_output=True;user_facing_output=False
        dispatch_time={"value":None};headers_received_flag={"value":False};request_index=state.get("request_index")
        try:
            with self.lock:
                self.cancelled_waiting_headers=False
            def register(connection,response):
                with self.lock:self.connection=connection;self.response=response
            def dispatched():
                dispatch_time["value"]=time.monotonic()
                emit({"type":"request_dispatched","provider":"ollama","request_index":request_index})
            def headers_received(response):
                headers_received_flag["value"]=True
                elapsed=time.monotonic()-dispatch_time["value"] if dispatch_time["value"] is not None else None
                emit({"type":"request_headers","provider":"ollama","request_index":request_index,
                      "http_status":response.status,"http_reason":response.reason,"header_wait_seconds":elapsed})
            response=open_http_stream(req,cancel,register,on_dispatched=dispatched,on_headers=headers_received)
            with self.lock:
                if self.response is None:self.response=response
            while True:
                if cancel.is_set():
                    partial={key:result["message"][key] for key in ("content","thinking") if result["message"].get(key)}
                    raise RequestCancelled("Cancelled",{"role":"assistant",**partial} if partial else None)
                line=response.readline()
                if not line:break
                item=json.loads(line.decode());msg=item.get("message") or {};text=msg.get("content") or "";thinking=msg.get("thinking") or ""
                if first_output and (text or thinking or msg.get("tool_calls")):
                    first_output=False
                    phase="generating" if text or msg.get("tool_calls") else "reasoning"
                    user_facing_output=phase=="generating"
                    emit({"type":"phase","phase":phase,"elapsed":time.monotonic()-request_started})
                elif not user_facing_output and (text or msg.get("tool_calls")):
                    user_facing_output=True
                    emit({"type":"phase","phase":"generating","elapsed":time.monotonic()-request_started})
                if thinking:result["message"]["thinking"]=result["message"].get("thinking","")+thinking;emit({"type":"thinking","text":thinking,"at":time.monotonic()})
                if text:result["message"]["content"]+=text;emit({"type":"text","text":text,"at":time.monotonic()})
                for idx,call in enumerate(msg.get("tool_calls") or []):
                    fn=call.get("function") or {};provider_id=call.get("id")
                    ordinal=call.get("index",fn.get("index",idx))
                    if provider_id and provider_id in call_ids:ordinal=call_ids[provider_id]
                    if not isinstance(ordinal,int) or isinstance(ordinal,bool):ordinal=idx
                    if provider_id:call_ids[provider_id]=ordinal
                    slot=calls.setdefault(ordinal,{"type":"function","function":{"name":"","arguments":""}})
                    if provider_id:slot["id"]=provider_id
                    if call.get("type"):slot["type"]=call["type"]
                    if fn.get("name"):slot["function"]["name"]=fn["name"]
                    args=fn.get("arguments","")
                    if isinstance(args,str):slot["function"]["arguments"]+=args
                    elif isinstance(args,dict):slot["function"]["arguments"]=args
                    partial_args=slot["function"].get("arguments","")
                    argument_characters=(len(partial_args) if isinstance(partial_args,str)
                                         else len(json.dumps(partial_args,ensure_ascii=False,separators=(",",":"))))
                    emit({"type":"tool_call_progress","provider":"ollama","request_index":request_index,
                          "call_index":ordinal,"tool_name":slot["function"].get("name",""),
                          "argument_characters":argument_characters,"at":time.time()})
                for key in ("done","done_reason","total_duration","load_duration","prompt_eval_count","prompt_eval_cached_count","eval_count","prompt_eval_duration","eval_duration"):
                    if key in item:result[key]=item[key]
            if cancel.is_set():
                partial={key:result["message"][key] for key in ("content","thinking") if result["message"].get(key)}
                raise RequestCancelled("Cancelled",{"role":"assistant",**partial} if partial else None)
            if calls:
                for assembled in calls.values():
                    raw=assembled["function"].get("arguments","")
                    if isinstance(raw,str):
                        try:assembled["function"]["arguments"]=json.loads(raw or "{}")
                        except json.JSONDecodeError:pass
                result["message"]["tool_calls"]=[calls[i] for i in sorted(calls)]
            if result.get("done") is not True:
                raise RuntimeError("Ollama stream closed without its final done=true record; received output was preserved as partial.")
            emit({"type":"request_complete","provider":"ollama","request_index":request_index,
                  "provider_done":result.get("done"),"provider_done_reason":result.get("done_reason")})
            return result
        except Exception as exc:
            with self.lock:active_response=self.response
            response_is_error=active_response is not None and getattr(active_response,"status",0) >= 400
            has_partial_stream=bool(result["message"].get("content") or result["message"].get("thinking") or calls)
            failure_phase=("provider_http_error" if response_is_error else
                           "waiting_response_headers" if cancel.is_set() and self.cancelled_waiting_headers else
                           "streaming_response" if headers_received_flag["value"] or has_partial_stream else
                           "waiting_response_headers" if dispatch_time["value"] is not None else "connecting_or_sending")
            emit({"type":"request_error","provider":"ollama","request_index":request_index,
                  "phase":failure_phase,
                  "cancelled":cancel.is_set(),"error":str(exc)})
            if cancel.is_set():
                partial={key:result["message"][key] for key in ("content","thinking") if result["message"].get(key)}
                raise RequestCancelled("Cancelled",{"role":"assistant",**partial} if partial else None) from exc
            partial={key:result["message"][key] for key in ("content","thinking") if result["message"].get(key)}
            raise InferenceStreamError(str(exc),{"role":"assistant",**partial} if partial else None) from exc
        finally:
            with self.lock:connection=self.connection;active_response=self.response;self.connection=None;self.response=None
            interrupt_connection(connection,active_response)

class AgentEngine:
    def __init__(self,root:Path,model:str=MODEL,context:int=CONTEXT_MAX,access:str="ask",approval_mode:str="per-action",event:Callable[[dict],None]|None=None,approve:Callable[[str,Any],bool]|None=None,base_url:str="http://127.0.0.1:11434",runtime_options:dict|None=None,max_tokens:int=4096,temperature:float=0.2,project_instructions:str="",mcp_tools:list[dict]|None=None,mcp_call:Callable|None=None,harness_profile:str="standard",task_checkpoint:dict[str,Any]|None=None,skills_enabled:bool=True,skill_sources:set[str]|list[str]|tuple[str,...]|None=None,task_intent:str="auto",operation_result_lookup:Callable[[str],str|None]|None=None,permission_rules:list[dict[str,Any]]|None=None,ask_user:Callable[[dict[str,Any]],str]|None=None,session_approval_granted:bool=False,steering_handler:Callable[[list[dict],bool],bool]|None=None,permission_workspace_root:Path|str|None=None,steering_interrupt_event:threading.Event|None=None):
        self.workspace=Workspace(root,access);self.knowledge=ProjectKnowledge(root);self.project_instructions=project_instructions or self.knowledge.instructions();self.model=model;self.context=ContextManager(context);self.approval_mode=approval_mode;self.approved_task=False;self.approved_session=bool(session_approval_granted);self.mcp_tools=[] if access=="plan" else (mcp_tools or []);self.mcp_call=mcp_call
        self.event=event or (lambda e:None);self.approve=approve or (lambda n,a:False);self.runtime=OllamaRuntime(base_url);self.cancel_event=threading.Event();self.stop_after_operation=threading.Event();self._approval_lock=threading.RLock()
        self.harness_profile=harness_profile if harness_profile in {"standard","small-model"} else "standard"
        self.task_intent=task_intent if task_intent in {"auto","discuss","plan","implement"} else "auto"
        self.steering_handler=steering_handler
        self.steering_interrupt_event=steering_interrupt_event or threading.Event()
        self.inference_cancel_signal=_InferenceCancelSignal(self.cancel_event,self.steering_interrupt_event)
        # The app supplies a durable session lookup. Keep an in-memory fallback
        # as well so standalone AgentEngine/CLI sessions can recover earlier
        # operation output without advertising a tool that cannot work.
        self.operation_results:dict[str,str]={}
        def lookup_operation_result(operation_id:str)->str|None:
            if operation_result_lookup is not None:
                saved=operation_result_lookup(operation_id)
                if saved is not None:return saved
            return self.operation_results.get(operation_id)
        checkpoint=task_checkpoint if isinstance(task_checkpoint,dict) else {}
        self.permission_workspace_root=Path(permission_workspace_root or root)
        self.permission_rules=permission_rules_for_workspace(validate_permission_rules(permission_rules or []),self.permission_workspace_root)
        read_output_budget=self.context.budget(max_tokens)
        registry=ToolRegistry(self.workspace,self._approval,self.event,self.cancel_event,skills_enabled=skills_enabled,skill_sources=skill_sources,operation_result_lookup=lookup_operation_result,permission_rules=self.permission_rules,task_checkpoint=checkpoint,read_output_token_budget=read_output_budget,ask_user=ask_user)
        registry.task_intent=self.task_intent
        explicit_task_mode={"discuss":"discussion","plan":"plan","implement":"implement"}.get(self.task_intent)
        if explicit_task_mode:
            registry.task_mode=explicit_task_mode
            current_task(checkpoint)["task_mode"]=explicit_task_mode
        registry.plan=copy.deepcopy(current_task(checkpoint).get("plan",[]))
        checkpoint_state=current_task(checkpoint)
        registry.plan_source=_saved_plan_source(registry.task_mode,registry.plan,checkpoint_state.get("plan_source"))
        if not (isinstance(checkpoint_state.get("plan_source"),str)
                and checkpoint_state.get("plan_source") in {"agent", "workbench"}) and registry.plan:
            checkpoint_state["plan_source"]=registry.plan_source
        self._selected_tool_names:set[str]=set()
        self._deferred_tool_names:list[str]=[]
        self.state={"project_instructions":project_instructions,"model":model,"context":context,"temperature":temperature,"max_tokens":max_tokens,"reasoning_budget_tokens":None,"runtime_options":runtime_options or {},"registry":registry,"mcp_tools":self.mcp_tools,"messages":[],"task_checkpoint":checkpoint,"skills_enabled":bool(skills_enabled),"skill_sources":tuple(("project","user") if skill_sources is None else skill_sources),"request_tools":None,"deferred_tool_names":[]}
    def _advance_workbench_plan(self,name:str,status:str,*,final:bool=False,verification:str|None=None,project_changed:bool=False,post_change_inspection:bool=False)->None:
        """Reflect host-observed work in the starter checklist without rewriting agent plans."""
        registry=self.state["registry"]
        plan=registry.plan
        if not plan or registry.plan_source!="workbench":return
        inspect_tools={"list_files","search_files","read_file","git_status","git_diff","git_log"}
        write_tools={"write_file","append_file","edit_file","apply_patch"}
        changed=False
        for item in plan:
            if not isinstance(item,dict):continue
            phase=str(item.get("phase", "")).casefold()
            if not phase:
                # Migrate old host checklists without relying on their display text going forward.
                title=str(item.get("title","")).casefold()
                phase=("inspect" if "inspect relevant project context" in title else
                       "implementation" if "implement the requested changes" in title else
                       "verification" if "inspect the changes and run relevant approved checks" in title else
                       "planning" if "refine " in title else "")
            target=None
            if phase=="inspect" and name in inspect_tools:
                target="completed" if status=="completed" else "in_progress"
            elif phase=="planning" and name=="update_plan" and status=="completed":
                target="completed"
            elif phase=="implementation" and name in write_tools|{"run_command","run_shell"}:
                target="completed" if final and project_changed else "in_progress"
            elif phase=="verification":
                if name in inspect_tools|{"run_command","run_shell"} and status=="completed":target="in_progress"
                if final and (verification=="command_completed" or (verification=="inspected" and post_change_inspection)):target="completed"
            elif phase=="present" and final:
                target="completed"
            elif phase=="task_progress" and final:
                target="completed"
            if target and item.get("status")!=target:
                item["status"]=target;changed=True
        if changed:
            checkpoint=self.state["task_checkpoint"]
            current=current_task(checkpoint)
            current["plan"]=copy.deepcopy(plan)
            pending=next((item for item in plan if plan_item_is_open(item)),None)
            current["next_action"]=(str((pending or {}).get("title") or (pending or {}).get("text") or "Continue the active task plan.")
                                    if pending else "")
            self.event({"type":"plan","items":copy.deepcopy(plan),"acceptanceCriteria":copy.deepcopy(current.get("acceptance_criteria",[])),"source":"workbench"})
    def _approval(self,name:str,args:Any)->bool:
        with self._approval_lock:
            return self._approval_locked(name,args)
    def _approval_locked(self,name:str,args:Any)->bool:
        checkpoint=self.state["task_checkpoint"]
        policy=permission_decision(name,args,self.permission_rules)
        if policy=="deny":
            record_decision(checkpoint,{"kind":"permission_rule","tool":name,"decision":"deny"})
            return False
        if policy=="allow":
            record_decision(checkpoint,{"kind":"permission_rule","tool":name,"decision":"allow"})
            return True
        if policy=="ask":
            ok=self.approve(name,args)
            if self.cancel_event.is_set():
                record_decision(checkpoint,{"kind":"permission_rule","tool":name,"decision":"cancelled","rule":"ask"})
                raise RequestCancelled("Cancelled while waiting for permission-rule approval")
            record_decision(checkpoint,{"kind":"permission_rule","tool":name,"decision":"approved" if ok else "denied","rule":"ask"})
            return ok
        if self.approval_mode=="auto":
            record_decision(checkpoint,{"kind":"tool_approval","tool":name,"decision":"auto-approved","mode":"auto"})
            return True
        if self.approval_mode=="per-task" and self.approved_task:
            record_decision(checkpoint,{"kind":"tool_approval","tool":name,"decision":"covered-by-prior-task-approval","mode":"per-task"})
            return True
        if self.approval_mode=="per-session" and self.approved_session:
            record_decision(checkpoint,{"kind":"tool_approval","tool":name,"decision":"covered-by-prior-session-approval","mode":"per-session"})
            return True
        ok=self.approve(name,args)
        if self.cancel_event.is_set():
            record_decision(checkpoint,{"kind":"tool_approval","tool":name,"decision":"cancelled","mode":self.approval_mode})
            raise RequestCancelled("Cancelled while waiting for approval")
        record_decision(checkpoint,{"kind":"tool_approval","tool":name,"decision":"approved" if ok else "denied","mode":self.approval_mode})
        if ok and self.approval_mode=="per-task":self.approved_task=True
        if ok and self.approval_mode=="per-session":self.approved_session=True
        return ok
    def cancel(self):self.cancel_event.set();self.runtime.cancel()
    def finish_after_operation(self):self.stop_after_operation.set()
    def _tools_for_request(self,base_messages:list[dict],selected_names:set[str]|None=None)->list[dict]:
        catalog=copy.deepcopy(self.state["registry"].schemas+self.mcp_tools)
        selected_names=set(self._selected_tool_names if selected_names is None else selected_names)
        by_name={(tool.get("function") or {}).get("name"):tool for tool in catalog}
        unknown=selected_names-set(by_name)
        if unknown:raise ValueError("Selected tool schema is no longer available in this workspace: "+", ".join(sorted(unknown)))
        # Tool schemas are part of the actual model input budget. Keep catalog
        # discovery available, then fit broadly useful and task-relevant tools
        # into the remaining budget. A large fixed "required" bundle made a
        # valid 4K request fail before the model could do any work; deferred
        # capabilities remain selectable through load_tools.
        required_names={"load_tools"}
        required=[name for name in by_name if name in required_names]
        terms={term.casefold() for term in re.findall(r"[a-zA-Z0-9_-]{3,}",str(base_messages[-1].get("content","")))}
        def score(tool:dict)->tuple[int,int]:
            fn=tool.get("function") or {};name=str(fn.get("name",""));description=str(fn.get("description",""))
            overlap=len(terms & {token.casefold() for token in re.findall(r"[a-zA-Z0-9_-]{3,}",name+" "+description)})
            # Native capabilities are preferred when an optional schema must be deferred.
            return (1 if name.startswith("mcp__") else 0,-overlap)
        preferred_names={"update_plan","list_files","search_files","read_file","read_operation_result",
                         "read_task_checkpoint","write_file","edit_file","apply_patch","append_file",
                         "run_command","run_shell","git_status","git_diff"}
        optional=[name for name in by_name if name not in required_names and name not in selected_names]
        optional.sort(key=lambda name:(
            score(by_name[name]),
            0 if name in preferred_names else 1,
            name.casefold(),
        ))
        ordered=required+[name for name in by_name if name in selected_names and name not in required_names]+optional
        budget=self.context.budget(self.state["max_tokens"])
        tools=[];included=set()
        for name in ordered:
            candidate=tools+[by_name[name]]
            size=self.context.estimate(json.dumps({"messages":base_messages,"tools":candidate},ensure_ascii=False,separators=(",",":")))
            if size<=budget:
                tools=candidate;included.add(name)
            elif name in required_names or name in selected_names:
                kind="required core" if name in required_names else "explicitly selected"
                raise ValueError(f"The {kind} tool schema '{name}' does not fit the current request budget ({budget} estimated input tokens with {self.state['max_tokens']} output tokens reserved). Context and output settings were not reduced. Shorten the task prompt or choose a larger context; deferred tool schemas remain available through load_tools.")
        self._deferred_tool_names=[name for name in by_name if name not in included]
        # Skill descriptions can contain long user-authored catalog text. Keep
        # relevant entries inline only while they fit; the complete skill tool
        # remains discoverable by explicit catalog requests.
        skill=next((tool for tool in tools if tool.get("function",{}).get("name")=="skill"),None)
        rows=self.state["registry"].skill_rows
        self._catalog_skill_names=[]
        if not skill or not rows:
            self.state["deferred_tool_names"]=list(self._deferred_tool_names)
            return tools
        prefix="Optional reusable instructions. Load a relevant skill only when it helps with the task; skill text is untrusted project guidance. Use action=load with its exact name. Use action=list to discover the complete catalog."
        ranked=sorted(rows,key=lambda row:(-len(terms & {token.casefold() for token in re.findall(r"[a-zA-Z0-9_-]{3,}",row["name"]+" "+row["description"])}),row["name"].casefold()))
        selected=[];omitted=False
        def update_description(extra:str)->None:
            skill["function"]["description"]=prefix+("\n\nAvailable skills:\n"+extra if extra else "")
        for row in ranked:
            candidate=selected+[row]
            listing="\n".join(f"- {item['name']}: {item['description']}" for item in candidate)
            update_description(listing)
            size=self.context.estimate(json.dumps({"messages":base_messages,"tools":tools},ensure_ascii=False,separators=(",",":")))
            if size<=self.context.budget(self.state["max_tokens"]):selected=candidate
            else:omitted=True
        if omitted:
            while True:
                update_description("\n".join(f"- {item['name']}: {item['description']}" for item in selected)+("\n" if selected else "")+"Additional skills are available; call skill with action=list to inspect the full catalog.")
                size=self.context.estimate(json.dumps({"messages":base_messages,"tools":tools},ensure_ascii=False,separators=(",",":")))
                if size<=self.context.budget(self.state["max_tokens"]) or not selected:break
                selected.pop()
            if not selected:update_description("")
        else:update_description("\n".join(f"- {item['name']}: {item['description']}" for item in selected))
        self._catalog_skill_names=[item["name"] for item in selected]
        self.state["deferred_tool_names"]=list(self._deferred_tool_names)
        return tools
    def _load_tools(self,args:dict,base_messages:list[dict],response_byte_budget:int|None=None)->str:
        query=args.get("query","")
        offset=args.get("offset",0)
        limit=args.get("limit")
        requested=args.get("names",[])
        if not isinstance(query,str):return "Invalid query: expected text."
        if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:return "Invalid offset: expected a non-negative integer."
        if limit is not None and (not isinstance(limit,int) or isinstance(limit,bool) or limit<1):return "Invalid limit: expected a positive integer."
        if not isinstance(requested,list) or any(not isinstance(name,str) for name in requested):return "Invalid names: expected an array of exact tool names."
        catalog=copy.deepcopy(self.state["registry"].schemas+self.mcp_tools)
        by_name={(tool.get("function") or {}).get("name"):tool for tool in catalog}
        if requested:
            unknown=[name for name in requested if name not in by_name]
            if unknown:return "Unavailable tool name(s): "+", ".join(unknown)+". Use the current project's visible catalog; access policy may hide tools."
            candidate=self._selected_tool_names|set(requested)
            try:self._tools_for_request(base_messages,candidate)
            except ValueError as exc:return f"Tool schemas were not selected: {exc}"
            self._selected_tool_names=candidate
        needle=query.casefold().strip()
        rows=[tool for tool in catalog if (not requested or tool.get("function",{}).get("name") in requested)
              and (not needle or needle in json.dumps(tool,ensure_ascii=False).casefold())]
        available_rows=rows[offset:offset+limit] if limit is not None else rows[offset:]
        if response_byte_budget is None:
            page=available_rows
        else:
            # An explicit page size is a preference, not permission to overflow
            # the remaining model-response budget. Honor it when it fits, then
            # let the model request the next page using next_offset.
            page=[]
            for row in available_rows:
                candidate=page+[row]
                preview={"total_matches":len(rows),"offset":offset,"next_offset":offset+len(candidate),
                         "selected_for_next_request":sorted(self._selected_tool_names),"schemas":candidate}
                serialized_size=len(json.dumps(preview,ensure_ascii=False).encode("utf8"))
                if serialized_size>max(0,response_byte_budget):
                    if page:break
                    name=(row.get("function") or {}).get("name","unknown")
                    return (f"Tool schema '{name}' needs {serialized_size:,} response bytes, but only "
                            f"{max(0,response_byte_budget):,} remain. No schema was returned; narrow the "
                            "catalog search or adjust the selected request settings yourself.")
                page=candidate
        next_offset=offset+len(page)
        result={"total_matches":len(rows),"offset":offset,"next_offset":next_offset if next_offset<len(rows) else None,
                "selected_for_next_request":sorted(self._selected_tool_names),"schemas":page}
        return json.dumps(result,ensure_ascii=False)
    def _tool_output_budget(self,request_messages:list[dict],assistant_message:dict,prior_tool_messages:list[dict],tools:list[dict],checkpoint:dict[str,Any],tool_name:str,remaining_calls:int)->int:
        """Allocate the actual remaining selected input budget across this tool batch."""
        input_budget=self.context.budget(self.state["max_tokens"])
        candidate=request_messages+[assistant_message,*prior_tool_messages,{"role":"tool","tool_name":tool_name,"content":""}]
        used=self.context.estimate(json.dumps({"messages":candidate,"tools":tools},ensure_ascii=False,separators=(",",":")))
        available=max(0,input_budget-used)
        # Reserve a small envelope for a resumable-result pointer, then divide
        # the actual remaining request budget among calls still pending.
        envelope_reserve=min(256,available*2)
        return max(0,(available//max(1,remaining_calls))*2-envelope_reserve)
    def _can_parallelize_file_writes(self,parsed_batch:list[tuple[int,dict,str,dict[str,Any]]])->bool:
        """Only parallelize separate file targets after their approval path is non-interactive."""
        if len(parsed_batch)<2:return False
        registry=self.state["registry"]
        # ``ask`` access invokes the interactive approval callback even when
        # the task/session approval mode is otherwise granted. Keep those
        # calls ordered so the user sees one approval at a time.
        if self.workspace.access in {"plan","copy","ask"}:return False
        approved_mode=(self.approval_mode=="auto" or
                       (self.approval_mode=="per-task" and self.approved_task) or
                       (self.approval_mode=="per-session" and self.approved_session))
        if not approved_mode:return False
        targets=[];identities=set()
        for _,_,name,args in parsed_batch:
            if name not in {"write_file","append_file","edit_file"}:return False
            if permission_decision(name,args,self.permission_rules) in {"ask","deny"}:return False
            raw=args.get("path")
            if not isinstance(raw,str) or not raw:return False
            try:path=registry.workspace.resolve(raw)
            except (OSError,ValueError,RuntimeError):return False
            # External scopes can have an independent approval decision; keep
            # those operations ordered even when their final path is distinct.
            if path!=registry.workspace.root and registry.workspace.root not in path.parents:return False
            key=os.path.normcase(str(path))
            for prior_key,prior_path,prior_identity in targets:
                if key==prior_key or prior_path in path.parents or path in prior_path.parents:return False
                if prior_identity is not None:
                    try:current=path.stat();identity=(current.st_dev,current.st_ino)
                    except OSError:identity=None
                    if identity==prior_identity:return False
            try:
                current=path.stat();identity=(current.st_dev,current.st_ino)
            except OSError:
                identity=None
            if identity is not None:
                if identity in identities:return False
                identities.add(identity)
            targets.append((key,path,identity))
        return True

    def run(self,prompt:str,messages:list[dict]|None=None)->list[dict]:
        access_policy={
            "plan":"Read-only: do not request project writes or commands.",
            "copy":"Structured file tools edit this disposable workspace copy. Commands run as the current Windows user and are not OS-sandboxed; they may access files outside this copy, including the source project. Commands use the selected approval mode and persistent permission rules. Do not describe copy mode as containing command effects.",
            "ask":"Project file changes and commands use the selected approval mode and persistent permission rules; the host blocks actions that are not allowed.",
            "edit":"Project file changes are allowed subject to persistent permission rules and the selected approval mode. Commands use the same host policy and run with the current user's permissions.",
        }[self.workspace.access]
        system={"role":"system","content":agent_system_prompt(access_policy,self.project_instructions,self.harness_profile,self.state["skills_enabled"],self.task_intent,approval_policy_guidance(self.approval_mode))}
        base_system_content = system["content"]
        msgs=messages if messages is not None else []
        _normalize_tool_history(msgs)
        if msgs and msgs[0].get("role")=="system":msgs[0]=system
        else:msgs.insert(0,system)
        retrieval_prefix=prompt+"\n\nRelevant project files:\n"
        base_request=[system,{"role":"user","content":retrieval_prefix}]
        tools=self._tools_for_request(base_request)
        remaining=max(0,self.context.budget(self.state["max_tokens"])-self.context.estimate(json.dumps({"messages":base_request,"tools":tools},ensure_ascii=False,separators=(",",":"))))
        retrieved=self.context.retrieve(self.workspace.root,prompt,limit=remaining,cancel_event=self.cancel_event)
        msgs.append({"role":"user","content":prompt+("\n\nRelevant project files:\n"+retrieved if retrieved else "")})
        self.state["registry"].conversation_messages=msgs
        checkpoint=self.state["task_checkpoint"]
        if not isinstance(checkpoint.get("current"), dict):
            checkpoint = begin_task(
                checkpoint if checkpoint else None,
                prompt=prompt,
                turn_id=uuid.uuid4().hex,
                workspace=str(self.workspace.root),
                settings={"task_intent": self.task_intent, "context": self.context.context,
                          "max_tokens": self.state["max_tokens"], "model": self.model},
                plan=self.state["registry"].plan,
            )
            self.state["task_checkpoint"] = checkpoint
            self.state["registry"].task_checkpoint = checkpoint
        checkpoint_state=current_task(checkpoint)
        checkpoint_state.setdefault("goal",prompt)
        checkpoint_state["workspace"]=str(self.workspace.root)
        registry=self.state["registry"]
        if self.task_intent=="auto" and not registry.plan:
            registry.plan=initial_task_plan(prompt,"auto")
            if registry.plan:
                registry.plan_source="workbench"
                record_plan(checkpoint,registry.plan,task_mode=registry.task_mode,plan_source="workbench")
                self.event({"type":"plan","items":copy.deepcopy(registry.plan),
                            "acceptanceCriteria":copy.deepcopy(checkpoint_state.get("acceptance_criteria",[])),
                            "source":"workbench","taskMode":registry.task_mode})
        checkpoint_state["plan"]=copy.deepcopy(self.state["registry"].plan or checkpoint_state.get("plan",[]))
        checkpoint_state["status"]="active"
        checkpoint_state["updated_at"]=time.time()
        start=time.monotonic();request_index=0;seen_continuation_signatures=set();last_truncated_response_signature=None;plan_only_signature=None;plan_only_guidance_signature=None;current_continuation_guidance=None;completed_project_change=False;command_side_effects_possible=False;mutation_attempted=False;tool_action_index=0;last_effect_action_index=0;last_inspection_action_index=0;action_denied=False;verification_steps=[]
        implementation_requested=(
            True if self.task_intent=="implement" else False if self.task_intent in {"discuss", "plan"} else
            bool(checkpoint_state.get("implementation_requested") or _restored_plan_requests_implementation(
                self.task_intent,self.state["registry"].task_mode,self.state["registry"].plan_source,
                checkpoint_state.get("plan",[])))
        )
        record_implementation_requested(checkpoint,implementation_requested)
        try:
            while True:
                if self.cancel_event.is_set():raise RequestCancelled()
                if self.stop_after_operation.is_set():
                    finish_task(checkpoint,"stopped")
                    self.event({"type":"stopped_after_operation","elapsed":time.monotonic()-start});return msgs
                # A steering handler may append durable user updates only at this
                # request boundary, after the prior response and its complete tool
                # exchange have been resolved. It must not mutate an active stream
                # or an outstanding assistant tool-call batch.
                if self.steering_handler and self.steering_handler(msgs, False):
                    self.steering_interrupt_event.clear()
                    self.event({"type":"steering_applied","elapsed":time.monotonic()-start})
                msgs[0]["content"] = _replace_continuation_guidance(base_system_content, current_continuation_guidance)
                tools=self._tools_for_request(base_request)
                request_messages=self.context.compact(msgs,tools,self.state["max_tokens"],self.event,checkpoint=checkpoint)
                request_index+=1
                self.state["request_tools"]=tools
                manifest=build_prompt_manifest(
                    request_messages,tools,request_index=request_index,context=self.context.context,
                    output_tokens=self.state["max_tokens"],input_budget=self.context.budget(self.state["max_tokens"]),
                    skills_enabled=self.state["skills_enabled"],skill_sources=self.state["skill_sources"],
                    available_skills=self.state["registry"].skill_rows,catalog_skill_names=getattr(self,"_catalog_skill_names",[]),
                    project_instructions=self.project_instructions,harness_profile=self.harness_profile,
                    task_intent=self.task_intent,access=self.workspace.access)
                request_scope=checkpoint_state.get("active_turn_id") or checkpoint_state.get("task_id", "task")
                self.event({"type":"prompt_manifest","activityId":f"{request_scope}-request-{request_index}",**manifest,
                            "selected_tool_names":[(item.get("function") or {}).get("name","") for item in tools],
                            "deferred_tool_names":list(self._deferred_tool_names)})
                self.event({"type":"phase","phase":"waiting","elapsed":time.monotonic()-start,"context":self.context.context,"budget":self.context.budget(self.state["max_tokens"])})
                self.state["request_index"] = request_index
                try:
                    result=self.runtime.stream(self.state,request_messages,self.inference_cancel_signal,self.event)
                except RequestCancelled as exc:
                    if self.steering_interrupt_event.is_set() and not self.cancel_event.is_set():
                        partial=exc.partial_message
                        saved_partial={"role":"assistant","partial_response":True,"steering_interrupted":True}
                        if isinstance(partial,dict):
                            saved_partial.update({key:partial[key] for key in ("content","thinking")
                                                  if isinstance(partial.get(key),str) and partial[key]})
                        if saved_partial.get("content") or saved_partial.get("thinking"):
                            msgs.append(saved_partial)
                            record_assistant_status(checkpoint,saved_partial)
                        self.event({"type":"steering_interrupted","request_index":request_index,
                                    "partial_response_saved":bool(saved_partial.get("content") or saved_partial.get("thinking")),
                                    "elapsed":time.monotonic()-start})
                        continue
                    raise
                msg=result["message"]
                calls=msg.get("tool_calls") or []
                if calls or _assistant_has_final_content(msg) or (isinstance(msg.get("thinking"),str) and msg["thinking"].strip()):
                    msgs.append({"role":"assistant",**msg})
                record_assistant_status(checkpoint,msg)
                assistant_tool_message={"role":"assistant",**msg}
                batch_tool_messages=[]
                if calls:
                    self.event({"type":"tool_batch","request_index":request_index,"assistantMessage":{"role":"assistant",**msg},"calls":[
                        {"id":call.get("id",f"call-{index}"),"name":(call.get("function") or {}).get("name","")}
                        for index,call in enumerate(calls)
                    ]})
                if calls and self.stop_after_operation.is_set():
                    for index,call in enumerate(calls):
                        fn=call.get("function") or {};msgs.append({"role":"tool","tool_name":fn.get("name","unknown_tool"),"content":"Not executed: the user chose to stop after the current operation."})
                    finish_task(checkpoint,"stopped")
                    self.event({"type":"stopped_after_operation","elapsed":time.monotonic()-start});return msgs
                if not calls:
                    # A steer can arrive during a final prose response, when the
                    # normal loop would otherwise return without another request.
                    # Persist it and continue this same task before reporting done.
                    if self.steering_handler and self.steering_handler(msgs, True):
                        self.steering_interrupt_event.clear()
                        self.event({"type":"steering_applied","elapsed":time.monotonic()-start})
                        continue
                    if _provider_response_was_truncated(result):
                        response_signature=json.dumps({"content":msg.get("content",""),"thinking":msg.get("thinking","")},ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
                        if response_signature==last_truncated_response_signature:
                            blocker=("The provider ended with the same partial response again after reaching the selected output-token limit. "
                                     "The configured output setting was preserved; review the saved response and continue from the checkpoint if needed.")
                            record_blocker(checkpoint,blocker)
                            saved_plan=checkpoint_state.get("plan",[])
                            implementation_requested=_implementation_evidence(
                                self.task_intent,self.state["registry"].task_mode,self.state["registry"].plan_source,
                                saved_plan if isinstance(saved_plan,list) else [],completed_project_change,
                                command_side_effects_possible,mutation_attempted) or implementation_requested
                            record_implementation_requested(checkpoint,implementation_requested)
                            verification=_verification_report(verification_steps,implementation_requested,completed_project_change,
                                command_side_effects_possible,last_inspection_action_index>last_effect_action_index)
                            verification["status"]="response_truncated"
                            verification["incomplete_reason"]=blocker
                            finish_task(checkpoint,"failed",verification,blocker=blocker,outcome="response_truncated")
                            self.event({"type":"error","error":blocker,"workflowOutcome":"response_truncated",
                                        "providerDoneReason":result.get("done_reason") or result.get("finish_reason"),
                                        "verification":verification,"elapsed":time.monotonic()-start})
                            return msgs
                        last_truncated_response_signature=response_signature
                        current_continuation_guidance=(
                            "The provider ended the previous response at the selected output-token limit. Continue this same task from the saved checkpoint, current plan, conversation, and completed tool results. "
                            "Do not repeat completed operations or restart planning. If implementation work remains, take the next safe structured tool action; otherwise continue the user-visible answer from where it stopped. "
                            "The selected output-token setting is unchanged. If a large file needs more output, write it with complete validated tool calls or ordered append operations; never execute partial tool arguments.")
                        self.event({"type":"phase","phase":"continuing","elapsed":time.monotonic()-start,
                                    "reason":"provider response reached the selected output-token limit"})
                        continue
                    missing_final_response = not _assistant_has_final_content(msg)
                    post_change_inspection=last_inspection_action_index>last_effect_action_index
                    implementation_requested = _implementation_evidence(
                        self.task_intent, self.state["registry"].task_mode, self.state["registry"].plan_source,
                        checkpoint_state.get("plan", []) if isinstance(checkpoint_state.get("plan", []), list) else [],
                        completed_project_change, command_side_effects_possible, mutation_attempted) or implementation_requested
                    record_implementation_requested(checkpoint,implementation_requested)
                    review_required=implementation_requested and last_effect_action_index>last_inspection_action_index
                    plan=checkpoint_state.get("plan",[])
                    plan_remaining=self.state["registry"].plan_source=="agent" and _plan_has_open_work(plan if isinstance(plan,list) else [])
                    unfinished=_needs_implementation_continuation(
                        implementation_requested,self.workspace.access,action_denied,
                        completed_project_change,review_required,plan_remaining,
                        command_effects_inspected=command_side_effects_possible and post_change_inspection)
                    progress_signature=_continuation_progress_signature(checkpoint_state,verification_steps,msg)
                    if (unfinished or missing_final_response) and progress_signature not in seen_continuation_signatures:
                        seen_continuation_signatures.add(progress_signature)
                        if missing_final_response and not unfinished:
                            continuation="Return a concise user-visible response for the user. Summarize only work confirmed by saved tool results and state the terminal status or the remaining blocker. Do not start another operation unless the saved plan or evidence shows work remains."
                            reason="model produced no user-visible final response"
                        elif review_required:
                            continuation="A project-writing tool or approved command may have changed project state, but no later workspace inspection is recorded. Inspect the workspace after that action now using read_file, git_status, or git_diff as applicable. Identify changed files where the tools can observe them. Do not claim a command passed tests unless its output confirms that specific check. If effects cannot be determined, state that limitation."
                            reason="implementation action or command returned without a later workspace inspection"
                        elif plan_remaining:
                            pending=next((item for item in plan if isinstance(item,dict) and plan_item_is_open(item)),{})
                            next_action=str(checkpoint_state.get("next_action") or pending.get("title") or pending.get("text") or checkpoint_state.get("goal") or "the next unfinished implementation step").strip()
                            continuation=(f"The saved implementation plan is already available. Its next open step is: {next_action}. "
                                "Start that step now through the appropriate structured project tool: inspect the needed files if context is missing, otherwise make the planned change. "
                                "Do not re-plan unless new evidence changes the work. After the operation, inspect its result and update the plan to reflect actual progress. "
                                "If blocked, record the concrete blocker and the next safe action rather than silently ending.")
                            reason="agent-owned implementation plan still contains open work"
                        else:
                            next_action=str(checkpoint_state.get("next_action") or "Inspect the selected project and identify the first concrete implementation step.").strip()
                            continuation=(f"The implementation has not produced a project operation yet. Start the next safe action now: {next_action}. "
                                "Use an appropriate structured project tool; if the action needs inspection first, inspect only what is needed and then implement. "
                                "Do not repeat or expand the plan without new evidence. Update the plan after observable progress. "
                                "Ask a structured user question only if a missing choice materially affects the work; if genuinely blocked, record the exact blocker and next safe action.")
                            reason="no structured project effect has been recorded for this implementation task"
                        current_continuation_guidance = continuation
                        self.event({"type":"phase","phase":"continuing","elapsed":time.monotonic()-start,"reason":reason})
                        continue
                    if unfinished:
                        blocker="The model returned without completing the implementation and made no new observable tool, plan, or verification progress after the continuation request. Review the saved plan and resume from the next action; no runtime deadline or token setting was changed."
                        record_blocker(checkpoint,blocker)
                        verification=_verification_report(verification_steps,implementation_requested,completed_project_change,
                            command_side_effects_possible,post_change_inspection)
                        verification["status"]="implementation_incomplete"
                        verification["incomplete_reason"]=blocker
                        workflow_outcome="implementation_incomplete"
                        finish_task(checkpoint,"stopped",verification,blocker=blocker,outcome=workflow_outcome)
                        self.event({"type":"complete","elapsed":time.monotonic()-start,"usage":result,"implementationRequested":implementation_requested,"projectChanged":completed_project_change,"actionDenied":action_denied,"verification":verification,"workflowOutcome":workflow_outcome})
                        return msgs
                    if missing_final_response:
                        blocker="The model returned no user-visible final response after a follow-up request. Review the saved plan and completed operations, then resume if needed; completed operations are not replayed automatically."
                        record_blocker(checkpoint,blocker)
                        verification=_verification_report(verification_steps,implementation_requested,completed_project_change,
                            command_side_effects_possible,post_change_inspection)
                        verification["status"]="response_missing"
                        verification["incomplete_reason"]=blocker
                        finish_task(checkpoint,"failed",verification,blocker=blocker,outcome="response_missing")
                        self.event({"type":"error","error":blocker,"workflowOutcome":"response_missing",
                                    "verification":verification,"elapsed":time.monotonic()-start})
                        return msgs
                    verification=_verification_report(verification_steps,implementation_requested,completed_project_change,
                        command_side_effects_possible,post_change_inspection)
                    self._advance_workbench_plan("run_command", "completed", final=True,
                                                 verification=str(verification.get("status", "")),
                                                 project_changed=completed_project_change,
                                                 post_change_inspection=post_change_inspection)
                    workflow_outcome=_workflow_outcome(self.workspace.access,action_denied,completed_project_change,verification,checkpoint_state.get("plan",[]),self.task_intent,implementation_requested,self.state["registry"].task_mode)
                    finish_task(checkpoint,"completed",verification,outcome=workflow_outcome)
                    self.event({"type":"complete","elapsed":time.monotonic()-start,"usage":result,"implementationRequested":implementation_requested,"projectChanged":completed_project_change,"actionDenied":action_denied,"verification":verification,"workflowOutcome":workflow_outcome});return msgs
                # Run independent read-only calls together. Mixed batches, writes,
                # commands, MCP, and permission-prompted actions stay ordered.
                readonly_names={"list_files","search_files","read_file","read_operation_result","read_task_checkpoint","read_conversation_history","git_status","git_diff","git_log"}
                prepared_batch:dict[int,dict[str,Any]]={}
                if len(calls)>1:
                    parsed_batch=[];batch_is_readonly=True;batch_write_candidate=True
                    available_schemas=tools
                    for batch_index,batch_call in enumerate(calls):
                        batch_fn=batch_call.get("function") or {};batch_name=batch_fn.get("name","");batch_raw=batch_fn.get("arguments",{})
                        try:batch_args=json.loads(batch_raw or "{}") if isinstance(batch_raw,str) else batch_raw
                        except json.JSONDecodeError:batch_args=None
                        batch_schema=next((item.get("function",{}).get("parameters",{}) for item in available_schemas if item.get("function",{}).get("name")==batch_name),None)
                        batch_invalid=not isinstance(batch_args,dict) or batch_schema is None or bool(_validate_tool_arguments(batch_args,batch_schema))
                        if batch_name not in readonly_names or batch_invalid or permission_decision(batch_name,batch_args if isinstance(batch_args,dict) else {},self.permission_rules) is not None:
                            batch_is_readonly=False
                        if batch_name not in {"write_file","append_file","edit_file"} or batch_invalid:
                            batch_write_candidate=False
                        parsed_batch.append((batch_index,batch_call,batch_name,batch_args if isinstance(batch_args,dict) else {}))
                    batch_is_parallel_write=batch_write_candidate and self._can_parallelize_file_writes(parsed_batch)
                    if (batch_is_readonly or batch_is_parallel_write) and not self.cancel_event.is_set():
                        if batch_is_parallel_write and self.task_intent=="auto" and not self.state["registry"].plan:
                            registry=self.state["registry"]
                            registry.plan=initial_task_plan(prompt,"implement")
                            registry.plan_source="workbench";registry.task_mode="implement"
                            record_plan(checkpoint,registry.plan,[],task_mode="implement",plan_source="workbench")
                            self.event({"type":"plan","items":copy.deepcopy(registry.plan),
                                        "acceptanceCriteria":[],"source":"workbench","taskMode":"implement"})
                        futures={}
                        with ThreadPoolExecutor() as executor:
                            for batch_index,batch_call,batch_name,batch_args in parsed_batch:
                                operation_id=uuid.uuid4().hex;operation_started=time.time()
                                self.event({"type":"tool_start","operationId":operation_id,"toolCallId":batch_call.get("id",f"call-{batch_index}"),"name":batch_name,"arguments":batch_args,"status":"running","startedAt":operation_started})
                                output_budget=self._tool_output_budget(request_messages,assistant_tool_message,[],tools,checkpoint,batch_name,len(calls))
                                future=executor.submit(self.state["registry"].run,batch_name,batch_args,output_byte_budget=output_budget)
                                futures[future]=(batch_index,operation_id,operation_started,output_budget)
                            for future in as_completed(futures):
                                batch_index,operation_id,operation_started,output_budget=futures[future]
                                try:out=future.result()
                                except Exception as exc:out=f"Tool error: {type(exc).__name__}: {exc}"
                                batch_name=parsed_batch[batch_index][2]
                                completed_at=time.time();status=_tool_activity_status(batch_name,str(out))
                                affected=[parsed_batch[batch_index][3]["path"]] if batch_name in {"write_file","append_file","edit_file"} else []
                                operation_event={"type":"tool_done","operationId":operation_id,"toolCallId":parsed_batch[batch_index][1].get("id",f"call-{batch_index}"),"name":batch_name,"result":str(out),"status":status,"affectedFiles":affected,"startedAt":operation_started,"completedAt":completed_at}
                                self.operation_results[operation_id]=str(out);record_operation(checkpoint,operation_event,parsed_batch[batch_index][3]);self.event(operation_event)
                                prepared_batch[batch_index]={"operation_id":operation_id,"started":operation_started,"output":str(out),"budget":output_budget,"completed_event":True}
                for index,call in enumerate(calls):
                    fn=call.get("function") or {};name=fn.get("name","");raw_args=fn.get("arguments",{})
                    prepared=prepared_batch.get(index)
                    args_error=None
                    if isinstance(raw_args,str):
                        try:args=json.loads(raw_args or "{}")
                        except json.JSONDecodeError as exc:args={};args_error=f"Invalid JSON tool arguments: {exc}"
                    else:args=raw_args
                    if not isinstance(args,dict):args_error="Tool arguments must be a JSON object.";args={}
                    available_schemas=tools
                    schema=next((item.get("function",{}).get("parameters",{}) for item in available_schemas if item.get("function",{}).get("name")==name),None)
                    if not name:args_error=args_error or "Tool call is missing its function name."
                    elif schema is None:args_error=args_error or f"Unknown tool: {name}. Use only a tool listed in the current request."
                    elif not args_error:args_error=_validate_tool_arguments(args,schema)
                    if self.cancel_event.is_set() and not prepared:
                        status="cancelled"
                        out="Cancelled before this tool action ran."
                        operation_id=uuid.uuid4().hex;operation_started=time.time()
                        self.event({"type":"tool_start","operationId":operation_id,"toolCallId":call.get("id",f"call-{index}"),"name":name or "unknown_tool","arguments":args,"status":"running","startedAt":operation_started})
                        operation_event={"type":"tool_done","operationId":operation_id,"toolCallId":call.get("id",f"call-{index}"),"name":name or "unknown_tool","result":out,"status":"cancelled","affectedFiles":[],"startedAt":operation_started,"completedAt":time.time()}
                        self.operation_results[operation_id]=out
                        record_operation(checkpoint,operation_event,args)
                        self.event(operation_event)
                    elif args_error:
                        status="failed"
                        out=f"Invalid tool call: {args_error} Correct the arguments to match the tool schema and try again."
                        operation_id=uuid.uuid4().hex;operation_started=time.time()
                        self.event({"type":"tool_start","operationId":operation_id,"toolCallId":call.get("id",f"call-{index}"),"name":name or "unknown_tool","arguments":args,"status":"running","startedAt":operation_started})
                        operation_event={"type":"tool_done","operationId":operation_id,"toolCallId":call.get("id",f"call-{index}"),"name":name or "unknown_tool","result":out,"status":"failed","affectedFiles":[],"startedAt":operation_started,"completedAt":time.time()}
                        self.operation_results[operation_id]=out
                        record_operation(checkpoint,operation_event,args)
                        self.event(operation_event)
                    else:
                        prepared=prepared_batch.get(index)
                        operation_id=prepared["operation_id"] if prepared else uuid.uuid4().hex
                        operation_started=prepared["started"] if prepared else time.time()
                        registry=self.state["registry"]
                        if (not prepared and self.task_intent == "auto"
                                and name in {"write_file", "append_file", "edit_file", "apply_patch", "run_command", "run_shell"}
                                and not registry.plan):
                            # A real structured project-changing operation is
                            # reliable evidence that Automatic mode needs an
                            # implementation checklist. Publish it before the
                            # operation; do not guess from prompt wording.
                            registry.plan=initial_task_plan(prompt,"implement")
                            registry.plan_source="workbench"
                            registry.task_mode="implement"
                            record_plan(checkpoint,registry.plan,[],task_mode="implement",plan_source="workbench")
                            self.event({"type":"plan","items":copy.deepcopy(registry.plan),
                                        "acceptanceCriteria":[],"source":"workbench","taskMode":"implement"})
                        tool_call_id=call.get("id",f"call-{index}")
                        if prepared:
                            out=prepared["output"]
                        else:
                            previous_event=registry.event
                            registry.event=lambda event,op=operation_id:self.event({**event,"operationId":op})
                            self.event({"type":"tool_start","operationId":operation_id,"toolCallId":tool_call_id,"name":name,"arguments":args,"status":"running","startedAt":operation_started})
                            try:
                                if name=="load_tools":
                                    discovery_budget=self._tool_output_budget(request_messages,assistant_tool_message,batch_tool_messages,tools,checkpoint,name,len(calls)-index)
                                    out=self._load_tools(args,base_request,discovery_budget)
                                elif name.startswith("mcp__") and self.mcp_call:
                                    if not self._approval(name,args):out="Denied by user."
                                    else:out=self.mcp_call(name,args)
                                else:out=self.state["registry"].run(name,args,output_byte_budget=self._tool_output_budget(request_messages,assistant_tool_message,batch_tool_messages,tools,checkpoint,name,len(calls)-index))
                            except RequestCancelled:out="Cancelled before the workspace file operation completed."
                            except UncertainFileOperationError as exc:out=f"Uncertain: {exc}"
                            except MCPRequestCancelled:
                                out="Uncertain: MCP action was cancelled after dispatch; the server may have applied it. Inspect before retrying."
                            except MCPRequestUncertain as exc:out=f"Uncertain: {exc}"
                            except Exception as exc:
                                if name.startswith("mcp__"):
                                    out=f"Uncertain: MCP call failed after dispatch ({type(exc).__name__}); server-side effects may have occurred. Inspect before retrying."
                                else:out=f"Tool error: {type(exc).__name__}: {exc}"
                            finally:registry.event=previous_event
                        affected=[]
                        if name in {"read_file","write_file","append_file","edit_file"} and isinstance(args.get("path"),str):affected=[args["path"]]
                        elif name=="apply_patch":
                            affected=[line[4:].strip() for line in str(args.get("patch","" )).splitlines() if line.startswith("+++ ") and line[4:].strip()!="/dev/null"]
                        status = _tool_activity_status(name, str(out))
                        tool_action_index+=1
                        if permission_decision(name,args,self.permission_rules)=="deny":
                            record_decision(checkpoint,{"kind":"permission_rule","tool":name,"decision":"deny"})
                        if status=="denied":action_denied=True
                        # In Automatic mode, implementation intent comes from host-observed
                        # operations or the agent's structured plan, never keyword matches
                        # against the user's wording or the model's prose.
                        if self.task_intent == "auto" and name in {"write_file", "append_file", "edit_file", "apply_patch", "run_command", "run_shell"} and status not in {"denied", "blocked"}:
                            mutation_attempted=True
                            implementation_requested=True
                            record_implementation_requested(checkpoint)
                        if name in {"write_file","append_file","edit_file","apply_patch"} and status=="completed":
                            completed_project_change=True
                            last_effect_action_index=tool_action_index
                        if name in {"run_command","run_shell"} and implementation_requested and status not in {"denied","blocked"}:
                            command_side_effects_possible=True
                            last_effect_action_index=tool_action_index
                        if (completed_project_change or command_side_effects_possible) and name in {"read_file","git_diff","git_status"} and status=="completed":
                            verification_steps.append({"kind":"inspection","tool":name,"arguments":copy.deepcopy(args),"result":"completed"})
                            last_inspection_action_index=tool_action_index
                        elif (completed_project_change or implementation_requested) and name in {"run_command","run_shell"} and status not in {"denied","blocked"}:
                            verification_steps.append({"kind":"command","tool":name,"arguments":copy.deepcopy(args),"result":status})
                        operation_event={"type":"tool_done","operationId":operation_id,"toolCallId":call.get("id",f"call-{index}"),"name":name,"result":str(out),"status":status,"affectedFiles":affected,"startedAt":operation_started,"completedAt":time.time()}
                        self.operation_results[operation_id]=str(out)
                        if not (prepared and prepared.get("completed_event")):
                            record_operation(checkpoint,operation_event,args)
                            self.event(operation_event)
                        self._advance_workbench_plan(name,status)
                        if status == "uncertain":
                            checkpoint_state = current_task(checkpoint)
                            checkpoint_state["uncertain_operation"] = {
                                **{
                                    key: copy.deepcopy(operation_event.get(key))
                                    for key in ("operationId", "toolCallId", "name", "result", "status", "affectedFiles", "completedAt")
                                    if key in operation_event
                                },
                                "arguments": copy.deepcopy(args),
                            }
                    if name=="update_plan" and not args_error:
                        record_plan(checkpoint,self.state["registry"].plan,args.get("acceptance_criteria"),
                                    args.get("decisions"),self.state["registry"].task_mode,plan_source="agent")
                        if self.task_intent == "auto":
                            if (self.state["registry"].task_mode == "plan_only" and not mutation_attempted
                                    and not completed_project_change and not command_side_effects_possible):
                                implementation_requested=False
                                record_implementation_requested(checkpoint,False)
                            elif _plan_requests_implementation(
                                    self.state["registry"].task_mode, self.state["registry"].plan_source,
                                    self.state["registry"].plan):
                                implementation_requested=True
                                record_implementation_requested(checkpoint)
                    prepared=prepared_batch.get(index)
                    output_budget=prepared["budget"] if prepared else self._tool_output_budget(request_messages,assistant_tool_message,batch_tool_messages,tools,checkpoint,name,len(calls)-index)
                    context_result=tool_result_for_context(str(out),output_budget,operation_id)
                    tool_message={"role":"tool","tool_name":name or "unknown_tool","content":context_result}
                    msgs.append(tool_message);batch_tool_messages.append(tool_message)
                    if name.startswith("mcp__") and status == "uncertain" and not self.cancel_event.is_set():
                        reason="MCP operation outcome is uncertain; inspect its remote effects before any retry. Remaining batched actions were not executed."
                        for pending_index,pending in enumerate(calls[index+1:],start=index+1):
                            pending_fn=pending.get("function") or {}
                            pending_name=pending_fn.get("name","unknown_tool")
                            pending_result="Not executed: an earlier MCP operation has an uncertain result and requires inspection before continuing."
                            pending_id=pending.get("id",f"call-{pending_index}")
                            pending_operation_id=uuid.uuid4().hex
                            pending_started=time.time()
                            self.event({"type":"tool_start","operationId":pending_operation_id,"toolCallId":pending_id,
                                        "name":pending_name,"arguments":copy.deepcopy(pending_fn.get("arguments",{})),
                                        "status":"blocked","startedAt":pending_started})
                            skipped_event={"type":"tool_done","operationId":pending_operation_id,"toolCallId":pending_id,
                                           "name":pending_name,"result":pending_result,"status":"blocked","affectedFiles":[],
                                           "startedAt":pending_started,"completedAt":time.time()}
                            self.operation_results[pending_operation_id]=pending_result
                            record_operation(checkpoint,skipped_event,pending_fn.get("arguments",{}))
                            self.event(skipped_event)
                            msgs.append({"role":"tool","tool_name":pending_name,"content":pending_result})
                        finish_task(checkpoint,"failed",blocker=reason,outcome="blocked")
                        self.event({"type":"error","error":reason,"workflowOutcome":"blocked"})
                        return msgs
                    # Safe read batches are already in flight together. Drain and record
                    # every submitted operation before stopping ahead of the next model call.
                    if self.stop_after_operation.is_set() and not prepared:
                        for pending_index,pending in enumerate(calls[index+1:],start=index+1):
                            pending_fn=pending.get("function") or {};msgs.append({"role":"tool","tool_name":pending_fn.get("name","unknown_tool"),"content":"Not executed: the user chose to stop after the active operation."})
                        finish_task(checkpoint,"stopped")
                        self.event({"type":"stopped_after_operation","elapsed":time.monotonic()-start});return msgs
                    if self.cancel_event.is_set() and not prepared:
                        for pending_index,pending in enumerate(calls[index+1:],start=index+1):
                            pending_fn=pending.get("function") or {};msgs.append({"role":"tool","tool_name":pending_fn.get("name","unknown_tool"),"content":"Cancelled before this tool action ran."})
                        raise RequestCancelled()
                # A parallel batch was fully submitted before cancellation was
                # observed. Preserve every submitted operation's actual result
                # and checkpoint, then stop before requesting another response.
                if self.cancel_event.is_set():
                    raise RequestCancelled()
                plan_only_batch=bool(calls) and all(
                    ((call.get("function") or {}).get("name") == "update_plan") for call in calls
                )
                if plan_only_batch:
                    progress_signature=_continuation_progress_signature(checkpoint_state,verification_steps)
                    if plan_only_signature != progress_signature:
                        plan_only_signature=progress_signature
                        plan_only_guidance_signature=None
                    elif plan_only_guidance_signature != progress_signature:
                        if action_denied:
                            current_continuation_guidance=(
                                "A project action was denied. Respect that decision and do not retry or route around it. "
                                "Stop issuing plan-only updates and give the user a concise blocked status with the saved "
                                "plan and safe alternatives."
                            )
                        elif self.workspace.access == "plan" and implementation_requested:
                            current_continuation_guidance=(
                                "The selected project access is read-only. Do not attempt a file change or command. Stop "
                                "issuing plan-only updates and present the saved plan and the read-only blocker to the user."
                            )
                        elif implementation_requested:
                            next_action=str(checkpoint_state.get("next_action") or checkpoint_state.get("goal") or
                                           "the next unfinished implementation step").strip()
                            current_continuation_guidance=(
                                f"The implementation plan is saved and may remain expandable, but pending plan edits alone "
                                f"do not advance the project. Start the next open step now: {next_action}. Use a project "
                                "inspection or implementation tool as appropriate; do not issue another plan-only update "
                                "unless new project evidence or user direction changes the plan."
                            )
                        else:
                            current_continuation_guidance=(
                                "The current plan is saved. Stop issuing plan-only updates without new evidence and give "
                                "the user a concise response from the saved plan. Use a permitted inspection only if it is "
                                "needed to answer the request."
                            )
                        plan_only_guidance_signature=progress_signature
                        self.event({"type":"phase","phase":"continuing","elapsed":time.monotonic()-start,
                                    "reason":"repeated plan-only tool calls without project or verification progress"})
                    else:
                        blocked=bool(action_denied or (self.workspace.access == "plan" and implementation_requested))
                        incomplete=bool(implementation_requested and not blocked)
                        outcome="blocked" if blocked else "implementation_incomplete" if incomplete else "response_missing"
                        blocker=(
                            "A project action was denied; the decision was preserved and dependent work was not retried."
                            if action_denied else
                            "The selected project access is read-only, so requested implementation cannot proceed."
                            if blocked else
                            "The model repeated plan-only tool calls after the implementation handoff without new "
                            "project, host-decision, user-update, or verification evidence. The saved plan and operation "
                            "history are available to continue from the next project action."
                            if incomplete else
                            "The model repeated plan-only tool calls without producing a user-visible response. The saved "
                            "plan and activity remain available for recovery."
                        )
                        verification=_verification_report(verification_steps,implementation_requested,
                            completed_project_change,command_side_effects_possible,False)
                        verification["status"]=outcome
                        verification["incomplete_reason"]=blocker
                        terminal_status="stopped" if blocked or incomplete else "failed"
                        finish_task(checkpoint,terminal_status,verification,blocker=blocker,outcome=outcome)
                        terminal_event={"elapsed":time.monotonic()-start,"usage":result,
                                        "implementationRequested":implementation_requested,
                                        "projectChanged":completed_project_change,"actionDenied":action_denied,
                                        "verification":verification,"workflowOutcome":outcome}
                        if outcome == "response_missing":
                            self.event({"type":"error","error":blocker,**terminal_event})
                        else:
                            self.event({"type":"complete",**terminal_event})
                        return msgs
                elif calls:
                    # Any non-plan tool call is fresh observable work. A prior
                    # plan-only nudge is no longer relevant after project evidence.
                    plan_only_signature=None
                    plan_only_guidance_signature=None
                    current_continuation_guidance=None
        except RequestCancelled as exc:
            partial=exc.partial_message
            if isinstance(partial,dict) and any(partial.get(key) for key in ("content","thinking")):
                msgs.append({"role":"assistant",**{key:partial[key] for key in ("content","thinking") if partial.get(key)}})
            finish_task(checkpoint,"cancelled")
            self.event({"type":"cancelled","elapsed":time.monotonic()-start})
        except MCPRequestCancelled:
            finish_task(checkpoint,"cancelled")
            self.event({"type":"cancelled","elapsed":time.monotonic()-start})
        except Exception as exc:
            partial=getattr(exc,"partial_message",None)
            partial_saved=isinstance(partial,dict) and any(partial.get(key) for key in ("content","thinking"))
            if partial_saved:
                saved={"role":"assistant",**{key:partial[key] for key in ("thinking","content") if partial.get(key)},"partial_response":True}
                msgs.append(saved)
                record_assistant_status(checkpoint,saved)
            finish_task(checkpoint,"failed",blocker=f"{type(exc).__name__}: {exc}")
            self.event({"type":"error","error":f"{type(exc).__name__}: {exc}","partial_response_saved":partial_saved})
        return msgs




