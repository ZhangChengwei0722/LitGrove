from __future__ import annotations

from copy import deepcopy
from typing import Any


SECTIONS = (
    "research_background_significance",
    "research_problem",
    "method_principle_advantages",
    "conclusions_applications",
    "innovation",
    "limitations",
    "future_outlook",
)
NOW = "2026-01-01T00:00:00Z"


def make_bundle(domain: str) -> dict[str, Any]:
    if domain not in {"alpha", "beta"}:
        raise ValueError("domain must be alpha or beta")
    digit = "a" if domain == "alpha" else "b"
    profile_id = f"domain-{domain}"
    ids = _ids(digit)
    fingerprint_1 = digit * 64
    fingerprint_2 = ("c" if digit == "a" else "d") * 64

    workspace = {
        "contract_version": "1.0",
        "workspace": {
            "id": ids["workspace"],
            "knowledge_root": "./knowledge",
            "source_roots": [{"root_id": f"{domain}-sources", "path": "./sources", "read_only_assets": True}],
            "local_inbox": "./inbox",
            "domain_profile": "./domain-profile.yaml",
        },
        "runtime": {"path_serialization": "workspace_relative_posix", "default_encoding": "utf-8", "line_ending": "lf"},
    }
    profile = {
        "contract_version": "1.0",
        "domain_profile": {"id": profile_id, "name": f"Fictional {domain.title()} Studies", "version": "1.0"},
        "paper_card_sections": [{"section_id": value, "label": value.replace("_", " ").title()} for value in SECTIONS],
        "evidence_axes": ["input", "process", "outcome"],
        "question_types": ["mechanism", "comparison"],
        "terminology": {"sample": "synthetic case"},
        "step7_extensions": {},
    }
    papers = [
        _paper(ids["paper_1"], f"{domain}/study-one.txt", fingerprint_1, ids["paper_2"], domain, 1),
        _paper(ids["paper_2"], f"{domain}/study-two.txt", fingerprint_2, ids["paper_1"], domain, 2),
    ]
    pages = [
        _page(ids["paper_1"], ids["event"], f"Synthetic {domain} observation one."),
        _page(ids["paper_2"], ids["event"], f"Synthetic {domain} observation two."),
    ]
    evidence = [
        _evidence(ids["evidence_1"], ids["paper_1"], "The synthetic intervention changed the primary response.", "Primary response was higher in the fabricated comparison.", fingerprint_1),
        _evidence(ids["evidence_2"], ids["paper_1"], "The reported method used a matched synthetic control.", "A matched control was used for the fabricated procedure.", fingerprint_1, "control"),
        _evidence(ids["evidence_3"], ids["paper_2"], "The second synthetic study reported a compatible response pattern.", "The fabricated response followed the same direction.", fingerprint_2),
    ]
    queues = [
        _queue(ids["queue_1"], ids["paper_1"], "The response applies to every setting.", "The synthetic record covers one setting only."),
        _queue(ids["queue_2"], ids["paper_2"], "The mechanism is fully established.", "A necessary discriminating control is absent."),
        _queue(ids["queue_3"], ids["paper_1"], "The matched control resolves every alternative explanation.", "The synthetic control narrows but does not eliminate alternative explanations."),
    ]
    units_1 = [
        _unit(ids["unit_1"], SECTIONS[1], "The study asks whether the synthetic intervention changes the response.", "reported_result", "grounded", [ids["evidence_1"]]),
        _unit(ids["unit_2"], SECTIONS[2], "The design uses a matched control for the fabricated procedure.", "method_description", "revised", [ids["evidence_2"]]),
        _unit(ids["unit_3"], SECTIONS[4], "The pattern may reflect an unmeasured intermediate process.", "interpretation", "interpretive", []),
    ]
    units_2 = [
        _unit(ids["unit_4"], SECTIONS[0], "The second study provides background for the comparison.", "background", "background_only", [], source_page=None),
        _unit(ids["unit_5"], SECTIONS[5], "The causal mechanism needs an additional discriminating control.", "limitation", "needs_resolution", [], [ids["queue_2"]]),
        _unit(ids["unit_6"], SECTIONS[3], "The second study reports a compatible response direction.", "reported_result", "grounded", [ids["evidence_3"]]),
    ]
    cards = [
        _card(ids["paper_1"], profile_id, units_1),
        _card(ids["paper_2"], profile_id, units_2),
    ]
    questions = [
        _question(
            ids["question_1"],
            "Which synthetic response patterns agree across studies?",
            profile_id,
            [
                _link(ids["qlink_1"], ids["paper_1"], [ids["unit_1"]], [ids["evidence_1"]], [ids["queue_1"]]),
                _link(ids["qlink_2"], ids["paper_2"], [ids["unit_6"]], [ids["evidence_3"]], []),
            ],
        ),
        _question(
            ids["question_2"],
            "Which design features limit interpretation?",
            profile_id,
            [_link(ids["qlink_3"], ids["paper_1"], [ids["unit_2"]], [ids["evidence_2"]], [ids["queue_3"]])],
        ),
    ]
    synthesis = _step7_common(
        ids["synthesis"], "synthesis", ids["question_1"],
        [{"paper_id": ids["paper_1"], "card_unit_ids": [ids["unit_1"]]}, {"paper_id": ids["paper_2"], "card_unit_ids": [ids["unit_6"]]}],
        [ids["evidence_1"], ids["evidence_3"]], [ids["queue_1"]], "aggregate",
    )
    synthesis.update({
        "claim": "Both synthetic studies report a response in the same direction.",
        "scope": "The two fabricated settings represented in the fixture.",
        "agreement_pattern": "Direction agrees while magnitude is not compared.",
        "conflict_pattern": "No direct conflict is represented.",
        "boundary_statement": "The fixture does not support universal generalization.",
    })
    angle = _step7_common(
        ids["angle"], "review_angle", ids["question_1"],
        [{"paper_id": ids["paper_1"], "card_unit_ids": [ids["unit_1"]]}, {"paper_id": ids["paper_2"], "card_unit_ids": [ids["unit_6"]]}],
        [ids["evidence_1"], ids["evidence_3"]], [ids["queue_1"]], "compare",
    )
    angle.update({
        "thesis": "Organize the synthetic studies by response comparability and control completeness.",
        "organizing_axes": ["response comparability", "control completeness"],
        "included_clusters": ["compatible response direction"],
        "excluded_scope": ["settings not represented in the fixture"],
        "why_this_angle_adds_value": "It separates observed agreement from unsupported generalization.",
    })
    insight = _step7_common(
        ids["insight"], "insight", ids["question_2"],
        [{"paper_id": ids["paper_1"], "card_unit_ids": [ids["unit_2"]]}],
        [ids["evidence_2"]], [ids["queue_3"]], "experiment_design",
    )
    insight.update({
        "insight_type": "experimental_idea",
        "hypothesis_or_idea": "Adding the missing synthetic control may distinguish two explanations.",
        "rationale": "The current fixture records the control gap without resolving it.",
        "falsification_condition": "Both explanations remain indistinguishable after the added control.",
        "minimum_test": "Run the fabricated comparison with one added control arm.",
    })
    crossview = _step7_common(
        ids["crossview"], "cross_view", ids["question_1"],
        [{"paper_id": ids["paper_1"], "card_unit_ids": [ids["unit_1"]]}, {"paper_id": ids["paper_2"], "card_unit_ids": [ids["unit_6"]]}],
        [ids["evidence_1"], ids["evidence_3"]], [ids["queue_1"]], "contrast",
    )
    crossview.update({
        "source_views": [ids["synthesis"], ids["angle"]],
        "relation_type": "complements",
        "why_interesting": "Agreement and study organization expose different aspects of the same fixture.",
        "shared_dimension": "response direction",
        "non_equivalence_warning": "The fabricated methods and settings are not identical.",
    })
    event = {
        "schema_version": "1.0", "event_id": ids["event"], "operation": "synthetic_validation", "actor": "cli", "result": "success",
        "input_refs": [ids["paper_1"], ids["paper_2"]], "output_refs": [ids["evidence_1"], ids["evidence_2"], ids["evidence_3"]],
        "created_at": NOW, "fixture_origin": "synthetic_from_scratch",
    }
    guardian = {
        "schema_version": "1.0", "guardian_report_id": ids["guardian"], "workspace_id": ids["workspace"], "status": "success", "findings": [],
        "created_at": NOW, "fixture_origin": "synthetic_from_scratch",
    }
    records = [("workspace", workspace), ("domain-profile", profile)]
    records += [("registry-paper", item) for item in papers]
    records += [("parsed-page", item) for item in pages]
    records += [("evidence", item) for item in evidence]
    records += [("review-queue", item) for item in queues]
    records += [("paper-card", item) for item in cards]
    records += [("question-mapping", item) for item in questions]
    records += [("step7-synthesis", synthesis), ("step7-review-angle", angle), ("step7-insight", insight), ("step7-cross-view", crossview)]
    records += [("process-event", event), ("guardian-report", guardian)]
    return {"fixture_origin": "synthetic_from_scratch", "records": [{"kind": kind, "record": value} for kind, value in records]}


