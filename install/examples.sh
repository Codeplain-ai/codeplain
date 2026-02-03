#!/bin/bash

set -euo pipefail

# Brand Colors (use exported colors if available, otherwise define them)
YELLOW="${YELLOW:-\033[38;2;224;255;110m}"
GREEN="${GREEN:-\033[38;2;121;252;150m}"
BLUE="${BLUE:-\033[38;2;10;31;212m}"
BLACK="${BLACK:-\033[38;2;26;26;26m}"
RED="${RED:-\033[38;2;239;68;68m}"
GRAY="${GRAY:-\033[38;2;128;128;128m}"
BOLD="${BOLD:-\033[1m}"
NC="${NC:-\033[0m}"

# Detect if terminal has a light background (if not already detected by parent script)
if [ -z "${TERM_BACKGROUND:-}" ]; then
    detect_light_background() {
        # Allow explicit override via environment variable
        if [ "${CODEPLAIN_LIGHT_THEME:-}" = "1" ] || [ "${CODEPLAIN_LIGHT_THEME:-}" = "true" ]; then
            return 0  # Light theme
        fi
        if [ "${CODEPLAIN_DARK_THEME:-}" = "1" ] || [ "${CODEPLAIN_DARK_THEME:-}" = "true" ]; then
            return 1  # Dark theme
        fi

        # Check COLORFGBG environment variable (format: "fg;bg")
        if [ -n "${COLORFGBG:-}" ]; then
            local bg="${COLORFGBG##*;}"
            case "$bg" in
                7|15) return 0 ;;  # White/bright white background = light
                0|8) return 1 ;;   # Black/dark gray background = dark
            esac
        fi

        # Default to dark background
        return 1
    }

    if detect_light_background; then
        TERM_BACKGROUND="light"
    else
        TERM_BACKGROUND="dark"
    fi
fi

# Set accent colors based on terminal background
# Light background: use BLUE+BOLD for success messages and links, BLACK+BOLD for highlights
# Dark background: use GREEN for checkmarks, YELLOW for links and highlights
if [ "$TERM_BACKGROUND" = "light" ]; then
    CHECK_COLOR="${CHECK_COLOR:-${BLUE}${BOLD}}"  # #0A1FD4 Bold for success messages
    LINK_COLOR="${LINK_COLOR:-$BLUE}"
    HIGHLIGHT_COLOR="${HIGHLIGHT_COLOR:-${BLACK:-\033[38;2;26;26;26m}${BOLD}}"  # Black + Bold
else
    CHECK_COLOR="${CHECK_COLOR:-$GREEN}"
    LINK_COLOR="${LINK_COLOR:-$YELLOW}"
    HIGHLIGHT_COLOR="${HIGHLIGHT_COLOR:-$YELLOW}"
fi

# Examples configuration
EXAMPLES_FOLDER_NAME="plainlang-examples"
EXAMPLES_DOWNLOAD_URL="https://github.com/Codeplain-ai/plainlang-examples/archive/refs/tags/0.1.zip"

# Show current directory and ask for extraction path
CURRENT_DIR=$(pwd)
echo -e "  current folder: ${LINK_COLOR}${CURRENT_DIR}${NC}"
echo ""
echo -e "  extract examples here, or enter a different path:"
echo ""
read -r -p "  [Enter for current, or type path]: " EXTRACT_PATH < /dev/tty
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
    echo -e "  ${GRAY}creating directory...${NC}"
    mkdir -p "$EXTRACT_PATH" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "  ${RED}✗${NC} failed to create directory: ${EXTRACT_PATH}"
        echo -e "  ${GRAY}skipping example download.${NC}"
        SKIP_DOWNLOAD=true
    fi
fi

if [ "$SKIP_DOWNLOAD" = false ]; then
    echo -e "  ${GRAY}downloading examples...${NC}"

    # Download the zip file
    TEMP_ZIP=$(mktemp)
    curl -L -s -o "$TEMP_ZIP" "$EXAMPLES_DOWNLOAD_URL"

    if [ $? -eq 0 ] && [ -s "$TEMP_ZIP" ]; then
        echo -e "  ${GRAY}extracting to ${EXTRACT_PATH}...${NC}"

        # Extract the zip file
        unzip -q -o "$TEMP_ZIP" -d "$EXTRACT_PATH" 2>/dev/null

        if [ $? -eq 0 ]; then
            # Find and rename extracted directory to remove version number
            EXTRACTED_DIR="${EXTRACT_PATH}/${EXAMPLES_FOLDER_NAME}"
            VERSIONED_DIR=$(find "$EXTRACT_PATH" -maxdepth 1 -type d -name "${EXAMPLES_FOLDER_NAME}-*" | head -1)
            if [ -n "$VERSIONED_DIR" ]; then
                rm -rf "$EXTRACTED_DIR" 2>/dev/null  # Remove existing if present
                mv "$VERSIONED_DIR" "$EXTRACTED_DIR"
            fi

            # Remove the .gitignore file from the root of the extracted directory
            if [ -f "${EXTRACTED_DIR}/.gitignore" ]; then
                rm -f "${EXTRACTED_DIR}/.gitignore"
            fi

            echo ""
            echo -e "  ${CHECK_COLOR}✓ examples downloaded successfully!${NC}"
            echo ""
            echo -e "  examples are in: ${LINK_COLOR}${EXTRACTED_DIR}${NC}"
            echo ""
        else
            echo -e "  ${RED}✗${NC} failed to extract examples."
        fi

        # Clean up temp file
        rm -f "$TEMP_ZIP"
    else
        echo -e "  ${RED}✗${NC} failed to download examples."
        rm -f "$TEMP_ZIP"
    fi

    echo ""
    read -r -p "  press [Enter] to continue..." < /dev/tty
fi
