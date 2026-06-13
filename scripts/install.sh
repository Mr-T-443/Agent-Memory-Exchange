#!/usr/bin/env sh
# Installer script for Linux and macOS.
set -eu

# Locate Python 3.10+.
PYTHON=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PYTHON="$c"; break; fi
done
[ -z "$PYTHON" ] && { echo "Python 3.10+ is required but was not found." >&2; exit 1; }
"$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" || {
    echo "Python 3.10+ is required (found $("$PYTHON" --version 2>&1))." >&2
    exit 1
}

# Ensure user install directory is on PATH.
export PATH="$HOME/.local/bin:$("$PYTHON" -m site --user-base 2>/dev/null)/bin:$PATH"

# CLI invocation wrapper.
amx_cli() {
    if command -v amx >/dev/null 2>&1; then amx "$@"; else "$PYTHON" -m amx.cli "$@"; fi
}

# Check for existing installation.
INSTALLED_VER=""
if command -v amx >/dev/null 2>&1; then
    INSTALLED_VER=$(amx version 2>/dev/null | head -n1 || true)
    [ -z "$INSTALLED_VER" ] && INSTALLED_VER="(installed)"
fi

if [ -n "$INSTALLED_VER" ]; then
    printf "AMX %s is already installed. Update it? [Y/n] " "$INSTALLED_VER"
    read ans < /dev/tty
    case "$ans" in [Nn]*) echo "Keeping existing version. Nothing changed."; exit 0 ;; esac
else
    printf "AMX is not installed. Install it now? [Y/n] "
    read ans < /dev/tty
    case "$ans" in [Nn]*) echo "Cancelled."; exit 0 ;; esac
fi

# Perform installation.
# Select install source.
if [ "${AMX_SOURCE:-}" != "" ]; then
    SOURCE="$AMX_SOURCE"
elif [ -f "pyproject.toml" ] && grep -q 'name = "amx"' pyproject.toml 2>/dev/null; then
    SOURCE="."
else
    SOURCE="git+https://github.com/Mr-T-443/Agent-Memory-Exchange.git"
fi

# Choose install method.
HAVE_PIPX=0; command -v pipx >/dev/null 2>&1 && HAVE_PIPX=1
HAVE_VENV=0; "$PYTHON" -m venv --help >/dev/null 2>&1 && HAVE_VENV=1
VENV_DIR="$HOME/.amx-venv"

METHOD="${AMX_INSTALL_METHOD:-}"
if [ -z "$METHOD" ]; then
    if [ "$HAVE_PIPX" = 1 ] && [ "$HAVE_VENV" = 1 ]; then
        echo ""
        echo "Two install methods are available:"
        echo "  1. pipx  — isolated, managed for you (recommended)"
        echo "  2. venv  — a dedicated virtualenv at $VENV_DIR (no pipx needed)"
        printf "Which? [1] "
        read m < /dev/tty
        case "$m" in 2|venv) METHOD="venv" ;; *) METHOD="pipx" ;; esac
    elif [ "$HAVE_PIPX" = 1 ]; then METHOD="pipx"
    elif [ "$HAVE_VENV" = 1 ]; then METHOD="venv"
    fi
fi

echo ""
if [ "$METHOD" = "pipx" ]; then
    echo "Installing amx from '$SOURCE' with pipx ..."
    pipx install --force "$SOURCE"
elif [ "$METHOD" = "venv" ]; then
    echo "Creating a dedicated virtualenv at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
    echo "Installing amx from '$SOURCE' into the venv ..."
    "$VENV_DIR/bin/pip" install --upgrade "$SOURCE"
    # Create launcher symlinks on PATH.
    mkdir -p "$HOME/.local/bin"
    ln -sf "$VENV_DIR/bin/amx" "$HOME/.local/bin/amx"
    [ -e "$VENV_DIR/bin/amx-server" ] && ln -sf "$VENV_DIR/bin/amx-server" "$HOME/.local/bin/amx-server"
    # Use local venv executable.
    amx_cli() { "$VENV_DIR/bin/amx" "$@"; }
elif "$PYTHON" -m pip install --user --upgrade "$SOURCE"; then
    :
else
    echo "" >&2
    echo "No pipx and no venv module, and pip can't install here (PEP 668)." >&2
    echo "Install one of them, then re-run this installer:" >&2
    echo "  Debian/Ubuntu:  sudo apt install pipx   (or python3-venv)" >&2
    echo "  macOS:          brew install pipx" >&2
    echo "  other:          $PYTHON -m pip install --user pipx" >&2
    exit 1
fi

# Ensure local bin is on PATH.
export PATH="$HOME/.local/bin:$("$PYTHON" -m site --user-base 2>/dev/null)/bin:$PATH"
if ! command -v amx >/dev/null 2>&1; then
    echo "Note: 'amx' isn't on your PATH yet. Add this to your shell profile:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo ""
echo "amx installed:"
amx_cli info || true

# Set up Foundry IQ grounding.
echo ""
printf "Connect Foundry IQ (Azure AI Search) for grounded retrieval? [y/N] "
read use_foundry < /dev/tty
case "$use_foundry" in
    [Yy]*)
        printf "  Azure AI Search endpoint (https://<service>.search.windows.net): "
        read endpoint < /dev/tty
        printf "  API key: "
        read api_key < /dev/tty
        printf "  Index name [amx-memory]: "
        read idx < /dev/tty
        [ -z "$idx" ] && idx="amx-memory"

        if [ -z "$endpoint" ] || [ -z "$api_key" ]; then
            echo "Endpoint and API key are required. Skipping Foundry IQ setup."
            echo "Run 'amx enable-foundry' later to set it up."
        else
            ENV_DIR="$HOME/.amx"
            ENV_FILE="$ENV_DIR/.env"
            mkdir -p "$ENV_DIR"

            # Update or append key-value pair in env file.
            set_env_key() {
                k="$1" v="$2"
                if [ -f "$ENV_FILE" ] && grep -q "^$k=" "$ENV_FILE"; then
                    tmp="$ENV_FILE.tmp"
                    sed "s|^$k=.*|$k=$v|" "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
                else
                    printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"
                fi
            }
            set_env_key "AMX_FOUNDRY_IQ_ENDPOINT" "$endpoint"
            set_env_key "AMX_FOUNDRY_IQ_API_KEY"   "$api_key"
            set_env_key "AMX_FOUNDRY_IQ_INDEX"      "$idx"

            echo ""
            echo "Credentials saved. Testing connection and enabling sync..."
            amx_cli enable-foundry < /dev/tty
        fi
        ;;
    *)
        echo "Skipped. Run 'amx enable-foundry' later to add Foundry IQ."
        ;;
esac

# Register AMX in AI clients.
echo ""
printf "Register AMX in your AI clients (Claude Code, Cursor, Codex, ...)? [Y/n] "
read reg < /dev/tty
case "$reg" in
    [Nn]*) echo "Skipped. Run 'amx install-mcp' anytime." ;;
    *) amx_cli install-mcp < /dev/tty ;;
esac

# Print next steps.
cat <<'NEXT'

Next steps:
  1. Restart your AI client so it picks up the AMX server.
     (Skipped registration? Run: amx install-mcp)
  2. Tell your assistant: "set up AMX"
  3. Full guide: docs/WORKING.md
NEXT
