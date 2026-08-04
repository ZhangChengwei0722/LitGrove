from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from benchmarks.p11_operational_density.generator import inspect_generated_workspace
from benchmarks.p11_operational_density.measurement import measure_maintenance
from research_kb.storage.json_io import serialize_json


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    generated = inspect_generated_workspace(
        Path(args[0]),
        validate_full_bundle=False,
    )
    result = measure_maintenance(generated)
    sys.stdout.buffer.write(serialize_json(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
