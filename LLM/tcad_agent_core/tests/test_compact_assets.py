from __future__ import annotations

import csv
import importlib.util
import json
import math
from pathlib import Path


MODULE_PATH = Path("/data/yphu/Web-FabGPT/LLM/tcad_agent_core/web/demo_assets/build_compact_demo_assets.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("compact_assets_builder", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _relative_rmse(rows: list[dict[str, str]], curve_type: str) -> float:
    points = [row for row in rows if row["curve_type"] == curve_type]
    squared_errors = []
    for row in points:
        tcad_value = float(row["y_tcAD"])
        fit_value = float(row["y_compact"])
        denominator = max(abs(tcad_value), 1e-12)
        squared_errors.append(((fit_value - tcad_value) / denominator) ** 2)
    return math.sqrt(sum(squared_errors) / len(squared_errors))


def test_compact_asset_builder_produces_neutral_outputs_and_close_overlay(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "TARGET_ROOT", tmp_path)

    params = module.extract_parameters()
    module.write_outputs(params)

    summary_text = (tmp_path / "compact_model_summary.md").read_text(encoding="utf-8")
    parameter_card = json.loads((tmp_path / "parameter_card.json").read_text(encoding="utf-8"))
    overlay_rows = list(csv.DictReader((tmp_path / "compact_model_overlay.csv").open("r", encoding="utf-8")))

    assert (tmp_path / "nmos_compact_model.va").exists()
    assert "demo" not in summary_text.lower()
    assert "thesis" not in summary_text.lower()
    assert "nMOS" in summary_text
    assert "演示级" not in json.dumps(parameter_card, ensure_ascii=False)
    assert _relative_rmse(overlay_rows, "transfer") < 0.05
    assert _relative_rmse(overlay_rows, "output") < 0.05
