#!/usr/bin/env python3
"""Accept only the known regression assertions on the unchanged baseline."""
import collections
import json
import pathlib
import re
import sys

PACKAGE = "github.com/Tencent/WeKnora/cli/cmd/api"
HTTP = "TestAPI_PaginateTruncatedHTTPBody"
READ = "TestAPI_PaginateReadError"
NIL = 'Expected error with "response body read failed" in chain but got nil.'
TEXT = "FormatOptions.Emit: cannot emit text mode as JSON; caller must render human-readable separately"
SHAPE = "input.invalid_argument: --paginate: page 2 response not in expected shape: unexpected end of JSON input"
EXTRA = "network.error: GET /api/v1/items?page=3&page_size=50: requested another page after a failed read"
STATUS = r'server.error: HTTP 502: {\"error\":\"unavailable\"}'
EXPECTED = {}
for mode in ("json", "text"):
    EXPECTED[f"{HTTP}/{mode}"] = ('Expected error with "unexpected EOF" in chain but got nil.', "stdout:")
    for case, reasons in {
        "first_complete_page": (NIL if mode == "json" else TEXT,),
        "first_non-paginated_response": (NIL,),
        "later_complete_page": (NIL if mode == "json" else TEXT,),
        "later_truncated_page": (SHAPE,),
        "later_page_with_more_results": (EXTRA, "requests: 3"),
        "error_status": (STATUS,),
    }.items():
        EXPECTED[f"{READ}/{case}/{mode}"] = reasons


def normalize(output):
    output = re.sub(
        r"(?m)(Error Trace:\t)[^\n]*?/cli/cmd/api/api_test.go:(\d+)",
        r"\1<SOURCE>/cli/cmd/api/api_test.go:\2", output,
    )
    output = re.sub(r"(?m)^(--- FAIL: [^ \n]+) \(\d+(?:\.\d+)?s\)$", r"\1 (<elapsed>s)", output)
    return re.sub(r"(?m)^(FAIL\t[^\t\n]+\t)\d+(?:\.\d+)?s$", r"\1<elapsed>s", output)


def verify(path, exit_code):
    if exit_code != 1:
        raise ValueError(f"expected go test exit 1, got {exit_code}")
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not events:
        raise ValueError("no test events")
    if any(e.get("Package") != PACKAGE for e in events):
        raise ValueError("unexpected package or build event")
    all_output = "".join(e.get("Output", "") for e in events)
    if "panic:" in all_output or "DATA RACE" in all_output:
        raise ValueError("unrelated panic or race failure")
    expected_tests = set(EXPECTED) | {HTTP, READ}
    expected_counts = collections.Counter({name: 1 for name in expected_tests})
    if any(e.get("Action") not in {"start", "run", "output", "fail"} for e in events):
        raise ValueError("unknown action, skipped/passing test, or build failure")
    if any(("Output" in e) != (e.get("Action") == "output") for e in events):
        raise ValueError("malformed output event")
    if any("Test" in e and e["Test"] not in expected_tests for e in events):
        raise ValueError("unknown test or test-associated output")
    runs = collections.Counter(e["Test"] for e in events if e.get("Action") == "run" and "Test" in e)
    failures = collections.Counter(e["Test"] for e in events if e.get("Action") == "fail" and "Test" in e)
    if runs != expected_counts or failures != expected_counts:
        raise ValueError("each exact intended test must run and fail once")
    package_actions = [e["Action"] for e in events if "Test" not in e and e.get("Action") != "output"]
    if package_actions != ["start", "fail"]:
        raise ValueError("expected exactly one package start and one package failure")
    if any(e.get("Action") == "start" and "Test" in e for e in events):
        raise ValueError("unexpected test start event")
    expected_output = json.loads(pathlib.Path(__file__).with_name("baseline-diagnostics.json").read_text())
    if set(expected_output) != expected_tests | {""}:
        raise ValueError("diagnostic fixture does not cover the exact intended test set")
    for name, wanted in expected_output.items():
        output = "".join(e.get("Output", "") for e in events if e.get("Test", "") == name)
        if normalize(output) != wanted:
            raise ValueError(f"{name or PACKAGE}: unexpected or extra diagnostic output\n{output}")
    for name, reasons in sorted(EXPECTED.items()):
        output = "".join(e.get("Output", "") for e in events if e.get("Test") == name)
        if any(reason not in output for reason in reasons):
            raise ValueError(f"{name}: expected assertion reasons missing: {reasons!r}\n{output}")
        if "panic:" in output or "DATA RACE" in output:
            raise ValueError(f"{name}: unrelated runtime failure")
        print(f"EXPECTED FAIL {name}: {'; '.join(reasons)}")
    print(f"Verified all {len(EXPECTED)} intended failing subcases; no unrelated failure accepted.")


if __name__ == "__main__":
    try:
        verify(pathlib.Path(sys.argv[1]), int(sys.argv[2]))
    except (IndexError, ValueError, OSError) as error:
        raise SystemExit(f"Baseline validation rejected: {error}")
