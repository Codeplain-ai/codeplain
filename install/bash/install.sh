#!/bin/bash

set -euo pipefail

# Base URL for additional scripts
CODEPLAIN_SCRIPTS_BASE_URL="${CODEPLAIN_SCRIPTS_BASE_URL:-https://codeplain.ai}"

# Base URL for the Codeplain API (used to verify the API key)
CODEPLAIN_API_URL="${CODEPLAIN_API_URL:-https://api.codeplain.ai}"

# Brand Colors (True Color / 24-bit)
YELLOW='\033[38;2;224;255;110m'    # #E0FF6E
GREEN='\033[38;2;121;252;150m'     # #79FC96
GREEN_LIGHT='\033[38;2;197;220;217m' # #C5DCD9
GREEN_DARK='\033[38;2;34;57;54m'   # #223936
BLUE='\033[38;2;10;31;212m'        # #0A1FD4
BLACK='\033[38;2;26;26;26m'        # #1A1A1A
WHITE='\033[38;2;255;255;255m'     # #FFFFFF
RED='\033[38;2;239;68;68m'         # #EF4444
GRAY='\033[38;2;128;128;128m'      # #808080
GRAY_LIGHT='\033[38;2;211;211;211m' # #D3D3D3
BOLD='\033[1m'
NC='\033[0m' # No Color / Reset

# Export colors for child scripts
export YELLOW GREEN GREEN_LIGHT GREEN_DARK BLUE BLACK WHITE RED GRAY GRAY_LIGHT BOLD NC

NONINTERACTIVE="${CODEPLAIN_INSTALL_NONINTERACTIVE:-0}"

if [ "$NONINTERACTIVE" = "1" ]; then
    echo "Running in non-interactive mode (CODEPLAIN_INSTALL_NONINTERACTIVE=1)"
else
    clear
fi
echo -e "Started ${YELLOW}${BOLD}*codeplain CLI${NC} installation..."

# Install uv if not present
install_uv() {
    echo -e "Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for this session
    export PATH="$HOME/.local/bin:$PATH"
}

# Trim leading/trailing whitespace (spaces, tabs, newlines, carriage returns).
# Users often copy the API key with surrounding whitespace or newlines.
trim_whitespace() {
    local value="$*"
    value="${value#"${value%%[![:space:]]*}"}"  # strip leading whitespace
    value="${value%"${value##*[![:space:]]}"}"   # strip trailing whitespace
    printf '%s' "$value"
}

# Verify an API key against the Codeplain API's /status endpoint.
# Sets the global VALIDATION_HTTP_CODE and returns 0 only when the key is valid
# (HTTP 200). This checks only the API key, nothing else about the install.
validate_api_key() {
    local key="$1"
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time 30 \
        -X POST "${CODEPLAIN_API_URL}/status" \
        -H "Content-Type: application/json" \
        --data "{\"api_key\":\"${key}\"}" 2>/dev/null || true)
    VALIDATION_HTTP_CODE="${http_code:-000}"
    [ "$VALIDATION_HTTP_CODE" = "200" ]
}

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo -e "${GRAY}uv is not installed.${NC}"
    install_uv
    echo -e "${GREEN}✓${NC} uv installed successfully"
    echo -e ""
fi

echo -e "${GREEN}✓${NC} uv detected"
echo -e ""

# Install or upgrade codeplain using uv tool
if uv tool list 2>/dev/null | grep -q "^codeplain"; then
    CURRENT_VERSION=$(uv tool list 2>/dev/null | grep "^codeplain" | sed 's/codeplain v//')
    echo -e "${GRAY}codeplain ${CURRENT_VERSION} is already installed.${NC}"
    echo -e "Upgrading to latest version..."
    echo -e ""
    uv tool upgrade codeplain &> /dev/null
    NEW_VERSION=$(uv tool list 2>/dev/null | grep "^codeplain" | sed 's/codeplain v//')
    if [ "$CURRENT_VERSION" = "$NEW_VERSION" ]; then
        echo -e "${GREEN}✓${NC} codeplain is already up to date (${NEW_VERSION})"
    else
        echo -e "${GREEN}✓${NC} codeplain upgraded from ${CURRENT_VERSION} to ${NEW_VERSION}!"
    fi
else
    echo -e "Installing codeplain...${NC}"
    echo -e ""
    uv tool install codeplain
    if [ "$NONINTERACTIVE" != "1" ]; then
        clear
    fi
    echo -e "${GREEN}✓ codeplain installed successfully!${NC}"
fi

