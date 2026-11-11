#!/bin/sh
# tokenage installer: the client, the server, or both.
#
#   curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash
#       asks what to install; the default is the client, on the hosted server
#   ... | bash -s -- --server     the server only
#   ... | bash -s -- --client     the client only (TOKENAGE_SERVER=<url> names its server)
#   ... | bash -s -- --server --client
#       both: the server, then a client signed in to it
#   curl -fsSL https://YOUR_SERVER/install.sh | sh
#       asks what to install; the default is the client, on that server
#
# With a terminal it asks for the components (unless --client/--server is
# given) and the client's server URL. --yes, TOKENAGE_YES=1, no terminal and
# `tokenage update` (TOKENAGE_SKIP_LOGIN=1) skip the questions. A preset URL is
# the default answer to the URL prompt. In unattended runs without component
# flags, a preset URL selects the client and no preset installs both locally.
# An unattended --client needs a server URL.
# Pin the client with --client-version X.Y.Z or TOKENAGE_CLIENT_VERSION=X.Y.Z.
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
HOSTED_SERVER=https://app.tokenage.dev
TOKENAGE_INSTALL_COMMIT=${TOKENAGE_INSTALL_COMMIT:-__TOKENAGE_INSTALL_COMMIT__}
REPOSITORY=Haannbboo/tokenage
BRANCH=${TOKENAGE_BRANCH:-main}
PYTHON_VERSION=${TOKENAGE_PYTHON_VERSION:-3.13}
CLIENT_VERSION=${TOKENAGE_CLIENT_VERSION:-}
CLIENT_VERSION_SET=0

say() {
  printf 'tokenage: %s\n' "$*"
}

fail() {
  printf 'tokenage: error: %s\n' "$*" >&2
  exit 1
}

: "${HOME:?HOME must be set}"
TRACKER_HOME=${TOKENAGE_HOME:-$HOME/.tokenage}
BIN_DIR=${TOKENAGE_BIN_DIR:-$HOME/.local/bin}
ORIGINAL_PATH=${PATH:-}
SERVER_URL=${TOKENAGE_SERVER:-$TOKENAGE_INSTALL_SERVER}
case "$SERVER_URL" in
  __TOKENAGE_SERVER_URL"__") SERVER_URL= ;;
esac
case "$TOKENAGE_INSTALL_COMMIT" in
  __TOKENAGE_INSTALL_COMMIT"__") TOKENAGE_INSTALL_COMMIT= ;;
esac

# ── Which components ───────────────────────────────────────────────
# Component selection.
WANT_CLIENT=0
WANT_SERVER=0
ASSUME_YES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --client) WANT_CLIENT=1; shift ;;
    --server) WANT_SERVER=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --client-version)
      [ "$#" -ge 2 ] || fail '--client-version requires a version'
      case "$2" in -*) fail '--client-version requires a version' ;; esac
      CLIENT_VERSION=$2
      CLIENT_VERSION_SET=1
      shift 2
      ;;
    *) fail "unknown option: $1" ;;
  esac
done
is_true() {
  case "$1" in 1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}
SKIP_LOGIN=0
if is_true "${TOKENAGE_SKIP_LOGIN:-}"; then SKIP_LOGIN=1; fi
CLIENT_PINNED=0
if [ "$CLIENT_VERSION_SET" = 1 ] || [ -n "$CLIENT_VERSION" ]; then
  CLIENT_PINNED=1
fi
# Ask only with a terminal for both the question and the answer; stdin is the
# script itself under `curl | sh`. The same opt-outs as client/prompts.py.
ASK=1
[ "$ASSUME_YES" = 0 ] || { ASK=0; TOKENAGE_YES=1; export TOKENAGE_YES; }
for opt_out in "${TOKENAGE_YES:-}" "${TOKENAGE_NO_PROMPT:-}" "${TOKENAGE_SKIP_LOGIN:-}"; do
  if is_true "$opt_out"; then ASK=0; fi
done
if [ "$ASK" = 1 ] && { [ ! -t 1 ] || ! ( : </dev/tty ) 2>/dev/null; }; then
  ASK=0
fi

for command_name in curl tar sed awk mktemp mkdir mv ln rm cp chmod readlink cat grep uname tr head find; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command not found: $command_name"
done

