#!/bin/bash

set -euo pipefail

# Base URL for additional scripts
CODEPLAIN_SCRIPTS_BASE_URL="${CODEPLAIN_SCRIPTS_BASE_URL:-https://codeplain.ai}"

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

# Detect if terminal has a light background
detect_light_background() {
    # Allow explicit override via environment variable
    if [ "${CODEPLAIN_LIGHT_THEME:-}" = "1" ] || [ "${CODEPLAIN_LIGHT_THEME:-}" = "true" ]; then
        return 0  # Light theme
    fi
    if [ "${CODEPLAIN_DARK_THEME:-}" = "1" ] || [ "${CODEPLAIN_DARK_THEME:-}" = "true" ]; then
        return 1  # Dark theme
    fi

    # Check COLORFGBG environment variable (format: "fg;bg")
    # Set by some terminals like xterm, rxvt, etc.
    if [ -n "${COLORFGBG:-}" ]; then
        local bg="${COLORFGBG##*;}"
        case "$bg" in
            7|15) return 0 ;;  # White/bright white background = light
            0|8) return 1 ;;   # Black/dark gray background = dark
        esac
    fi

    # Default to dark background (most common for terminals)
    # Use CODEPLAIN_LIGHT_THEME=1 to override for light terminals
    return 1
}

# Set accent colors based on terminal background
# Light background: use BLUE+BOLD for success messages and links, BLACK+BOLD for highlights
# Dark background: use GREEN for checkmarks, YELLOW for links and highlights
if detect_light_background; then
    TERM_BACKGROUND="light"
    CHECK_COLOR="${BLUE}${BOLD}"  # #0A1FD4 Bold for success messages
    LINK_COLOR="$BLUE"            # #0A1FD4
    HIGHLIGHT_COLOR="${BLACK}${BOLD}"  # Black + Bold for highlights (instead of yellow)
else
    TERM_BACKGROUND="dark"
    CHECK_COLOR="$GREEN"    # #79FC96
    LINK_COLOR="$YELLOW"    # #E0FF6E
    HIGHLIGHT_COLOR="$YELLOW"  # Yellow for highlights
fi

# Export colors for child scripts
export YELLOW GREEN GREEN_LIGHT GREEN_DARK BLUE BLACK WHITE RED GRAY GRAY_LIGHT BOLD NC
export TERM_BACKGROUND CHECK_COLOR LINK_COLOR HIGHLIGHT_COLOR

clear
echo -e "started ${HIGHLIGHT_COLOR}*codeplain CLI${NC} installation..."

# Install uv if not present
install_uv() {
    echo -e "installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for this session
    export PATH="$HOME/.local/bin:$PATH"
}

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo -e "${GRAY}uv is not installed.${NC}"
    install_uv
    echo -e "${CHECK_COLOR}✓ uv installed successfully${NC}"
    echo -e ""
fi

echo -e "${CHECK_COLOR}✓ uv detected${NC}"
echo -e ""

# Install or upgrade codeplain using uv tool
if uv tool list 2>/dev/null | grep -q "^codeplain"; then
    CURRENT_VERSION=$(uv tool list 2>/dev/null | grep "^codeplain" | sed 's/codeplain v//')
    echo -e "${GRAY}codeplain ${CURRENT_VERSION} is already installed.${NC}"
    echo -e "upgrading to latest version..."
    echo -e ""
    uv tool upgrade codeplain &> /dev/null
    NEW_VERSION=$(uv tool list 2>/dev/null | grep "^codeplain" | sed 's/codeplain v//')
    if [ "$CURRENT_VERSION" = "$NEW_VERSION" ]; then
        echo -e "${CHECK_COLOR}✓ codeplain is already up to date (${NEW_VERSION})${NC}"
    else
        echo -e "${CHECK_COLOR}✓ codeplain upgraded from ${CURRENT_VERSION} to ${NEW_VERSION}!${NC}"
    fi
else
    echo -e "installing codeplain...${NC}"
    echo -e ""
    uv tool install codeplain
    clear
    echo -e "${CHECK_COLOR}✓ codeplain installed successfully!${NC}"
fi

# Check if API key already exists
SKIP_API_KEY_SETUP=false
if [ -n "${CODEPLAIN_API_KEY:-}" ]; then
    echo -e "  you already have an API key configured."
    echo ""
    echo -e "  would you like to log in and get a new one?"
    echo ""
    read -r -p "  [y/N]: " GET_NEW_KEY < /dev/tty
    echo ""

    if [[ ! "$GET_NEW_KEY" =~ ^[Yy]$ ]]; then
        echo -e "${CHECK_COLOR}✓ using existing API key.${NC}"
        SKIP_API_KEY_SETUP=true
    fi
fi

if [ "$SKIP_API_KEY_SETUP" = false ]; then
    echo -e "go to ${LINK_COLOR}https://platform.codeplain.ai${NC} and sign up to get your API key."
    echo ""
    read -r -p "paste your API key here: " API_KEY < /dev/tty
    echo ""
fi

if [ "$SKIP_API_KEY_SETUP" = true ]; then
    : # API key already set, nothing to do
elif [ -z "${API_KEY:-}" ]; then
    echo -e "${GRAY}no API key provided. you can set it later with:${NC}"
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
        echo -e "${CHECK_COLOR}✓ API key saved to ${SHELL_RC}${NC}"
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
clear
echo ""
echo -e "${NC}"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e ""
cat << 'EOF'
               _            _       _
   ___ ___   __| | ___ _ __ | | __ _(_)_ __
  / __/ _ \ / _` |/ _ \ '_ \| |/ _` | | '_ \
 | (_| (_) | (_| |  __/ |_) | | (_| | | | | |
  \___\___/ \__,_|\___| .__/|_|\__,_|_|_| |_|
                      |_|
EOF
echo ""
echo -e "${CHECK_COLOR}✓ Sign in successful.${NC}"
echo ""
echo -e "  ${HIGHLIGHT_COLOR}welcome to *codeplain!${NC}"
echo ""
echo -e "  spec-driven, production-ready code generation"
echo ""
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  would you like to get a quick intro to ***plain specification language?"
echo ""
read -r -p "  [Y/n]: " WALKTHROUGH_CHOICE < /dev/tty
echo ""

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

# Download examples step
clear
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e "  ${HIGHLIGHT_COLOR}Example Projects${NC}"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  we've prepared some example Plain projects for you"
echo -e "  to explore and experiment with."
echo ""
echo -e "  would you like to download them?"
echo ""
read -r -p "  [Y/n]: " DOWNLOAD_EXAMPLES < /dev/tty
echo ""

# Run examples download if user agrees
if [[ ! "${DOWNLOAD_EXAMPLES:-}" =~ ^[Nn]$ ]]; then
    run_script "examples.sh"
fi

# Final message
clear
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e "  ${HIGHLIGHT_COLOR}You're all set!${NC}"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  thank you for using *codeplain!"
echo ""
echo -e "  learn more at ${LINK_COLOR}https://plainlang.org/${NC}"
echo ""
echo -e "  ${CHECK_COLOR}happy development!${NC} 🚀"
echo ""

# Replace this subshell with a fresh shell that has the new environment
# Reconnect stdin to terminal (needed when running via curl | bash)
exec "$SHELL" < /dev/tty
