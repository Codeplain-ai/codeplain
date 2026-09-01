$ErrorActionPreference = 'Stop'

# Brand Colors (use exported colors if available, otherwise define them)
if (-not $env:YELLOW)     { $YELLOW      = "$([char]27)[38;2;224;255;110m" } else { $YELLOW      = $env:YELLOW }
if (-not $env:GREEN)      { $GREEN       = "$([char]27)[38;2;121;252;150m" } else { $GREEN       = $env:GREEN }
if (-not $env:WHITE)      { $WHITE       = "$([char]27)[38;2;255;255;255m" } else { $WHITE       = $env:WHITE }
if (-not $env:RED)        { $RED         = "$([char]27)[38;2;239;68;68m"   } else { $RED         = $env:RED }
if (-not $env:GRAY)       { $GRAY        = "$([char]27)[38;2;128;128;128m" } else { $GRAY        = $env:GRAY }
if (-not $env:BOLD)       { $BOLD        = "$([char]27)[1m"               } else { $BOLD        = $env:BOLD }
if (-not $env:NC)         { $NC          = "$([char]27)[0m"               } else { $NC          = $env:NC }

# Symbols built from code points so this file stays pure ASCII. Windows PowerShell 5.1
# reads BOM-less files as ANSI (CP1252), where a literal U+2713 decodes to a smart quote
# that silently terminates the enclosing string and breaks parsing.
$CHECK = [char]0x2713
$CROSS = [char]0x2717

# Examples configuration
$EXAMPLES_FOLDER_NAME = "plainlang-examples"
$EXAMPLES_DOWNLOAD_URL = "https://codeplain.ai/examples/windows"

# Make the user acknowledge that the examples were not installed. The
# installation continues either way, but the failure must not scroll past
# unseen - the installer clears the screen in its next step.
function Confirm-ContinueAfterFailure {
    Write-Host ""
    Write-Host "  ${YELLOW}The examples were not installed.${NC} ${GRAY}The installation will continue.${NC}"
    Write-Host ""
    Read-Host "  Continue? Press ${WHITE}[Enter]${NC} to acknowledge"
    Write-Host ""
}

# Expand-Archive lives in the Microsoft.PowerShell.Archive module, shipped with
# PowerShell 5.0 and later. Checked before anything is downloaded, so a machine
# without it is told what to do instead of just seeing an extraction failure.
function Show-MissingArchiveCmdletMessage {
    Write-Host "  ${RED}${CROSS} Cannot extract the examples: Expand-Archive is not available.${NC}"
    Write-Host ""
    Write-Host "  ${GRAY}Expand-Archive requires PowerShell 5.0 or later. Install a newer${NC}"
    Write-Host "  ${GRAY}PowerShell and re-run the installer to get the examples:${NC}"
    Write-Host ""
    Write-Host "  ${WHITE}${BOLD}https://aka.ms/powershell${NC}"
    Write-Host ""
    Write-Host "  ${GRAY}The examples are also available at:${NC}"
    Write-Host "  ${WHITE}https://github.com/Codeplain-ai/plainlang-examples${NC}"
    Write-Host ""
}

if (-not (Get-Command Expand-Archive -ErrorAction SilentlyContinue)) {
    Show-MissingArchiveCmdletMessage
    Confirm-ContinueAfterFailure
    exit 0
}

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
        Write-Host "  ${RED}${CROSS} Failed to create directory: ${EXTRACT_PATH}${NC}"
        Confirm-ContinueAfterFailure
        $SKIP_DOWNLOAD = $true
    }
}

$EXAMPLES_INSTALLED = $false

if (-not $SKIP_DOWNLOAD) {
    Write-Host "  ${GRAY}Downloading examples...${NC}"

    $TEMP_ZIP = Join-Path ([System.IO.Path]::GetTempPath()) "plainlang-examples.zip"

    try {
        Invoke-WebRequest -Uri $EXAMPLES_DOWNLOAD_URL -OutFile $TEMP_ZIP -UseBasicParsing

        # Check for the zip magic bytes rather than handing Expand-Archive a
        # server error page. Read via a stream: 'Get-Content -Encoding Byte' is
        # Windows PowerShell 5.1 only, '-AsByteStream' is PowerShell 6+ only.
        $IS_ZIP = $false
        if (Test-Path $TEMP_ZIP) {
            $HEADER = New-Object byte[] 2
            $STREAM = [System.IO.File]::OpenRead($TEMP_ZIP)
            try {
                $BYTES_READ = $STREAM.Read($HEADER, 0, 2)
            } finally {
                $STREAM.Close()
            }
            $IS_ZIP = ($BYTES_READ -eq 2 -and $HEADER[0] -eq 0x50 -and $HEADER[1] -eq 0x4B)
        }

        if (-not $IS_ZIP -and (Test-Path $TEMP_ZIP)) {
            Write-Host "  ${RED}${CROSS} Failed to download examples.${NC}"
            Write-Host "  ${GRAY}The downloaded file is not a zip archive.${NC}"
            Remove-Item -Path $TEMP_ZIP -Force -ErrorAction SilentlyContinue
        } elseif ($IS_ZIP) {
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
                Write-Host "  ${GREEN}${CHECK} Examples downloaded successfully!${NC}"
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
                $EXAMPLES_INSTALLED = $true
            } catch {
                # Keep the cmdlet's own message: it is the only clue about why
                # extraction failed (corrupt download, no space, no permissions).
                Write-Host "  ${RED}${CROSS} Failed to extract examples.${NC}"
                Write-Host "  ${GRAY}$($_.Exception.Message)${NC}"
            }

            Remove-Item -Path $TEMP_ZIP -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "  ${RED}${CROSS} Failed to download examples.${NC}"
        }
    } catch {
        Write-Host "  ${RED}${CROSS} Failed to download examples.${NC}"
        Remove-Item -Path $TEMP_ZIP -Force -ErrorAction SilentlyContinue
    }

    if ($EXAMPLES_INSTALLED) {
        Write-Host ""
        Read-Host "  Press ${WHITE}[Enter]${NC} to continue..."
    } else {
        Confirm-ContinueAfterFailure
    }
}
