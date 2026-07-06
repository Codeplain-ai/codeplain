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
EXAMPLES_DOWNLOAD_URL="https://github.com/Codeplain-ai/plainlang-examples/archive/refs/tags/0.1.zip"

# Show current directory and ask for extraction path
CURRENT_DIR=$(pwd)
echo -e "  Current folder: ${WHITE}${CURRENT_DIR}${NC}"
echo ""
echo -e "  Extract examples here, or enter a different path:"
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
    echo -e "  ${GRAY}Creating directory...${NC}"
    mkdir -p "$EXTRACT_PATH" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "  ${RED}✗ Failed to create directory: ${EXTRACT_PATH}${NC}"
        echo -e "  ${GRAY}Skipping example download.${NC}"
        SKIP_DOWNLOAD=true
    fi
fi

if [ "$SKIP_DOWNLOAD" = false ]; then
    echo -e "  ${GRAY}Downloading examples...${NC}"

    # Download the zip file
    TEMP_ZIP=$(mktemp)
    curl -L -s -o "$TEMP_ZIP" "$EXAMPLES_DOWNLOAD_URL"

    if [ $? -eq 0 ] && [ -s "$TEMP_ZIP" ]; then
        echo -e "  ${GRAY}Extracting to ${EXTRACT_PATH}...${NC}"

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
            echo -e "  ${GREEN}✓ Examples downloaded successfully!${NC}"
            echo ""
            echo -e "  Examples are in: ${WHITE}${EXTRACTED_DIR}${NC}"
            echo ""
        else
            echo -e "  ${RED}✗ Failed to extract examples.${NC}"
        fi

        # Clean up temp file
        rm -f "$TEMP_ZIP"
    else
        echo -e "  ${RED}✗ Failed to download examples.${NC}"
        rm -f "$TEMP_ZIP"
    fi

    echo ""
    PROMPT=$(printf '%b' "  Press ${WHITE}[Enter]${NC} to continue...")
    read -r -p "$PROMPT" < /dev/tty
fi
