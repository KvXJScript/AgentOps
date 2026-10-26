"""The report workflow as one small, bounded LangGraph graph.

    START → validate → retrieve ─┬→ analyze ─┬→ draft → verify → END
                                 └→ END       └→ END      (insufficient evidence)

No checkpointer: a run lives in one process and its progress is persisted by
the `StageTracker` (stage transitions), not by LangGraph. Swapping in a
checkpointer + a durable queue is the seam for resumable runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol, cast

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentops.application.reports import nodes
from agentops.application.reports.nodes import ReportState, RetrievalPolicy, StageContext
from agentops.domain.errors import DomainError
from agentops.domain.ports import ChatModel
from agentops.domain.reports import StageName
from agentops.domain.retrieval import Retriever

# The happy path is 5 node steps. A small, explicit ceiling turns any future
# loop bug into a failed run instead of a runaway one.
MAX_GRAPH_STEPS = 8

type Node = Callable[[ReportState, StageContext], Awaitable[ReportState]]
type ReportGraph = CompiledStateGraph[ReportState, None, ReportState, ReportState]


class _GraphNode(Protocol):
    def __call__(self, state: ReportState) -> Awaitable[ReportState]: ...


class StageTracker(Protocol):
    async def stage_started(self, name: StageName) -> StageContext: ...

    async def stage_finished(self, name: StageName, ctx: StageContext) -> None: ...


class WorkflowStepLimitExceeded(DomainError):
    code = "internal_error"
    message = "The report workflow exceeded its step limit."


@dataclass(frozen=True, slots=True)
class WorkflowServices:
    retriever: Retriever
    model: ChatModel
    policy: RetrievalPolicy


def _tracked(name: StageName, node: Node, tracker: StageTracker) -> _GraphNode:
    async def run(state: ReportState) -> ReportState:
        ctx = await tracker.stage_started(name)
        update = await node(state, ctx)
        await tracker.stage_finished(name, ctx)
        return update

    run.__name__ = name.value
    return run


def _continue_unless_insufficient(next_node: str) -> Callable[[ReportState], str]:
    def route(state: ReportState) -> str:
        return END if state.get("insufficient") else next_node

    return route


def build_report_graph(services: WorkflowServices, tracker: StageTracker) -> ReportGraph:
    stages: dict[StageName, Node] = {
        StageName.VALIDATE: nodes.validate,
        StageName.RETRIEVE: partial(nodes.retrieve, retriever=services.retriever, policy=services.policy),
        StageName.ANALYZE: partial(nodes.analyze, model=services.model),
        StageName.DRAFT: partial(nodes.draft, model=services.model),
        StageName.VERIFY: nodes.verify,
    }
    graph = StateGraph(ReportState)
    for name, node in stages.items():
        graph.add_node(name.value, _tracked(name, node, tracker))

    graph.add_edge(START, StageName.VALIDATE.value)
    graph.add_edge(StageName.VALIDATE.value, StageName.RETRIEVE.value)
    graph.add_conditional_edges(
        StageName.RETRIEVE.value,
        _continue_unless_insufficient(StageName.ANALYZE.value),
        [StageName.ANALYZE.value, END],
    )
    graph.add_conditional_edges(
        StageName.ANALYZE.value,
        _continue_unless_insufficient(StageName.DRAFT.value),
        [StageName.DRAFT.value, END],
    )
    graph.add_edge(StageName.DRAFT.value, StageName.VERIFY.value)
    graph.add_edge(StageName.VERIFY.value, END)
    return graph.compile()


async def run_report_graph(
    graph: ReportGraph, initial: ReportState, *, max_steps: int = MAX_GRAPH_STEPS
) -> ReportState:
    try:
        result: Any = await graph.ainvoke(initial, {"recursion_limit": max_steps})
    except GraphRecursionError as exc:
        raise WorkflowStepLimitExceeded() from exc
    return cast(ReportState, result)