# Server-only installs ignore even invalid pins.
validate_client_version() {
  [ "$CLIENT_PINNED" = 1 ] || return 0
  [ -n "$CLIENT_VERSION" ] || fail '--client-version requires a version'
  case "$CLIENT_VERSION" in *'
'*) fail 'invalid client version; expected MAJOR.MINOR.PATCH' ;; esac
  if ! printf '%s\n' "$CLIENT_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    fail 'invalid client version; expected MAJOR.MINOR.PATCH'
  fi
}
# Explicit client flags and unattended defaults guarantee a client install.
if [ "$WANT_CLIENT" = 1 ] || [ "$ASK:$WANT_SERVER" = 0:0 ]; then
  validate_client_version
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
trap 'exit 1' HUP TERM
trap 'exit 130' INT

# uv is installed in the user's bin directory and is used to manage Python.
ensure_uv() {
  PATH=$BIN_DIR:$HOME/.local/bin:$PATH
  export PATH
  command -v uv >/dev/null 2>&1 && return
  say 'Installing uv...'
  curl -fsSL https://astral.sh/uv/install.sh -o "$WORK_DIR/uv-install.sh" || return 1
  sh "$WORK_DIR/uv-install.sh" || return 1
  command -v uv >/dev/null 2>&1
}

resolve_client_commit() {
  if [ "$CLIENT_PINNED" = 1 ] && [ -s "$WORK_DIR/resolve-error" ]; then
    cat "$WORK_DIR/resolve-error" >&2
    exit 1
  fi
  if [ "$CLIENT_PINNED" = 0 ] &&
    [ -n "$TOKENAGE_INSTALL_COMMIT" ]; then
    COMMIT=$TOKENAGE_INSTALL_COMMIT
  elif [ -f "$WORK_DIR/resolved-commit" ]; then
    COMMIT=$(cat "$WORK_DIR/resolved-commit")
    return
  else
    if [ "$CLIENT_PINNED" = 1 ]; then
      say "Resolving client $CLIENT_VERSION..."
      status=$(curl -sSL --connect-timeout 10 --max-time 30 -D "$WORK_DIR/commit.headers" -w '%{http_code}' -H 'Accept: application/vnd.github+json' \
        "https://api.github.com/repos/$REPOSITORY/commits/refs/tags/client-v$CLIENT_VERSION" \
        -o "$WORK_DIR/commit.json") || fail 'could not resolve client version: offline/DNS, TLS or network failure'
      case "$status" in
        200) ;;
        404|422) fail "client version $CLIENT_VERSION not found" ;;
        429) fail "could not resolve client version: GitHub rate limit (HTTP $status)" ;;
        403)
          if grep -Eiq '^x-ratelimit-remaining:[[:space:]]*0[[:space:]]*$' "$WORK_DIR/commit.headers"; then
            fail 'could not resolve client version: GitHub rate limit (HTTP 403)'
          fi
          fail 'could not resolve client version: HTTP 403'
          ;;
        *) fail "could not resolve client version: HTTP $status" ;;
      esac
    else
      say 'Resolving the latest client source...'
      curl -fsSL -H 'Accept: application/vnd.github+json' \
        "https://api.github.com/repos/$REPOSITORY/commits/$BRANCH" \
        -o "$WORK_DIR/commit.json" || fail 'could not resolve the latest GitHub commit'
    fi
    COMMIT=$(sed -n 's/.*"sha"[[:space:]]*:[[:space:]]*"\([a-fA-F0-9]\{40\}\)".*/\1/p' "$WORK_DIR/commit.json" | awk 'length($0) == 40 { print; exit }')
  fi
  case "$COMMIT" in
    *[!0-9a-f]*|'') fail 'the source commit SHA is invalid' ;;
  esac
  [ "${#COMMIT}" -eq 40 ] || fail 'the source commit SHA is invalid'

  printf '%s\n' "$COMMIT" > "$WORK_DIR/resolved-commit"
}

