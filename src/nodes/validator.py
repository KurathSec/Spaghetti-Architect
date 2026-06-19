"""Cross-language compile/run validation (blueprint §15).

Delivers the "all five languages don't crash" promise. A reference **oracle**
computes the expected ``result_var`` values from clean logic; each language is then
actually run and its JSON stdout compared against the oracle. Missing toolchains
SKIP (neither blocking nor pretending).

Note on KEY_VALUE_LOOKUP: the oracle resolves against the operation's own
``pairs`` (the exact mapping every generator enumerates in its cascade), so the
equivalency check validates *generation fidelity* rather than IR-authoring
consistency between ``pairs`` and the ``map_name`` fixture.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional

_TIMEOUT = 30  # seconds, per compile or run step


@dataclass
class ValidationResult:
    language: str
    status: str          # "PASS" | "FAIL" | "SKIP"
    detail: str


# --------------------------------------------------------------------------- #
# 1) oracle: clean implementation computing expected results
# --------------------------------------------------------------------------- #
def oracle(program) -> dict:
    env = dict(program.inputs)
    out: Dict[str, object] = {}
    for op in program.operations:
        if op.op == "MEMBERSHIP_CHECK":
            out[op.result_var] = env[op.target_var] in env[op.collection_name]
        else:  # KEY_VALUE_LOOKUP — mirror the generated cascade (uses pairs)
            out[op.result_var] = op.pairs.get(env[op.key_var], op.default_value)
        env[op.result_var] = out[op.result_var]
    return out


# --------------------------------------------------------------------------- #
# 2) per-language toolchains
# --------------------------------------------------------------------------- #
TOOLCHAINS = {
    "python": None,                 # built-in exec
    "javascript": ["node"],
    "go": ["go"],
    "java": ["javac", "java"],
    "cpp": ["g++"],
}


def validate(language: str, source: str, program) -> ValidationResult:
    expected = oracle(program)
    if language == "python":
        return _validate_python(source, expected)

    tools = TOOLCHAINS.get(language)
    if tools is None:
        return ValidationResult(language, "SKIP", "no runner configured")
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        return ValidationResult(language, "SKIP", f"missing toolchain: {', '.join(missing)}")

    try:
        actual = _compile_and_run(language, source, program)
    except Exception as ex:  # noqa: BLE001 - surface any compile/run failure as FAIL
        return ValidationResult(language, "FAIL", f"compile/run failed: {ex}")

    ok = _equal(actual, expected)
    return ValidationResult(
        language,
        "PASS" if ok else "FAIL",
        "results match" if ok else f"expected {expected} got {actual}",
    )


def _validate_python(source: str, expected: dict) -> ValidationResult:
    ns: Dict[str, object] = {}
    try:
        exec(compile(source, "<spaghetti>", "exec"), ns, ns)   # controlled namespace
    except Exception as ex:  # noqa: BLE001
        return ValidationResult("python", "FAIL", f"exec raised: {ex}")
    actual = {k: ns.get(k) for k in expected}
    ok = _equal(actual, expected)
    return ValidationResult(
        "python",
        "PASS" if ok else "FAIL",
        "results match" if ok else f"expected {expected} got {actual}",
    )


def _equal(actual: dict, expected: dict) -> bool:
    """Type-aware comparison (JSON ``true`` != ``1``) over the same key set."""
    if set(actual) != set(expected):
        return False
    return all(
        json.dumps(actual[k], sort_keys=True) == json.dumps(expected[k], sort_keys=True)
        for k in expected
    )


# --------------------------------------------------------------------------- #
# 3) compile & run the statically/dynamically typed targets
# --------------------------------------------------------------------------- #
def _compile_and_run(language: str, source: str, program) -> dict:
    if language == "javascript":
        return _run_javascript(source)
    if language == "go":
        return _run_go(source)
    if language == "java":
        return _run_java(source, program)
    if language == "cpp":
        return _run_cpp(source)
    raise ValueError(f"no runner for language: {language}")


def _run_javascript(source: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "main.js")
        _write(path, source)
        out = _run(["node", path], cwd=d)
        return _parse_last_json(out)


def _run_go(source: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        _write(os.path.join(d, "main.go"), source)
        # A throwaway module so `go run .` works without network or a real module path
        # (the program only uses the standard library).
        _write(os.path.join(d, "go.mod"), "module spaghetti\n\ngo 1.16\n")
        # Persist the build cache across runs (stable, but isolated from the user's
        # real ~/.cache/go-build). GOCACHE is purely a compilation cache, so reusing
        # it does not affect output — it just avoids recompiling the stdlib cold every
        # time (measured ~22x faster on a warm cache).
        cache = os.path.join(tempfile.gettempdir(), "spaghetti_gocache")
        env = dict(os.environ, GOFLAGS="", GOCACHE=cache)
        out = _run(["go", "run", "."], cwd=d, env=env)
        return _parse_last_json(out)


def _run_java(source: str, program) -> dict:
    cls = program.module_name[:1].upper() + program.module_name[1:]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, f"{cls}.java")
        _write(path, source)
        _run(["javac", path], cwd=d)
        out = _run([_java_runtime(), "-cp", d, cls], cwd=d)
        return _parse_last_json(out)


def _java_runtime() -> str:
    """The ``java`` co-located with ``javac``, so the runtime JDK matches the
    compiler JDK. Avoids ``UnsupportedClassVersionError`` when several JDKs are
    installed and PATH/``alternatives`` point ``java`` and ``javac`` at different
    ones (e.g. javac=Zulu 26 but /usr/bin/java=OpenJDK 25)."""
    javac = shutil.which("javac")
    if javac:
        cand = os.path.join(os.path.dirname(os.path.realpath(javac)), "java")
        if os.path.exists(cand):
            return cand
    return "java"


def _run_cpp(source: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "main.cpp")
        binp = os.path.join(d, "main_bin")
        _write(src, source)
        _run(["g++", "-O0", "-std=c++17", "-o", binp, src], cwd=d)
        out = _run([binp], cwd=d)
        return _parse_last_json(out)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _run(cmd: List[str], cwd: str, env: Optional[dict] = None) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} exited {proc.returncode}\n"
            f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}"
        )
    return proc.stdout


def _parse_last_json(stdout: str) -> dict:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("no stdout produced")
    return json.loads(lines[-1])
