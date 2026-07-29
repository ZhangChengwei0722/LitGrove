from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_kb.storage.transactions import MANUAL_RESOLUTION_ACTIONS, TransactionManager
from research_kb.workspace import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class TransactionRecoveryResult:
    status: str
    dry_run: bool
    actions: tuple[dict[str, Any], ...]
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dry_run": self.dry_run,
            "actions": [dict(item) for item in self.actions],
        }


class TransactionRecoveryService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
    ):
        self.transaction_manager = transaction_manager or TransactionManager(layout)

    def recover(self, *, dry_run: bool) -> TransactionRecoveryResult:
        actions = tuple(self.transaction_manager.recover(dry_run=dry_run))
        needs_resolution = any(item["action"] in MANUAL_RESOLUTION_ACTIONS for item in actions)
        return TransactionRecoveryResult(
            "needs_resolution" if needs_resolution else "success",
            dry_run,
            actions,
            4 if needs_resolution else 0,
        )
