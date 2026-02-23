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

# Export colors for child scripts
export YELLOW GREEN GREEN_LIGHT GREEN_DARK BLUE BLACK WHITE RED GRAY GRAY_LIGHT BOLD NC

clear
echo -e "started ${YELLOW}${BOLD}*codeplain CLI${NC} installation..."

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
    echo -e "${GREEN}✓${NC} uv installed successfully"
    echo -e ""
fi

echo -e "${GREEN}✓${NC} uv detected"
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
        echo -e "${GREEN}✓${NC} codeplain is already up to date (${NEW_VERSION})"
    else
        echo -e "${GREEN}✓${NC} codeplain upgraded from ${CURRENT_VERSION} to ${NEW_VERSION}!"
    fi
else
    echo -e "installing codeplain...${NC}"
    echo -e ""
    uv tool install codeplain
    clear
    echo -e "${GREEN}✓ codeplain installed successfully!${NC}"
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
        echo -e "${GREEN}✓${NC} using existing API key."
        SKIP_API_KEY_SETUP=true
    fi
fi

if [ "$SKIP_API_KEY_SETUP" = false ]; then
    echo -e "go to ${YELLOW}https://platform.codeplain.ai${NC} and sign up to get your API key."
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
echo -e "${GREEN}✓${NC} Sign in successful."
echo ""
echo -e "  ${YELLOW}welcome to *codeplain!${NC}"
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
echo -e "  ${YELLOW}${BOLD}Example Projects${NC}"
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
echo -e "  ${YELLOW}${BOLD}You're all set!${NC}"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  thank you for using ${YELLOW}*codeplain!${NC}"
echo ""
echo -e "  ${BOLD}next steps:${NC}"
echo ""
echo -e "  join our Discord community: ${YELLOW}https://discord.gg/4qQJaMu7Y${NC}"
echo ""
echo -e "  learn more about ${YELLOW}***plain${NC} at ${YELLOW}https://plainlang.org/${NC}"
echo ""
echo -e "  ${GREEN}happy development!${NC} 🚀"
echo ""

# Replace this subshell with a fresh shell that has the new environment
# Reconnect stdin to terminal (needed when running via curl | bash)
exec "$SHELL" < /dev/tty
