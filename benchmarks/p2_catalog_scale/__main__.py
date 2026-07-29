from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

from benchmarks.p2_catalog_scale.generator import (
    DEFAULT_SEED,
    export_portable_small_seed,
    generate_workspace,
    inspect_generated_workspace,
)
from benchmarks.p2_catalog_scale.measurement import (
    estimate_reference_workload,
    measure_catalog_reads,
    measure_core_catalog,
    measure_projection_rebuild,
    measure_registry_delta,
)
from benchmarks.p2_catalog_scale.profiles import profile_by_id
from research_kb.errors import ResearchKBError
from research_kb.storage.json_io import read_json_document, serialize_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "profile":
            profile = profile_by_id(args.profile)
            result = {
                "status": "success",
                "profile_id": profile.profile_id,
                "parameters": profile.parameters(),
                "paper_count": profile.paper_count,
                "scientific_catalog_item_count": profile.scientific_catalog_item_count,
                "operational_catalog_item_count": profile.operational_catalog_item_count,
                "catalog_item_count": profile.catalog_item_count,
            }
        elif args.command == "generate":
            generated = generate_workspace(
                Path(args.target),
                profile_id=args.profile,
                seed=args.seed,
            )
            result = _generated_summary(generated)
        elif args.command == "inspect":
            result = _generated_summary(inspect_generated_workspace(Path(args.target)))
        elif args.command == "export-small":
            generated = inspect_generated_workspace(Path(args.source_target))
            export_portable_small_seed(generated)
            result = {
                "status": "success",
                "profile_id": "p2-small",
                "fixture": "tests/fixtures/p2_small",
            }
        elif args.command == "measure":
            receipt = measure_core_catalog(
                Path(args.target),
                repetitions=args.repetitions,
                incremental_change_count=args.incremental_change_count,
                warm_up_runs=args.warm_up_runs,
                restore_projection=not args.leave_stale_projection,
            )
            if args.output is not None:
                _write_absent_output(parser, Path(args.output), receipt)
            result = {
                "status": "success",
                "profile_id": receipt["profile_id"],
                "catalog_item_count": receipt["catalog_item_count"],
                "payload_restored": receipt["payload_restored"],
                "receipt_written": args.output is not None,
                "receipt": receipt if args.output is None else None,
            }
        elif args.command == "measure-registry-delta":
            receipt = measure_registry_delta(
                Path(args.target),
                repetitions=args.repetitions,
                incremental_change_count=args.incremental_change_count,
            )
            if args.output is not None:
                _write_absent_output(parser, Path(args.output), receipt)
            result = {
                "status": "success",
                "profile_id": receipt["profile_id"],
                "payload_restored": receipt["payload_restored"],
                "projection_registry_restored": receipt["projection_registry_restored"],
                "receipt_written": args.output is not None,
                "receipt": receipt if args.output is None else None,
            }
        elif args.command == "measure-projection-rebuild":
            receipt = measure_projection_rebuild(
                Path(args.target),
                repetitions=args.repetitions,
                warm_up_runs=args.warm_up_runs,
            )
            if args.output is not None:
                _write_absent_output(parser, Path(args.output), receipt)
            result = {
                "status": "success",
                "profile_id": receipt["profile_id"],
                "payload_restored": receipt["payload_restored"],
                "receipt_written": args.output is not None,
                "receipt": receipt if args.output is None else None,
            }
        elif args.command == "measure-catalog-reads":
            receipt = measure_catalog_reads(
                Path(args.target),
                repetitions=args.repetitions,
            )
            if args.output is not None:
                _write_absent_output(parser, Path(args.output), receipt)
            result = {
                "status": "success",
                "profile_id": receipt["profile_id"],
                "payload_restored": receipt["payload_restored"],
                "current_record_status": receipt["registry_detail"][
                    "current_record_status"
                ],
                "receipt_written": args.output is not None,
                "receipt": receipt if args.output is None else None,
            }
        elif args.command == "generate-measure":
            preflight = read_json_document(
                Path(args.preflight_receipt),
                record_kind="p2-preflight-receipt",
            )
            _require_current_preflight(parser, preflight, args.profile, Path(args.target).parent)
            generated = generate_workspace(
                Path(args.target),
                profile_id=args.profile,
                seed=args.seed,
            )
            receipt = measure_core_catalog(
                generated,
                repetitions=args.repetitions,
                incremental_change_count=args.incremental_change_count,
                warm_up_runs=args.warm_up_runs,
                restore_projection=not args.leave_stale_projection,
            )
            if args.output is not None:
                _write_absent_output(parser, Path(args.output), receipt)
            result = {
                "status": "success",
                "profile_id": generated.profile.profile_id,
                "catalog_item_count": generated.profile.catalog_item_count,
                "content_tree_digest": generated.manifest["content_tree_digest"],
                "payload_restored": receipt["payload_restored"],
                "receipt_written": args.output is not None,
            }
        elif args.command == "estimate":
            pilot = inspect_generated_workspace(Path(args.pilot_target))
            receipt = read_json_document(
                Path(args.pilot_receipt),
                record_kind="p2-measurement-receipt",
            )
            free_bytes = (
                args.free_bytes
                if args.free_bytes is not None
                else shutil.disk_usage(pilot.target).free
            )
            estimate = estimate_reference_workload(
                pilot.manifest,
                receipt,
                target_profile_id=args.profile,
                free_bytes=free_bytes,
            )
            if args.output is not None:
                _write_absent_output(parser, Path(args.output), estimate)
            result = {
                "status": "success",
                **estimate,
                "receipt_written": args.output is not None,
            }
        else:
            parser.error("unknown benchmark command")
            return 2
    except ResearchKBError as error:
        print(json.dumps({"status": "error", "diagnostic": error.diagnostic.to_dict()}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _generated_summary(generated) -> dict[str, Any]:
    return {
        "status": "success",
        "generator_contract_version": generated.profile.generator_contract_version,
        "profile_id": generated.profile.profile_id,
        "workspace_config": "workspace/workspace.yaml",
        "file_count": generated.manifest["total_file_count"],
        "byte_count": generated.manifest["byte_count"],
        "catalog_item_count": generated.profile.catalog_item_count,
        "content_tree_digest": generated.manifest["content_tree_digest"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P2 deterministic catalog-scale benchmark")
    commands = parser.add_subparsers(dest="command", required=True)

    profile = commands.add_parser("profile", help="show exact profile counts without generation")
    profile.add_argument("--profile", required=True)

    generate = commands.add_parser("generate", help="generate one absent operation-owned target")
    generate.add_argument("--profile", required=True)
    generate.add_argument("--target", required=True)
    generate.add_argument("--seed", default=DEFAULT_SEED)

    inspect = commands.add_parser("inspect", help="verify one generated target")
    inspect.add_argument("--target", required=True)

    export = commands.add_parser("export-small", help="export p2-small to the fixed fixture path")
    export.add_argument("--source-target", required=True)

    measure = commands.add_parser("measure", help="measure Core catalog operations")
    measure.add_argument("--target", required=True)
    measure.add_argument("--repetitions", type=int, default=5)
    measure.add_argument("--incremental-change-count", type=int, default=1_000)
    measure.add_argument("--warm-up-runs", type=int, default=1)
    measure.add_argument("--leave-stale-projection", action="store_true")
    measure.add_argument("--output")

    delta = commands.add_parser(
        "measure-registry-delta",
        help="measure the benchmark-only Registry delta against an existing projection",
    )
    delta.add_argument("--target", required=True)
    delta.add_argument("--repetitions", type=int, default=5)
    delta.add_argument("--incremental-change-count", type=int, default=1_000)
    delta.add_argument("--output")

    rebuild = commands.add_parser(
        "measure-projection-rebuild",
        help="measure projection rebuild without query or cursor workloads",
    )
    rebuild.add_argument("--target", required=True)
    rebuild.add_argument("--repetitions", type=int, default=1)
    rebuild.add_argument("--warm-up-runs", type=int, default=0)
    rebuild.add_argument("--output")

    reads = commands.add_parser(
        "measure-catalog-reads",
        help="measure restart binding, selective FTS and authoritative detail",
    )
    reads.add_argument("--target", required=True)
    reads.add_argument("--repetitions", type=int, default=20)
    reads.add_argument("--output")

    combined = commands.add_parser(
        "generate-measure",
        help="generate and measure in one process after a passing preflight",
    )
    combined.add_argument("--profile", required=True)
    combined.add_argument("--target", required=True)
    combined.add_argument("--seed", default=DEFAULT_SEED)
    combined.add_argument("--preflight-receipt", required=True)
    combined.add_argument("--repetitions", type=int, default=1)
    combined.add_argument("--incremental-change-count", type=int, default=1_000)
    combined.add_argument("--warm-up-runs", type=int, default=0)
    combined.add_argument("--leave-stale-projection", action="store_true")
    combined.add_argument("--output")

    estimate = commands.add_parser("estimate", help="estimate a target profile from a measured pilot")
    estimate.add_argument("--pilot-target", required=True)
    estimate.add_argument("--pilot-receipt", required=True)
    estimate.add_argument("--profile", required=True)
    estimate.add_argument("--free-bytes", type=int)
    estimate.add_argument("--output")
    return parser


def _write_absent_output(
    parser: argparse.ArgumentParser,
    output: Path,
    value: dict[str, Any],
) -> None:
    if output.exists() or not output.parent.is_dir():
        parser.error("benchmark output must be an absent file under an existing parent")
    output.write_bytes(serialize_json(value))


def _require_current_preflight(
    parser: argparse.ArgumentParser,
    preflight: dict[str, Any],
    profile_id: str,
    target_parent: Path,
) -> None:
    if (
        preflight.get("estimation_contract_version") != "p2-reference-estimate@1.0"
        or preflight.get("target_profile_id") != profile_id
        or preflight.get("may_proceed") is not True
    ):
        parser.error("generate-measure requires a passing matching preflight receipt")
    if not target_parent.is_dir():
        parser.error("generation target parent does not exist")
    current_free = shutil.disk_usage(target_parent).free
    required = int(preflight["estimated_total_bytes"]) + int(
        preflight["required_free_space_reserve_bytes"]
    )
    if current_free < required:
        parser.error("current free space no longer satisfies the preflight reserve")


if __name__ == "__main__":
    raise SystemExit(main())
