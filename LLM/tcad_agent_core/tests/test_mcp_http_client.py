from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

from src.mcp_http_client import TcadMCPHTTPClient


class _DumpableResult:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, mode: str = "python"):
        return self.payload


def test_decode_call_result_prefers_structured_content():
    result = _DumpableResult(
        {
            "isError": False,
            "structuredContent": {"ok": True, "value": 1},
            "content": [{"type": "text", "text": "ignored"}],
        }
    )

    out = TcadMCPHTTPClient._decode_call_result(result)

    assert out["is_error"] is False
    assert out["data"] == {"ok": True, "value": 1}


def test_decode_call_result_parses_single_json_text():
    result = {
        "isError": False,
        "structuredContent": None,
        "content": [{"type": "text", "text": '{"ok": true, "items": [1, 2]}'}],
    }

    out = TcadMCPHTTPClient._decode_call_result(result)

    assert out["data"] == {"ok": True, "items": [1, 2]}


def test_decode_call_result_falls_back_to_text_list():
    result = {
        "isError": False,
        "structuredContent": None,
        "content": [
            {"type": "text", "text": "line1"},
            {"type": "text", "text": "line2"},
        ],
    }

    out = TcadMCPHTTPClient._decode_call_result(result)

    assert out["data"] == {"texts": ["line1", "line2"]}
