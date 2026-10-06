#!/usr/bin/env python3
"""Fail-closed verifier for frozen pagination baseline-red and fixed-green runs.

Usage: python3 verify_baseline.py LOG ACTUAL_GO_EXIT_CODE [--mode baseline|fixed]

Exit 0 means evidence matches the selected mode; exit 1 rejects it. Baseline
requires actual Go exit 1 and exactly 10 expected failures plus 16 passes. Fixed
requires actual Go exit 0 and all 26 cases passing. This does not execute Go or
establish log provenance. The caller must capture the real command exit status
without masking pipeline failures, and reject any separately captured stderr.

The allowlist is deliberately bound to paginate_text_test.go SHA256
123afbf5a950501f47fec07473bfcd8142542055cd404b87e83fbf5adf33431c.
Only absolute POSIX checkout prefixes and elapsed durations vary in expected
test output. Assertion lines, whitespace, names and complete error text do not.
JSON timestamps and finite, nonnegative elapsed numbers may vary. Output event
chunk boundaries may vary; no output bytes are discarded. Unknown fields,
actions, tests, packages, and any extra assertion or diagnostic fail closed.
"""

import argparse
import datetime
import json
import math
import re
from pathlib import Path


PACKAGE = "github.com/Tencent/WeKnora/cli/cmd/api"
FORMAT_TEST = "TestAPI_PaginateFormats"
WRITER_TEST = "TestAPI_PaginateTextWriteError"
GUARD_ERROR = (
    "FormatOptions.Emit: cannot emit text mode as JSON; "
    "caller must render human-readable separately"
)
FORMAT_CASES = (
    ("text_single_page", "fail"),
    ("text_multiple_capped_pages", "fail"),
    ("text_empty", "fail"),
    ("text_environment", "fail"),
    ("text_flag_overrides_json_environment", "fail"),
    ("json_flag_overrides_text_environment", "pass"),
    ("default_pipe", "pass"),
    ("default_tty", "pass"),
    ("json", "pass"),
    ("ndjson", "pass"),
    ("json_jq", "pass"),
    ("ndjson_jq", "pass"),
    ("text_environment_jq_rejected", "fail"),
    ("text_environment_malformed_jq_rejected", "fail"),
    ("text_jq_rejected", "pass"),
    ("text_precise_values", "fail"),
    ("json_precise_values", "pass"),
    ("ndjson_precise_values", "pass"),
    ("text_no_metadata", "pass"),
    ("text_non_json_fallback", "pass"),
    ("text_fallback_existing_newline", "pass"),
    ("text_fallback_empty_body", "pass"),
    ("text_non_GET", "pass"),
    ("text_non_paginated", "pass"),
)
TREE = (
    (FORMAT_TEST, FORMAT_CASES),
    (WRITER_TEST, (("false", "fail"), ("true", "fail"))),
)
LEAVES = {
    parent + "/" + case: status
    for parent, cases in TREE
    for case, status in cases
}
STATUSES = {FORMAT_TEST: "fail", WRITER_TEST: "fail", **LEAVES}
TIME_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
# Match one whole trace line only. Never strip arbitrary lines, extra frames,
# error substrings, or assertion line numbers. Keep the exact source suffix.
TRACE_PATTERN = re.compile(
    r"(?m)^(        \tError Trace:\t)"
    r"(?:/[^/\x00-\x1f:]+)*/cli/cmd/api/paginate_text_test\.go:"
    r"(159|160|161|165|225)\n"
)


class VerificationError(ValueError):
    """The log is not the exact expected baseline failure."""


def _reject(message):
    raise VerificationError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate JSON key: " + key)
        result[key] = value
    return result


def _nonfinite(value):
    _reject("non-finite JSON number: " + value)


def _expected_controls(mode):
    controls = [("start", None)]
    for parent, cases in TREE:
        controls.append(("run", parent))
        for case, status in cases:
            name = parent + "/" + case
            status = status if mode == "baseline" else "pass"
            controls.extend((("run", name), (status, name)))
        controls.append(("fail" if mode == "baseline" else "pass", parent))
    controls.append(("fail" if mode == "baseline" else "pass", None))
    return controls