# uv installs Python on demand, which runs the questions in the shared
# client/prompts.py, fetched at the resolved client commit. The numbered prompts
# below are the fallback when that fails; Ctrl-C (130) cancels the install.
normalize_server_scheme() {
  case "$1" in
    *://*) printf '%s://%s\n' "$(printf '%s' "${1%%://*}" | tr '[:upper:]' '[:lower:]')" "${1#*://}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}
valid_server_url() {
  case "$1" in
    *[[:space:]]*) return 1 ;;
    *://*) ;;
    *) return 1 ;;
  esac
  url=$(normalize_server_scheme "$1")
  authority=${url#*://}
  authority=${authority%%[/?#]*}
  case "$authority" in ''|:*|*@*) return 1 ;; esac
  case "$authority" in
    \[?*\]*) host=${authority%%]*}]; port=${authority#*]} ;;
    *\[*|*\]*) return 1 ;;
    *) host=${authority%%:*}; port=${authority#"$host"} ;;
  esac
  case "$port" in
    ''|:) ;;
    :*) port=${port#:}
        case "$port" in *[!0-9]*) return 1 ;; esac
        [ "$port" -le 65535 ] 2>/dev/null || return 1 ;;
    *) return 1 ;;
  esac
  host=$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')
  case "${url%%://*}://$host" in
    https://?*|http://localhost|http://127.0.0.1|http://\[::1\]) return 0 ;;
    *) return 1 ;;
  esac
}
ask_clack() {
  if ! (resolve_client_commit) 2>"$WORK_DIR/resolve-error"; then
    return 1
  fi
  COMMIT=$(cat "$WORK_DIR/resolved-commit")
  ensure_uv || return 1
  mkdir "$WORK_DIR/prompt" || return 1
  curl -fsSL "https://raw.githubusercontent.com/$REPOSITORY/$COMMIT/client/prompts.py" \
    -o "$WORK_DIR/prompt/prompts.py" || return 1
  picked=
  [ "$WANT_CLIENT" = 0 ] || picked=client
  say 'Preparing the installer (first run downloads Python and prompt_toolkit)...'
  uv run --quiet --no-project --python "$PYTHON_VERSION" --with 'prompt_toolkit>=3.0,<4' python -I "$WORK_DIR/prompt/prompts.py" \
    install "$WORK_DIR/answers" "$picked" "$SERVER_URL" "$HOSTED_SERVER" </dev/tty || return
  picked=$(sed -n 1p "$WORK_DIR/answers") || return 1
  answer=$(sed -n 2p "$WORK_DIR/answers") || return 1
  case "$picked" in
    client) valid_server_url "$answer" || return 1; WANT_CLIENT=1; SERVER_URL=$answer ;;
    server) [ "$WANT_CLIENT:$answer" = 0:- ] || return 1; WANT_SERVER=1 ;;
    both) [ "$WANT_CLIENT:$answer" = 0:- ] || return 1; WANT_CLIENT=1; WANT_SERVER=1 ;;
    *) return 1 ;;
  esac
}
if [ "$ASK" = 1 ] && [ "$WANT_SERVER" = 0 ]; then
  if ask_clack 2>"$WORK_DIR/prompt-error"; then
    ASK=0
  else
    [ "$?" != 130 ] || exit 130
    printf '%s\n' 'note: shared installer prompt failed; falling back to numbered questions' >&2
  fi
fi
# Shells defer a trapped SIGINT until a pending read returns. The reader must
# be a child with default INT handling so it exits promptly, while the parent's
# INT trap still exits 130 and runs cleanup. Print the prompt in the child too,
# once its signal handling is ready.
read_answer() {
  (
    trap - INT
    printf '%s' "$1"
    read -r answer </dev/tty || exit 1
    printf '%s\n' "$answer" >"$WORK_DIR/answer"
  ) || {
    rc=$?
    [ "$rc" = 130 ] || fail 'installer input closed; installation cancelled'
    exit 130
  }
  answer=$(cat "$WORK_DIR/answer")
}
if [ "$WANT_CLIENT$WANT_SERVER" = 00 ] && [ "$ASK" = 1 ]; then
  printf '\ntokenage installer\n\nWhat do you want to install?\n'
  printf '  1) Client - track the agents on this machine      [default]\n'
  printf '  2) Server - host your own dashboard and collector\n'
  printf '  3) Both   - your own server + a client signed in to it\n'
  while :; do
    read_answer '> '
    case "$answer" in
      ''|1) WANT_CLIENT=1; break ;;
      2) WANT_SERVER=1; break ;;
      3) WANT_CLIENT=1; WANT_SERVER=1; break ;;
    esac
    printf 'Answer 1, 2 or 3.\n'
  done
fi
if [ "$WANT_CLIENT$WANT_SERVER" = 00 ]; then
  WANT_CLIENT=1
  [ -n "$SERVER_URL" ] || WANT_SERVER=1
fi
# The component answer is now final; validate before any server work.
if [ "$WANT_CLIENT" = 1 ]; then validate_client_version; fi
# The both-install's client uses the server it installs (set below).
if [ "$WANT_CLIENT$WANT_SERVER" = 10 ]; then
  if [ "$ASK" = 1 ]; then
    SERVER_URL=${SERVER_URL:-$HOSTED_SERVER}
    while :; do
      read_answer "Server URL [$SERVER_URL]: "
      candidate=${answer:-$SERVER_URL}
      if valid_server_url "$candidate"; then SERVER_URL=$candidate; break; fi
      printf 'Enter an HTTPS URL (HTTP is allowed for localhost).\n'
    done
  fi
  [ -n "$SERVER_URL" ] || fail 'no server URL: set TOKENAGE_SERVER=<url>, or install the server too (--server --client)'
