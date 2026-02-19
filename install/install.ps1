$ErrorActionPreference = 'Stop'

# Base URL for additional scripts
if (-not $env:CODEPLAIN_SCRIPTS_BASE_URL) {
    $env:CODEPLAIN_SCRIPTS_BASE_URL = "https://codeplain.ai"
}

# Brand Colors (True Color / 24-bit)
$ESC = [char]27
$YELLOW     = "$ESC[38;2;224;255;110m"      # #E0FF6E
$GREEN      = "$ESC[38;2;121;252;150m"       # #79FC96
$GREEN_LIGHT= "$ESC[38;2;197;220;217m"       # #C5DCD9
$GREEN_DARK = "$ESC[38;2;34;57;54m"          # #223936
$BLUE       = "$ESC[38;2;10;31;212m"         # #0A1FD4
$BLACK      = "$ESC[38;2;26;26;26m"          # #1A1A1A
$WHITE      = "$ESC[38;2;255;255;255m"       # #FFFFFF
$RED        = "$ESC[38;2;239;68;68m"         # #EF4444
$GRAY       = "$ESC[38;2;128;128;128m"       # #808080
$GRAY_LIGHT = "$ESC[38;2;211;211;211m"       # #D3D3D3
$BOLD       = "$ESC[1m"
$NC         = "$ESC[0m"                      # No Color / Reset

# Export colors for child scripts (as environment variables)
$env:YELLOW = $YELLOW
$env:GREEN = $GREEN
$env:GREEN_LIGHT = $GREEN_LIGHT
$env:GREEN_DARK = $GREEN_DARK
$env:BLUE = $BLUE
$env:BLACK = $BLACK
$env:WHITE = $WHITE
$env:RED = $RED
$env:GRAY = $GRAY
$env:GRAY_LIGHT = $GRAY_LIGHT
$env:BOLD = $BOLD
$env:NC = $NC

Clear-Host
Write-Host "started ${YELLOW}${BOLD}*codeplain CLI${NC} installation..."

# Install uv if not present
function Install-Uv {
    Write-Host "installing uv package manager..."
    if ($IsWindows -or ($env:OS -eq "Windows_NT")) {
        irm https://astral.sh/uv/install.ps1 | iex
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    } else {
        bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
        $env:PATH = "$HOME/.local/bin:$env:PATH"
    }
}

# Check if uv is installed
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "${GRAY}uv is not installed.${NC}"
    Install-Uv
    Write-Host "${GREEN}✓${NC} uv installed successfully"
    Write-Host ""
}

Write-Host "${GREEN}✓${NC} uv detected"
Write-Host ""

# Install or upgrade codeplain using uv tool
$codeplainLine = @(uv tool list 2>$null) | Where-Object { $_ -match '^codeplain' } | Select-Object -First 1
if ($codeplainLine) {
    $currentVersion = ($codeplainLine -replace 'codeplain v', '').Trim()
    Write-Host "${GRAY}codeplain ${currentVersion} is already installed.${NC}"
    Write-Host "upgrading to latest version..."
    Write-Host ""
    uv tool upgrade codeplain 2>&1 | Out-Null
    $newLine = @(uv tool list 2>$null) | Where-Object { $_ -match '^codeplain' } | Select-Object -First 1
    $newVersion = ($newLine -replace 'codeplain v', '').Trim()
    if ($currentVersion -eq $newVersion) {
        Write-Host "${GREEN}✓${NC} codeplain is already up to date (${newVersion})"
    } else {
        Write-Host "${GREEN}✓${NC} codeplain upgraded from ${currentVersion} to ${newVersion}!"
    }
} else {
    Write-Host "installing codeplain...${NC}"
    Write-Host ""
    uv tool install codeplain
    Clear-Host
    Write-Host "${GREEN}✓ codeplain installed successfully!${NC}"
}

# Check if API key already exists
$skipApiKeySetup = $false
if ($env:CODEPLAIN_API_KEY) {
    Write-Host "  you already have an API key configured."
    Write-Host ""
    Write-Host "  would you like to log in and get a new one?"
    Write-Host ""
    $getNewKey = Read-Host "  [y/N]"
    Write-Host ""

    if ($getNewKey -notmatch '^[Yy]$') {
        Write-Host "${GREEN}✓${NC} using existing API key."
        $skipApiKeySetup = $true
    }
}

$apiKey = $null
if (-not $skipApiKeySetup) {
    Write-Host "go to ${YELLOW}https://platform.codeplain.ai${NC} and sign up to get your API key."
    Write-Host ""
    $apiKey = Read-Host "paste your API key here"
    Write-Host ""
}