# Check if API key already exists
SKIP_API_KEY_SETUP=false
API_KEY_VERIFIED=false
if [ -n "${CODEPLAIN_API_KEY:-}" ]; then
    USE_EXISTING=false
    if [ "$NONINTERACTIVE" = "1" ]; then
        USE_EXISTING=true
    else
        echo -e "  You already have an API key configured."
        echo ""
        echo -e "${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to log in and get a new one?${NC}"
        echo ""
        read -r -p " [y/N]: " GET_NEW_KEY < /dev/tty
        echo ""

        if [[ ! "$GET_NEW_KEY" =~ ^[Yy]$ ]]; then
            USE_EXISTING=true
        fi
    fi

    # Verify the existing key before trusting it, so a stale/expired key
    # doesn't slip through with a false "Sign in successful".
    if [ "$USE_EXISTING" = true ]; then
        echo -e "${GRAY}Verifying your existing API key...${NC}"
        if validate_api_key "$CODEPLAIN_API_KEY"; then
            echo -e "${GREEN}✓${NC} Using existing API key."
            SKIP_API_KEY_SETUP=true
            API_KEY_VERIFIED=true
        elif [ "$VALIDATION_HTTP_CODE" = "401" ]; then
            if [ "$NONINTERACTIVE" = "1" ]; then
                echo -e "${RED}Existing CODEPLAIN_API_KEY is invalid.${NC}"
                echo -e "${GRAY}Set a valid CODEPLAIN_API_KEY and re-run the installer.${NC}"
                SKIP_API_KEY_SETUP=true
            else
                echo -e "${RED}Your existing API key is invalid. Please enter a new one.${NC}"
                echo ""
                # SKIP_API_KEY_SETUP stays false: fall through to the paste flow.
            fi
        else
            # Could not reach the API. Keep the existing key rather than
            # blocking the user over a transient network issue.
            echo -e "${GRAY}Could not verify the existing API key (could not reach ${CODEPLAIN_API_URL}). Keeping it.${NC}"
            SKIP_API_KEY_SETUP=true
        fi
    fi
fi

if [ "$SKIP_API_KEY_SETUP" = false ]; then
    if [ "$NONINTERACTIVE" = "1" ]; then
        echo -e "${GRAY}No CODEPLAIN_API_KEY set; skipping key setup (non-interactive mode).${NC}"
    else
        echo -e "Go to ${YELLOW}https://platform.codeplain.ai${NC} and sign up to get your API key."
        echo ""
        # Keep prompting until we get a valid API key (or the user submits an
        # empty value to skip). The pasted key is trimmed and verified against
        # the Codeplain API before we accept it.
        while true; do
            read -r -p "Paste your API key here: " API_KEY < /dev/tty
            echo ""
            API_KEY="$(trim_whitespace "$API_KEY")"

            # Empty input: let the user skip key setup for now.
            if [ -z "$API_KEY" ]; then
                break
            fi

            echo -e "${GRAY}Verifying your API key...${NC}"
            if validate_api_key "$API_KEY"; then
                echo -e "${GREEN}✓${NC} API key verified."
                echo ""
                API_KEY_VERIFIED=true
                break
            elif [ "$VALIDATION_HTTP_CODE" = "401" ]; then
                echo -e "${RED}Invalid API key. Please make sure the full key was copied.${NC}"
                echo ""
            else
                echo -e "${RED}Could not verify the API key (could not reach ${CODEPLAIN_API_URL}).${NC}"
                echo -e "${GRAY}Check your internet connection and try again.${NC}"
                echo ""
            fi
        done
    fi
fi

if [ "$SKIP_API_KEY_SETUP" = true ]; then
    : # API key already set, nothing to do
elif [ -z "${API_KEY:-}" ]; then
    echo -e "${GRAY}No API key provided. You can set it later with:${NC}"
    echo -e "  export CODEPLAIN_API_KEY=\"your_api_key\""
else
    # Export for current session
    export CODEPLAIN_API_KEY="$API_KEY"

    # Detect user's default shell from $SHELL (works even when script runs in different shell)
    case "$SHELL" in
        */zsh)
            SHELL_RC="$HOME/.zshrc"
            ;;
        */bash)
            SHELL_RC="$HOME/.bashrc"
            ;;
        *)
            SHELL_RC="$HOME/.profile"
            ;;
    esac

    # Create the file if it doesn't exist
    touch "$SHELL_RC"

    # Add to shell config if not already present
    if ! grep -q "CODEPLAIN_API_KEY" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# codeplain API Key" >> "$SHELL_RC"
        echo "export CODEPLAIN_API_KEY=\"$API_KEY\"" >> "$SHELL_RC"
        echo -e "${GREEN}✓ API key saved to ${SHELL_RC}${NC}"
    else
        # Update existing key (different sed syntax for macOS vs Linux)
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|export CODEPLAIN_API_KEY=.*|export CODEPLAIN_API_KEY=\"$API_KEY\"|" "$SHELL_RC"
        else
            sed -i "s|export CODEPLAIN_API_KEY=.*|export CODEPLAIN_API_KEY=\"$API_KEY\"|" "$SHELL_RC"
        fi
    fi
