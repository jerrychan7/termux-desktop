#!/data/data/com.termux/files/usr/bin/bash
# Install desk in Termux.
#
# From a checkout, this runs the checkout's own entry file and needs no
# network access:
#
#     ./install.sh
#
# Piped into bash, this downloads the latest release archive first:
#
#     curl -fsSL https://raw.githubusercontent.com/jerrychan7/termux-desktop/main/install.sh | bash
#
# Set DESK_VERSION to install one release instead of the latest one:
#
#     DESK_VERSION=0.0.1 ./install.sh
#
# Arguments after the script name are passed to desk install, for example
# --force.
#
# This script only finds the sources: it downloads and extracts the archive,
# then runs desk install --source. desk install does the copying and the
# version file.

set -euo pipefail

repository="jerrychan7/termux-desktop"
release_url="https://github.com/$repository/releases/latest"
archive_url="https://github.com/$repository/archive/refs/tags/%s.tar.gz"

script_directory=""
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "$script_directory" && -f "$script_directory/desk" && -f "$script_directory/src/termux_desk/cli.py" ]]; then
    exec "$script_directory/desk" install --source "$script_directory" "$@"
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "desk needs python3; installing the python package with pkg." >&2
    pkg install -y python || {
        echo "could not install python; run pkg install python" >&2
        exit 1
    }
fi

for program in curl tar; do
    if ! command -v "$program" >/dev/null 2>&1; then
        echo "desk install needs $program; run pkg install $program" >&2
        exit 1
    fi
done

temporary_directory="${TMPDIR:-${PREFIX:-/data/data/com.termux/files/usr}/tmp}/desk-install.$$"
mkdir -p "$temporary_directory"
trap 'rm -rf "$temporary_directory"' EXIT

if [[ -n "${DESK_VERSION:-}" ]]; then
    tag="v${DESK_VERSION#v}"
else
    location="$(curl -sI "$release_url" | tr -d '\r' | awk 'tolower($1) == "location:" { print $2 }')"
    if [[ -z "$location" ]]; then
        echo "no release was found for $repository." >&2
        echo "run ./install.sh from a checkout instead." >&2
        exit 1
    fi
    tag="${location##*/}"
fi

if [[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$tag is not a release tag of the form vX.Y.Z." >&2
    exit 1
fi

archive="$temporary_directory/release.tar.gz"
echo "downloading desk $tag" >&2
curl -fsSL "$(printf "$archive_url" "$tag")" -o "$archive"

tar -xzf "$archive" -C "$temporary_directory"
sources=""
for candidate in "$temporary_directory"/termux-desktop-*/; do
    if [[ -f "$candidate/desk" ]]; then
        sources="${candidate%/}"
        break
    fi
done
if [[ -z "$sources" ]]; then
    echo "the archive of $tag does not contain the desk sources." >&2
    exit 1
fi

"$sources/desk" install --source "$sources" "$@"
