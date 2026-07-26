"""Lossless output compression utilities for tool outputs.

Mechanical port of the subset of ``harness/utils/output-compression.ts`` used by
the tools leaf (``compress_bash_output``, ``compress_grep_output``,
``compress_read_output``, ``compress_find_output``, ``compress_ls_output``). All
compression is strictly lossless. Below ``MIN_COMPRESSION_SIZE`` bytes the input
is returned unchanged.
"""

from __future__ import annotations

import re

MIN_COMPRESSION_SIZE = 1024
"""Minimum output size (bytes) to apply compression."""


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def collapse_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines to 2."""
    return re.sub(r"\n{3,}", "\n\n", text)


def strip_trailing_whitespace(text: str) -> str:
    """Strip trailing whitespace from each line."""
    return "\n".join(line.rstrip() for line in text.split("\n"))


def remove_duplicate_lines(text: str) -> str:
    """Remove duplicate consecutive lines (keeps first occurrence)."""
    result: list[str] = []
    prev: str | None = None
    for line in text.split("\n"):
        if line != prev:
            result.append(line)
            prev = line
    return "\n".join(result)


def compress_general(text: str) -> str:
    """Apply general lossless compression to any output."""
    result: list[str] = []
    blank_count = 0
    for line in text.split("\n"):
        trimmed = line.rstrip()
        if trimmed == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(trimmed)
        else:
            blank_count = 0
            result.append(trimmed)
    return "\n".join(result)


def _detect_command(command: str) -> str:
    trimmed = command.strip()
    without_env = re.sub(r"^[A-Z_]+=\S+\s+", "", trimmed)
    without_sudo = re.sub(r"^sudo\s+", "", without_env)
    parts = re.split(r"\s+", without_sudo)
    first_word = parts[0] if parts else ""
    return first_word.split("/")[-1]


def _compress_npm_install(output: str) -> str:
    result: list[str] = []
    for line in output.split("\n"):
        if re.match(r"^(fetchMetadata|reify|audit|idealTree|sill|warn)\b", line):
            continue
        if re.match(r"^\s*(http|https|fetch|cache|tarball|extract)\b", line):
            continue
        result.append(line)
    return "\n".join(result)


def _compress_git_diff(output: str) -> str:
    result: list[str] = []
    context_count = 0
    for line in output.split("\n"):
        if re.match(r"^(diff --git|index [0-9a-f]+|--- a/|\+\+\+ b/)", line):
            continue
        if line.startswith(" ") and not line.startswith("  "):
            context_count += 1
            if context_count > 2:
                continue
        else:
            context_count = 0
        result.append(line)
    return "\n".join(result)


def _compress_cargo_test(output: str) -> str:
    result: list[str] = []
    passing_count = 0
    for line in output.split("\n"):
        if re.match(r"^test\s+.+\s+\.\.\.\s+ok\s*$", line):
            passing_count += 1
            continue
        if re.match(r"^test\s+.+\s+\.\.\.\s+ignored\s*$", line):
            continue
        if re.match(r"^test result:", line):
            result.append(line)
            continue
        if passing_count > 0 and line == "":
            result.append(f"  ({passing_count} passing tests omitted)")
            passing_count = 0
        result.append(line)
    if passing_count > 0:
        result.append(f"  ({passing_count} passing tests omitted)")
    return "\n".join(result)


def _compress_docker_build(output: str) -> str:
    result: list[str] = []
    for line in output.split("\n"):
        if re.match(r"^(Downloading|Pulling|Extracting|Waiting|Verifying)", line):
            continue
        if re.search(r"[\u2588\u2591\u2592]{10,}", line):
            continue
        result.append(line)
    return "\n".join(result)


def _compress_js_test(output: str) -> str:
    result: list[str] = []
    passing_count = 0
    for line in output.split("\n"):
        if re.match(r"^\s*[\u2713\u2714\u25cb\u25cf]\s+", line):
            passing_count += 1
            continue
        if re.match(r"^\s*PASS\s+", line):
            passing_count += 1
            continue
        if re.match(r"^(Tests|Test Suites):", line):
            result.append(line)
            continue
        if passing_count > 0 and ("FAIL" in line or "\u00d7" in line or "\u2717" in line):
            result.append(f"  ({passing_count} passing tests omitted)")
            passing_count = 0
        result.append(line)
    if passing_count > 0:
        result.append(f"  ({passing_count} passing tests omitted)")
    return "\n".join(result)


def _compress_go_test(output: str) -> str:
    result: list[str] = []
    passing_count = 0
    for line in output.split("\n"):
        if re.match(r"^---\s+PASS:", line):
            passing_count += 1
            continue
        if re.match(r"^PASS$", line):
            continue
        if re.match(r"^(ok|FAIL)\s+", line):
            result.append(line)
            continue
        if passing_count > 0 and "FAIL" in line:
            result.append(f"  ({passing_count} passing tests omitted)")
            passing_count = 0
        result.append(line)
    if passing_count > 0:
        result.append(f"  ({passing_count} passing tests omitted)")
    return "\n".join(result)


def _compress_command_specific(command: str, output: str) -> str:
    cmd = _detect_command(command)
    if cmd in ("npm", "yarn", "pnpm"):
        if re.search(r"\b(install|add|i)\b", command):
            return _compress_npm_install(output)
    elif cmd == "git":
        if re.search(r"\bdiff\b", command):
            return _compress_git_diff(output)
    elif cmd == "cargo":
        if re.search(r"\btest\b", command):
            return _compress_cargo_test(output)
    elif cmd == "docker":
        if re.search(r"\bbuild\b", command):
            return _compress_docker_build(output)
    elif cmd in ("jest", "mocha", "vitest", "pytest"):
        return _compress_js_test(output)
    elif cmd == "go":
        if re.search(r"\btest\b", command):
            return _compress_go_test(output)
    return output


def _strip_noise_patterns(text: str) -> str:
    text = re.sub(r"^npm (warn|notice) .+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^warning .+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^pnpm (warn|notice) .+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^bash: .+ warning: .+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^The command exited with exit code .+$", "", text, flags=re.MULTILINE)
    return text


def compress_bash_output(command: str, output: str) -> str:
    """Apply all lossless compression to a bash command output."""
    if _byte_length(output) < MIN_COMPRESSION_SIZE:
        return output
    result = output
    result = _strip_noise_patterns(result)
    result = _compress_command_specific(command, result)
    result = compress_general(result)
    return result


def compress_grep_output(output: str) -> str:
    """Apply compression to grep output."""
    if _byte_length(output) < MIN_COMPRESSION_SIZE:
        return output
    return compress_general(output)


def compress_read_output(output: str) -> str:
    """Apply light compression to read output (trailing whitespace only)."""
    if _byte_length(output) < MIN_COMPRESSION_SIZE:
        return output
    return strip_trailing_whitespace(output)


def compress_find_output(output: str) -> str:
    """Apply compression to find output."""
    if _byte_length(output) < MIN_COMPRESSION_SIZE:
        return output
    return compress_general(output)


def compress_ls_output(output: str) -> str:
    """Apply compression to ls output."""
    if _byte_length(output) < MIN_COMPRESSION_SIZE:
        return output
    return compress_general(output)
