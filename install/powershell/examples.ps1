$ErrorActionPreference = 'Stop'

# Brand Colors (use exported colors if available, otherwise define them)
if (-not $env:YELLOW)     { $YELLOW      = "$([char]27)[38;2;224;255;110m" } else { $YELLOW      = $env:YELLOW }
if (-not $env:GREEN)      { $GREEN       = "$([char]27)[38;2;121;252;150m" } else { $GREEN       = $env:GREEN }
if (-not $env:WHITE)      { $WHITE       = "$([char]27)[38;2;255;255;255m" } else { $WHITE       = $env:WHITE }
if (-not $env:RED)        { $RED         = "$([char]27)[38;2;239;68;68m"   } else { $RED         = $env:RED }
if (-not $env:GRAY)       { $GRAY        = "$([char]27)[38;2;128;128;128m" } else { $GRAY        = $env:GRAY }
if (-not $env:BOLD)       { $BOLD        = "$([char]27)[1m"               } else { $BOLD        = $env:BOLD }
if (-not $env:NC)         { $NC          = "$([char]27)[0m"               } else { $NC          = $env:NC }

# Examples configuration
$EXAMPLES_FOLDER_NAME = "plainlang-examples"
$EXAMPLES_DOWNLOAD_URL = "https://codeplain.ai/examples/windows"

# Show current directory and ask for extraction path
$CURRENT_DIR = Get-Location
Write-Host "  Current folder: ${WHITE}${CURRENT_DIR}${NC}"
Write-Host ""
Write-Host "  Extract examples here, or enter a different path:"
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
    Write-Host "  ${GRAY}Creating directory...${NC}"
    try {
        New-Item -ItemType Directory -Path $EXTRACT_PATH -Force | Out-Null
    } catch {
        Write-Host "  ${RED}✗ Failed to create directory: ${EXTRACT_PATH}${NC}"
        Write-Host "  ${GRAY}Skipping example download.${NC}"
        $SKIP_DOWNLOAD = $true
    }
}

if (-not $SKIP_DOWNLOAD) {
    Write-Host "  ${GRAY}Downloading examples...${NC}"

    $TEMP_ZIP = Join-Path ([System.IO.Path]::GetTempPath()) "plainlang-examples.zip"

    try {
        Invoke-WebRequest -Uri $EXAMPLES_DOWNLOAD_URL -OutFile $TEMP_ZIP -UseBasicParsing

        if (Test-Path $TEMP_ZIP) {
            Write-Host "  ${GRAY}Extracting to ${EXTRACT_PATH}...${NC}"

            try {
                # Extract the zip file (contents are at the zip root, so extract into the target folder)
                $EXTRACTED_DIR = Join-Path $EXTRACT_PATH $EXAMPLES_FOLDER_NAME
                if (Test-Path $EXTRACTED_DIR) {
                    Remove-Item -Path $EXTRACTED_DIR -Recurse -Force -ErrorAction SilentlyContinue
                }
                Expand-Archive -Path $TEMP_ZIP -DestinationPath $EXTRACTED_DIR -Force

                # Remove the .gitignore file from the root of the extracted directory
                $GITIGNORE_PATH = Join-Path $EXTRACTED_DIR ".gitignore"
                if (Test-Path $GITIGNORE_PATH) {
                    Remove-Item -Path $GITIGNORE_PATH -Force
                }

                Clear-Host
                Write-Host ""
                Write-Host "  ${GREEN}✓ Examples downloaded successfully!${NC}"
                Write-Host ""
                Write-Host "  ${GRAY}Examples are in: ${EXTRACTED_DIR}${NC}"
                Write-Host ""
                Write-Host "  ${WHITE}${BOLD}Try the hello, world example:${NC}"
                Write-Host ""
                Write-Host "  ${GRAY}Example folder:${NC} ${WHITE}cd ${EXTRACTED_DIR}\hello-world\python${NC}"
                Write-Host ""
                Write-Host "  ${GRAY}Render the example:${NC} ${WHITE}codeplain hello-world-python.plain${NC}"
                Write-Host ""
                Write-Host "  ${GRAY}See hello-world/python/README.md for details.${NC}"
                Write-Host ""
            } catch {
                Write-Host "  ${RED}✗ Failed to extract examples.${NC}"
            }

            Remove-Item -Path $TEMP_ZIP -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "  ${RED}✗ Failed to download examples.${NC}"
        }
    } catch {
        Write-Host "  ${RED}✗ Failed to download examples.${NC}"
        Remove-Item -Path $TEMP_ZIP -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Read-Host "  Press ${WHITE}[Enter]${NC} to continue..."
}
