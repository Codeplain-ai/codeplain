#!/bin/bash

set -euo pipefail

# Brand Colors (use exported colors if available, otherwise define them)
YELLOW="${YELLOW:-\033[38;2;224;255;110m}"
GREEN="${GREEN:-\033[38;2;121;252;150m}"
WHITE="${WHITE:-\033[38;2;255;255;255m}"
RED="${RED:-\033[38;2;239;68;68m}"
GRAY="${GRAY:-\033[38;2;128;128;128m}"
BOLD="${BOLD:-\033[1m}"
NC="${NC:-\033[0m}"

# Examples configuration
EXAMPLES_FOLDER_NAME="plainlang-examples"
EXAMPLES_DOWNLOAD_URL="https://codeplain.ai/examples/unix"

# Prompt the user, reading from the terminal when there is one. Falling back to
# stdin keeps the script usable in non-interactive environments instead of
# dying on an unreadable /dev/tty.
prompt_user() {
    local prompt="$1"
    local varname="$2"

    # Test that /dev/tty can actually be opened: it exists but fails to open
    # when the process has no controlling terminal.
    if { : < /dev/tty; } 2>/dev/null; then
        read -r -p "$prompt" "$varname" < /dev/tty || true
    else
        read -r -p "$prompt" "$varname" || true
    fi
}

# Make the user acknowledge that the examples were not installed. The
# installation continues either way, but the failure must not scroll past
# unseen - the installer clears the screen in its next step.
confirm_continue_after_failure() {
    echo ""
    echo -e "  ${YELLOW}The examples were not installed.${NC} ${GRAY}The installation will continue.${NC}"
    echo ""
    prompt_user "$(printf '%b' "  Continue? Press ${WHITE}[Enter]${NC} to acknowledge: ")" _ACKNOWLEDGED
    echo ""
}

# Tell the user how to get unzip on their system, then let the installer move on.
report_missing_unzip_tool() {
    echo -e "  ${RED}✗ Cannot extract the examples: no 'unzip' command found.${NC}"
    echo ""
    echo -e "  ${GRAY}Install unzip and re-run the installer to get the examples:${NC}"
    echo ""
    case "$OSTYPE" in
        darwin*)
            echo -e "  ${WHITE}${BOLD}brew install unzip${NC}"
            ;;
        *)
            echo -e "  ${WHITE}${BOLD}apt-get install unzip${NC}   ${GRAY}(Debian/Ubuntu)${NC}"
            echo -e "  ${WHITE}${BOLD}dnf install unzip${NC}       ${GRAY}(Fedora/RHEL)${NC}"
            echo -e "  ${WHITE}${BOLD}apk add unzip${NC}           ${GRAY}(Alpine)${NC}"
            ;;
    esac
    echo ""
    echo -e "  ${GRAY}The examples are also available at:${NC}"
    echo -e "  ${WHITE}https://github.com/Codeplain-ai/plainlang-examples${NC}"
    echo ""
}

# Checked before anything is downloaded, so a missing unzip is reported up
# front instead of leaving the user with an installer that just stops.
if ! command -v unzip &> /dev/null; then
    report_missing_unzip_tool
    confirm_continue_after_failure
    exit 0
fi

# Show current directory and ask for extraction path
CURRENT_DIR=$(pwd)
echo -e "  Current folder: ${WHITE}${CURRENT_DIR}${NC}"
echo ""
echo -e "  Extract examples here, or enter a different path:"
echo ""
prompt_user "  [Enter for current, or type path]: " EXTRACT_PATH
echo ""

# Use current directory if empty
if [ -z "${EXTRACT_PATH:-}" ]; then
    EXTRACT_PATH="$CURRENT_DIR"
fi

# Expand ~ to home directory
EXTRACT_PATH="${EXTRACT_PATH/#\~/$HOME}"

SKIP_DOWNLOAD=false

