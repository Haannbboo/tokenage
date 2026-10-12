#!/bin/sh
# tokenage installer: the client, the server, or both.
#
#   curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash
#       both (default): the server, then a client signed in to it
#   ... | bash -s -- --server     the server only
#   ... | bash -s -- --client     the client only (TOKENAGE_SERVER=<url> names its server)
#   curl -fsSL https://YOUR_SERVER/install.sh | sh
#       the copy a server serves is preset to that server and installs the client
#
# The server is a git clone in ~/.tokenage/src. The client is its own snapshot and
# virtualenv under ~/.tokenage/versions with a `current` link, even on the machine
# that runs the server. The two installs share only the launcher and talk over HTTP.
#
# Environment: TOKENAGE_BRANCH (default main), TOKENAGE_SERVER, TOKENAGE_SKIP_LOGIN=1
# (refresh agent wiring instead of signing in; used by `tokenage update`).
# The API replaces the server and optional preview-commit placeholders when serving.
set -eu

TOKENAGE_INSTALL_SERVER=__TOKENAGE_SERVER_URL__
TOKENAGE_INSTALL_COMMIT=__TOKENAGE_INSTALL_COMMIT__
REPOSITORY=Haannbboo/tokenage
BRANCH=${TOKENAGE_BRANCH:-main}
PYTHON_VERSION=${TOKENAGE_PYTHON_VERSION:-3.13}

say() {
  printf 'tokenage: %s\n' "$*"
}

fail() {
  printf 'tokenage: error: %s\n' "$*" >&2
  exit 1
}

: "${HOME:?HOME must be set}"
TRACKER_HOME=${TOKENAGE_HOME:-$HOME/.tokenage}
SERVER_URL=${TOKENAGE_SERVER:-$TOKENAGE_INSTALL_SERVER}
case "$SERVER_URL" in
  __TOKENAGE_SERVER_URL"__") SERVER_URL= ;;
esac
case "$TOKENAGE_INSTALL_COMMIT" in
  __TOKENAGE_INSTALL_COMMIT"__") TOKENAGE_INSTALL_COMMIT= ;;
esac

# ── Which components ───────────────────────────────────────────────
# Only component flags are accepted.
WANT_CLIENT=0
WANT_SERVER=0
count=$#
while [ "$count" -gt 0 ]; do
  arg=$1
  shift
  case "$arg" in
    --client) WANT_CLIENT=1 ;;
    --server) WANT_SERVER=1 ;;
    *) set -- "$@" "$arg" ;;
  esac
  count=$((count - 1))
done
if [ "$WANT_CLIENT$WANT_SERVER" = 00 ]; then
  if [ -n "$SERVER_URL" ]; then WANT_CLIENT=1; else WANT_CLIENT=1; WANT_SERVER=1; fi
fi
[ "$#" -eq 0 ] || fail "unknown option: $1"
if [ "$WANT_CLIENT" = 1 ]; then say 'Installing: client'; fi
if [ "$WANT_SERVER" = 1 ]; then say 'Installing: server'; fi

for command_name in curl tar sed awk mktemp mkdir mv ln rm cp chmod readlink cat grep uname; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command not found: $command_name"
done

# ── Server ─────────────────────────────────────────────────────────
INSTALL_DIR=$TRACKER_HOME/src
if [ "$WANT_SERVER" = 1 ]; then
  command -v bash >/dev/null 2>&1 || fail 'bash is required but not found'
  if ! command -v git >/dev/null 2>&1; then
    say 'git not found, attempting to install...'
    installed=0
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update -qq && sudo apt-get install -y -qq git && installed=1
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y -q git && installed=1
    elif command -v yum >/dev/null 2>&1; then
      sudo yum install -y -q git && installed=1
    elif command -v apk >/dev/null 2>&1; then
      sudo apk add -q git && installed=1
    fi
    [ "$installed" = 1 ] || fail 'git is required and could not be installed automatically; install it and re-run'
  fi
  if [ -d "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR/.git" ]; then
    fail "$INSTALL_DIR exists but is not a git checkout; remove or move it, then re-run"
  fi
  if [ -d "$INSTALL_DIR/.git" ]; then
    origin_url=$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)
    case "$origin_url" in
      "https://github.com/$REPOSITORY.git"|"git@github.com:$REPOSITORY.git") ;;
      *) fail "unexpected origin for $INSTALL_DIR: $origin_url" ;;
    esac
    say "Updating the server in $INSTALL_DIR..."
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull origin "$BRANCH"
  else
    say "Cloning the server to $INSTALL_DIR..."
    mkdir -p "$TRACKER_HOME"
    git clone --branch "$BRANCH" "https://github.com/$REPOSITORY.git" "$INSTALL_DIR"
  fi
  bash "$INSTALL_DIR/src/scripts/bootstrap.sh"
  # Same version on both sides of a both-install.
  [ -n "$TOKENAGE_INSTALL_COMMIT" ] || TOKENAGE_INSTALL_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD)
fi
[ "$WANT_CLIENT" = 1 ] || exit 0

