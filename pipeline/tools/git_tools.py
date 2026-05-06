"""
pipeline/tools/git_tools.py
Parallax — Git operations for the PR Agent.

Responsibilities:
  - Generate unified diffs from fix_code patches (difflib — no subprocess Git needed)
  - Apply patches to in-memory file content before committing via GitHub API
  - Produce a structured diff summary consumed by the PR Agent
"""

import difflib
import os
import textwrap
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def generate_unified_diff(
    original: str,
    patched: str,
    filename: str,
    context_lines: int = 3,
) -> str:
    """
    Return a unified diff string between *original* and *patched* content.

    Args:
        original:      Original file content.
        patched:       Patched file content produced by fix_generator.
        filename:      File path used in the diff header (a/ b/ prefixed).
        context_lines: Number of surrounding context lines (default 3).

    Returns:
        Unified diff as a string, empty string when files are identical.
    """
    orig_lines   = original.splitlines(keepends=True)
    patch_lines  = patched.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        orig_lines,
        patch_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context_lines,
    ))
    return "".join(diff)


def apply_fix_to_content(
    original_content: str,
    fix_code: str,
    file_path: str,
) -> tuple[str, str]:
    """
    Apply *fix_code* to *original_content*.

    Strategy (in priority order):
      1. If fix_code is a complete file replacement (no diff markers), use it directly.
      2. If fix_code is a unified diff (starts with '---'), apply it via difflib.
      3. Fall back to returning the original with a comment prepended.

    Returns:
        (patched_content, diff_string) — diff_string is empty when no change occurred.
    """
    if not fix_code or not fix_code.strip():
        return original_content, ""

    # Case 1 — full file replacement
    if not fix_code.startswith("---"):
        diff = generate_unified_diff(original_content, fix_code, file_path)
        return fix_code, diff

    # Case 2 — unified diff supplied by the model
    patched = _apply_unified_diff(original_content, fix_code)
    if patched is None:
        # Patch failed — return original unchanged
        return original_content, ""

    diff = generate_unified_diff(original_content, patched, file_path)
    return patched, diff


def build_patch_summary(fixed_findings: list[dict]) -> str:
    """
    Build a human-readable patch summary for the PR body.

    Args:
        fixed_findings: List of finding dicts (from fix_generator), each may
                        contain 'fix_code', 'fix_explanation', 'file', 'type'.

    Returns:
        Markdown-formatted summary string.
    """
    if not fixed_findings:
        return "_No automated fixes were generated._"

    lines = []
    for i, finding in enumerate(fixed_findings, start=1):
        fid   = finding.get("id", f"F{i:03d}")
        ftype = finding.get("type", "Unknown")
        ffile = finding.get("file", "unknown")
        fexpl = finding.get("fix_explanation", "")
        fprio = finding.get("priority", "")
        fcvss = finding.get("cvss", "")

        lines.append(f"### [{fid}] {ftype} — `{ffile}`")
        if fprio or fcvss:
            meta = " · ".join(filter(None, [fprio, f"CVSS {fcvss}" if fcvss else ""]))
            lines.append(f"**Severity:** {meta}")
        if fexpl:
            lines.append(f"\n{fexpl.strip()}")
        if finding.get("fix_code"):
            lines.append("")  # blank line before code block
        lines.append("")

    return "\n".join(lines)


def extract_changed_files(fixed_findings: list[dict], file_contents: dict[str, str]) -> dict[str, str]:
    """
    Return a dict of {file_path: patched_content} for every finding that
    carries a fix_code.  Files not present in *file_contents* are skipped.

    Args:
        fixed_findings: Output of fix_generator — each entry may have 'fix_code'.
        file_contents:  Original file map from the Ingestor: {path: content}.

    Returns:
        Dict of files whose content changed after applying all patches.
    """
    # Accumulate patches per file (multiple findings can touch the same file)
    patched: dict[str, str] = {}

    for finding in fixed_findings:
        file_path = finding.get("file", "")
        fix_code  = finding.get("fix_code", "")

        if not fix_code or not file_path:
            continue

        # Start from the latest in-memory version of the file
        current = patched.get(file_path, file_contents.get(file_path, ""))
        if not current:
            continue

        new_content, _ = apply_fix_to_content(current, fix_code, file_path)
        patched[file_path] = new_content

    # Return only files that actually changed
    return {
        path: content
        for path, content in patched.items()
        if content != file_contents.get(path, "")
    }


def summarise_diff_stats(diff: str) -> dict:
    """
    Parse a unified diff string and return basic statistics.

    Returns:
        {"additions": int, "deletions": int, "chunks": int}
    """
    additions = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    chunks    = sum(1 for l in diff.splitlines() if l.startswith("@@"))
    return {"additions": additions, "deletions": deletions, "chunks": chunks}


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _apply_unified_diff(original: str, patch: str) -> Optional[str]:
    """
    Attempt to apply a unified diff string to *original*.
    Returns the patched content on success, None on failure.

    Uses a line-by-line state machine — no subprocess or external dep required.
    """
    orig_lines   = original.splitlines(keepends=True)
    patch_lines  = patch.splitlines(keepends=True)

    result_lines = list(orig_lines)  # we'll build a replacement list
    offset = 0  # running offset due to prior insertions/deletions

    i = 0
    while i < len(patch_lines):
        line = patch_lines[i]

        # Skip file headers
        if line.startswith("---") or line.startswith("+++"):
            i += 1
            continue

        # Hunk header: @@ -old_start,old_count +new_start,new_count @@
        if line.startswith("@@"):
            try:
                parts   = line.split("@@")[1].strip()
                old_part = parts.split()[0]  # e.g. -10,5
                old_start = int(old_part.split(",")[0].lstrip("-")) - 1  # 0-indexed
            except (IndexError, ValueError):
                i += 1
                continue

            # Collect all lines in this hunk
            i += 1
            hunk_orig   = []
            hunk_new    = []
            while i < len(patch_lines) and not patch_lines[i].startswith("@@") and \
                  not patch_lines[i].startswith("---") and not patch_lines[i].startswith("+++"):
                hl = patch_lines[i]
                if hl.startswith("-"):
                    hunk_orig.append(hl[1:])
                elif hl.startswith("+"):
                    hunk_new.append(hl[1:])
                else:
                    # Context line
                    hunk_orig.append(hl[1:] if hl.startswith(" ") else hl)
                    hunk_new.append(hl[1:] if hl.startswith(" ") else hl)
                i += 1

            # Locate the hunk in the result list (with offset)
            target = old_start + offset

            # Validate context — soft check only (don't abort on mismatch)
            result_lines = (
                result_lines[:target]
                + hunk_new
                + result_lines[target + len(hunk_orig):]
            )
            offset += len(hunk_new) - len(hunk_orig)
        else:
            i += 1

    return "".join(result_lines)
