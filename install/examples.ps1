$ErrorActionPreference = 'Stop'

# Brand Colors (use exported colors if available, otherwise define them)
if (-not $env:YELLOW)     { $YELLOW      = "$([char]27)[38;2;224;255;110m" } else { $YELLOW      = $env:YELLOW }
if (-not $env:GREEN)      { $GREEN       = "$([char]27)[38;2;121;252;150m" } else { $GREEN       = $env:GREEN }
if (-not $env:RED)        { $RED         = "$([char]27)[38;2;239;68;68m"   } else { $RED         = $env:RED }
if (-not $env:GRAY)       { $GRAY        = "$([char]27)[38;2;128;128;128m" } else { $GRAY        = $env:GRAY }
if (-not $env:BOLD)       { $BOLD        = "$([char]27)[1m"               } else { $BOLD        = $env:BOLD }
if (-not $env:NC)         { $NC          = "$([char]27)[0m"               } else { $NC          = $env:NC }

# Examples configuration
$EXAMPLES_FOLDER_NAME = "plainlang-examples"
$EXAMPLES_DOWNLOAD_URL = "https://github.com/Codeplain-ai/plainlang-examples/archive/refs/tags/0.1.zip"

# Show current directory and ask for extraction path
$CURRENT_DIR = Get-Location
Write-Host "  current folder: ${YELLOW}${CURRENT_DIR}${NC}"
Write-Host ""
Write-Host "  extract examples here, or enter a different path:"
Write-Host ""
$EXTRACT_PATH = Read-Host "  [Enter for current, or type path]"
Write-Host ""

# Use current directory if empty
if (-not $EXTRACT_PATH) {
    $EXTRACT_PATH = "$CURRENT_DIR"
}

# Expand ~ to home directory
if ($EXTRACT_PATH.StartsWith("~")) {
    $EXTRACT_PATH = $EXTRACT_PATH -replace "^~", $HOME
}

$SKIP_DOWNLOAD = $false

# Check if directory exists, create if not
if (-not (Test-Path $EXTRACT_PATH -PathType Container)) {
    Write-Host "  ${GRAY}creating directory...${NC}"
    try {
        New-Item -ItemType Directory -Path $EXTRACT_PATH -Force | Out-Null
    } catch {
        Write-Host "  ${RED}✗${NC} failed to create directory: ${EXTRACT_PATH}"
        Write-Host "  ${GRAY}skipping example download.${NC}"
        $SKIP_DOWNLOAD = $true
    }
}

if (-not $SKIP_DOWNLOAD) {
    Write-Host "  ${GRAY}downloading examples...${NC}"

    $TEMP_ZIP = Join-Path ([System.IO.Path]::GetTempPath()) "plainlang-examples.zip"

    try {
        Invoke-WebRequest -Uri $EXAMPLES_DOWNLOAD_URL -OutFile $TEMP_ZIP -UseBasicParsing

        if (Test-Path $TEMP_ZIP) {
            Write-Host "  ${GRAY}extracting to ${EXTRACT_PATH}...${NC}"

            try {
                Expand-Archive -Path $TEMP_ZIP -DestinationPath $EXTRACT_PATH -Force

                # Find and rename extracted directory to remove version number
                $EXTRACTED_DIR = Join-Path $EXTRACT_PATH $EXAMPLES_FOLDER_NAME
                $VERSIONED_DIR = Get-ChildItem -Path $EXTRACT_PATH -Directory -Filter "${EXAMPLES_FOLDER_NAME}-*" | Select-Object -First 1

                if ($VERSIONED_DIR) {
                    if (Test-Path $EXTRACTED_DIR) {
                        Remove-Item -Path $EXTRACTED_DIR -Recurse -Force -ErrorAction SilentlyContinue
                    }
                    Rename-Item -Path $VERSIONED_DIR.FullName -NewName $EXAMPLES_FOLDER_NAME
                }

                # Remove the .gitignore file from the root of the extracted directory
                $GITIGNORE_PATH = Join-Path $EXTRACTED_DIR ".gitignore"
                if (Test-Path $GITIGNORE_PATH) {
                    Remove-Item -Path $GITIGNORE_PATH -Force
                }

                Write-Host ""
                Write-Host "  ${GREEN}✓${NC} examples downloaded successfully!"
                Write-Host ""
                Write-Host "  examples are in: ${YELLOW}${EXTRACTED_DIR}${NC}"
                Write-Host ""
            } catch {
                Write-Host "  ${RED}✗${NC} failed to extract examples."
            }

            Remove-Item -Path $TEMP_ZIP -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "  ${RED}✗${NC} failed to download examples."
        }
    } catch {
        Write-Host "  ${RED}✗${NC} failed to download examples."
        Remove-Item -Path $TEMP_ZIP -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Read-Host "  press [Enter] to continue..."
}
