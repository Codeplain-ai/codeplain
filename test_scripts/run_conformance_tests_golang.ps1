#!/usr/bin/env pwsh

$ErrorActionPreference = 'Stop'

$UNRECOVERABLE_ERROR_EXIT_CODE = 69

# Check if build folder name is provided
if (-not $args[0]) {
    Write-Host "Error: No build folder name provided."
    Write-Host "Usage: $($MyInvocation.MyCommand.Name) <build_folder_name> <conformance_tests_folder>"
    exit $UNRECOVERABLE_ERROR_EXIT_CODE
}

# Check if conformance tests folder name is provided
if (-not $args[1]) {
    Write-Host "Error: No conformance tests folder name provided."
    Write-Host "Usage: $($MyInvocation.MyCommand.Name) <build_folder_name> <conformance_tests_folder>"
    exit $UNRECOVERABLE_ERROR_EXIT_CODE
}

$BuildFolder = $args[0]
$ConformanceTestsFolder = $args[1]

$current_dir = Get-Location

# Resolve conformance tests folder to an absolute path so it can be used
# from the build subfolder (where we'll Push-Location to next).
if (-not [System.IO.Path]::IsPathRooted($ConformanceTestsFolder)) {
    $ConformanceTestsFolder = Join-Path $current_dir $ConformanceTestsFolder
}

if (-not (Test-Path $ConformanceTestsFolder)) {
    Write-Host "Error: Conformance tests folder '$ConformanceTestsFolder' does not exist."
    exit $UNRECOVERABLE_ERROR_EXIT_CODE
}

$GO_BUILD_SUBFOLDER = Join-Path ([System.IO.Path]::GetTempPath()) "go_$(Split-Path $BuildFolder -Leaf)"

if ($env:VERBOSE -eq "1") {
    Write-Host "Preparing Go build subfolder: $GO_BUILD_SUBFOLDER"
}

# Check if the go build subfolder exists
if (Test-Path $GO_BUILD_SUBFOLDER) {
    # Delete all files and folders inside
    Get-ChildItem -Path $GO_BUILD_SUBFOLDER -Force | Remove-Item -Recurse -Force

    if ($env:VERBOSE -eq "1") {
        Write-Host "Cleanup completed."
    }
} else {
    if ($env:VERBOSE -eq "1") {
        Write-Host "Subfolder does not exist. Creating it..."
    }

    New-Item -ItemType Directory -Path $GO_BUILD_SUBFOLDER -Force | Out-Null
}

Copy-Item -Path "$BuildFolder/*" -Destination $GO_BUILD_SUBFOLDER -Recurse -Force

# Move to the subfolder
if (-not (Test-Path $GO_BUILD_SUBFOLDER)) {
    Write-Host "Error: Go build folder '$GO_BUILD_SUBFOLDER' does not exist."
    exit $UNRECOVERABLE_ERROR_EXIT_CODE
}

# Run a single conformance test suite. Expects the current working directory
# to be the build subfolder. Returns the suite's exit code.
function Invoke-ConformanceSuite {
    param([string]$SuiteFolder)

    if (Test-Path (Join-Path $SuiteFolder "go.mod")) {
        Write-Host "Running go get in conformance test directory..."
        Push-Location $SuiteFolder
        try {
            go get
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "No go.mod found in conformance test directory, skipping go get"
    }

    # Temporarily allow stderr output without throwing (Go may write to stderr)
    # ForEach-Object converts ErrorRecord objects (from stderr) to plain strings to avoid verbose error formatting
    $script:ErrorActionPreference = 'Continue'
    $output = go run (Join-Path $SuiteFolder "conformance_tests.go") 2>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ } } | Out-String
    $suite_exit_code = $LASTEXITCODE
    $script:ErrorActionPreference = 'Stop'

    # If there was an error, print the output
    if ($suite_exit_code -ne 0) {
        Write-Host $output
    }

    return $suite_exit_code
}

Push-Location $GO_BUILD_SUBFOLDER

try {
    Write-Host "Runinng go get in the build folder..."
    go get

    # Execute Go lang conformance tests
    Write-Host "Running Golang conformance tests...`n"

    if (Test-Path (Join-Path $ConformanceTestsFolder "conformance_tests.go")) {
        # Single conformance test suite ($ConformanceTestsFolder is the suite folder itself).
        $exit_code = Invoke-ConformanceSuite $ConformanceTestsFolder
        exit $exit_code
    }

    # $ConformanceTestsFolder is a folder of conformance test suites: run every
    # non-hidden subfolder that contains a conformance_tests.go file.
    $suites_run = 0
    $aggregated_exit_code = 0

    $suite_folders = Get-ChildItem -Path $ConformanceTestsFolder -Directory |
        Where-Object { -not $_.Name.StartsWith(".") } |
        Sort-Object Name

    foreach ($suite in $suite_folders) {
        if (-not (Test-Path (Join-Path $suite.FullName "conformance_tests.go"))) {
            continue
        }

        Write-Host "=== conformance suite: $($suite.Name) ==="

        $suite_exit_code = Invoke-ConformanceSuite $suite.FullName
        $suites_run += 1

        # Keep running the remaining suites so the full set of failures is
        # reported, but exit with the first failing suite's exit code.
        if ($suite_exit_code -ne 0 -and $aggregated_exit_code -eq 0) {
            $aggregated_exit_code = $suite_exit_code
        }
    }

    if ($suites_run -eq 0) {
        Write-Host "`nError: No conformance test suites discovered."
        exit 1
    }

    exit $aggregated_exit_code
} finally {
    Pop-Location
    if (Test-Path $GO_BUILD_SUBFOLDER) {
        Remove-Item -Path $GO_BUILD_SUBFOLDER -Recurse -Force -ErrorAction SilentlyContinue
    }
}
