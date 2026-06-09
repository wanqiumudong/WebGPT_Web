from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

from src.agent_system import TCADAgentSystem
from src.core import SessionSpec, SessionState, Targets


class _DummyTracer:
    def event(self, *args, **kwargs):
        return None


def _make_agent(tmp_path: Path) -> tuple[TCADAgentSystem, SessionState]:
    runtime_root = tmp_path / "runtime" / "default"
    for sub in ("run", "logs", "reports", "inputs"):
        (runtime_root / sub).mkdir(parents=True, exist_ok=True)
    state = SessionState(
        session_id="default",
        session_dir=runtime_root,
        spec=SessionSpec(
            requirement="demo",
            device_type="nmos",
            simulation_type="IdVg",
            parameters={},
            targets=Targets(),
        ),
        stage="created",
    )

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = runtime_root
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._save_state = lambda new_state: None
    return agent, state


def test_uploaded_sde_assets_keep_unique_files_and_restore_alias(tmp_path: Path):
    agent, state = _make_agent(tmp_path)
    source_a = tmp_path / "deck_a.cmd"
    source_b = tmp_path / "deck_b.cmd"
    source_a.write_text("deck-a", encoding="utf-8")
    source_b.write_text("deck-b", encoding="utf-8")

    agent.register_session_asset(str(source_a), file_name="deck_a.cmd", role="sde_cmd")
    agent.register_session_asset(str(source_b), file_name="deck_b.cmd", role="sde_cmd")

    alias_path = state.session_dir / "run" / "sde_dvs.cmd"
    assert alias_path.read_text(encoding="utf-8") == "deck-b"
    assert Path(state.artifacts["uploaded_asset::deck_a.cmd"]).name == "deck_a.cmd"
    assert Path(state.artifacts["uploaded_asset::deck_b.cmd"]).name == "deck_b.cmd"

    deleted = agent.delete_session_asset("deck_b.cmd")
    assert deleted["deleted"] is True
    assert alias_path.exists()
    assert alias_path.read_text(encoding="utf-8") == "deck-a"

    assets = agent.list_session_assets()["assets"]
    assert [item["file_name"] for item in assets] == ["deck_a.cmd"]
    assert assets[0]["active_keys"] == ["sde_cmd"]


def test_explicit_image_request_can_resolve_uploaded_file_name(tmp_path: Path):
    agent, state = _make_agent(tmp_path)
    image_path = state.session_dir / "inputs" / "foo.png"
    image_path.write_bytes(b"png")
    state.artifacts["uploaded_asset::foo.png"] = str(image_path)
    state.artifacts["uploaded_asset::foo.png::role"] = "input"
    state.artifacts["uploaded_asset::foo.png::registered_at"] = "1.0"

    resolved = agent._resolve_image_path_from_instruction("请看图 foo.png 里有什么", state)
    assert resolved == image_path
