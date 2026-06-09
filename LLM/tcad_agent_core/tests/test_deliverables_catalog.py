from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path('/data/yphu/TCAD_Agent/code')
sys.path.insert(0, str(ROOT))

from src.deliverables_catalog import build_active_manifest, build_manual_catalog


def test_recovered_catalog_counts_match_snapshot() -> None:
    manual = build_manual_catalog(ROOT)

    assert manual['task_count'] == 23


def test_active_manifest_contains_current_and_manual_cases_only() -> None:
    manifest = build_active_manifest(ROOT)
    cases = manifest['cases']
    case_ids = {item['case_id'] for item in cases}

    assert manifest['total_cases'] == 1 + 23
    assert 'RUN_1772343388_NMOS_E2E_DIRECT_V2' in case_ids
    assert 'T01_INTRO_DIODE_FULLFLOW' in case_ids
    assert 'L04_structure' not in case_ids
    assert manifest['excluded_archival_snapshots']
