#!/bin/sh
# Install from a release bundle or a source checkout. Uses a native executable.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$root"
prepare_only=false
if [ "${1:-}" = "--prepare-only" ]; then prepare_only=true; shift; fi
version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' herdr-plugin.toml | head -1)
cargo_bin=${CARGO:-cargo}
if ! command -v "$cargo_bin" >/dev/null 2>&1 && [ -x "${HOME}/.cargo/bin/cargo" ]; then
    cargo_bin="${HOME}/.cargo/bin/cargo"
fi
if [ -f Cargo.toml ] && command -v "$cargo_bin" >/dev/null 2>&1; then
    "$cargo_bin" build --release --locked --package herdr-agent-grid --bin herdr-agent-grid
    mkdir -p bin
    cp target/release/herdr-agent-grid bin/.herdr-agent-grid.new
    chmod 755 bin/.herdr-agent-grid.new
    mv bin/.herdr-agent-grid.new bin/herdr-agent-grid
    if "$prepare_only"; then exit 0; fi
    exec ./bin/herdr-agent-grid install "$@"
fi
installed=""
if [ -x bin/herdr-agent-grid ]; then installed=$(bin/herdr-agent-grid --version); fi
if [ "$installed" != "herdr-agent-grid $version" ]; then
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64) target=aarch64-apple-darwin ;;
        Darwin-x86_64) target=x86_64-apple-darwin ;;
        Linux-x86_64) target=x86_64-unknown-linux-gnu ;;
        Linux-aarch64|Linux-arm64) target=aarch64-unknown-linux-gnu ;;
        *) printf '%s\n' 'Unsupported platform; build from source with Rust 1.88+.' >&2; exit 1 ;;
    esac
    asset="herdr-agent-grid-v${version}-${target}.tar.gz"
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT HUP INT TERM
    url="https://github.com/jeffarese/herdr-agent-grid/releases/download/v${version}"
    curl --fail --location --proto '=https' --tlsv1.2 "$url/$asset" -o "$stage/$asset"
    curl --fail --location --proto '=https' --tlsv1.2 "$url/SHA256SUMS" -o "$stage/SHA256SUMS"
    (cd "$stage"
        awk -v file="$asset" '$2 == file { print }' SHA256SUMS > selected.sha256
        [ -s selected.sha256 ]
        if command -v sha256sum >/dev/null 2>&1; then sha256sum -c selected.sha256; else shasum -a 256 -c selected.sha256; fi
        tar -xzf "$asset"
    )
    mkdir -p bin
    cp "$stage/herdr-agent-grid/bin/herdr-agent-grid" bin/.herdr-agent-grid.new
    chmod 755 bin/.herdr-agent-grid.new
    mv bin/.herdr-agent-grid.new bin/herdr-agent-grid
fi
if "$prepare_only"; then exit 0; fi
exec ./bin/herdr-agent-grid install "$@"
