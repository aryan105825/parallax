"""
pipeline/tests/test_pipeline.py
Parallax — pipeline test suite

Run:
    cd pipeline && python -m pytest tests/ -v

Coverage:
    - State schema validation
    - Agent node unit tests (mocked LLM + GitHub calls)
    - git_tools diff/patch logic (pure Python, no external deps)
    - Graph wiring smoke test
    - FastAPI endpoint contracts
"""

import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# ── Path setup ─────────────────────────────────────────────────────────────────
# Allow imports from pipeline/ root when running from project root or pipeline/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def base_state():
    """Minimal valid ParallaxState for testing individual nodes."""
    return {
        "scan_id":      "test-scan-001",
        "trigger":      "raw code input",
        "target_branch": "main",
        "triggered_at": "2026-05-05T00:00:00Z",
        "status":       "running",
        "sse_events":   [],
        "repo_metadata":       None,
        "file_tree":           None,
        "file_contents":       None,
        "diff_summary":        None,
        "ingestor_error":      None,
        "fast_findings":       None,
        "fast_latency_ms":     None,
        "fast_model_version":  None,
        "fast_error":          None,
        "deep_findings":       None,
        "deep_latency_ms":     None,
        "deep_model_version":  None,
        "deep_error":          None,
        "consensus_findings":  None,
        "conflicts":           None,
        "consensus_summary":   None,
        "consensus_error":     None,
        "fixed_findings":      None,
        "fix_error":           None,
        "pr_url":              None,
        "pr_number":           None,
        "pr_branch":           None,
        "pr_error":            None,
        "completed_at":        None,
    }


@pytest.fixture
def sample_findings():
    return [
        {
            "id": "F001",
            "type": "SQL Injection",
            "file": "src/auth.py",
            "line": 12,
            "priority": "P0",
            "cvss": 9.8,
            "owasp": "A03:2021",
            "cwe": "CWE-89",
            "source": "consensus",
            "fix_hint": "Use parameterised queries",
        }
    ]


# ==============================================================================
# State schema
# ==============================================================================

class TestParallaxState:
    def test_state_keys_present(self, base_state):
        from state import ParallaxState
        required = [
            "scan_id", "trigger", "target_branch", "triggered_at",
            "status", "sse_events",
        ]
        for key in required:
            assert key in base_state, f"Missing required key: {key}"

    def test_sse_events_is_list(self, base_state):
        assert isinstance(base_state["sse_events"], list)


# ==============================================================================
# git_tools — pure Python, no mocking needed
# ==============================================================================

class TestGitTools:
    def test_generate_unified_diff_detects_change(self):
        from tools.git_tools import generate_unified_diff
        orig   = "a = 1\nb = 2\n"
        patched = "a = 1\nb = 3\n"
        diff   = generate_unified_diff(orig, patched, "example.py")
        assert "-b = 2" in diff
        assert "+b = 3" in diff

    def test_generate_unified_diff_identical_files(self):
        from tools.git_tools import generate_unified_diff
        content = "no changes here\n"
        diff    = generate_unified_diff(content, content, "unchanged.py")
        assert diff == ""

    def test_apply_fix_full_replacement(self):
        from tools.git_tools import apply_fix_to_content
        original  = "SELECT * FROM users WHERE id = ' + uid\n"
        fix_code  = "cursor.execute('SELECT * FROM users WHERE id = %s', (uid,))\n"
        patched, diff = apply_fix_to_content(original, fix_code, "src/db.py")
        assert patched == fix_code
        assert len(diff) > 0

    def test_apply_fix_no_fix_code(self):
        from tools.git_tools import apply_fix_to_content
        original = "original content\n"
        patched, diff = apply_fix_to_content(original, "", "file.py")
        assert patched == original
        assert diff == ""

    def test_build_patch_summary_with_findings(self, sample_findings):
        from tools.git_tools import build_patch_summary
        summary = build_patch_summary(sample_findings)
        assert "F001" in summary
        assert "SQL Injection" in summary
        assert "src/auth.py" in summary

    def test_build_patch_summary_empty(self):
        from tools.git_tools import build_patch_summary
        summary = build_patch_summary([])
        assert "No automated fixes" in summary

    def test_extract_changed_files(self, sample_findings):
        from tools.git_tools import extract_changed_files
        finding_with_fix = dict(sample_findings[0])
        finding_with_fix["fix_code"] = "cursor.execute('SELECT * FROM users WHERE id=%s',(uid,))\n"
        original_contents = {"src/auth.py": "SELECT * FROM users WHERE id=' + uid\n"}

        changed = extract_changed_files([finding_with_fix], original_contents)
        assert "src/auth.py" in changed
        assert changed["src/auth.py"] == finding_with_fix["fix_code"]

    def test_extract_changed_files_missing_file(self, sample_findings):
        from tools.git_tools import extract_changed_files
        finding_with_fix = dict(sample_findings[0])
        finding_with_fix["fix_code"] = "fixed"
        # file_contents is empty — should silently skip
        changed = extract_changed_files([finding_with_fix], {})
        assert changed == {}

    def test_summarise_diff_stats(self):
        from tools.git_tools import generate_unified_diff, summarise_diff_stats
        diff = generate_unified_diff("a = 1\nb = 2\n", "a = 1\nb = 3\n", "f.py")
        stats = summarise_diff_stats(diff)
        assert stats["additions"] == 1
        assert stats["deletions"] == 1
        assert stats["chunks"]    == 1