if ($skipApiKeySetup) {
    # API key already set, nothing to do
} elseif (-not $apiKey) {
    Write-Host "${GRAY}no API key provided. you can set it later with:${NC}"
    Write-Host '  $env:CODEPLAIN_API_KEY = "your_api_key"'
} else {
    # Set for current session
    $env:CODEPLAIN_API_KEY = $apiKey

    # Persist as user environment variable (survives reboots)
    [Environment]::SetEnvironmentVariable('CODEPLAIN_API_KEY', $apiKey, 'User')
    Write-Host "${GREEN}✓ API key saved to user environment variables${NC}"
}

# ASCII Art Welcome
Clear-Host
Write-Host ""
Write-Host "${NC}"
Write-Host "${GRAY}────────────────────────────────────────────${NC}"
Write-Host ""
Write-Host @'
               _            _       _
   ___ ___   __| | ___ _ __ | | __ _(_)_ __
  / __/ _ \ / _` |/ _ \ '_ \| |/ _` | | '_ \
 | (_| (_) | (_| |  __/ |_) | | (_| | | | | |
  \___\___/ \__,_|\___| .__/|_|\__,_|_|_| |_|
                      |_|
'@
Write-Host ""
Write-Host "${GREEN}✓${NC} Sign in successful."
Write-Host ""
Write-Host "  ${YELLOW}welcome to *codeplain!${NC}"
Write-Host ""
Write-Host "  spec-driven, production-ready code generation"
Write-Host ""
Write-Host ""
Write-Host "${GRAY}────────────────────────────────────────────${NC}"
Write-Host ""
Write-Host "  would you like to get a quick intro to ***plain specification language?"
Write-Host ""
$walkthroughChoice = Read-Host "  [Y/n]"
Write-Host ""

# Determine script directory for local execution
$ScriptDir = ""
if ($PSScriptRoot) {
    $ScriptDir = $PSScriptRoot
}

# Helper function to run a script (local or remote)
function Invoke-SubScript {
    param([string]$ScriptName)

    $scriptPath = ""

    # Check possible local paths
    if ($ScriptDir -and (Test-Path (Join-Path $ScriptDir $ScriptName))) {
        $scriptPath = Join-Path $ScriptDir $ScriptName
    } elseif (Test-Path (Join-Path ".\install" $ScriptName)) {
        $scriptPath = Join-Path ".\install" $ScriptName
    } elseif (Test-Path ".\$ScriptName") {
        $scriptPath = ".\$ScriptName"
    }

    if ($scriptPath) {
        # Run locally
        & $scriptPath
    } else {
        # Download and run
        $tempFile = Join-Path ([System.IO.Path]::GetTempPath()) $ScriptName
        Invoke-WebRequest -Uri "${env:CODEPLAIN_SCRIPTS_BASE_URL}/${ScriptName}" -OutFile $tempFile -UseBasicParsing
        try {
            & $tempFile
        } finally {
            Remove-Item $tempFile -ErrorAction SilentlyContinue
        }
    }
}

# Run walkthrough if user agrees
if ($walkthroughChoice -notmatch '^[Nn]$') {
    Invoke-SubScript "walkthrough.ps1"
}

# Download examples step
Clear-Host
Write-Host ""
Write-Host "${GRAY}────────────────────────────────────────────${NC}"
Write-Host "  ${YELLOW}${BOLD}Example Projects${NC}"
Write-Host "${GRAY}────────────────────────────────────────────${NC}"
Write-Host ""
Write-Host "  we've prepared some example Plain projects for you"
Write-Host "  to explore and experiment with."
Write-Host ""
Write-Host "  would you like to download them?"
Write-Host ""
$downloadExamples = Read-Host "  [Y/n]"
Write-Host ""

# Run examples download if user agrees
if ($downloadExamples -notmatch '^[Nn]$') {
    Invoke-SubScript "examples.ps1"
}

# Final message
Clear-Host
Write-Host ""
Write-Host "${GRAY}────────────────────────────────────────────${NC}"
Write-Host "  ${YELLOW}${BOLD}You're all set!${NC}"
Write-Host "${GRAY}────────────────────────────────────────────${NC}"
Write-Host ""
Write-Host "  thank you for using ${YELLOW}*codeplain!${NC}"
Write-Host ""
Write-Host "  ${BOLD}next steps:${NC}"
Write-Host ""
Write-Host "  join our Discord community: ${YELLOW}https://discord.gg/4qQJaMu7Y${NC}"
Write-Host ""
Write-Host "  learn more about ${YELLOW}***plain${NC} at ${YELLOW}https://plainlang.org/${NC}"
Write-Host ""
Write-Host "  ${GREEN}happy development!${NC} 🚀"
Write-Host ""

# Refresh environment for this session
# Unlike bash's exec "$SHELL", PowerShell doesn't need to restart the shell.
# The API key is already set in $env:CODEPLAIN_API_KEY for this session,
# and persisted via [Environment]::SetEnvironmentVariable for new sessions.
# Refresh PATH to pick up uv tool installations.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
