from __future__ import annotations

from dataclasses import dataclass


GENERATOR_CONTRACT_VERSION = "p11-operational-density-generator@1.0"


@dataclass(frozen=True, slots=True)
class OperationalProfile:
    profile_id: str
    job_count: int
    task_count: int
    report_only_result_count: int
    process_event_count: int
    guardian_report_count: int
    journal_count: int
    maintenance_trigger_count: int
    maintenance_key_count: int

    def __post_init__(self) -> None:
        values = self.parameters()
        if any(not isinstance(value, int) or value < 0 for key, value in values.items() if key.endswith("_count")):
            raise ValueError("operational profile counts must be non-negative integers")
        if self.report_only_result_count > self.task_count:
            raise ValueError("report-only result count exceeds Agent Task count")
        if self.journal_count > self.process_event_count:
            raise ValueError("journal count exceeds process event count")
        required_events = (
            self.journal_count
            + self.job_count * 2
            + self.task_count * 2
            + self.report_only_result_count * 2
        )
        if required_events > self.process_event_count:
            raise ValueError("process event count cannot close all generated Job and Agent Task states")
        if self.maintenance_key_count > self.maintenance_trigger_count:
            raise ValueError("maintenance key count exceeds trigger count")

    def parameters(self) -> dict[str, int | str]:
        return {
            "profile_id": self.profile_id,
            "job_count": self.job_count,
            "task_count": self.task_count,
            "report_only_result_count": self.report_only_result_count,
            "process_event_count": self.process_event_count,
            "guardian_report_count": self.guardian_report_count,
            "journal_count": self.journal_count,
            "maintenance_trigger_count": self.maintenance_trigger_count,
            "maintenance_key_count": self.maintenance_key_count,
        }


_PROFILES = {
    item.profile_id: item
    for item in (
        OperationalProfile("p11-operational-small", 10, 10, 2, 100, 2, 4, 20, 2),
        OperationalProfile(
            "p11-operational-recovery-windows-v1",
            25_000,
            25_000,
            5_000,
            250_000,
            10_000,
            10_000,
            100_000,
            1_000,
        ),
    )
}


def profile_by_id(profile_id: str) -> OperationalProfile:
    try:
        return _PROFILES[profile_id]
    except KeyError as error:
        raise ValueError(f"unknown P11 operational profile: {profile_id}") from error


__all__ = ["GENERATOR_CONTRACT_VERSION", "OperationalProfile", "profile_by_id"]
