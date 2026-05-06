"""
github_tools.py — Real GitHub API integration for Parallax.

Implements:
  fetch_repo_files  — recursively fetches a repo tree via GitHub REST API v3
  create_pr         — creates a branch, applies fix_code patches via difflib,
                      commits, and opens a Pull Request with the full spec body
"""

import os
import re
import base64
import difflib
import requests
from typing import Optional

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"
_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh",
    ".bash", ".zsh", ".yaml", ".yml", ".toml", ".json", ".md", ".txt",
    ".env", ".cfg", ".ini", ".xml", ".html", ".css", ".sql",
}


def _gh_headers() -> dict:
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_repo_url(repo_url: str) -> tuple[str, str]:
    """
    Accept any of:
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      https://github.com/owner/repo@branch   (branch stripped before calling)
    Returns (owner, repo_name).
    """
    url = repo_url.rstrip("/").removesuffix(".git")
    match = re.search(r"github\.com/([^/]+)/([^/@]+)", url)
    if not match:
        raise ValueError(f"Cannot parse GitHub repo URL: {repo_url!r}")
    return match.group(1), match.group(2)


def _is_text_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in _TEXT_EXTENSIONS


# ---------------------------------------------------------------------------
# fetch_repo_files
# ---------------------------------------------------------------------------

def fetch_repo_files(repo_url: str, branch: str = "main") -> dict[str, str]:
    """
    Fetch all text source files from a GitHub repository at the given branch.

    Returns:
        {filepath: file_content_as_string, ...}

    File size guardrail is applied by the caller (ingestor_node) so this
    function returns raw content strings without truncating.
    """
    # Strip @branch suffix if present in URL
    if "@" in repo_url.split("github.com")[-1]:
        repo_url, branch = repo_url.rsplit("@", 1)

    owner, repo = _parse_repo_url(repo_url)
    headers = _gh_headers()
    max_file_bytes = int(os.getenv("PIPELINE_MAX_FILE_SIZE_MB", "10")) * 1024 * 1024

    # Step 1: Get the recursive tree for the branch
    tree_url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    resp = requests.get(tree_url, headers=headers, timeout=30)
    resp.raise_for_status()
    tree_data = resp.json()

    blobs = [
        item for item in tree_data.get("tree", [])
        if item["type"] == "blob" and _is_text_file(item["path"])
        and item.get("size", 0) <= max_file_bytes
    ]

    # Step 2: Fetch each blob (up to 200 files to avoid abuse limits)
    file_contents: dict[str, str] = {}
    for item in blobs[:200]:
        blob_url = f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs/{item['sha']}"
        try:
            blob_resp = requests.get(blob_url, headers=headers, timeout=20)
            blob_resp.raise_for_status()
            blob = blob_resp.json()
            if blob.get("encoding") == "base64":
                raw = base64.b64decode(blob["content"]).decode("utf-8", errors="replace")
            else:
                raw = blob.get("content", "")
            file_contents[item["path"]] = raw
        except Exception:
            # Skip files that fail individually — never crash the whole scan
            continue

    return file_contents


# ---------------------------------------------------------------------------
# create_pr
# ---------------------------------------------------------------------------