def invalid_bundle(case: str, domain: str = "alpha") -> dict[str, Any]:
    bundle = deepcopy(make_bundle(domain))
    records = bundle["records"]
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for entry in records:
        by_kind.setdefault(entry["kind"], []).append(entry["record"])
    if case == "unsupported_version":
        by_kind["registry-paper"][0]["schema_version"] = "2.0"
    elif case == "duplicate_id":
        by_kind["evidence"][1]["evidence_id"] = by_kind["evidence"][0]["evidence_id"]
    elif case == "unresolved_reference":
        by_kind["question-mapping"][0]["paper_links"][0]["selected_card_unit_ids"] = ["unit_f0000000-0000-4000-8000-000000000000"]
    elif case == "grounding_mismatch":
        unit = by_kind["paper-card"][0]["sections"][1]["units"][0]
        unit["evidence_ids"] = []
    elif case == "queue_as_evidence":
        by_kind["step7-insight"][0]["evidence_base"] = [by_kind["review-queue"][0]["queue_id"]]
    elif case == "unauthorized_state":
        by_kind["evidence"][0]["review_status"] = "verified"
    elif case == "duplicate_paper_card":
        records.append(deepcopy(next(entry for entry in records if entry["kind"] == "paper-card")))
    elif case == "evidence_expansion_mismatch":
        candidate = by_kind["step7-insight"][0]
        candidate["evidence_base"] = [by_kind["evidence"][0]["evidence_id"]]
        candidate["input_snapshot"]["evidence_ids"] = list(candidate["evidence_base"])
    elif case == "profile_unresolved":
        by_kind["paper-card"][0]["domain_profile_id"] = "missing-profile"
    elif case == "source_view_unresolved":
        by_kind["step7-cross-view"][0]["source_views"] = ["angle_f0000000-0000-4000-8000-000000000000"]
    elif case == "step7_boundary":
        del by_kind["step7-insight"][0]["minimum_test"]
    elif case == "snapshot_mismatch":
        by_kind["step7-insight"][0]["input_snapshot"]["evidence_ids"] = [by_kind["evidence"][0]["evidence_id"]]
    elif case == "unit_cross_paper_evidence":
        by_kind["paper-card"][0]["sections"][1]["units"][0]["evidence_ids"] = [by_kind["evidence"][2]["evidence_id"]]
    elif case == "wrong_paper_unit":
        by_kind["step7-insight"][0]["paper_card_base"][0]["paper_id"] = by_kind["registry-paper"][1]["paper_id"]
    elif case == "ambiguous_source_path":
        by_kind["registry-paper"][0]["source_ref"]["relative_path"] = "alpha//study-one.txt"
    elif case == "unknown_source_root":
        by_kind["registry-paper"][0]["source_ref"]["root_id"] = "missing-sources"
    elif case == "fingerprint_mismatch":
        by_kind["evidence"][0]["source_fingerprint"] = by_kind["registry-paper"][1]["source_fingerprint"]
    elif case == "card_sections_profile_mismatch":
        by_kind["paper-card"][0]["sections"][0], by_kind["paper-card"][0]["sections"][1] = (
            by_kind["paper-card"][0]["sections"][1],
            by_kind["paper-card"][0]["sections"][0],
        )
    elif case == "card_boundary_cross_paper":
        by_kind["paper-card"][0]["sections"][1]["units"][0]["boundary_refs"] = [by_kind["review-queue"][1]["queue_id"]]
    elif case == "question_boundary_cross_paper":
        by_kind["question-mapping"][1]["paper_links"][0]["boundary_refs"] = [by_kind["review-queue"][1]["queue_id"]]
    elif case == "step7_boundary_cross_paper":
        candidate = by_kind["step7-insight"][0]
        candidate["review_queue_refs"] = [by_kind["review-queue"][1]["queue_id"]]
        candidate["input_snapshot"]["review_queue_ids"] = list(candidate["review_queue_refs"])
    elif case == "synthesis_same_paper_twice":
        candidate = by_kind["step7-synthesis"][0]
        candidate["paper_card_base"][1] = {
            "paper_id": by_kind["registry-paper"][0]["paper_id"],
            "card_unit_ids": [by_kind["paper-card"][0]["sections"][2]["units"][0]["unit_id"]],
        }
        candidate["evidence_base"] = [
            by_kind["evidence"][0]["evidence_id"],
            by_kind["evidence"][1]["evidence_id"],
        ]
        candidate["input_snapshot"]["card_unit_ids"] = [
            item for base in candidate["paper_card_base"] for item in base["card_unit_ids"]
        ]
        candidate["input_snapshot"]["evidence_ids"] = list(candidate["evidence_base"])
    elif case == "crossview_self_reference":
        candidate = by_kind["step7-cross-view"][0]
        candidate["source_views"] = [candidate["candidate_id"]]
    elif case == "guardian_status_mismatch":
        by_kind["guardian-report"][0]["status"] = "warning"
    else:
        raise ValueError(f"unknown invalid case: {case}")
    return bundle