fi

# ASCII Art Welcome
if [ "$NONINTERACTIVE" != "1" ]; then
    clear
fi
cat << 'EOF'
               _            _       _
   ___ ___   __| | ___ _ __ | | __ _(_)_ __
  / __/ _ \ / _` |/ _ \ '_ \| |/ _` | | '_ \
 | (_| (_) | (_| |  __/ |_) | | (_| | | | | |
  \___\___/ \__,_|\___| .__/|_|\__,_|_|_| |_|
                      |_|
EOF
echo ""
# Only claim success when a verified API key is actually configured.
if [ "$API_KEY_VERIFIED" = true ]; then
    echo -e "${GREEN}✓ Sign in successful.${NC}"
    echo ""
fi
echo -e "  ${WHITE}Welcome to *codeplain!${NC}"
echo ""
echo -e "  ${GRAY}Spec-driven, production-ready code generation${NC}"
echo ""
if [ "$NONINTERACTIVE" = "1" ]; then
    WALKTHROUGH_CHOICE="n"
else
    echo -e "${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to get a quick intro to ***plain specification language?${NC}"
    echo ""
    read -r -p "  [Y/n]: " WALKTHROUGH_CHOICE < /dev/tty
    echo ""
fi

# Determine script directory for local execution
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# Helper function to run a script (local or remote)
run_script() {
    local script_name="$1"
    local script_path=""

    # Check possible local paths
    if [ -n "$SCRIPT_DIR" ] && [ -f "${SCRIPT_DIR}/${script_name}" ]; then
        script_path="${SCRIPT_DIR}/${script_name}"
    elif [ -f "./install/${script_name}" ]; then
        script_path="./install/${script_name}"
    elif [ -f "./${script_name}" ]; then
        script_path="./${script_name}"
    fi

    if [ -n "$script_path" ]; then
        # Run locally
        bash "$script_path" < /dev/tty
    else
        # Download and run
        bash <(curl -fsSL "${CODEPLAIN_SCRIPTS_BASE_URL}/${script_name}") < /dev/tty
    fi
}

# Run walkthrough if user agrees
if [[ ! "$WALKTHROUGH_CHOICE" =~ ^[Nn]$ ]]; then
    run_script "walkthrough.sh"
fi

# Install plain-forge step
if [ "$NONINTERACTIVE" = "1" ]; then
    INSTALL_PLAIN_FORGE="n"
else
    clear
    echo ""
    echo -e "  ${YELLOW}${BOLD}plain-forge${NC}"
    echo ""
    echo -e "  plain-forge plugs into your AI coding agent (Claude Code, Codex,"
    echo -e "  ForgeCode, OpenCode, ...) and turns a conversation into a"
    echo -e "  complete ***plain spec, keeping it maintained as you grow it."
    echo ""
    echo -e "  ${GRAY}Read more: https://github.com/Codeplain-ai/plain-forge${NC}"
    echo ""
    echo -e "  ${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to install it now?${NC}"
    echo ""
    read -r -p "  [Y/n]: " INSTALL_PLAIN_FORGE < /dev/tty
    echo ""
fi

PLAIN_FORGE_INSTALLED=false
if [[ ! "${INSTALL_PLAIN_FORGE:-}" =~ ^[Nn]$ ]]; then
    if command -v npx &> /dev/null; then
        npx plain-forge install < /dev/tty
        PLAIN_FORGE_INSTALLED=true
        echo ""
    else
        echo -e "${GRAY}npx not found. Install Node.js, then run:${NC}"
        echo -e "  npx plain-forge install"
        echo ""
    fi
fi

# Install plyn editor extension step
EDITOR_CMDS=()
command -v cursor &> /dev/null && EDITOR_CMDS+=("cursor:Cursor")
command -v code &> /dev/null && EDITOR_CMDS+=("code:VS Code")

if [ "$NONINTERACTIVE" = "1" ]; then
    INSTALL_PLYN="n"