# ==============================================================================
# Ingestor node
# ==============================================================================

class TestIngestorNode:
    def test_ingestor_raw_input(self, base_state):
        from agents.ingestor import ingestor_node
        state = {**base_state, "trigger": "print('hello world')"}

        with patch("tools.embedding_client.get_embedding", return_value=[]), \
             patch("tools.vector_store.index_files", return_value=None):
            result = ingestor_node(state)

        assert "file_contents" in result
        assert "raw_input.txt" in result["file_contents"]
        assert result["file_contents"]["raw_input.txt"] == "print('hello world')"

    def test_ingestor_emits_sse_events(self, base_state):
        from agents.ingestor import ingestor_node
        state = {**base_state, "trigger": "some code"}

        with patch("tools.embedding_client.get_embedding", return_value=[]), \
             patch("tools.vector_store.index_files", return_value=None):
            result = ingestor_node(state)

        events = result.get("sse_events", [])
        event_types = [e["event"] for e in events]
        assert "agent_start"    in event_types
        assert "agent_complete" in event_types

    def test_ingestor_skips_large_files(self, base_state):
        from agents.ingestor import ingestor_node
        big_content = "x" * (11 * 1024 * 1024)  # 11 MB > default 10 MB limit
        state = {**base_state, "trigger": "https://github.com/owner/repo"}

        mock_files = {"large_file.py": big_content}
        with patch("tools.github_tools.fetch_repo_files", return_value=mock_files), \
             patch("tools.embedding_client.get_embedding", return_value=[]),         \
             patch("tools.vector_store.index_files", return_value=None):
            result = ingestor_node(state)

        assert result["file_contents"]["large_file.py"].startswith("[SKIPPED")


# ==============================================================================
# Fast Analyst node
# ==============================================================================

class TestFastAnalystNode:
    def _mock_llm_response(self, findings):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({"findings": findings})
                }
            }]
        }

    def test_fast_analyst_returns_findings(self, base_state):
        from agents.fast_analyst import fast_analyst_node
        state = {
            **base_state,
            "file_contents": {"src/auth.py": "query = 'SELECT * FROM users WHERE id = ' + uid"},
        }
        mock_resp = self._mock_llm_response([{"id": "F001", "type": "SQL Injection"}])

        with patch("llm_client.LLMClient.call_fast_analyst", return_value=mock_resp):
            result = fast_analyst_node(state)

        assert result["fast_findings"] == [{"id": "F001", "type": "SQL Injection"}]
        assert result["fast_latency_ms"] >= 0

    def test_fast_analyst_handles_llm_error(self, base_state):
        from agents.fast_analyst import fast_analyst_node
        state = {**base_state, "file_contents": {"f.py": "code"}}

        with patch("llm_client.LLMClient.call_fast_analyst", side_effect=Exception("timeout")):
            result = fast_analyst_node(state)

        assert result["fast_findings"] == []
        assert "timeout" in result["fast_error"]

    def test_fast_analyst_emits_sse(self, base_state):
        from agents.fast_analyst import fast_analyst_node
        state = {**base_state, "file_contents": {"f.py": "code"}}
        mock_resp = self._mock_llm_response([])

        with patch("llm_client.LLMClient.call_fast_analyst", return_value=mock_resp):
            result = fast_analyst_node(state)

        event_types = [e["event"] for e in result["sse_events"]]
        assert "agent_start"    in event_types
        assert "agent_complete" in event_types


# ==============================================================================
# Consensus node
# ==============================================================================

class TestConsensusNode:
    def _mock_consensus_response(self):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "consensus_findings": [{"id": "F001", "source": "consensus"}],
                        "conflicts": [],
                        "merge_recommendation": "BLOCK",
                        "merge_recommendation_reason": "P0 SQL injection present",
                    })
                }
            }]
        }

    def test_consensus_merges_findings(self, base_state, sample_findings):
        from agents.consensus import consensus_node
        state = {
            **base_state,
            "fast_findings": sample_findings,
            "deep_findings": sample_findings,
            "fast_latency_ms": 487,
            "deep_latency_ms": 4832,
        }

        with patch("llm_client.LLMClient.call_consensus", return_value=self._mock_consensus_response()):
            result = consensus_node(state)

        assert result["consensus_findings"] == [{"id": "F001", "source": "consensus"}]
        assert result["consensus_summary"] == "P0 SQL injection present"

    def test_consensus_emits_parallel_models_complete(self, base_state):
        from agents.consensus import consensus_node
        state = {
            **base_state,
            "fast_findings":   [],
            "deep_findings":   [],
            "fast_latency_ms": 487,
            "deep_latency_ms": 4832,
        }

        with patch("llm_client.LLMClient.call_consensus", return_value=self._mock_consensus_response()):
            result = consensus_node(state)

        event_types = [e["event"] for e in result["sse_events"]]
        assert "parallel_models_complete" in event_types

        pmc = next(e for e in result["sse_events"] if e["event"] == "parallel_models_complete")
        assert pmc["data"]["fast_latency_ms"] == 487
        assert pmc["data"]["deep_latency_ms"] == 4832


