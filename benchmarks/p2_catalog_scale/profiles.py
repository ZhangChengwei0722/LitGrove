from __future__ import annotations

from dataclasses import dataclass, fields


GENERATOR_CONTRACT_VERSION = "p2-catalog-generator@1.0"


@dataclass(frozen=True, slots=True)
class GenerationProfile:
    profile_id: str
    primary_paper_count: int
    review_paper_count: int
    card_units_per_primary: int
    evidence_per_primary: int
    review_units_per_review: int
    question_count: int
    step7_synthesis_count: int
    step7_review_angle_count: int
    step7_insight_count: int
    step7_cross_view_count: int
    process_event_count: int
    guardian_report_count: int
    generator_contract_version: str = GENERATOR_CONTRACT_VERSION

    def __post_init__(self) -> None:
        values = {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name.endswith("_count") or field.name.startswith("step7_")
        }
        if any(not isinstance(value, int) or value < 0 for value in values.values()):
            raise ValueError("generation profile counts must be non-negative integers")
        if self.paper_count < 1:
            raise ValueError("generation profile must contain at least one paper")
        if self.process_event_count < self.paper_count:
            raise ValueError("generation profile requires one parse event per paper")
        if self.primary_paper_count and (
            self.card_units_per_primary < 1 or self.evidence_per_primary < 1
        ):
            raise ValueError("primary papers require Card Units and Evidence")
        if self.review_paper_count and self.review_units_per_review < 1:
            raise ValueError("review papers require Review Units")

    @property
    def paper_count(self) -> int:
        return self.primary_paper_count + self.review_paper_count

    @property
    def step7_candidate_count(self) -> int:
        return sum(
            (
                self.step7_synthesis_count,
                self.step7_review_angle_count,
                self.step7_insight_count,
                self.step7_cross_view_count,
            )
        )

    @property
    def scientific_catalog_item_count(self) -> int:
        return (
            self.paper_count
            + self.primary_paper_count * self.card_units_per_primary
            + self.primary_paper_count * self.evidence_per_primary
            + self.review_paper_count
            + self.review_paper_count * self.review_units_per_review
            + self.question_count
            + self.step7_candidate_count
        )

    @property
    def operational_catalog_item_count(self) -> int:
        return self.process_event_count + self.guardian_report_count

    @property
    def catalog_item_count(self) -> int:
        return self.scientific_catalog_item_count + self.operational_catalog_item_count

    def parameters(self) -> dict[str, int | str]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


_PROFILES = {
    profile.profile_id: profile
    for profile in (
        GenerationProfile(
            "p2-small",
            primary_paper_count=3,
            review_paper_count=1,
            card_units_per_primary=3,
            evidence_per_primary=1,
            review_units_per_review=3,
            question_count=2,
            step7_synthesis_count=1,
            step7_review_angle_count=1,
            step7_insight_count=1,
            step7_cross_view_count=1,
            process_event_count=12,
            guardian_report_count=2,
        ),
        GenerationProfile(
            "p2-pilot-v1",
            primary_paper_count=400,
            review_paper_count=100,
            card_units_per_primary=3,
            evidence_per_primary=1,
            review_units_per_review=3,
            question_count=0,
            step7_synthesis_count=0,
            step7_review_angle_count=0,
            step7_insight_count=0,
            step7_cross_view_count=0,
            process_event_count=4_900,
            guardian_report_count=100,
        ),
        GenerationProfile(
            "p2-r0-scale-v1",
            primary_paper_count=40_000,
            review_paper_count=10_000,
            card_units_per_primary=3,
            evidence_per_primary=1,
            review_units_per_review=3,
            question_count=0,
            step7_synthesis_count=0,
            step7_review_angle_count=0,
            step7_insight_count=0,
            step7_cross_view_count=0,
            process_event_count=490_000,
            guardian_report_count=10_000,
        ),
    )
}


def profile_by_id(profile_id: str) -> GenerationProfile:
    try:
        return _PROFILES[profile_id]
    except KeyError as error:
        raise ValueError(f"unknown P2 catalog generation profile: {profile_id}") from error


__all__ = ["GENERATOR_CONTRACT_VERSION", "GenerationProfile", "profile_by_id"]
