#!/bin/sh
# Install or update asc on Linux from a GitHub Release tarball.
set -eu

REPO="nibarAhmed/agent-speech-check"
APP="agent-speech-check"

die() {
    printf 'error: %s\n' "$1" >&2
    exit 1
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        die "sha256sum or shasum is required"
    fi
}

check_semver() {
    printf '%s\n' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'
}

fetch() {
    if [ -n "${ASC_RELEASE_URL:-}" ]; then
        curl -fsSL -o "$2" "$1"
    else
        curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 -o "$2" "$1"
    fi
}

main() {
    [ "$#" -eq 0 ] || die "unexpected arguments"

    os=$(uname -s)
    [ "$os" = "Linux" ] || die "this installer is for Linux (found: $os)"

    raw_arch=$(uname -m)
    case "$raw_arch" in
        x86_64 | amd64) arch="x86_64" ;;
        aarch64 | arm64) arch="aarch64" ;;
        *) die "unsupported architecture: $raw_arch (supported: x86_64, aarch64)" ;;
    esac

    need_cmd curl
    need_cmd tar
    need_cmd awk
    need_cmd grep
    need_cmd mktemp
    need_cmd tr

    if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
        die "sha256sum or shasum is required"
    fi

    [ -n "${HOME:-}" ] || die "HOME is not set"

    expected_version=""
    if [ -n "${ASC_RELEASE_URL:-}" ]; then
        release_url=${ASC_RELEASE_URL%/}
    else
        redirect=$(curl -fsS --proto '=https' --proto-redir '=https' --tlsv1.2 \
            -o /dev/null -w '%{redirect_url}' \
            "https://github.com/$REPO/releases/latest") \
            || die "could not look up the latest release"
        tag=${redirect##*/}
        ver=${tag#v}
        case "$tag" in
            v*) check_semver "$ver" || die "unexpected release tag from GitHub: $tag" ;;
            *) die "unexpected release tag from GitHub: $tag" ;;
        esac
        expected_version=$ver
        release_url="https://github.com/$REPO/releases/download/$tag"
    fi

    asset="$APP-linux-$arch.tar.gz"
    install_root="$HOME/.local/share/$APP"
    versions_dir="$install_root/versions"
    bin_dir="$HOME/.local/bin"

    tmpdir=""
    staging=""
    backup=""
    target=""
    tmp_link=""
    tmp_bin=""
    cleanup() {
        if [ -n "$backup" ] && [ -n "$target" ] && [ ! -e "$target" ] && [ -e "$backup" ]; then
            mv -- "$backup" "$target" 2>/dev/null || true
        fi
        [ -n "$tmp_link" ] && rm -f -- "$tmp_link"
        [ -n "$tmp_bin" ] && rm -f -- "$tmp_bin"
        [ -n "$staging" ] && rm -rf -- "$staging"
        [ -n "$tmpdir" ] && rm -rf -- "$tmpdir"
        rmdir "$versions_dir" 2>/dev/null || true
        rmdir "$install_root" 2>/dev/null || true
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP

    tmpdir=$(mktemp -d)

    fetch "$release_url/SHA256SUMS" "$tmpdir/SHA256SUMS" \
        || die "failed to download SHA256SUMS from $release_url"
    fetch "$release_url/$asset" "$tmpdir/$asset" \
        || die "failed to download $asset from $release_url"

    expected_sha=$(awk -v file="$asset" 'NF == 2 && ($2 == file || $2 == "*" file) { print $1; exit }' "$tmpdir/SHA256SUMS" \
        | tr 'A-F' 'a-f')
    printf '%s\n' "$expected_sha" | grep -Eq '^[0-9a-f]{64}$' \
        || die "SHA256SUMS has no valid SHA-256 entry for $asset"

    actual_sha=$(sha256_file "$tmpdir/$asset" | tr 'A-F' 'a-f')
    [ "$actual_sha" = "$expected_sha" ] \
        || die "SHA-256 mismatch for $asset (expected $expected_sha, got $actual_sha)"

    mkdir -p "$versions_dir"
    # Stage on the same filesystem so moving into versions/ is an atomic rename.
    staging=$(mktemp -d "$install_root/.installing.XXXXXX")
    tar -xzf "$tmpdir/$asset" -C "$staging"

    version=""
    for entry in "$staging/$APP/versions"/*; do
        [ -d "$entry" ] || continue
        [ -z "$version" ] || die "unexpected archive layout: multiple versions in $asset"
        version=${entry##*/}
    done
    [ -n "$version" ] && check_semver "$version" \
        || die "unexpected archive layout in $asset"
    if [ -n "$expected_version" ] && [ "$version" != "$expected_version" ]; then
        die "archive contains version $version, expected $expected_version"
    fi

    extracted="$staging/$APP/versions/$version"
    [ -f "$extracted/asc" ] && [ -x "$extracted/asc" ] \
        || die "archive is missing an executable asc binary"

    if [ -x "$install_root/current/asc" ]; then
        # Resolve through `current` so `_stop_all` sees versions/V/asc and waits for all copies.
        cur_exe=$(readlink -f "$install_root/current/asc" 2>/dev/null || printf '%s' "$install_root/current/asc")
        "$cur_exe" _stop_all || die "could not stop running asc processes"
    fi

    target="$versions_dir/$version"
    if [ -e "$target" ] || [ -L "$target" ]; then
        backup="$versions_dir/.old-$version.$$"
        rm -rf -- "$backup"
        mv -- "$target" "$backup"
    fi
    mv -- "$extracted" "$target"

    tmp_link="$install_root/.current.$$"
    rm -f -- "$tmp_link"
    ln -s "versions/$version" "$tmp_link"
    mv -Tf -- "$tmp_link" "$install_root/current"
    tmp_link=""

    if [ -n "$backup" ]; then
        rm -rf -- "$backup"
        backup=""
    fi
    for old in "$versions_dir"/*; do
        [ -e "$old" ] || [ -L "$old" ] || continue
        [ "${old##*/}" = "$version" ] && continue
        rm -rf -- "$old"
    done

    mkdir -p "$bin_dir"
    tmp_bin="$bin_dir/.asc.$$"
    rm -f -- "$tmp_bin"
    ln -s "$install_root/current/asc" "$tmp_bin"
    mv -Tf -- "$tmp_bin" "$bin_dir/asc"
    tmp_bin=""

    printf 'Installed asc %s to %s/asc\n' "$version" "$bin_dir"
    case ":${PATH:-}:" in
        *":$bin_dir:"* | *":$bin_dir/:"*) ;;
        *)
            printf 'Note: %s is not on PATH. Add it to your shell profile:\n' "$bin_dir"
            printf '  export PATH="%s:$PATH"\n' "$bin_dir"
            ;;
    esac
}

main "$@"
