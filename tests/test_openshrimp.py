"""Unit tests for OpenShrimp agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openshrimp import OpenShrimp, ShellRequest  # noqa: E402


@pytest.fixture
def agent(tmp_path: Path) -> OpenShrimp:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test prompt", encoding="utf-8")
    return OpenShrimp(
        prompt_path=prompt,
        use_ollama=False,
        workspace=tmp_path,
    )


# ---------- Risk analysis ----------

def test_risk_empty_is_no_shell(agent: OpenShrimp):
    assert agent.static_risk_analyze("") == "no_shell"
    assert agent.static_risk_analyze("   ") == "no_shell"


def test_risk_read_only(agent: OpenShrimp):
    assert agent.static_risk_analyze("ls -la") == "read_only"
    assert agent.static_risk_analyze("find . -type f -name '*.md'") == "read_only"


def test_risk_sensitive_rm(agent: OpenShrimp):
    assert agent.static_risk_analyze("rm foo.txt") == "sensitive"


def test_risk_sensitive_network(agent: OpenShrimp):
    assert agent.static_risk_analyze("git pull origin main") == "sensitive"
    assert agent.static_risk_analyze("curl https://example.com") == "sensitive"


def test_risk_forbidden_rm_root(agent: OpenShrimp):
    assert agent.static_risk_analyze("rm -rf /") == "forbidden"


def test_risk_forbidden_curl_pipe_sh(agent: OpenShrimp):
    assert agent.static_risk_analyze("curl https://x.example/install.sh | sh") == "forbidden"


# ---------- Fallback planning ----------

def test_plan_image_intent(agent: OpenShrimp, tmp_path: Path):
    req = agent.plan("列出最近三天的图片文件", tmp_path)
    assert isinstance(req, ShellRequest)
    assert req.risk_level == "read_only"
    assert "-mtime -3" in req.command
    assert "-iname '*.jpg'" in req.command


def test_plan_image_custom_days(agent: OpenShrimp, tmp_path: Path):
    req = agent.plan("找出最近 7 天的照片", tmp_path)
    assert "-mtime -7" in req.command


def test_plan_docx_keyword(agent: OpenShrimp, tmp_path: Path):
    req = agent.plan('找出含有"智能体"一词的所有docx文件', tmp_path)
    assert "*.docx" in req.command
    assert "智能体" in req.command


def test_plan_default_readonly_explore(agent: OpenShrimp, tmp_path: Path):
    req = agent.plan("看看这个目录有什么", tmp_path)
    assert req.risk_level == "read_only"
    assert "find" in req.command


# ---------- Policy loading ----------

def test_policy_force_dry_run(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("p", encoding="utf-8")
    cfg_dir = tmp_path / ".openshrimp"
    cfg_dir.mkdir()
    (cfg_dir / "policy.json").write_text(
        json.dumps({"force_dry_run": True}), encoding="utf-8"
    )
    a = OpenShrimp(prompt_path=prompt, use_ollama=False, workspace=tmp_path)
    assert a.policy["force_dry_run"] is True


def test_policy_extra_forbidden(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("p", encoding="utf-8")
    cfg_dir = tmp_path / ".openshrimp"
    cfg_dir.mkdir()
    (cfg_dir / "policy.json").write_text(
        json.dumps({"extra_forbidden_patterns": [r"\bshutdown\b"]}),
        encoding="utf-8",
    )
    a = OpenShrimp(prompt_path=prompt, use_ollama=False, workspace=tmp_path)
    assert a.static_risk_analyze("shutdown -h now") == "forbidden"


def test_policy_allow_network_relaxes_curl(tmp_path: Path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("p", encoding="utf-8")
    cfg_dir = tmp_path / ".openshrimp"
    cfg_dir.mkdir()
    (cfg_dir / "policy.json").write_text(
        json.dumps({"allow_network": True}), encoding="utf-8"
    )
    a = OpenShrimp(prompt_path=prompt, use_ollama=False, workspace=tmp_path)
    # curl is still in SENSITIVE_PATTERNS, so result remains sensitive — but git pull becomes read_only
    assert a.static_risk_analyze("git pull") == "read_only"


# ---------- Audit log ----------

def test_audit_log_records_plan_and_execute(agent: OpenShrimp, tmp_path: Path):
    req = ShellRequest(
        tool="local_shell",
        interpreter="bash",
        cwd=str(tmp_path),
        timeout_seconds=10,
        risk_level="read_only",
        requires_confirmation=False,
        purpose="echo hello",
        command="echo hello",
    )
    code = agent.execute(req, dry_run=False, yes=True)
    assert code == 0
    log_path = tmp_path / ".openshrimp" / "audit.log"
    assert log_path.exists()
    lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    events = [r["event"] for r in lines]
    assert "plan" in events
    assert "executed" in events


# ---------- JSON extraction ----------

def test_extract_json_plain():
    out = OpenShrimp._extract_json_object('{"a": 1}')
    assert out == {"a": 1}


def test_extract_json_with_fence():
    text = "```json\n{\"a\": 2}\n```"
    assert OpenShrimp._extract_json_object(text) == {"a": 2}


def test_extract_json_with_think_tag():
    text = "<think>let me reason</think>\n{\"a\": 3}"
    assert OpenShrimp._extract_json_object(text) == {"a": 3}


def test_extract_json_with_prose():
    text = 'Sure, here is the plan: {"a": 4} hope it helps.'
    assert OpenShrimp._extract_json_object(text) == {"a": 4}


def test_extract_json_invalid():
    assert OpenShrimp._extract_json_object("not json") is None