def _ids(digit: str) -> dict[str, str]:
    def value(namespace: str, number: int) -> str:
        return f"{namespace}_{digit}{number:07d}-0000-4000-8000-{number:012d}"
    return {
        "workspace": value("workspace", 1),
        "paper_1": value("paper", 1), "paper_2": value("paper", 2),
        "unit_1": value("unit", 1), "unit_2": value("unit", 2), "unit_3": value("unit", 3),
        "unit_4": value("unit", 4), "unit_5": value("unit", 5), "unit_6": value("unit", 6),
        "evidence_1": value("evidence", 1), "evidence_2": value("evidence", 2), "evidence_3": value("evidence", 3),
        "queue_1": value("queue", 1), "queue_2": value("queue", 2), "queue_3": value("queue", 3),
        "question_1": value("question", 1), "question_2": value("question", 2),
        "qlink_1": value("qlink", 1), "qlink_2": value("qlink", 2), "qlink_3": value("qlink", 3),
        "synthesis": value("synthesis", 1), "angle": value("angle", 1), "insight": value("insight", 1), "crossview": value("crossview", 1),
        "event": value("event", 1), "guardian": value("guardian", 1),
    }


def _paper(paper_id: str, relative_path: str, fingerprint: str, duplicate_id: str, domain: str, number: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "paper_id": paper_id,
        "source_ref": {"root_id": f"{domain}-sources", "relative_path": relative_path},
        "source_fingerprint": {"algorithm": "sha256", "value": fingerprint},
        "bibliography": {"title": f"Fabricated {domain.title()} Study {number}", "authors": ["Synthetic Author"], "year": 2026, "doi": None},
        "screening_status": "candidate", "duplicate_candidate_ids": [duplicate_id],
        "review_status": "ai_checked", "automation_status": "passed_auto_checks", "created_at": NOW, "updated_at": NOW,
        "fixture_origin": "synthetic_from_scratch",
    }