# ── Client ─────────────────────────────────────────────────────────
# In the both-install the client's server is the one just installed here.
if [ "$WANT_SERVER" = 1 ]; then
  api_port=$(grep -E '^[[:space:]]+api_port:' "$TRACKER_HOME/config.yaml" 2>/dev/null | head -1 | awk '{print $2}')
  SERVER_URL=http://127.0.0.1:${api_port:-4001}
fi
case "$SERVER_URL" in
  '') fail 'no server URL: set TOKENAGE_SERVER=<url>, or install the server too (--server --client)' ;;
  https://?*) ;;
  http://localhost|http://localhost:*|http://localhost/*|http://localhost\?*|http://localhost\#*) ;;
  http://127.0.0.1|http://127.0.0.1:*|http://127.0.0.1/*|http://127.0.0.1\?*|http://127.0.0.1\#*) ;;
  http://\[::1\]|http://\[::1\]:*|http://\[::1\]/*|http://\[::1\]\?*|http://\[::1\]\#*) ;;
  *) fail 'the hosted server URL must use HTTPS (HTTP is allowed for localhost)' ;;
esac
SERVER_AUTHORITY=${SERVER_URL#*://}
SERVER_AUTHORITY=${SERVER_AUTHORITY%%/*}
SERVER_AUTHORITY=${SERVER_AUTHORITY%%\?*}
SERVER_AUTHORITY=${SERVER_AUTHORITY%%\#*}
[ -n "$SERVER_AUTHORITY" ] || fail 'the hosted server URL has no host'
case "$SERVER_URL" in
  *[[:space:]]*) fail 'the hosted server URL contains whitespace' ;;
esac
while [ "${SERVER_URL%/}" != "$SERVER_URL" ]; do
  SERVER_URL=${SERVER_URL%/}
done

VERSIONS_DIR=$TRACKER_HOME/versions
BIN_DIR=${TOKENAGE_BIN_DIR:-$HOME/.local/bin}
ORIGINAL_PATH=${PATH:-}
OS_NAME=$(uname -s)
case "$OS_NAME" in
  Darwin|Linux) ;;
  *) fail "unsupported operating system: $OS_NAME (use macOS or Linux)" ;;
esac
LAUNCHER=$BIN_DIR/tokenage
if [ -e "$TRACKER_HOME/current" ] && [ ! -L "$TRACKER_HOME/current" ]; then
  fail "$TRACKER_HOME/current already exists and is not a managed link; move it aside before installing"
fi

# The launcher is shared with the server install and recognised by the marker
# line it carries. Either install may have placed it (the server's is a symlink
# into its clone); this one replaces it with a copy from the client snapshot.
if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  if ! grep -q '^# tokenage launcher$' "$LAUNCHER" 2>/dev/null; then
    fail "$LAUNCHER already exists; move it aside to keep that installation, then run this installer again"
  fi
fi

TMP_ROOT=${TMPDIR:-/tmp}
WORK_DIR=$(mktemp -d "$TMP_ROOT/tokenage-install.XXXXXX") || fail 'could not create temporary directory'
STAGING_DIR=
cleanup() {
  rm -rf "$WORK_DIR"
  if [ -n "$STAGING_DIR" ]; then
    rm -rf "$STAGING_DIR"
  fi
}
trap cleanup 0
trap 'exit 1' HUP INT TERM
umask 077
mkdir -p "$VERSIONS_DIR" "$BIN_DIR"

# uv is installed in the user's bin directory and is used to manage Python.
PATH=$BIN_DIR:$HOME/.local/bin:$PATH
export PATH
if ! command -v uv >/dev/null 2>&1; then
  say 'Installing uv...'
  curl -fsSL https://astral.sh/uv/install.sh -o "$WORK_DIR/uv-install.sh" || fail 'could not download uv installer'
  sh "$WORK_DIR/uv-install.sh"
  PATH=$BIN_DIR:$HOME/.local/bin:$PATH
  export PATH
fi
command -v uv >/dev/null 2>&1 || fail 'uv installation did not provide the uv command'

say "Installing Python $PYTHON_VERSION..."
uv python install "$PYTHON_VERSION"

if [ -n "$TOKENAGE_INSTALL_COMMIT" ]; then
  COMMIT=$TOKENAGE_INSTALL_COMMIT
else
  say 'Resolving the latest client source...'
  curl -fsSL -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/$REPOSITORY/commits/$BRANCH" \
    -o "$WORK_DIR/commit.json" || fail 'could not resolve the latest GitHub commit'
  COMMIT=$(sed -n 's/.*"sha"[[:space:]]*:[[:space:]]*"\([a-fA-F0-9]\{40\}\)".*/\1/p' "$WORK_DIR/commit.json" | awk 'length($0) == 40 { print; exit }')
fi
case "$COMMIT" in
  *[!0-9a-f]*|'') fail 'the source commit SHA is invalid' ;;
esac
[ "${#COMMIT}" -eq 40 ] || fail 'the source commit SHA is invalid'

VERSION_DIR=$VERSIONS_DIR/$COMMIT
if [ ! -x "$VERSION_DIR/.venv/bin/python" ]; then
  say "Downloading source snapshot $COMMIT..."
  curl -fsSL "https://github.com/$REPOSITORY/archive/$COMMIT.tar.gz" \
    -o "$WORK_DIR/source.tar.gz" || fail 'could not download the source snapshot'
  mkdir "$WORK_DIR/extracted"
  tar -xzf "$WORK_DIR/source.tar.gz" -C "$WORK_DIR/extracted" || fail 'could not extract the source snapshot'
  SOURCE_DIR=$WORK_DIR/extracted/tokenage-$COMMIT
  [ -d "$SOURCE_DIR/client" ] || fail 'source snapshot does not contain the client package'
  [ -f "$SOURCE_DIR/client/pyproject.toml" ] || fail 'client runtime requirements are missing'

  STAGING_CANDIDATE=$VERSIONS_DIR/.install-$COMMIT-$$
  [ ! -e "$VERSION_DIR" ] || fail 'an incomplete version directory already exists; remove it and retry'
  mkdir "$STAGING_CANDIDATE" || fail 'could not create staging directory'
  STAGING_DIR=$STAGING_CANDIDATE
  cp -R "$SOURCE_DIR"/. "$STAGING_DIR" || fail 'could not stage the source snapshot'
  printf '%s\n' "$COMMIT" > "$STAGING_DIR/client/COMMIT"

  say 'Installing client runtime dependencies...'
  uv venv --managed-python --python "$PYTHON_VERSION" "$STAGING_DIR/.venv"
  uv pip install --python "$STAGING_DIR/.venv/bin/python" \
    -r "$STAGING_DIR/client/pyproject.toml"
  mv "$STAGING_DIR" "$VERSION_DIR"
  STAGING_DIR=
fi

[ -x "$VERSION_DIR/.venv/bin/python" ] || fail 'client Python environment is incomplete'
if [ ! -f "$VERSION_DIR/client/COMMIT" ]; then
  printf '%s\n' "$COMMIT" > "$VERSION_DIR/client/COMMIT"
fi
TOKENAGE_CLIENT_COMMIT=$COMMIT PYTHONPATH=$VERSION_DIR \
  "$VERSION_DIR/.venv/bin/python" -P -m client check-server --server "$SERVER_URL"

# Install the shared launcher. It resolves the client and the server (when one
# is installed) from $TOKENAGE_HOME on every run.
[ -f "$VERSION_DIR/client/bin/tokenage" ] || fail 'the source snapshot is missing client/bin/tokenage'
LAUNCHER_TMP=$BIN_DIR/.tokenage-$$
cp "$VERSION_DIR/client/bin/tokenage" "$LAUNCHER_TMP"
chmod 755 "$LAUNCHER_TMP"
mv -f "$LAUNCHER_TMP" "$LAUNCHER"

# Replace current via a same-directory rename so a failed install keeps the old version active.
CURRENT_TMP=$TRACKER_HOME/.current-$$
ln -s "versions/$COMMIT" "$CURRENT_TMP"
"$VERSION_DIR/.venv/bin/python" -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' \
  "$CURRENT_TMP" "$TRACKER_HOME/current"

if [ "${TOKENAGE_SKIP_LOGIN:-0}" = 1 ]; then
  say "Refreshing agent configuration with the updated client..."
  TOKENAGE_CLIENT_COMMIT=$COMMIT PYTHONPATH=$VERSION_DIR \
    "$VERSION_DIR/.venv/bin/python" -P -m client setup && SETUP_RC=0 || SETUP_RC=$?
  # 3 = EXIT_NOT_SIGNED_IN in client/setup.py
  case $SETUP_RC in
    0) ;;
    3) say "Not signed in; agents left unwired. Run: $BIN_DIR/tokenage login" ;;
    *) fail 'client update installed, but agent configuration refresh failed' ;;
  esac
  say "Installed tokenage $COMMIT. Existing credentials were preserved; sign-in skipped."
else
  say "Installed tokenage $COMMIT. Starting sign-in..."
  if ( : </dev/tty ) >/dev/null 2>&1; then
    TOKENAGE_CLIENT_COMMIT=$COMMIT PYTHONPATH=$VERSION_DIR \
      "$VERSION_DIR/.venv/bin/python" -P -m client login --server "$SERVER_URL" </dev/tty
  else
    say "No interactive terminal is available. Finish setup with: $BIN_DIR/tokenage login --server $SERVER_URL"
  fi
fi

# The both-install also runs the client service; failure is not fatal.
if [ "$WANT_SERVER" = 1 ]; then
  TOKENAGE_CLIENT_COMMIT=$COMMIT PYTHONPATH=$VERSION_DIR \
    "$VERSION_DIR/.venv/bin/python" -P -m client client start ||
    say 'Client service not started; see: tokenage client start'
fi

# Compare with the user's original PATH, before adding uv's installation directory.
case ":${ORIGINAL_PATH}:" in
  *":$BIN_DIR:"*) ;;
  *)
    printf '\nAdd this directory to your shell PATH to run tokenage from anywhere:\n  export PATH="%s:$PATH"\n' "$BIN_DIR"
    ;;
esac