else
    clear
    echo ""
    echo -e "${GRAY}────────────────────────────────────────────${NC}"
    echo -e "  ${YELLOW}${BOLD}plyn Editor Extension${NC}"
    echo -e "${GRAY}────────────────────────────────────────────${NC}"
    echo ""
    echo -e "  plyn adds ***plain syntax highlighting to VS Code and Cursor."
    echo ""
    if [ "${#EDITOR_CMDS[@]}" -gt 0 ]; then
        EDITOR_NAMES=$(printf '%s, ' "${EDITOR_CMDS[@]#*:}")
        echo -e "  Detected ${EDITOR_NAMES%, }."
        echo ""
        echo -e "  ${YELLOW}?${NC} ${WHITE}${BOLD}Install the extension now?${NC}"
        echo ""
        read -r -p "  [Y/n]: " INSTALL_PLYN < /dev/tty
        echo ""
    else
        echo -e "  Install it manually from:"
        echo -e "  ${YELLOW}https://marketplace.visualstudio.com/items?itemName=Codeplain.plyn${NC}"
        echo ""
        INSTALL_PLYN="n"
    fi
fi

PLYN_INSTALLED=false
if [ "${#EDITOR_CMDS[@]}" -gt 0 ] && [[ ! "${INSTALL_PLYN:-}" =~ ^[Nn]$ ]]; then
    for entry in "${EDITOR_CMDS[@]}"; do
        editor_cmd="${entry%%:*}"
        editor_name="${entry#*:}"
        "$editor_cmd" --install-extension Codeplain.plyn
        echo -e "${GREEN}✓${NC} plyn installed for ${editor_name}"
    done
    PLYN_INSTALLED=true
    echo ""
fi

# Download examples step
if [ "$NONINTERACTIVE" = "1" ]; then
    DOWNLOAD_EXAMPLES="n"
else
    clear
    echo ""
    echo -e "  ${YELLOW}${BOLD}Example Projects${NC}"
    echo ""
    echo -e "  We've prepared some example ***plain projects for you"
    echo -e "  to explore and experiment with."
    echo ""
    echo -e "  ${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to download them?${NC}"
    echo ""
    read -r -p "  [Y/n]: " DOWNLOAD_EXAMPLES < /dev/tty
    echo ""
fi

# Run examples download if user agrees
if [[ ! "${DOWNLOAD_EXAMPLES:-}" =~ ^[Nn]$ ]]; then
    run_script "examples.sh"
fi

# Final verification: make sure the installed codeplain can actually run and
# reach the API. Only meaningful when an API key is configured, so users who
# skipped the key are not falsely told something went wrong.
if [ -n "${CODEPLAIN_API_KEY:-}" ]; then
    # Ensure the freshly installed tool is findable in this session.
    export PATH="$HOME/.local/bin:$PATH"
    echo -e "${GRAY}Verifying your installation...${NC}"
    if codeplain --status > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Installation verified."
    else
        echo -e "${RED}Something went wrong during installation.${NC}"
        echo -e "${GRAY}Please restart your terminal and try again, or reinstall with:${NC}"
        echo -e "  uv tool install --force codeplain"
        exit 1
    fi
fi

# Final message
if [ "$NONINTERACTIVE" != "1" ]; then
    clear
fi
echo ""
echo -e "  ${WHITE}${BOLD}You're all set!${NC}"
echo ""
echo -e "  ${GRAY}Thank you for using *codeplain!${NC}"
echo ""
echo -e "  ${WHITE}${BOLD}Next steps:${NC}"
echo ""
STEP_NUM=1
if [ "$PLAIN_FORGE_INSTALLED" = false ]; then
    echo -e "  ${GRAY}${STEP_NUM}.${NC} ${GRAY}Let your agent work in specs, not code:${NC}"
    echo -e "     ${WHITE}${BOLD}npx plain-forge install${NC}"
    echo ""
    STEP_NUM=$((STEP_NUM + 1))
fi
if [ "$PLYN_INSTALLED" = false ]; then
    echo -e "  ${GRAY}${STEP_NUM}.${NC} ${GRAY}Get ***plain syntax highlighting in VS Code / Cursor:${NC}"
    echo -e "     ${WHITE}${BOLD}https://marketplace.visualstudio.com/items?itemName=Codeplain.plyn${NC}"
    echo ""
    STEP_NUM=$((STEP_NUM + 1))
fi
echo -e "  ${GRAY}${STEP_NUM}.${NC} ${GRAY}Convert your spec into tested and validated code:${NC}"
echo -e "     ${WHITE}${BOLD}codeplain your-project.plain${NC}"
echo ""
echo -e "  ${GRAY}Discord: https://discord.gg/cgbynb9hFq   Docs: https://plainlang.org/${NC}"
echo ""
echo -e "  ${GRAY}Happy development!${NC} 🚀"
echo ""

if [ "$NONINTERACTIVE" != "1" ]; then
    # Replace this subshell with a fresh shell that has the new environment
    # Reconnect stdin to terminal (needed when running via curl | bash)
    exec "$SHELL" < /dev/tty
fi