# Check if directory exists, create if not
if [ ! -d "$EXTRACT_PATH" ]; then
    echo -e "  ${GRAY}Creating directory...${NC}"
    if ! mkdir -p "$EXTRACT_PATH" 2>/dev/null; then
        echo -e "  ${RED}✗ Failed to create directory: ${EXTRACT_PATH}${NC}"
        confirm_continue_after_failure
        SKIP_DOWNLOAD=true
    fi
fi

EXAMPLES_INSTALLED=false

if [ "$SKIP_DOWNLOAD" = false ]; then
    echo -e "  ${GRAY}Downloading examples...${NC}"

    # Download the zip file
    TEMP_ZIP=$(mktemp)
    DOWNLOAD_OK=false
    DOWNLOAD_ERROR="Could not download ${EXAMPLES_DOWNLOAD_URL}"
    if curl -L -s -o "$TEMP_ZIP" "$EXAMPLES_DOWNLOAD_URL" && [ -s "$TEMP_ZIP" ]; then
        DOWNLOAD_OK=true
    fi

    # A server error page is a successful download of the wrong thing, so check
    # for the zip magic bytes rather than handing unzip an HTML page.
    if [ "$DOWNLOAD_OK" = true ] && [ "$(head -c 2 "$TEMP_ZIP")" != "PK" ]; then
        DOWNLOAD_OK=false
        DOWNLOAD_ERROR="The downloaded file is not a zip archive."
    fi

    if [ "$DOWNLOAD_OK" = true ]; then
        echo -e "  ${GRAY}Extracting to ${EXTRACT_PATH}...${NC}"

        # Extract the zip file (contents are at the zip root, so extract into the target folder)
        EXTRACTED_DIR="${EXTRACT_PATH}/${EXAMPLES_FOLDER_NAME}"
        rm -rf "$EXTRACTED_DIR" 2>/dev/null || true  # Remove existing if present

        # Keep the extractor's own error output: it is the only clue about why
        # extraction failed (corrupt download, no disk space, no permissions).
        if EXTRACT_OUTPUT=$(unzip -q -o "$TEMP_ZIP" -d "$EXTRACTED_DIR" 2>&1); then
            # Remove the .gitignore file from the root of the extracted directory
            if [ -f "${EXTRACTED_DIR}/.gitignore" ]; then
                rm -f "${EXTRACTED_DIR}/.gitignore"
            fi

            clear || true
            echo ""
            echo -e "  ${GREEN}✓ Examples downloaded successfully!${NC}"
            echo ""
            echo -e "  ${GRAY}Examples are in: ${EXTRACTED_DIR}${NC}"
            echo ""
            echo -e "  ${WHITE}${BOLD}Try the hello, world example:${NC}"
            echo ""
            echo -e "  ${GRAY}Example folder:${NC} ${WHITE}cd ${EXTRACTED_DIR}/hello-world/python${NC}"
            echo ""
            echo -e "  ${GRAY}Render the example:${NC} ${WHITE}codeplain hello-world-python.plain${NC}"
            echo ""
            echo -e "  ${GRAY}See hello-world/python/README.md for details.${NC}"
            echo ""
            EXAMPLES_INSTALLED=true
        else
            echo -e "  ${RED}✗ Failed to extract examples.${NC}"
            if [ -n "$EXTRACT_OUTPUT" ]; then
                echo -e "  ${GRAY}$(echo "$EXTRACT_OUTPUT" | tail -n 3)${NC}"
            fi
        fi

        # Clean up temp file
        rm -f "$TEMP_ZIP"
    else
        echo -e "  ${RED}✗ Failed to download examples.${NC}"
        echo -e "  ${GRAY}${DOWNLOAD_ERROR}${NC}"
        rm -f "$TEMP_ZIP"
    fi

    if [ "$EXAMPLES_INSTALLED" = true ]; then
        echo ""
        prompt_user "$(printf '%b' "  Press ${WHITE}[Enter]${NC} to continue...")" _CONTINUE
    else
        confirm_continue_after_failure
    fi
fi
