#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR=${PERFETTO_SOURCE_DIR:-"$PWD/perfetto-src"}
REV=936f1383538f2858707a1c35faff6c6809f68503
if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    git -c core.autocrlf=false clone --no-checkout https://github.com/google/perfetto.git "$SOURCE_DIR"
    git -C "$SOURCE_DIR" -c core.autocrlf=false checkout --detach "$REV"
fi
cd "$SOURCE_DIR"
if [[ "$(git rev-parse HEAD)" != "$REV" ]]; then
    echo "Expected Perfetto revision $REV in $SOURCE_DIR" >&2
    exit 1
fi
tools/install-build-deps --android --no-dev-tools
tools/gn gen out/android --args='target_os="android" target_cpu="arm64" is_debug=false'
tools/ninja -C out/android -j 4 trace_processor_shell
git rev-parse HEAD
file out/android/trace_processor_shell
