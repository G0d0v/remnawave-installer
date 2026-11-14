#!/usr/bin/env bash
# G0d0v Remnawave Installer — verified release bootstrap.
set -Eeuo pipefail

VERSION='1.0.0'
REPOSITORY='G0d0v/remnawave-installer'
CLI_ROOT='/opt/godov-remnawave-cli'

fail() { printf '\n  G0d0v: %s\n\n' "$*" >&2; exit 1; }

case "${1:-}" in
    --help|-h)
        printf 'G0d0v Remnawave Installer %s\n' "$VERSION"
        printf 'Usage: bash install.sh [--check | --version | CLI_ARGUMENTS]\n'
        printf 'Without arguments: install the verified CLI and open its menu.\n'
        exit 0 ;;
    --version) printf '%s\n' "$VERSION"; exit 0 ;;
esac

[[ "$(uname -s)" == 'Linux' ]] || fail 'Use an Ubuntu or Debian Linux server.'
[[ -r /etc/os-release ]] || fail '/etc/os-release is missing.'
# This is an operating-system-owned file, not a downloaded configuration.
# shellcheck source=/dev/null
. /etc/os-release
case "${ID}:${VERSION_ID}" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|ubuntu:26.10|debian:12|debian:13) ;;
    *) fail 'Supported OS: Ubuntu 22.04/24.04/26.04/26.10, Debian 12/13.' ;;
esac
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) fail 'Supported architecture: amd64 or arm64.' ;; esac

if [[ "${1:-}" == '--check' ]]; then
    printf 'OS: %s %s\nArchitecture: %s\n' "$ID" "$VERSION_ID" "$(uname -m)"
    if command -v python3 >/dev/null; then python3 --version; fi
    if command -v docker >/dev/null; then docker compose version || true; fi
    printf 'Compatibility check complete. No changes made.\n'
    exit 0
fi

(( EUID == 0 )) || fail 'Run sudo -i first, then run this command as root.'
umask 077
if ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 curl ca-certificates
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail 'Python 3.10+ is required.'

[[ ! -L "$CLI_ROOT" && ! -L "$CLI_ROOT/releases" ]] || fail 'CLI directory must not be a symlink.'
if [[ -e /usr/local/bin/godov-remna ]] && ! head -n 3 /usr/local/bin/godov-remna | grep -q '^# G0d0v managed launcher$'; then
    fail '/usr/local/bin/godov-remna already exists and is not managed by this installer.'
fi
mkdir -p "$CLI_ROOT/releases"
stage="$(mktemp -d /tmp/godov-bootstrap.XXXXXXXX)"
cleanup() { [[ -n "${stage:-}" && "$stage" == /tmp/godov-bootstrap.* ]] && rm -rf -- "$stage"; }
trap cleanup EXIT
package="godov-remnawave-v${VERSION}.tar.gz"
base="https://github.com/${REPOSITORY}/releases/download/v${VERSION}"
printf '\n  G0d0v · downloading verified release %s…\n\n' "$VERSION"
curl --fail --silent --show-error --location --retry 3 --proto '=https' --tlsv1.2 "$base/$package" -o "$stage/$package"
curl --fail --silent --show-error --location --retry 3 --proto '=https' --tlsv1.2 "$base/SHA256SUMS" -o "$stage/SHA256SUMS"

python3 - "$stage" "$package" <<'PY'
import hashlib
import pathlib
import re
import shutil
import sys
import tarfile

stage = pathlib.Path(sys.argv[1])
package = sys.argv[2]
checksums = (stage / 'SHA256SUMS').read_text()
match = re.fullmatch(r'([a-f0-9]{64})  ' + re.escape(package) + r'\n?', checksums)
if not match or hashlib.sha256((stage / package).read_bytes()).hexdigest() != match[1]:
    raise SystemExit('G0d0v: release checksum mismatch; nothing installed.')
destination = stage / 'code'
destination.mkdir()
with tarfile.open(stage / package, 'r:gz') as archive:
    members = archive.getmembers()
    names = set()
    total = 0
    for member in members:
        name = pathlib.PurePosixPath(member.name)
        total += member.size
        if (not member.isfile() or name.is_absolute() or '..' in name.parts
                or ':' in member.name or '\\' in member.name or member.name in names
                or str(name) != member.name or total > 16 * 1024 * 1024):
            raise SystemExit('G0d0v: invalid release archive.')
        names.add(member.name)
    if not {'manage.py', 'VERSION', 'godov/__init__.py', 'godov/cli.py'}.issubset(names):
        raise SystemExit('G0d0v: incomplete release archive.')
    for member in members:
        target = destination / member.name
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(member) as source, target.open('xb') as output:
            shutil.copyfileobj(source, output)
PY

target="$CLI_ROOT/releases/$VERSION"
[[ ! -L "$target" ]] || fail 'Release directory must not be a symlink.'
if [[ ! -e "$target" ]]; then
    mv -- "$stage/code" "$target"
elif [[ ! -f "$target/VERSION" || "$(cat "$target/VERSION")" != "$VERSION" ]]; then
    fail 'Existing release directory has an unexpected version.'
fi
ln -sfnT "releases/$VERSION" "$CLI_ROOT/current"
launcher="$(mktemp /usr/local/bin/.godov-remna.XXXXXXXX)"
cat > "$launcher" <<'SH'
#!/usr/bin/env bash
# G0d0v managed launcher
set -Eeuo pipefail
exec python3 /opt/godov-remnawave-cli/current/manage.py "$@"
SH
chmod 0755 "$launcher"
mv -f -- "$launcher" /usr/local/bin/godov-remna
cleanup
trap - EXIT
if [[ $# -eq 0 && ! -t 0 ]]; then
    [[ -r /dev/tty ]] || fail 'A terminal is required. Or pass install --config FILE --yes.'
    exec /usr/local/bin/godov-remna </dev/tty
fi
exec /usr/local/bin/godov-remna "$@"
