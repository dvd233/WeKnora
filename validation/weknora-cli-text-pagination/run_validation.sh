#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(realpath "$1")
WORK=$(realpath -m "$2")
HERE=$(cd "$(dirname "$0")" && pwd)
BINDING="$HERE/binding.json"
python3 "$HERE/verify_source.py" "$BINDING" "$SOURCE"
[ ! -e "$WORK" ] || { echo 'Refusing to reuse a validation work directory' >&2; exit 1; }
mkdir -p "$WORK"/{tools,downloads,logs,gopath,gocache,tmp,home,config,deps-home,deps-config,deps/cli,deps/client,baseline,bin}
BASE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["base_sha"])' "$BINDING")
GO_VERSION=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["go_version"])' "$BINDING")
GO_SHA=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["go_archive_sha256"])' "$BINDING")
TEST_SHA=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["test_sha256"])' "$BINDING")
ARCHIVE="$WORK/downloads/go${GO_VERSION}.linux-amd64.tar.gz"
curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
  "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o "$ARCHIVE"
printf '%s  %s\n' "$GO_SHA" "$ARCHIVE" | sha256sum --check -
tar -xzf "$ARCHIVE" -C "$WORK/tools"
GO="$WORK/tools/go/bin/go"
[ "$(env -i HOME="$WORK/home" PATH="$WORK/tools/go/bin:/usr/bin:/bin" GOENV=off GOTOOLCHAIN=local "$GO" version)" = 'go version go1.26.8 linux/amd64' ]

# Acquire modules separately from source execution, using only the bound manifests.
cp "$SOURCE/cli/go.mod" "$SOURCE/cli/go.sum" "$WORK/deps/cli/"
cp "$SOURCE/client/go.mod" "$SOURCE/client/go.sum" "$WORK/deps/client/"
(
  cd "$WORK/deps/cli"
  env -i HOME="$WORK/deps-home" XDG_CONFIG_HOME="$WORK/deps-config" \
    PATH="$WORK/tools/go/bin:/usr/bin:/bin" GOPATH="$WORK/gopath" GOCACHE="$WORK/gocache" \
    GOENV=off GOTOOLCHAIN=local GOWORK=off GOFLAGS='-mod=readonly -buildvcs=false' \
    GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    LANG=C.UTF-8 "$GO" mod download
)

clean() {
  env -i HOME="$WORK/home" XDG_CONFIG_HOME="$WORK/config" TMPDIR="$WORK/tmp" \
    PATH="$WORK/tools/go/bin:/usr/bin:/bin" GOPATH="$WORK/gopath" GOCACHE="$WORK/gocache" \
    GOENV=off GOTOOLCHAIN=local GOWORK=off GOFLAGS='-mod=readonly -buildvcs=false' \
    GOPROXY=off GOSUMDB=off GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    LANG=C.UTF-8 TERM=dumb NO_COLOR=1 "$@"
}

# Only the identical bound regression file is added to the baseline archive.
git -C "$SOURCE" archive "$BASE" cli client | tar -x -C "$WORK/baseline"
cp "$SOURCE/cli/cmd/api/paginate_text_test.go" "$WORK/baseline/cli/cmd/api/"
printf '%s  %s\n' "$TEST_SHA" "$WORK/baseline/cli/cmd/api/paginate_text_test.go" | sha256sum --check -
cmp "$SOURCE/cli/cmd/api/paginate_text_test.go" "$WORK/baseline/cli/cmd/api/paginate_text_test.go"
SELECT='TestAPI_Paginate(Formats|TextWriteError)$'
(
  cd "$WORK/baseline/cli"
  set +e
  clean "$GO" test ./cmd/api -run "$SELECT" -count=1 -json \
    > "$WORK/logs/baseline.jsonl" 2> "$WORK/logs/baseline.stderr"
  status=$?
  set -e
  cat "$WORK/logs/baseline.jsonl"
  [ ! -s "$WORK/logs/baseline.stderr" ] || { cat "$WORK/logs/baseline.stderr" >&2; exit 1; }
  clean python3 "$HERE/verify_baseline.py" "$WORK/logs/baseline.jsonl" "$status" --mode baseline
)
(
  cd "$SOURCE/cli"
  clean "$GO" mod verify
  clean "$GO" test ./cmd/api -run "$SELECT" -count=1 -json \
    > "$WORK/logs/fixed.jsonl" 2> "$WORK/logs/fixed.stderr"
  cat "$WORK/logs/fixed.jsonl"
  [ ! -s "$WORK/logs/fixed.stderr" ] || { cat "$WORK/logs/fixed.stderr" >&2; exit 1; }
  clean python3 "$HERE/verify_baseline.py" "$WORK/logs/fixed.jsonl" 0 --mode fixed
  clean "$GO" test -count=1 ./... | tee "$WORK/logs/tests.log"
  clean "$GO" test -race -count=1 ./... | tee "$WORK/logs/race.log"
  clean "$GO" vet ./... | tee "$WORK/logs/vet.log"
  clean "$GO" build ./... | tee "$WORK/logs/build.log"
  clean "$GO" build -o "$WORK/bin/weknora" .
  clean scripts/check-skill-wire-vocab.sh
  clean scripts/check-secret-tokens.sh
  [ -z "$(clean gofmt -l cmd/api/api.go cmd/api/paginate_text_test.go)" ]
  clean git diff --check "$BASE" HEAD
)
# A passing job must finish on exactly the same source checkout.
python3 "$HERE/verify_source.py" "$BINDING" "$SOURCE"
echo 'Source-bound baseline and fixed validation passed.'
