from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path("/data/yphu/TCAD_Agent/code")


@pytest.mark.skipif(
    os.getenv("TCAD_ENABLE_REAL_FLOW_TEST") != "1",
    reason="Real Sentaurus flow test is manual-only. Set TCAD_ENABLE_REAL_FLOW_TEST=1 to enable.",
)
def test_real_flow() -> None:
    # 使用核心 API 做真实链路测试，避免依赖 CLI 形态。
    cmd = [
        "python3",
        "-c",
        (
            "import json;"
            "from pathlib import Path;"
            "from main import clean_runtime;"
            "from src.agent_system import TCADAgentSystem;"
            "clean_runtime();"
            "agent=TCADAgentSystem(Path('/data/yphu/TCAD_Agent/code'));"
            "out=agent.agent_decide_and_execute('生成2D NMOS器件，包含Si衬底、SiO2栅氧，完成IdVg仿真，请执行完整流程并完成验证');"
            "print(json.dumps(out, ensure_ascii=False))"
        ),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, check=False, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("stage") == "validated", data
