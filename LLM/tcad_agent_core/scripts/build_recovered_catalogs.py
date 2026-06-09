#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path('/data/yphu/TCAD_Agent/code')
sys.path.insert(0, str(ROOT))

from src.deliverables_catalog import write_catalog_bundle


def main() -> int:
    written = write_catalog_bundle(ROOT)
    print(json.dumps({"written": [str(path) for path in written]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
