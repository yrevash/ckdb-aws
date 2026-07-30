from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import ChangeEvent, ClosedWindow, QueueMessage
from .embedding import EmbeddingModel
from .grouping import group_completed_incidents
from .model import ConsolidationModel
from .repository import RunbookMutation, RunbookRepository
from .storage import WindowStore


@dataclass(frozen=True)
class ProcessingResult:
    buffered_events: int = 0
    completed_groups: int = 0
    mutations: tuple[RunbookMutation, ...] = ()


class ConsolidationProcessor:
    def __init__(
        self,
        *,
        store: WindowStore,
        model: ConsolidationModel,
        embedder: EmbeddingModel,
        repository: RunbookRepository,
    ) -> None:
        self._store = store
        self._model = model
        self._embedder = embedder
        self._repository = repository

    def process(self, message: QueueMessage) -> ProcessingResult:
        if isinstance(message, ChangeEvent):
            self._store.put(message)
            return ProcessingResult(buffered_events=1)
        if not isinstance(message, ClosedWindow):
            raise TypeError(f"unsupported message type: {type(message)!r}")

        events = self._store.read_through(message.watermark)
        groups = group_completed_incidents(events)
        mutations: list[RunbookMutation] = []
        completed_event_ids: set[str] = set()

        for group in groups:
            result = self._model.consolidate(group)
            embedded_candidate = replace(
                result.candidate,
                embedding=self._embedder.embed(result.candidate.trigger_desc),
            )
            embedded_result = replace(result, candidate=embedded_candidate)
            mutation = self._repository.apply(
                embedded_result.candidate, group.idempotency_key
            )
            self._store.archive(embedded_result, group.idempotency_key)
            mutations.append(mutation)
            completed_event_ids.update(event.event_id for event in group.episodes)

        completed_events = [
            event for event in events if event.event_id in completed_event_ids
        ]
        self._store.mark_processed(completed_events, message.watermark)
        return ProcessingResult(
            completed_groups=len(groups),
            mutations=tuple(mutations),
        )
