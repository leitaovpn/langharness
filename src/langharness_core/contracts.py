"""Public service specifications shared by all plugins."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from langchain_core.language_models.chat_models import BaseChatModel

from langharness_plugin.validation import service_contract

SPEC_LLM = "agent.plugin.llm"
SPEC_TOOL = "agent.plugin.tools"
SPEC_MIDDLEWARE = "agent.plugin.middleware"
SPEC_SYSTEM_PROMPT = "agent.plugin.system_prompt"
SPEC_RESPONSE_FORMAT = "agent.plugin.response_format"
SPEC_STATE_SCHEMA = "agent.plugin.state_schema"
SPEC_CONTEXT_SCHEMA = "agent.plugin.context_schema"
SPEC_CHECKPOINTER = "agent.plugin.checkpointer"
SPEC_STORE = "agent.plugin.store"
SPEC_INTERRUPT_BEFORE = "agent.plugin.interrupt_before"
SPEC_INTERRUPT_AFTER = "agent.plugin.interrupt_after"
SPEC_DEBUG = "agent.plugin.debug"
SPEC_NAME = "agent.plugin.name"
SPEC_CACHE = "agent.plugin.cache"
SPEC_TRANSFORMERS = "agent.plugin.transformers"
SPEC_AGENT_LOOP = "agent.loop"
SPEC_SESSION_INDEX = "session.index"
SPEC_AGENT_REGISTRY = "agent.registry"
SPEC_AGENT_DIRECTORY = "agent.directory"
SPEC_AGENT_SERVER = "agent.server"
SPEC_PROMPT_SECTION = "agent.plugin.prompt_section"
SPEC_WORKSPACE = "agent.plugin.workspace"
SPEC_WORKSPACE_STATE = "agent.plugin.workspace_state"
SPEC_GOAL = "agent.plugin.goal"
SPEC_VERIFIER = "agent.plugin.verifier"
SPEC_JOB_MANAGER = "agent.plugin.job_manager"
SPEC_RUN_CONTROLLER = "agent.plugin.run_controller"
SPEC_TOOL_PRESENTATION = "agent.plugin.tool_presentation"
SPEC_PTC_RUNTIME = "agent.plugin.ptc_runtime"
SPEC_SANDBOX = "agent.plugin.sandbox"
ModelProtocol = Literal["anthropic", "chat", "responses"]
ToolPresentationMode = Literal["native", "ptc", "both"]

ALL_PLUGIN_SPECS = (
    SPEC_LLM,
    SPEC_TOOL,
    SPEC_MIDDLEWARE,
    SPEC_SYSTEM_PROMPT,
    SPEC_RESPONSE_FORMAT,
    SPEC_STATE_SCHEMA,
    SPEC_CONTEXT_SCHEMA,
    SPEC_CHECKPOINTER,
    SPEC_STORE,
    SPEC_INTERRUPT_BEFORE,
    SPEC_INTERRUPT_AFTER,
    SPEC_DEBUG,
    SPEC_NAME,
    SPEC_CACHE,
    SPEC_TRANSFORMERS,
    SPEC_PROMPT_SECTION,
    SPEC_WORKSPACE,
    SPEC_WORKSPACE_STATE,
    SPEC_GOAL,
    SPEC_VERIFIER,
    SPEC_JOB_MANAGER,
    SPEC_RUN_CONTROLLER,
    SPEC_TOOL_PRESENTATION,
    SPEC_PTC_RUNTIME,
    SPEC_SANDBOX,
    SPEC_AGENT_LOOP,
    SPEC_SESSION_INDEX,
    SPEC_AGENT_REGISTRY,
    SPEC_AGENT_DIRECTORY,
)


@dataclass(frozen=True, slots=True)
class PromptSection:
    """One ordered, named contribution to an agent's system prompt.

    ``order`` decides placement (smaller first), ``source`` is free-form
    provenance recorded in the per-request trace, and ``max_bytes`` caps this
    section alone on top of the assembler's total budget.
    """

    name: str
    order: int
    text: str
    source: str
    max_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class PromptContext:
    """What a section provider may use to shape its contribution.

    ``touched_paths`` are workspace-relative POSIX paths the conversation's
    tool calls have already named, which is what lets a provider decide how
    deep into a repository its instructions must reach.
    """

    workspace: str
    touched_paths: tuple[str, ...] = ()
    model: str | None = None


@dataclass(frozen=True, slots=True)
class MutationRecord:
    """One write an agent made, at the generation it produced.

    ``before_sha256`` is ``None`` for a created file and ``after_sha256`` is
    ``None`` for a deleted one, so a report can tell creation from deletion
    without a separate flag.
    """

    path: str
    generation: int
    tool: str
    before_sha256: str | None
    after_sha256: str | None


GoalStatus = Literal[
    "active", "awaiting_user", "verifying", "complete", "blocked", "cancelled"
]
TERMINAL_GOAL_STATUSES: frozenset[str] = frozenset({"complete", "blocked", "cancelled"})


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    """One thing the user asked for, and the evidence that it holds.

    ``evidence`` names a tool call id, a file hash, a test case, or the literal
    ``"manual/user_review"``. A criterion with no evidence can never satisfy
    the host, which is what stops "it works" from counting.
    """

    id: str
    text: str
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class Goal:
    """A durable objective plus the bookkeeping that gates its completion."""

    id: str
    revision: int
    objective: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    status: GoalStatus
    rounds_started: int
    max_rounds: int
    mutation_generation: int
    blocker_history: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """One gate command the host ran, and how it ended."""

    name: str
    command: tuple[str, ...]
    exit_code: int
    stdout_digest: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class VerificationArtifact:
    """A workspace file the goal produced, pinned at a content hash."""

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """What the host observed when it verified one goal revision.

    Only the summary lives here; full command output stays in the audit log so
    verification noise cannot crowd out the conversation.
    """

    id: str
    goal_revision: int
    mutation_generation: int
    started_at: str
    finished_at: str
    checks: tuple[VerificationCheck, ...]
    artifacts: tuple[VerificationArtifact, ...]
    passed: bool


JobState = Literal["running", "exited", "killed", "timed_out"]


@dataclass(frozen=True, slots=True)
class JobRecord:
    """One command the agent started, and where it got to.

    ``owner`` is ``"<agent_id>::<session_id>"``: a job belongs to the
    conversation that started it, not to the process.
    """

    id: str
    command: tuple[str, ...]
    cwd: str
    pid: int
    state: JobState
    owner: str
    started_at: str
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class Continue:
    """Run another round with this synthetic follow-up as the next input."""

    message: str
    round: int


@dataclass(frozen=True, slots=True)
class AwaitUser:
    """Stop the automatic run; a real user message is needed to go on."""

    reason: str


@dataclass(frozen=True, slots=True)
class Stop:
    """End the run. ``wrapup`` is what the final model step is asked to cover."""

    status: str
    reason: str
    wrapup: str


RunDecision = Continue | AwaitUser | Stop


@service_contract(SPEC_LLM)
@runtime_checkable
class LLMProvider(Protocol):
    """Contract implemented by every ``agent.plugin.llm`` service."""

    def get_model(self) -> BaseChatModel: ...

    def get_protocol(self) -> ModelProtocol: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_TOOL)
@runtime_checkable
class ToolProvider(Protocol):
    """Contract implemented by every ``agent.plugin.tools`` service."""

    def get_tools(self) -> list[Any]: ...

    def get_tool_presentations(self) -> dict[str, str]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_MIDDLEWARE)
@runtime_checkable
class MiddlewareProvider(Protocol):
    """Contract implemented by every ``agent.plugin.middleware`` service."""

    def get_middlewares(self) -> list[Any]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_SYSTEM_PROMPT)
@runtime_checkable
class SystemPromptProvider(Protocol):
    """Contract implemented by every ``agent.plugin.system_prompt`` service."""

    def get_system_prompt(self) -> str: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_PROMPT_SECTION)
@runtime_checkable
class PromptSectionProvider(Protocol):
    """Contract implemented by every ``agent.plugin.prompt_section`` service."""

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_WORKSPACE)
@runtime_checkable
class WorkspaceProvider(Protocol):
    """Contract implemented by every ``agent.plugin.workspace`` service."""

    def get_workspace(self) -> str: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_WORKSPACE_STATE)
@runtime_checkable
class WorkspaceStateProvider(Protocol):
    """Contract implemented by every ``agent.plugin.workspace_state`` service."""

    def get_generation(self) -> int: ...

    def record_mutation(
        self,
        path: str,
        tool: str,
        *,
        before_sha256: str | None = None,
        after_sha256: str | None = None,
    ) -> int: ...

    def mutations(self, since: int = 0) -> list[MutationRecord]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_GOAL)
@runtime_checkable
class GoalProvider(Protocol):
    """Contract implemented by every ``agent.plugin.goal`` service."""

    def create_goal(
        self,
        objective: str,
        acceptance_criteria: list[dict[str, str]],
        *,
        max_rounds: int = 32,
    ) -> Goal: ...

    def get_goal(self, goal_id: str) -> Goal | None: ...

    def current_goal(self) -> Goal | None: ...

    def update_goal(
        self,
        goal_id: str,
        *,
        expected_revision: int,
        status: str | None = None,
        blocker: str | None = None,
    ) -> Goal: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_VERIFIER)
@runtime_checkable
class VerificationProvider(Protocol):
    """Contract implemented by every ``agent.plugin.verifier`` service."""

    def verify(self, goal_revision: int) -> VerificationReport: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_JOB_MANAGER)
@runtime_checkable
class JobManagerProvider(Protocol):
    """Contract implemented by every ``agent.plugin.job_manager`` service."""

    def start_job(self, command: list[str], *, cwd: str, owner: str) -> JobRecord: ...

    def get_job(self, job_id: str) -> JobRecord | None: ...

    def list_jobs(self, owner: str | None = None) -> list[JobRecord]: ...

    def write_stdin(self, job_id: str, data: str) -> JobRecord: ...

    def read_output(self, job_id: str, offset: int = 0) -> tuple[str, int]: ...

    def wait_job(self, job_id: str, timeout_seconds: float) -> JobRecord: ...

    def stop_job(self, job_id: str) -> JobRecord: ...

    def drain_events(self) -> list[dict[str, Any]]: ...

    def shutdown_jobs(self) -> None: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_RUN_CONTROLLER)
@runtime_checkable
class RunControllerProvider(Protocol):
    """Contract implemented by every ``agent.plugin.run_controller`` service."""

    def decide(
        self, *, rounds_run: int, checkpointer_available: bool
    ) -> RunDecision: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_TOOL_PRESENTATION)
@runtime_checkable
class ToolPresentationProvider(Protocol):
    """Select how tools are presented to a model in one agent scope."""

    def get_mode(self) -> ToolPresentationMode: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_PTC_RUNTIME)
@runtime_checkable
class PTCRuntimeProvider(Protocol):
    """Execute one program and dispatch its host-tool calls."""

    async def execute(
        self,
        *,
        code: str,
        sdk: str,
        dispatch: Any,
        timeout_ms: int = 120_000,
    ) -> dict[str, Any]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_SANDBOX)
@runtime_checkable
class SandboxProvider(Protocol):
    """Describe and apply an optional OS-level process sandbox."""

    def get_isolation(self) -> Literal["os-sandbox", "process-only"]: ...

    def wrap_command(self, argv: list[str], cwd: str) -> list[str]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_RESPONSE_FORMAT)
@runtime_checkable
class ResponseFormatProvider(Protocol):
    def get_response_format(self) -> Any: ...


@service_contract(SPEC_STATE_SCHEMA)
@runtime_checkable
class StateSchemaProvider(Protocol):
    def get_state_schema(self) -> Any: ...


@service_contract(SPEC_CONTEXT_SCHEMA)
@runtime_checkable
class ContextSchemaProvider(Protocol):
    def get_context_schema(self) -> Any: ...


@service_contract(SPEC_CHECKPOINTER)
@runtime_checkable
class CheckpointerProvider(Protocol):
    def get_checkpointer(self) -> Any: ...


@service_contract(SPEC_STORE)
@runtime_checkable
class StoreProvider(Protocol):
    def get_store(self) -> Any: ...


@service_contract(SPEC_INTERRUPT_BEFORE)
@runtime_checkable
class InterruptBeforeProvider(Protocol):
    def get_interrupt_before(self) -> list[str]: ...


@service_contract(SPEC_INTERRUPT_AFTER)
@runtime_checkable
class InterruptAfterProvider(Protocol):
    def get_interrupt_after(self) -> list[str]: ...


@service_contract(SPEC_DEBUG)
@runtime_checkable
class DebugProvider(Protocol):
    def get_debug(self) -> bool: ...


@service_contract(SPEC_NAME)
@runtime_checkable
class NameProvider(Protocol):
    def get_name(self) -> str | None: ...


@service_contract(SPEC_CACHE)
@runtime_checkable
class CacheProvider(Protocol):
    def get_cache(self) -> Any: ...


@service_contract(SPEC_TRANSFORMERS)
@runtime_checkable
class TransformersProvider(Protocol):
    def get_transformers(self) -> list[Any]: ...


@service_contract(SPEC_AGENT_LOOP)
@runtime_checkable
class AgentLoopProvider(Protocol):
    """Contract implemented by every ``agent.loop`` service."""

    def invoke(self, message: str, *, thread_id: str | None = None) -> Any: ...

    def astream(
        self, message: str, *, thread_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]: ...

    def describe(self) -> dict[str, Any]: ...


@service_contract(SPEC_SESSION_INDEX)
@runtime_checkable
class SessionIndexProvider(Protocol):
    """Contract implemented by every ``session.index`` service."""

    async def new_session(self, user_id: str) -> str: ...

    async def touch(self, user_id: str, session_id: str, agent_id: str) -> None: ...

    async def get_session(
        self, user_id: str, session_id: str
    ) -> dict[str, Any] | None: ...

    async def list_sessions(
        self, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...


@service_contract(SPEC_AGENT_REGISTRY)
@runtime_checkable
class AgentRegistryProvider(Protocol):
    """Contract implemented by every ``agent.registry`` service."""

    def list_agents(self) -> list[dict[str, Any]]: ...

    def get_agent(self, agent_id: str) -> dict[str, Any] | None: ...

    def create_agent(
        self, agent_id: str, name: str, description: str
    ) -> dict[str, Any]: ...

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]: ...

    def delete_agent(self, agent_id: str) -> None: ...


@service_contract(SPEC_AGENT_DIRECTORY)
@runtime_checkable
class AgentDirectoryProvider(Protocol):
    """Contract implemented by every ``agent.directory`` service."""

    def list_agents(self) -> list[dict[str, Any]]: ...

    def get_loop(self, agent_id: str) -> Any | None: ...

    def ensure_plugin_instance(
        self, agent_id: str, plugin: str, properties: dict[str, Any]
    ) -> None: ...

    def binding_properties(self, agent_id: str, plugin: str) -> dict[str, Any]: ...

    def apply_agent_config(self, agent_id: str, plugins: dict[str, Any]) -> None: ...

    def remove_agent(self, agent_id: str) -> None: ...

    def reload(self, agent_id: str | None = None) -> None: ...


@service_contract(SPEC_AGENT_SERVER)
@runtime_checkable
class AgentServerProvider(Protocol):
    def list_agents(self) -> list[dict[str, Any]]: ...

    def get_loop(self, agent_id: str) -> Any | None: ...

    def reload(self, agent_id: str | None = None) -> None: ...

    def replace_loop_package(self, package_id: str, contribution_id: str) -> None: ...
