#!/bin/bash

UNRECOVERABLE_ERROR_EXIT_CODE=69

NPM_INSTALL_OUTPUT_FILTER="up to date in|added [0-9]* packages, removed [0-9]* packages, and changed [0-9]* packages in|removed [0-9]* packages, and changed [0-9]* packages in|added [0-9]* packages in|removed [0-9]* packages in"

# Function to check and kill any Node process running on port 3000 (React development server)
check_and_kill_node_server() {
    # Find process listening on port 3000
    local pid=$(lsof -i :3000 -t 2>/dev/null)
    if [ ! -z "$pid" ]; then
        if ps -p $pid -o comm= | grep -q "node"; then
            printf "Found Node server running on port 3000. Killing it...\n"
            kill $pid 2>/dev/null
            sleep 1  # Give the process time to terminate
            if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
                printf "Node server terminated.\n"
            fi
        fi
    fi
}

# Function to get all child processes of a given PID and store them in a list
get_children() {
    local parent_pid=$1
    local children=$(pgrep -P $parent_pid)

    for child in $children
    do
        # Add the child process to the list
        processes_to_kill+=($child)
        # Recursively find the children of the child process
        get_children $child
    done
}

# Cleanup function to ensure all processes are terminated
cleanup() {
    # Kill any running npm processes started by this script
    if [ ! -z "${NPM_PID+x}" ]; then
        pkill -P $NPM_PID 2>/dev/null
        kill $NPM_PID 2>/dev/null
    fi

    # Kill React app and its children if they exist
    if [ ! -z "${REACT_APP_PID+x}" ]; then
        local processes_to_kill=()
        get_children $REACT_APP_PID

        # Kill the main process
        kill $REACT_APP_PID 2>/dev/null

        # Kill all the subprocesses
        for pid in "${processes_to_kill[@]}"
        do
            kill $pid 2>/dev/null
        done

        if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
            printf "React app is terminated!\n"
        fi
    fi

    # Remove temporary files if they exist
    [ -f "$build_output" ] && rm "$build_output" 2>/dev/null
}

# Set up trap to call cleanup function on script exit, interrupt, or termination
trap cleanup EXIT SIGINT SIGTERM

# Check for and kill any existing Node server from previous runs
check_and_kill_node_server

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

if [[ "$3" == "-v" || "$3" == "--verbose" ]]; then
  VERBOSE=1
fi

# Ensures that if any command in the pipeline fails (like npm run build), the entire pipeline
# will return a non-zero status, allowing the if condition to properly catch failures.
set -o pipefail

# Define the path to the subfolder
NODE_SUBFOLDER=$(dirname "$1")/node_$(basename "$1")

# Running React application
printf "### Step 1: Starting the React application in folder $NODE_SUBFOLDER...\n"

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Preparing Node subfolder: $NODE_SUBFOLDER\n"
fi

# Check if the node subfolder exists
if [ -d "$NODE_SUBFOLDER" ]; then
  # Find and delete all files and folders except "node_modules", "plain_modules", and "package-lock.json"
  find "$NODE_SUBFOLDER" -mindepth 1 ! -path "$NODE_SUBFOLDER/node_modules*" ! -path "$NODE_SUBFOLDER/plain_modules*" ! -name "package-lock.json" -exec rm -rf {} +

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
  printf "Error: Node build folder '$NODE_SUBFOLDER' does not exist.\n"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

npm install --prefer-offline --no-audit --no-fund --loglevel error | grep -Ev "$NPM_INSTALL_OUTPUT_FILTER"

if [ $? -ne 0 ]; then
  printf "Error: Installing Node modules.\n"
  exit 2
fi

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Building the application...\n"
fi

build_output=$(mktemp)

npm run build --loglevel silent > "$build_output" 2>&1

if [ $? -ne 0 ]; then
  printf "Error: Building application.\n"
  cat "$build_output"
  rm "$build_output"
  exit 2
fi

rm "$build_output"

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Starting the application...\n"
fi

# Start the React app in the background and redirect output to a log file
BROWSER=none npm start -- --no-open > app.log 2>&1 &

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Application is starting...\n"
fi

# Capture the process ID of the npm start command
REACT_APP_PID=$!
NPM_PID=$(pgrep -P $REACT_APP_PID npm)

# Wait for the "compiled successfully!" message in the log file
while true; do
  if grep -iq -E "compiled successfully|compiled with warnings" app.log; then
    break
  fi

  if grep -iq -E "VITE v[0-9]+\.[0-9]+\.[0-9]+[[:space:]]+ready in" app.log; then
    break
  fi

  if curl -s http://localhost:3000 >/dev/null 2>&1; then
    break
  fi

   # Check if the React app process is still running
  if ! ps -p $REACT_APP_PID > /dev/null 2>&1; then
    printf "Error in :ImplementationCode: (React app process (PID: $REACT_APP_PID) has terminated unexpectedly).\n"
    cat "app.log"
    exit 2
  fi

  sleep 0.1
done

# At this point, the React app is up and running in the background
if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "React app is up and running!\n"
fi

# Execute all Cypress conformance tests in the build folder
printf "### Step 2: Running Cypress conformance tests $2...\n"

# Define the path to the conformance tests subfolder
NODE_CONFORMANCE_TESTS_SUBFOLDER=$(dirname "$2")/node_$(basename "$2")

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Preparing conformance tests Node subfolder: $NODE_CONFORMANCE_TESTS_SUBFOLDER\n"
fi

# Check if the conformance tests node subfolder exists
if [ -d "$NODE_CONFORMANCE_TESTS_SUBFOLDER" ]; then
  # Find and delete all files and folders except "node_modules", "plain_modules", and "package-lock.json"
  find "$NODE_CONFORMANCE_TESTS_SUBFOLDER" -mindepth 1 ! -path "$NODE_CONFORMANCE_TESTS_SUBFOLDER/node_modules*" ! -path "$NODE_CONFORMANCE_TESTS_SUBFOLDER/plain_modules*" ! -name "package-lock.json" -exec rm -rf {} +

  if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
    printf "Cleanup completed, keeping 'node_modules' and 'package-lock.json'.\n"
  fi
else
  if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
    printf "Subfolder does not exist. Creating it...\n"
  fi

  mkdir -p $NODE_CONFORMANCE_TESTS_SUBFOLDER
fi

cp -R "$2"/* $NODE_CONFORMANCE_TESTS_SUBFOLDER

# Move to the subfolder with Cypress tests
cd "$NODE_CONFORMANCE_TESTS_SUBFOLDER" 2>/dev/null

if [ $? -ne 0 ]; then
  printf "Error: conformance tests Node folder '$NODE_CONFORMANCE_TESTS_SUBFOLDER' does not exist.\n"
  exit $UNRECOVERABLE_ERROR_EXIT_CODE
fi

npm install cypress --save-dev --prefer-offline --no-audit --no-fund --loglevel error | grep -Ev "$NPM_INSTALL_OUTPUT_FILTER"

if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
  printf "Running Cypress conformance tests...\n"
fi

BROWSERSLIST_IGNORE_OLD_DATA=1 npx cypress run --browser=chrome --config video=false 2>/dev/null
cypress_run_result=$?

if [ $cypress_run_result -ne 0 ]; then
  if [ "${VERBOSE:-}" -eq 1 ] 2>/dev/null; then
    printf "Error: Cypress conformance tests have failed.\n"
  fi
  exit 1
fi