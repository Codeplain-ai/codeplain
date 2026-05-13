#!/usr/bin/env pwsh

$ErrorActionPreference = 'Stop'

$UNRECOVERABLE_ERROR_EXIT_CODE = 69

# Check if subfolder name is provided
if (-not $args[0]) {
    Write-Host "Error: No subfolder name provided."
    Write-Host "Usage: $($MyInvocation.MyCommand.Name) <subfolder_name>"
    exit $UNRECOVERABLE_ERROR_EXIT_CODE
}

$BuildFolder = $args[0]

$GO_BUILD_SUBFOLDER = ".tmp/$BuildFolder"

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

Push-Location $GO_BUILD_SUBFOLDER

try {
    Write-Host "Running go get..."
    go get

    # Execute all Golang unittests in the subfolder
    Write-Host "Running Golang unittests in $BuildFolder..."
    go test
} finally {
    Pop-Location
}
