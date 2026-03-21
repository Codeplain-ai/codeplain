#!/bin/bash

UNRECOVERABLE_ERROR_EXIT_CODE=69

# ANSI escape code pattern to remove color codes and formatting from output
ANSI_ESCAPE_PATTERN="s/\x1b\[[0-9;]*[mK]//g"

# Ensures that if any command in the pipeline fails (like npm run build), the entire pipeline
# will return a non-zero status, allowing the if condition to properly catch failures.
set -o pipefail

# Check if subfolder name is provided
if [ -z "$1" ]; then
  echo "Error: No subfolder name provided."
  echo "Usage: $0 <subfolder_name>"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

# Define the path to the subfolder
NODE_SUBFOLDER=$(dirname "$1")/node_$(basename "$1")

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Preparing Node subfolder: $NODE_SUBFOLDER\n"
fi

# Check if the node subfolder exists
if [ -d "$NODE_SUBFOLDER" ]; then
  # Find and delete all files and folders except "node_modules", "build", and "package-lock.json"
  find "$NODE_SUBFOLDER" -mindepth 1 ! -path "$NODE_SUBFOLDER/node_modules*" ! -path "$NODE_SUBFOLDER/build*" ! -name "package-lock.json" -exec rm -rf {} +

  if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
    printf "Cleanup completed, keeping 'node_modules' and 'package-lock.json'.\n"
  fi
else
  if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
    printf "Subfolder does not exist. Creating it...\n"
  fi

  mkdir -p $NODE_SUBFOLDER
fi

cp -R "$1"/* $NODE_SUBFOLDER

# Move to the subfolder
cd "$NODE_SUBFOLDER" 2>/dev/null

if [ $? -ne 0 ]; then
  echo "Error: Subfolder '$1' does not exist."
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

# Install libraries
npm install

# Execute all React unittests in the subfolder
echo "Running React unittests in $1..."
npm test -- --runInBand --silent --detectOpenHandles 2>&1 | sed -E "$ANSI_ESCAPE_PATTERN"
TEST_EXIT_CODE=$?

# Check if tests failed
if [ $TEST_EXIT_CODE -ne 0 ]; then
  echo "Error: Tests failed with exit code $TEST_EXIT_CODE"
  exit $TEST_EXIT_CODE
fi

exit $TEST_EXIT_CODE