fi
if [ "$WANT_CLIENT" = 1 ]; then say 'Installing: client'; fi
if [ "$WANT_SERVER" = 1 ]; then say 'Installing: server'; fi

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
  # An unpinned both-install uses the server checkout for the client.
  if [ "$WANT_CLIENT:$CLIENT_PINNED" = 1:0 ] && [ -z "$TOKENAGE_INSTALL_COMMIT" ]; then
    TOKENAGE_INSTALL_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD)
  fi
fi
if [ "$WANT_CLIENT" = 0 ]; then
  if [ "$CLIENT_PINNED" = 1 ]; then
    say 'Ignoring client version for a server-only install.'
  fi
  exit 0
fi

resolve_client_commit

# ── Client ─────────────────────────────────────────────────────────
# In the both-install the client's server is the one just installed here.
if [ "$WANT_SERVER" = 1 ]; then
  api_port=$(grep -E '^[[:space:]]+api_port:' "$TRACKER_HOME/config.yaml" 2>/dev/null | head -1 | awk '{print $2}')
  SERVER_URL=http://127.0.0.1:${api_port:-4001}
fi
SERVER_URL=$(normalize_server_scheme "$SERVER_URL")
valid_server_url "$SERVER_URL" || fail 'the server URL must use HTTPS (HTTP is allowed for localhost) and have a host without userinfo or whitespace'
while [ "${SERVER_URL%/}" != "$SERVER_URL" ]; do
  SERVER_URL=${SERVER_URL%/}
done

VERSIONS_DIR=$TRACKER_HOME/versions
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

umask 077
mkdir -p "$VERSIONS_DIR" "$BIN_DIR"

ensure_uv || fail 'could not install uv'

say "Installing Python $PYTHON_VERSION..."
uv python install "$PYTHON_VERSION"

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

  if [ "$CLIENT_PINNED" = 1 ]; then
    snapshot_version=$(cat "$SOURCE_DIR/client/VERSION" 2>/dev/null) || fail 'source snapshot is missing client/VERSION'
    [ "$snapshot_version" = "$CLIENT_VERSION" ] || fail "client tag VERSION mismatch: requested $CLIENT_VERSION, snapshot has $snapshot_version"
  fi

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
elif [ "$CLIENT_PINNED" = 1 ]; then
  snapshot_version=$(cat "$VERSION_DIR/client/VERSION" 2>/dev/null) || fail 'source snapshot is missing client/VERSION'
  [ "$snapshot_version" = "$CLIENT_VERSION" ] || fail "client tag VERSION mismatch: requested $CLIENT_VERSION, snapshot has $snapshot_version"
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
PREVIOUS_VERSION=$(readlink "$TRACKER_HOME/current" 2>/dev/null || true)
CURRENT_TMP=$TRACKER_HOME/.current-$$
ln -s "versions/$COMMIT" "$CURRENT_TMP"
"$VERSION_DIR/.venv/bin/python" -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' \
  "$CURRENT_TMP" "$TRACKER_HOME/current"