def create_pr(
    repo_url: str,
    branch: str,
    title: str,
    body: str,
    fixed_findings: list,
) -> Optional[str]:
    """
    1. Resolve the default branch's HEAD SHA.
    2. Create a new branch `parallax/security-fixes-<scan_id_prefix>` from HEAD.
    3. For every finding that carries `fix_code`, apply the patch via difflib
       and push the updated blob → tree → commit.
    4. Open a Pull Request with the full spec body template.

    Returns the PR HTML URL, e.g. https://github.com/owner/repo/pull/42
    """
    owner, repo = _parse_repo_url(repo_url)
    headers = _gh_headers()

    # -----------------------------------------------------------------------
    # 1. Resolve base branch SHA
    # -----------------------------------------------------------------------
    base_branch = os.getenv("PIPELINE_TARGET_BRANCH", "main")
    ref_resp = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{base_branch}",
        headers=headers,
        timeout=15,
    )
    ref_resp.raise_for_status()
    base_sha = ref_resp.json()["object"]["sha"]

    # -----------------------------------------------------------------------
    # 2. Create the new branch
    # -----------------------------------------------------------------------
    create_ref_resp = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
        headers=headers,
        json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        timeout=15,
    )
    # 422 = ref already exists — safe to proceed
    if create_ref_resp.status_code not in (201, 422):
        create_ref_resp.raise_for_status()

    # -----------------------------------------------------------------------
    # 3. Apply fix_code patches for each finding that has one
    # -----------------------------------------------------------------------
    files_to_patch: dict[str, str] = {}

    for finding in fixed_findings:
        fix_code = finding.get("fix_code")
        file_path = finding.get("file")
        if not fix_code or not file_path:
            continue

        # Fetch the current file content from the new branch
        if file_path not in files_to_patch:
            file_resp = requests.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/contents/{file_path}",
                headers=headers,
                params={"ref": branch},
                timeout=15,
            )
            if file_resp.status_code == 200:
                raw = base64.b64decode(file_resp.json()["content"]).decode(
                    "utf-8", errors="replace"
                )
                files_to_patch[file_path] = raw
            else:
                # File may not exist yet (rare) — skip
                continue

        original = files_to_patch[file_path]

        # Use difflib to apply a unified-diff-style patch; fall back to
        # whole-file replacement when the finding provides a full rewrite.
        if fix_code.startswith("@@") or fix_code.startswith("---"):
            # Looks like a unified diff — attempt to apply it
            patched_lines = _apply_unified_diff(original, fix_code)
            files_to_patch[file_path] = patched_lines
        else:
            # Treat fix_code as the complete new file content
            files_to_patch[file_path] = fix_code

    # Push each patched file as a new blob
    new_tree_items = []
    for file_path, new_content in files_to_patch.items():
        blob_resp = requests.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs",
            headers=headers,
            json={"content": new_content, "encoding": "utf-8"},
            timeout=15,
        )
        blob_resp.raise_for_status()
        new_tree_items.append(
            {
                "path": file_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_resp.json()["sha"],
            }
        )

    if new_tree_items:
        # Create a new tree based on the current HEAD tree
        head_commit_resp = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/commits/{base_sha}",
            headers=headers,
            timeout=15,
        )
        head_commit_resp.raise_for_status()
        base_tree_sha = head_commit_resp.json()["tree"]["sha"]

        new_tree_resp = requests.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees",
            headers=headers,
            json={"base_tree": base_tree_sha, "tree": new_tree_items},
            timeout=15,
        )
        new_tree_resp.raise_for_status()
        new_tree_sha = new_tree_resp.json()["sha"]

        # Commit
        commit_message = (
            f"fix(security): Parallax automated security fixes\n\n"
            f"Applied {len(new_tree_items)} surgical patch(es) generated by\n"
            f"the Parallax dual-model analysis pipeline (AMD MI300X).\n\n"
            f"Findings addressed:\n"
            + "\n".join(
                f"  - [{f.get('priority','?')}] {f.get('type','?')} in {f.get('file','?')} "
                f"(CVSS {f.get('cvss','?')})"
                for f in fixed_findings
                if f.get("fix_code")
            )
        )

        commit_resp = requests.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/commits",
            headers=headers,
            json={
                "message": commit_message,
                "tree": new_tree_sha,
                "parents": [base_sha],
            },
            timeout=15,
        )
        commit_resp.raise_for_status()
        new_commit_sha = commit_resp.json()["sha"]

        # Update branch ref
        requests.patch(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
            headers=headers,
            json={"sha": new_commit_sha, "force": False},
            timeout=15,
        ).raise_for_status()

    # -----------------------------------------------------------------------
    # 4. Build full PR body following the spec template
    # -----------------------------------------------------------------------
    p0_findings = [f for f in fixed_findings if f.get("priority") == "P0"]
    p1_findings = [f for f in fixed_findings if f.get("priority") == "P1"]
    pr_body = _build_pr_body(body, fixed_findings, p0_findings, p1_findings)

    # -----------------------------------------------------------------------
    # 5. Open the Pull Request
    # -----------------------------------------------------------------------
    pr_resp = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers=headers,
        json={
            "title": title,
            "body": pr_body,
            "head": branch,
            "base": base_branch,
            "draft": False,
        },
        timeout=15,
    )
    pr_resp.raise_for_status()
    return pr_resp.json()["html_url"]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _apply_unified_diff(original: str, diff_text: str) -> str:
    """
    Best-effort application of a unified diff string to original file content.
    Falls back to original if the patch cannot be applied cleanly.
    """
    original_lines = original.splitlines(keepends=True)
    try:
        # Use difflib to reconstruct patched content
        diff_lines = diff_text.splitlines(keepends=True)
        result: list[str] = []
        orig_idx = 0

        i = 0
        while i < len(diff_lines):
            line = diff_lines[i]
            if line.startswith("@@"):
                # Parse hunk header: @@ -start,count +start,count @@
                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if match:
                    old_start = int(match.group(1)) - 1
                    # Copy unchanged lines up to this hunk
                    result.extend(original_lines[orig_idx:old_start])
                    orig_idx = old_start
                i += 1
                continue
            if line.startswith("-"):
                orig_idx += 1  # skip removed line
            elif line.startswith("+"):
                result.append(line[1:])  # add new line
            elif not line.startswith("\\"):
                result.append(line[1:] if line.startswith(" ") else line)
                orig_idx += 1
            i += 1

        # Append remaining original lines
        result.extend(original_lines[orig_idx:])
        return "".join(result)
    except Exception:
        return original  # safe fallback


def _build_pr_body(preamble: str, findings: list, p0: list, p1: list) -> str:
    """Build the full PR description following the Parallax spec template."""
    lines = [
        preamble.strip(),
        "",
        "---",
        "",
        "## 🔍 Findings Summary",
        "",
        f"| Priority | Count |",
        f"|----------|-------|",
        f"| P0 (Critical) | {len(p0)} |",
        f"| P1 (High) | {len(p1)} |",
        f"| Total | {len(findings)} |",
        "",
        "## 🛠 Patches Applied",
        "",
    ]

    for f in findings:
        if not f.get("fix_code"):
            continue
        lines += [
            f"### `{f.get('file', 'unknown')}` — {f.get('type', 'Unknown')}",
            "",
            f"**Priority:** {f.get('priority', '?')}  "
            f"**CVSS:** {f.get('cvss', '?')}  "
            f"**OWASP:** {f.get('owasp', '?')}  "
            f"**CWE:** {f.get('cwe', '?')}",
            "",
            f"> {f.get('fix_hint', '')}",
            "",
            "```diff",
            f.get("fix_code", ""),
            "```",
            "",
        ]

    lines += [
        "---",
        "",
        "## ⚡ AMD MI300X Parallel Analysis",
        "",
        "_Both Qwen2.5-Coder-7B and Qwen2.5-Coder-32B ran simultaneously on "
        "AMD Instinct MI300X 192 GB HBM3 unified memory. Wall time = "
        "`max(fast_latency, deep_latency)` — not their sum._",
        "",
        "---",
        "",
        "*Opened by [Parallax](https://github.com/aryan105825/parallax) · "
        "Dual-model parallel AI code security on AMD Instinct MI300X*",
    ]

    return "\n".join(lines)