def _page(paper_id: str, event_id: str, text: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "paper_id": paper_id, "parse_run_id": event_id,
        "parser": {"adapter": "synthetic-text", "version": "1.0"}, "pdf_page": 1, "printed_page": None,
        "text": text, "locator": "page:1:block:1", "created_at": NOW, "fixture_origin": "synthetic_from_scratch",
    }


def _evidence(evidence_id: str, paper_id: str, claim: str, quote: str, fingerprint: str, evidence_type: str = "reported_result") -> dict[str, Any]:
    return {
        "schema_version": "1.0", "evidence_id": evidence_id, "paper_id": paper_id, "claim": claim, "evidence_type": evidence_type,
        "quote": quote, "source_page": {"pdf_page": 1, "printed_page": None, "section": "Synthetic results", "figure_or_table": None},
        "locator": "page:1:block:1", "support_scope": "The fabricated comparison and stated response only.",
        "what_it_does_not_support": ["Universal generalization", "Unmeasured mechanisms"], "source_type": "primary", "canonical": True,
        "source_fingerprint": {"algorithm": "sha256", "value": fingerprint}, "review_status": "ai_checked", "automation_status": "passed_auto_checks",
        "created_at": NOW, "updated_at": NOW, "fixture_origin": "synthetic_from_scratch",
    }