def _expected_output(name, status):
    expected = "=== RUN   " + name + "\n"
    if name in LEAVES and status == "fail":
        def assertion(line, body):
            return (
                f"    paginate_text_test.go:{line}: \n"
                f"        \tError Trace:\t<SOURCE>/cli/cmd/api/paginate_text_test.go:{line}\n"
                + body
                + "        \tTest:       \t" + name + "\n"
            )

        if name in (
            FORMAT_TEST + "/text_environment_jq_rejected",
            FORMAT_TEST + "/text_environment_malformed_jq_rejected",
        ):
            expected += assertion(159,
                "        \tError:      \tNot equal: \n"
                "        \t            \texpected: 2\n"
                "        \t            \tactual  : 1\n"
            )
            expected += assertion(160,
                f'        \tError:      \t"{GUARD_ERROR}" does not contain '
                '"--jq requires --format json|ndjson"\n'
            )
            expected += assertion(161,
                "        \tError:      \tShould be zero, but was 3\n"
            )
        elif name.startswith(FORMAT_TEST + "/"):
            expected += assertion(165,
                "        \tError:      \tReceived unexpected error:\n"
                f"        \t            \t{GUARD_ERROR}\n"
            )
        else:
            expected += assertion(225,
                "        \tError:      \tTarget error should be in err chain:\n"
                '        \t            \texpected: "synthetic output failure"\n'
                f'        \t            \tin chain: "{GUARD_ERROR}"\n'
            )
    expected += "--- " + status.upper() + ": " + name + " (<SECONDS>s)\n"
    return expected


def _check_output(name, status, output):
    # Both substitutions are anchored to a complete, explicitly expected line.
    expected = _expected_output(name, status)
    normalized, trace_count = TRACE_PATTERN.subn(
        r"\1<SOURCE>/cli/cmd/api/paginate_text_test.go:\2\n", output
    )
    if trace_count != expected.count("        \tError Trace:\t"):
        _reject(f"wrong assertion trace count or source location for {name}")
    terminal = "--- " + status.upper() + ": " + name
    normalized, terminal_count = re.subn(
        r"(?m)^" + re.escape(terminal) + r" \([0-9]+\.[0-9]{2}s\)\n",
        terminal + " (<SECONDS>s)\n",
        normalized,
    )
    if terminal_count != 1:
        _reject(f"missing or duplicate exact output terminal for {name}")
    if normalized != expected:
        # Do not echo arbitrary injected diagnostic text into a CI report.
        mismatch = next(
            (i for i, (a, b) in enumerate(zip(normalized, expected)) if a != b),
            min(len(normalized), len(expected)),
        )
        _reject(f"exact output mismatch for {name} at normalized byte {mismatch}")


