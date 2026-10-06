#!/usr/bin/env python3
"""Require an exact reviewed feature commit, tree, two-file diff, and test blob."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ALLOWED = {"cli/cmd/api/api.go": "M", "cli/cmd/api/paginate_text_test.go": "A"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_binding(path):
    binding = json.loads(Path(path).read_text())
    for name in ("base_sha", "feature_sha", "feature_tree"):
        require(re.fullmatch(r"[0-9a-f]{40}", binding[name]) is not None,
                f"{name} must be the exact reviewed 40-character SHA; replace the pending placeholder before running")
    require(binding["feature_sha"] != binding["base_sha"], "feature must differ from baseline")
    require(set(binding["files"]) == set(ALLOWED), "binding must contain exactly the two allowed paths")
    for value in [binding["diff_sha256"], binding["test_sha256"], binding["go_archive_sha256"], *binding["files"].values()]:
        require(re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid SHA256 in binding")
    require(binding["test_sha256"] == binding["files"]["cli/cmd/api/paginate_text_test.go"], "test hash mismatch")
    require(binding["go_version"] == "1.26.8", "unexpected Go version")
    return binding


def verify(source, binding):
    def git(*args):
        return subprocess.check_output(["git", "--no-replace-objects", "-C", str(source), *args])

    base, feature = binding["base_sha"], binding["feature_sha"]
    require(git("rev-parse", "HEAD").decode().strip() == feature, "checkout HEAD differs from bound feature SHA")
    require(git("rev-parse", f"{feature}^{{tree}}").decode().strip() == binding["feature_tree"], "feature tree changed")
    parents = git("rev-list", "--parents", "-n", "1", feature).decode().split()
    require(parents == [feature, base], "feature must be one commit directly on the pinned baseline")
    require(git("status", "--porcelain=v1", "--untracked-files=all") == b"", "source checkout must be clean")
    statuses = git("diff", "--name-status", "--no-renames", base, feature).decode().splitlines()
    require(statuses == [f"{ALLOWED[p]}\t{p}" for p in sorted(ALLOWED)], "diff escaped the exact two-path allowlist")
    diff = git("diff", "--no-ext-diff", "--no-renames", "--binary", "--full-index", base, feature, "--", *sorted(ALLOWED))
    require(hashlib.sha256(diff).hexdigest() == binding["diff_sha256"], "full-index patch checksum changed")
    for path, expected in binding["files"].items():
        require(hashlib.sha256((source / path).read_bytes()).hexdigest() == expected, f"blob changed: {path}")
    print(f"Verified feature {feature}, tree {binding['feature_tree']}, exact two-path diff, and regression blob.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding")
    parser.add_argument("source", nargs="?")
    parser.add_argument("--print-feature", action="store_true")
    args = parser.parse_args()
    try:
        binding = load_binding(args.binding)
        if args.print_feature:
            print(binding["feature_sha"])
        else:
            require(args.source is not None, "source checkout is required")
            verify(Path(args.source).resolve(), binding)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Source binding rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
