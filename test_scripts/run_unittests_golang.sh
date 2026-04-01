#!/bin/bash

UNRECOVERABLE_ERROR_EXIT_CODE=69

# Check if subfolder name is provided
if [ -z "$1" ]; then
  echo "Error: No subfolder name provided."
  echo "Usage: $0 <subfolder_name>"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

GO_BUILD_SUBFOLDER=go_$1

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Preparing Go build subfolder: $GO_BUILD_SUBFOLDER\n"
fi

# Check if the go build subfolder exists
if [ -d "$GO_BUILD_SUBFOLDER" ]; then
  # Find and delete all files and folders
  find "$GO_BUILD_SUBFOLDER" -mindepth 1 -exec rm -rf {} +

  if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
    printf "Cleanup completed.\n"
  fi
else
  if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
    printf "Subfolder does not exist. Creating it...\n"
  fi

  mkdir -p $GO_BUILD_SUBFOLDER
fi

cp -R $1/* $GO_BUILD_SUBFOLDER

# Move to the subfolder
cd "$GO_BUILD_SUBFOLDER" 2>/dev/null

if [ $? -ne 0 ]; then
  clear
  printf "Error: Go build folder '$GO_BUILD_SUBFOLDER' does not exist.\n"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

echo "Running go get..."
go get

# Execute all Golang unittests in the subfolder
echo "Running Golang unittests in $1..."
go test