# ==============================================================================
# Fix Generator node
# ==============================================================================

class TestFixGeneratorNode:
    def _mock_fix_response(self, fixes):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({"fixes": fixes})
                }
            }]
        }

    def test_fix_generator_attaches_fix_code(self, base_state, sample_findings):
        from agents.fix_generator import fix_generator_node
        state = {**base_state, "consensus_findings": sample_findings}
        fix = {"id": "F001", "fix_code": "cursor.execute('SELECT...', (uid,))", "fix_explanation": "Use params"}

        with patch("llm_client.LLMClient.call_fix_generator", return_value=self._mock_fix_response([fix])):
            result = fix_generator_node(state)

        assert result["fixed_findings"][0]["fix_code"] == fix["fix_code"]

    def test_fix_generator_skips_empty_findings(self, base_state):
        from agents.fix_generator import fix_generator_node
        state = {**base_state, "consensus_findings": []}
        result = fix_generator_node(state)
        assert result["fixed_findings"] == []
        assert result["fix_error"] is None


# ==============================================================================
# PR Agent node
# ==============================================================================

class TestPRAgentNode:
    def test_pr_agent_non_github_trigger(self, base_state, sample_findings):
        from agents.pr_agent import pr_agent_node
        state = {**base_state, "trigger": "raw code", "fixed_findings": sample_findings}
        result = pr_agent_node(state)
        assert result["pr_url"] is None
        assert result["pr_error"] is None

    def test_pr_agent_github_trigger(self, base_state, sample_findings):
        from agents.pr_agent import pr_agent_node
        state = {
            **base_state,
            "trigger":        "https://github.com/owner/repo@main",
            "fixed_findings": sample_findings,
        }
        with patch("tools.github_tools.create_pr", return_value="https://github.com/owner/repo/pull/42"):
            result = pr_agent_node(state)

        assert result["pr_url"] == "https://github.com/owner/repo/pull/42"
        assert result["pr_number"] == 42
        assert result["pr_error"] is None

    def test_pr_agent_emits_sse(self, base_state):
        from agents.pr_agent import pr_agent_node
        state = {**base_state, "trigger": "raw code", "fixed_findings": []}
        result = pr_agent_node(state)
        event_types = [e["event"] for e in result["sse_events"]]
        assert "agent_start"    in event_types
        assert "agent_complete" in event_types


# ==============================================================================
# Graph wiring smoke test
# ==============================================================================

class TestGraphWiring:
    def test_graph_compiles(self):
        from graph import create_parallax_graph
        graph = create_parallax_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        from graph import create_parallax_graph
        graph = create_parallax_graph()
        # LangGraph compiled graphs expose node names via the graph attribute
        node_names = set(graph.graph.nodes.keys())
        expected = {"ingestor", "fast_analyst", "deep_analyst", "consensus", "fix_generator", "pr_agent"}
        assert expected.issubset(node_names), f"Missing nodes: {expected - node_names}"


# ==============================================================================
# FastAPI endpoint contracts
# ==============================================================================

class TestFastAPIEndpoints:
    @pytest.fixture(autouse=True)
    def client(self):
        from fastapi.testclient import TestClient
        import main as app_module
        self.client = TestClient(app_module.app)

    def test_health_endpoint_exists(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            resp = self.client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "dependencies" in body

    def test_post_scan_returns_scan_id(self):
        resp = self.client.post("/scan", json={"trigger": "print('hello')", "target_branch": "main"})
        assert resp.status_code == 200
        body = resp.json()
        assert "scan_id"    in body
        assert "stream_url" in body
        assert body["status"] == "pending"

    def test_get_scan_not_found(self):
        resp = self.client.get("/scan/nonexistent-id-xyz")
        assert resp.status_code == 404

    def test_get_scan_report_not_found(self):
        resp = self.client.get("/scan/nonexistent-id-xyz/report")
        assert resp.status_code == 404

    def test_scan_and_retrieve_state(self):
        # Start a scan
        post_resp = self.client.post("/scan", json={"trigger": "x = 1"})
        scan_id   = post_resp.json()["scan_id"]

        # Immediately retrieve state (will be pending or running)
        get_resp  = self.client.get(f"/scan/{scan_id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["scan_id"] == scan_id
        assert body["status"] in ("pending", "running", "complete", "error")