def _queue(queue_id: str, paper_id: str, claim: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "queue_id": queue_id, "paper_id": paper_id, "issue_type": "overclaim", "claim_candidate": claim,
        "reason": reason, "source_page": {"pdf_page": 1, "printed_page": None, "section": "Synthetic discussion", "figure_or_table": None},
        "locator": "page:1:block:2", "resolution_status": "needs_resolution", "not_evidence": True,
        "review_status": "ai_checked", "automation_status": "passed_auto_checks", "created_at": NOW, "updated_at": NOW,
        "fixture_origin": "synthetic_from_scratch",
    }


def _unit(unit_id: str, section_id: str, statement: str, statement_type: str, grounding_status: str, evidence_ids: list[str], boundary_refs: list[str] | None = None, source_page: dict[str, Any] | None | object = ...) -> dict[str, Any]:
    if source_page is ...:
        source_page = {"pdf_page": 1, "printed_page": None, "section": "Synthetic section", "figure_or_table": None}
    return {
        "unit_id": unit_id, "section_id": section_id, "statement": statement, "statement_type": statement_type,
        "grounding_status": grounding_status, "evidence_ids": evidence_ids, "boundary_refs": boundary_refs or [],
        "source_page": source_page, "confidence": "medium",
    }


def _card(paper_id: str, profile_id: str, units: list[dict[str, Any]]) -> dict[str, Any]:
    by_section = {section: [] for section in SECTIONS}
    for unit in units:
        by_section[unit["section_id"]].append(unit)
    return {
        "schema_version": "1.0", "paper_id": paper_id, "domain_profile_id": profile_id,
        "card_status": "calibrated", "review_status": "ai_checked", "automation_status": "passed_auto_checks",
        "sections": [{"section_id": section, "units": by_section[section]} for section in SECTIONS],
        "created_at": NOW, "updated_at": NOW, "fixture_origin": "synthetic_from_scratch",
    }


def _link(link_id: str, paper_id: str, units: list[str], evidence: list[str], queues: list[str]) -> dict[str, Any]:
    return {
        "question_link_id": link_id, "paper_id": paper_id, "selected_card_unit_ids": units,
        "role_in_question": "comparison", "relevance_rationale": "The synthetic unit addresses the fixture question.",
        "evidence_ids": evidence, "boundary_refs": queues,
    }


def _question(question_id: str, text: str, profile_id: str, links: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "question_id": question_id, "question_text": text, "scope": "The fabricated fixture records only.",
        "domain_profile_id": profile_id, "paper_links": links, "mapping_status": "ai_checked",
        "created_at": NOW, "updated_at": NOW, "fixture_origin": "synthetic_from_scratch",
    }


def _step7_common(candidate_id: str, candidate_type: str, question_id: str, paper_card_base: list[dict[str, Any]], evidence_base: list[str], queue_refs: list[str], operator: str) -> dict[str, Any]:
    unit_ids = [value for item in paper_card_base for value in item["card_unit_ids"]]
    return {
        "schema_version": "1.0", "candidate_id": candidate_id, "type": candidate_type, "question_id": question_id,
        "title": f"Synthetic {candidate_type.replace('_', ' ')} candidate", "candidate_status": "keep", "analysis_operator": operator,
        "paper_card_base": paper_card_base, "evidence_base": evidence_base, "review_queue_refs": queue_refs,
        "missing_evidence": ["Independent fabricated replication"], "assumptions": ["The synthetic records are comparable on the stated dimension"],
        "risk": ["The fixture does not represent external validity"], "testability": "Add one fabricated discriminating observation.",
        "next_action": "Retain as a contract-validation candidate.", "trace_status": "traceable",
        "input_snapshot": {"domain_profile_version": "1.0", "card_unit_ids": unit_ids, "evidence_ids": evidence_base, "review_queue_ids": queue_refs},
        "not_fact": True, "review_status": "ai_draft", "automation_status": "pending", "rejection_rationale": None,
        "created_at": NOW, "updated_at": NOW, "fixture_origin": "synthetic_from_scratch",
    }
