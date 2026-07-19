#!/bin/bash

UNRECOVERABLE_ERROR_EXIT_CODE=69

# Check if build folder name is provided
if [ -z "$1" ]; then
  printf "Error: No build folder name provided.\n"
  printf "Usage: $0 <build_folder_name> <conformance_tests_folder>\n"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

# Check if conformance tests folder name is provided
if [ -z "$2" ]; then
  printf "Error: No conformance tests folder name provided.\n"
  printf "Usage: $0 <build_folder_name> <conformance_tests_folder>\n"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

# Resolve the conformance tests folder to an absolute path so it can be used
# from the build subfolder (where we'll cd to next).
CONFORMANCE_TESTS_FOLDER=$(cd "$2" 2>/dev/null && pwd)

if [ -z "$CONFORMANCE_TESTS_FOLDER" ]; then
  printf "Error: Conformance tests folder '$2' does not exist.\n"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

GO_BUILD_SUBFOLDER="/tmp/go_$(basename "$1")"

trap 'rm -rf "$GO_BUILD_SUBFOLDER"' EXIT

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
  printf "Error: Go build folder '$GO_BUILD_SUBFOLDER' does not exist.\n"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

echo "Runinng go get in the build folder..."
go get

# Run a single conformance test suite located in $1. Expects the current
# working directory to be the build subfolder. Returns the suite's exit code.
run_suite() {
  suite_folder="$1"

  if [ -f "$suite_folder/go.mod" ]; then
    echo "Running go get in conformance test directory..."
    (cd "$suite_folder" && go get)
  else
    echo "No go.mod found in conformance test directory, skipping go get"
  fi

  output=$(go run "$suite_folder/conformance_tests.go" 2>&1)
  suite_exit_code=$?

  # If there was an error, print the output
  if [ $suite_exit_code -ne 0 ]; then
    echo "$output"
  fi

  return $suite_exit_code
}

printf "Running Golang conformance tests...\n\n"

if [ -f "$CONFORMANCE_TESTS_FOLDER/conformance_tests.go" ]; then
  # Single conformance test suite ("$2" is the suite folder itself).
  run_suite "$CONFORMANCE_TESTS_FOLDER"
  exit $?
fi

# "$2" is a folder of conformance test suites: run every non-hidden subfolder
# that contains a conformance_tests.go file.
suites_run=0
aggregated_exit_code=0

for suite_folder in "$CONFORMANCE_TESTS_FOLDER"/*/; do
  suite_name=$(basename "$suite_folder")

  case "$suite_name" in
    .*) continue ;;
  esac

  if [ ! -f "$suite_folder/conformance_tests.go" ]; then
    continue
  fi

  printf "=== conformance suite: %s ===\n" "$suite_name"

  run_suite "${suite_folder%/}"
  suite_exit_code=$?

  suites_run=$((suites_run + 1))

  # Keep running the remaining suites so the full set of failures is reported,
  # but exit with the first failing suite's exit code.
  if [ $suite_exit_code -ne 0 ] && [ $aggregated_exit_code -eq 0 ]; then
    aggregated_exit_code=$suite_exit_code
  fi
done

if [ $suites_run -eq 0 ]; then
  printf "\nError: No conformance test suites discovered.\n"
  exit 1
fi

exit $aggregated_exit_code