# Work from the versions directory and only remove plain immediate children.
# rm does not follow symlinks inside a removed tree; never pass it a symlink
# child (or traverse a symlink used as the versions directory itself).
prune_versions() (
  [ ! -L "$VERSIONS_DIR" ] || return 1
  configs=$(PYTHONPATH=$VERSION_DIR "$VERSION_DIR/.venv/bin/python" -P -c \
    'from client.setup import plugin_configs; print("\n".join(str(p) for p in plugin_configs().values()))') || return 1
  cd -P "$VERSIONS_DIR" || return 1
  previous=
  case "$PREVIOUS_VERSION" in
    versions/*) previous=${PREVIOUS_VERSION#versions/} ;;
    "$VERSIONS_DIR"/*) previous=${PREVIOUS_VERSION#"$VERSIONS_DIR"/} ;;
    "$PWD"/*) previous=${PREVIOUS_VERSION#"$PWD"/} ;;
  esac
  previous=${previous%/}
  case "$previous" in
    */*|.|..) previous= ;;
  esac
  prune_rc=0
  removed=0
  for version in * .[!.]* ..?*; do
    [ -d "$version" ] && [ ! -L "$version" ] || continue
    [ "$version" != "$COMMIT" ] && [ "$version" != "$previous" ] || continue
    case "$version" in
      .install-*) continue ;; # another installer's staging directory
    esac
    # Another installer may have swapped current or still be preparing a
    # snapshot. Re-read current and leave recent directories alone so neither
    # installer deletes the other's version; a later update can retire them.
    current=$(readlink "$TRACKER_HOME/current" 2>/dev/null) || return 1
    current=${current%/}
    case "$current" in
      "versions/$version"|"$VERSIONS_DIR/$version"|"$PWD/$version") continue ;;
    esac
    young=$(find "./$version" -maxdepth 0 -mmin -60) || continue
    [ -z "$young" ] || continue
    # Agents off PATH during update are not re-wired, and a failed plugin build
    # can also leave a config pointing more than one update back. Match the SHA
    # rather than a JSON-escaped absolute path to preserve those references.
    referenced=0
    case "$version" in
      *[!0-9a-f]*) ;;
      *)
        if [ "${#version}" -eq 40 ]; then
          while IFS= read -r config; do
            [ -f "$config" ] || continue
            grep -F -q -e "$version" "$config" && config_rc=0 || config_rc=$?
            [ "$config_rc" = 1 ] || referenced=1
          done <<EOF
$configs
EOF
        fi
        ;;
    esac
    if [ "$referenced" = 1 ]; then
      printf 'tokenage: warning: retaining client version %s referenced by an agent configuration (or configuration unreadable)\n' "$version" >&2
      continue
    fi
    # A partial delete must not leave a directory a reinstall of this commit
    # would reuse, so rename first. A failed delete retries on a later update.
    case "$version" in
      .removing-*) doomed=$version ;;
      *) doomed=.removing-$version ;;
    esac
    if { [ "$doomed" = "$version" ] || mv -- "./$version" "./$doomed"; } && rm -rf "./$doomed"; then
      removed=$((removed + 1))
    else
      prune_rc=1
    fi
  done
  if [ "$removed" -gt 0 ]; then
    say "Removed $removed old client version(s)."
  fi
  return "$prune_rc"
)

# Restart the new snapshot immediately after the swap.
INSTALL_RC=0
TOKENAGE_CLIENT_COMMIT=$COMMIT PYTHONPATH=$VERSION_DIR \
  "$VERSION_DIR/.venv/bin/python" -P -m client client restart --if-running || {
    printf '%s\n' 'tokenage: error: client update installed, but service restart failed' >&2
    INSTALL_RC=1
  }

if [ "$SKIP_LOGIN" = 1 ]; then
  say "Refreshing agent configuration with the updated client..."
  TOKENAGE_CLIENT_COMMIT=$COMMIT PYTHONPATH=$VERSION_DIR \
    "$VERSION_DIR/.venv/bin/python" -P -m client setup && SETUP_RC=0 || SETUP_RC=$?
  # 3 = EXIT_NOT_SIGNED_IN in client/setup.py
  case $SETUP_RC in
    0) ;;
    3) say "Not signed in; agents left unwired. Run: $BIN_DIR/tokenage login" ;;
    *) printf '%s\n' 'tokenage: error: client update installed, but agent configuration refresh failed' >&2; INSTALL_RC=1 ;;
  esac
  say "Installed tokenage $COMMIT. Existing credentials were preserved; sign-in skipped."
else
  say "Installed tokenage $COMMIT. Starting sign-in..."
  if ( : </dev/tty ) >/dev/null 2>&1; then
    TOKENAGE_CLIENT_COMMIT=$COMMIT PYTHONPATH=$VERSION_DIR \
      "$VERSION_DIR/.venv/bin/python" -P -m client login --server "$SERVER_URL" </dev/tty || INSTALL_RC=1
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

# Retire snapshots only after the service and agent refresh have completed.
# On failure an older process or agent configuration may still need its files.
if [ "$INSTALL_RC" = 0 ]; then
  prune_versions || printf '%s\n' 'tokenage: warning: could not clean up old client versions' >&2
else
  printf '%s\n' 'tokenage: warning: old client versions retained after an incomplete refresh' >&2
fi

# Compare with the user's original PATH, before adding uv's installation directory.
case ":${ORIGINAL_PATH}:" in
  *":$BIN_DIR:"*) ;;
  *)
    printf '\nAdd this directory to your shell PATH to run tokenage from anywhere:\n  export PATH="%s:$PATH"\n' "$BIN_DIR"
    ;;
esac

exit "$INSTALL_RC"