def verify_text(contents, actual_exit, mode="baseline"):
    """Validate an entire JSON-lines log; raise VerificationError on any drift."""
    if mode not in ("baseline", "fixed"):
        _reject(f"invalid mode: {mode!r}")
    expected_exit = 1 if mode == "baseline" else 0
    statuses = {name: result if mode == "baseline" else "pass" for name, result in STATUSES.items()}
    if type(actual_exit) is not int or actual_exit != expected_exit:
        _reject(f"actual Go exit must be {expected_exit}, got {actual_exit!r}")
    if not isinstance(contents, str) or not contents or not contents.endswith("\n"):
        _reject("empty log or missing final newline (possibly truncated)")
    controls = _expected_controls(mode)
    control_index = 0
    stack = []
    output = {name: "" for name in STATUSES}
    package_output = ""
    event_count = 0
    previous_time = None

    for line_number, raw in enumerate(contents.splitlines(), 1):
        if not raw:
            _reject(f"blank JSON line {line_number}")
        try:
            event = json.loads(raw, object_pairs_hook=_object, parse_constant=_nonfinite)
        except (ValueError, RecursionError) as exc:
            _reject(f"invalid JSON at line {line_number}: {exc}")
        if not isinstance(event, dict):
            _reject(f"JSON line {line_number} is not an event object")
        if control_index == len(controls):
            _reject("events after the package terminal result")
        if event.get("Package") != PACKAGE:
            _reject(f"wrong or missing package at line {line_number}")
        action = event.get("Action")
        if action not in ("start", "run", "output", "pass", "fail"):
            _reject(f"unsupported action at line {line_number}: {action!r}")
        name = event.get("Test")
        if "Test" in event and (not isinstance(name, str) or name not in STATUSES):
            _reject(f"unknown test at line {line_number}: {name!r}")
        keys = {"Time", "Action", "Package"}
        if name is not None:
            keys.add("Test")
        if action == "output":
            keys.add("Output")
        if action in ("pass", "fail"):
            keys.add("Elapsed")
        if set(event) != keys:
            _reject(f"unexpected or missing event fields at line {line_number}")
        timestamp = event["Time"]
        if not isinstance(timestamp, str) or not TIME_PATTERN.fullmatch(timestamp):
            _reject(f"invalid event timestamp at line {line_number}")
        try:
            instant = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            _reject(f"invalid event timestamp at line {line_number}")
        if previous_time is not None and instant < previous_time:
            _reject(f"non-monotonic event timestamp at line {line_number}")
        previous_time = instant
        if action in ("pass", "fail"):
            elapsed = event["Elapsed"]
            if type(elapsed) not in (int, float) or elapsed < 0 or elapsed > 1e12 or not math.isfinite(elapsed):
                _reject(f"invalid elapsed time at line {line_number}")
        event_count += 1
        if action == "output":
            text = event["Output"]
            if not isinstance(text, str) or not text:
                _reject(f"invalid output payload at line {line_number}")
            if name is None:
                if stack or control_index != len(controls) - 1:
                    _reject(f"package output outside final summary at line {line_number}")
                package_output += text
            else:
                if not stack or stack[-1] != name:
                    _reject(f"test output outside its active run at line {line_number}")
                output[name] += text
            continue
        expected = controls[control_index]
        if (action, name) != expected:
            _reject(
                f"control mismatch at line {line_number}: "
                f"expected {expected!r}, got {(action, name)!r}"
            )
        control_index += 1
        if action == "run":
            stack.append(name)
        elif name is not None:
            if not stack or stack.pop() != name:
                _reject(f"unbalanced test termination at line {line_number}")
            _check_output(name, statuses[name], output[name])

    if control_index != len(controls) or stack:
        _reject("incomplete run/terminal sequence (possibly truncated)")
    summary_prefix = r"FAIL\nFAIL\t" if mode == "baseline" else r"PASS\nok  \t"
    if not re.fullmatch(
        summary_prefix + re.escape(PACKAGE) + r"\t[0-9]+\.[0-9]{3}s\n",
        package_output,
    ):
        _reject("package output is not exactly the expected summary")
    return {
        "accepted": True,
        "actual_exit": actual_exit,
        "mode": mode,
        "package": PACKAGE,
        "package_result": "fail" if mode == "baseline" else "pass",
        "top_level_failed": [parent for parent, _ in TREE if statuses[parent] == "fail"],
        "top_level_passed": [parent for parent, _ in TREE if statuses[parent] == "pass"],
        "failed_leaves": [name for name in LEAVES if statuses[name] == "fail"],
        "passed_leaves": [name for name in LEAVES if statuses[name] == "pass"],
        "failed_leaf_count": 10 if mode == "baseline" else 0,
        "passed_leaf_count": 16 if mode == "baseline" else 26,
        "event_count": event_count,
    }


def verify_file(path, actual_exit, mode="baseline"):
    return verify_text(Path(path).read_text(encoding="utf-8"), actual_exit, mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="complete Go -json log; separately captured stderr must be empty")
    parser.add_argument("actual_exit", type=int, help="actual exit code captured from go test")
    parser.add_argument("--mode", choices=("baseline", "fixed"), default="baseline")
    args = parser.parse_args()
    try:
        result = verify_file(args.log, args.actual_exit, args.mode)
    except (VerificationError, OSError, UnicodeError) as exc:
        print(json.dumps({"accepted": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
