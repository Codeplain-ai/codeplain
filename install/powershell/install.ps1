$ErrorActionPreference = 'Stop'

# Non-interactive mode for unattended installs (CI, scripted setup). Skips all
# prompts and Clear-Host calls. Same env var name as install.sh.
$nonInteractive = ($env:CODEPLAIN_INSTALL_NONINTERACTIVE -eq "1")
if ($nonInteractive) {
    Write-Host "Running in non-interactive mode (CODEPLAIN_INSTALL_NONINTERACTIVE=1)"
}

# Base URL for additional scripts
if (-not $env:CODEPLAIN_SCRIPTS_BASE_URL) {
    $env:CODEPLAIN_SCRIPTS_BASE_URL = "https://codeplain.ai"
}

# Base URL for the Codeplain API (used to verify the API key)
if (-not $env:CODEPLAIN_API_URL) {
    $env:CODEPLAIN_API_URL = "https://api.codeplain.ai"
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

if (-not $nonInteractive) { Clear-Host }
Write-Host "Started ${YELLOW}${BOLD}*codeplain CLI${NC} installation..."

# Install uv if not present
function Install-Uv {
    Write-Host "Installing uv package manager..."
    if ($IsWindows -or ($env:OS -eq "Windows_NT")) {
        irm https://astral.sh/uv/install.ps1 | iex
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    } else {
        bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
        $env:PATH = "$HOME/.local/bin:$env:PATH"
    }
}

# Verify an API key against the Codeplain API's /status endpoint.
# Returns a status string: "valid" (HTTP 200), "invalid" (HTTP 401), or
# "error" (could not reach the API). This checks only the API key.
function Test-ApiKey {
    param([string]$Key)

    $body = @{ api_key = $Key } | ConvertTo-Json -Compress
    try {
        $response = Invoke-WebRequest -Uri "$($env:CODEPLAIN_API_URL)/status" `
            -Method Post `
            -ContentType "application/json" `
            -Body $body `
            -TimeoutSec 30 `
            -UseBasicParsing `
            -ErrorAction Stop
        if ($response.StatusCode -eq 200) { return "valid" }
        return "error"
    } catch {
        $statusCode = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -eq 401) { return "invalid" }
        return "error"
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

try {
    $uvOutput = uv tool list 2>$null
} catch {
    $uvOutput = @()
}

# Install or upgrade codeplain using uv tool
$codeplainLine = $uvOutput | Where-Object { $_ -match '^codeplain' } | Select-Object -First 1
if ($codeplainLine) {
    $currentVersion = ($codeplainLine -replace 'codeplain v', '').Trim()
    Write-Host "${GRAY}codeplain ${currentVersion} is already installed.${NC}"
    Write-Host "Upgrading to latest version..."
    Write-Host ""

    try {
    $upgradeOutput = & uv tool upgrade codeplain 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "uv exited with code $LASTEXITCODE"
    }
    } catch {
        Write-Host "${RED}Failed to upgrade codeplain.${NC}"
        Write-Host $_
    }
    $newLine = @(uv tool list 2>$null) | Where-Object { $_ -match '^codeplain' } | Select-Object -First 1
    $newVersion = ($newLine -replace 'codeplain v', '').Trim()
    if ($currentVersion -eq $newVersion) {
        Write-Host "${GREEN}✓${NC} codeplain is already up to date (${newVersion})"
    } else {
        Write-Host "${GREEN}✓${NC} codeplain upgraded from ${currentVersion} to ${newVersion}!"
    }
} else {
    Write-Host "Installing codeplain...${NC}"
    Write-Host ""
    uv tool install codeplain
    if (-not $nonInteractive) { Clear-Host }
    Write-Host "${GREEN}✓ codeplain installed successfully!${NC}"
}

# Ensure uv tool bin directory is on user PATH permanently (so codeplain is available)
$uvBinDir = Join-Path $env:USERPROFILE '.local\bin'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
    $pathEntries = $userPath -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    if ($uvBinDir -notin $pathEntries) {
        $newPath = ($userPath.TrimEnd(';') + ';' + $uvBinDir)
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        $env:Path = $uvBinDir + ';' + $env:Path
        Write-Host "${GREEN}✓${NC} added $uvBinDir to your user PATH"
    }
} else {
    [Environment]::SetEnvironmentVariable('Path', $uvBinDir, 'User')
    $env:Path = $uvBinDir + ';' + $env:Path
    Write-Host "${GREEN}✓${NC} added $uvBinDir to your user PATH"
}
Write-Host ""

# Check if API key already exists
$skipApiKeySetup = $false
if ($env:CODEPLAIN_API_KEY) {
    if ($nonInteractive) {
        Write-Host "${GREEN}✓${NC} Using existing CODEPLAIN_API_KEY (non-interactive mode)."
        $skipApiKeySetup = $true
    } else {
        Write-Host "  You already have an API key configured."
        Write-Host ""
        Write-Host "  ${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to log in and get a new one?${NC}"
        Write-Host ""
        $getNewKey = Read-Host "  [y/N]"
        Write-Host ""

        if ($getNewKey -notmatch '^[Yy]$') {
            Write-Host "${GREEN}✓${NC} Using existing API key."
            $skipApiKeySetup = $true
        }
    }
}

$apiKey = $null
if (-not $skipApiKeySetup) {
    if ($nonInteractive) {
        Write-Host "${GRAY}No CODEPLAIN_API_KEY set; skipping key setup (non-interactive mode).${NC}"
    } else {
        Write-Host "Go to ${YELLOW}https://platform.codeplain.ai${NC} and sign up to get your API key."
        Write-Host ""
        # Keep prompting until we get a valid API key (or the user submits an
        # empty value to skip). The pasted key is trimmed and verified against
        # the Codeplain API before we accept it.
        while ($true) {
            $apiKey = (Read-Host "Paste your API key here").Trim()
            Write-Host ""

            # Empty input: let the user skip key setup for now.
            if (-not $apiKey) {
                break
            }

            Write-Host "${GRAY}Verifying your API key...${NC}"
            $result = Test-ApiKey $apiKey
            if ($result -eq "valid") {
                Write-Host "${GREEN}✓${NC} API key verified."
                Write-Host ""
                break
            } elseif ($result -eq "invalid") {
                Write-Host "${RED}Invalid API key. Please make sure the full key was copied.${NC}"
                Write-Host ""
            } else {
                Write-Host "${RED}Could not verify the API key (could not reach $($env:CODEPLAIN_API_URL)).${NC}"
                Write-Host "${GRAY}Check your internet connection and try again.${NC}"
                Write-Host ""
            }
        }
    }
}

if ($skipApiKeySetup) {
    # API key already set, nothing to do
} elseif (-not $apiKey) {
    Write-Host "${GRAY}No API key provided. You can set it later with:${NC}"
    Write-Host '  $env:CODEPLAIN_API_KEY = "your_api_key"'
} else {
    # Set for current session
    $env:CODEPLAIN_API_KEY = $apiKey

    # Persist as user environment variable (survives reboots)
    [Environment]::SetEnvironmentVariable('CODEPLAIN_API_KEY', $apiKey, 'User')
    Write-Host "${GREEN}✓ API key saved to user environment variables${NC}"
}

# ASCII Art Welcome
if (-not $nonInteractive) { Clear-Host }
Write-Host @'
               _            _       _
   ___ ___   __| | ___ _ __ | | __ _(_)_ __
  / __/ _ \ / _` |/ _ \ '_ \| |/ _` | | '_ \
 | (_| (_) | (_| |  __/ |_) | | (_| | | | | |
  \___\___/ \__,_|\___| .__/|_|\__,_|_|_| |_|
                      |_|
'@
Write-Host ""
# Only claim success when a verified API key is actually configured.
if ($env:CODEPLAIN_API_KEY) {
    Write-Host "${GREEN}✓ Sign in successful.${NC}"
    Write-Host ""
}
Write-Host "  ${WHITE}Welcome to *codeplain!${NC}"
Write-Host ""
Write-Host "  ${GRAY}Spec-driven, production-ready code generation${NC}"
Write-Host ""
if ($nonInteractive) {
    $walkthroughChoice = "n"
} else {
    Write-Host "  ${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to get a quick intro to ***plain specification language?${NC}"
    Write-Host ""
    $walkthroughChoice = Read-Host "  [Y/n]"
    Write-Host ""
}

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

# Install plain-forge step
if ($nonInteractive) {
    $installPlainForge = "n"
} else {
    Clear-Host
    Write-Host ""
    Write-Host "  ${YELLOW}${BOLD}plain-forge${NC}"
    Write-Host ""
    Write-Host "  plain-forge plugs into your AI coding agent (Claude Code, Codex,"
    Write-Host "  ForgeCode, OpenCode, ...) and turns a conversation into a"
    Write-Host "  complete ***plain spec, keeping it maintained as you grow it."
    Write-Host ""
    Write-Host "  ${GRAY}Read more: https://github.com/Codeplain-ai/plain-forge${NC}"
    Write-Host ""
    Write-Host "  ${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to install it now?${NC}"
    Write-Host ""
    $installPlainForge = Read-Host "  [Y/n]"
    Write-Host ""
}

$plainForgeInstalled = $false
if ($installPlainForge -notmatch '^[Nn]$') {
    if (Get-Command npx -ErrorAction SilentlyContinue) {
        npx plain-forge install
        $plainForgeInstalled = $true
        Write-Host ""
    } else {
        Write-Host "${GRAY}npx not found. Install Node.js, then run:${NC}"
        Write-Host "  npx plain-forge install"
        Write-Host ""
    }
}

# Install plyn editor extension step
$editorCmds = @()
if (Get-Command cursor -ErrorAction SilentlyContinue) { $editorCmds += [pscustomobject]@{ Cmd = "cursor"; Name = "Cursor" } }
if (Get-Command code -ErrorAction SilentlyContinue) { $editorCmds += [pscustomobject]@{ Cmd = "code"; Name = "VS Code" } }

if ($nonInteractive) {
    $installPlyn = "n"
} else {
    Clear-Host
    Write-Host ""
    Write-Host "  ${YELLOW}${BOLD}plyn Editor Extension${NC}"
    Write-Host ""
    Write-Host "  plyn adds ***plain syntax highlighting to VS Code and Cursor."
    Write-Host ""
    if ($editorCmds.Count -gt 0) {
        $editorNames = ($editorCmds | ForEach-Object { $_.Name }) -join ", "
        Write-Host "  Detected ${editorNames}."
        Write-Host ""
        Write-Host "  ${YELLOW}?${NC} ${WHITE}${BOLD}Install the extension now?${NC}"
        Write-Host ""
        $installPlyn = Read-Host "  [Y/n]"
        Write-Host ""
    } else {
        Write-Host "  Install it manually from:"
        Write-Host "  ${YELLOW}https://marketplace.visualstudio.com/items?itemName=Codeplain.plyn${NC}"
        Write-Host ""
        $installPlyn = "n"
    }
}

$plynInstalled = $false
if ($editorCmds.Count -gt 0 -and $installPlyn -notmatch '^[Nn]$') {
    foreach ($editor in $editorCmds) {
        & $editor.Cmd --install-extension Codeplain.plyn
        Write-Host "${GREEN}✓${NC} plyn installed for $($editor.Name)"
    }
    $plynInstalled = $true
    Write-Host ""
}

# Download examples step
if ($nonInteractive) {
    $downloadExamples = "n"
} else {
    Clear-Host
    Write-Host ""
    Write-Host "  ${YELLOW}${BOLD}Example Projects${NC}"
    Write-Host ""
    Write-Host "  We've prepared some example ***plain projects for you"
    Write-Host "  to explore and experiment with."
    Write-Host ""
    Write-Host "  ${YELLOW}?${NC} ${WHITE}${BOLD}Would you like to download them?${NC}"
    Write-Host ""
    $downloadExamples = Read-Host "  [Y/n]"
    Write-Host ""
}

# Run examples download if user agrees
if ($downloadExamples -notmatch '^[Nn]$') {
    Invoke-SubScript "examples.ps1"
}

# Final message
if (-not $nonInteractive) { Clear-Host }
Write-Host ""
Write-Host "  ${WHITE}${BOLD}You're all set!${NC}"
Write-Host ""
Write-Host "  ${GRAY}Thank you for using *codeplain!${NC}"
Write-Host ""
Write-Host "  ${WHITE}${BOLD}Next steps:${NC}"
Write-Host ""
$stepNum = 1
if (-not $plainForgeInstalled) {
    Write-Host "  ${GRAY}${stepNum}.${NC} ${GRAY}Let your agent work in specs, not code:${NC}"
    Write-Host "     ${WHITE}${BOLD}npx plain-forge install${NC}"
    Write-Host ""
    $stepNum++
}
if (-not $plynInstalled) {
    Write-Host "  ${GRAY}${stepNum}.${NC} ${GRAY}Get ***plain syntax highlighting in VS Code / Cursor:${NC}"
    Write-Host "     ${WHITE}${BOLD}https://marketplace.visualstudio.com/items?itemName=Codeplain.plyn${NC}"
    Write-Host ""
    $stepNum++
}
Write-Host "  ${GRAY}${stepNum}.${NC} ${GRAY}Convert your spec into tested and validated code:${NC}"
Write-Host "     ${WHITE}${BOLD}codeplain your-project.plain${NC}"
Write-Host ""
Write-Host "  ${GRAY}Discord: https://discord.gg/cgbynb9hFq   Docs: https://plainlang.org/${NC}"
Write-Host ""
Write-Host "  ${GRAY}Happy development!${NC} 🚀"
Write-Host ""

# Refresh environment for this session
# Unlike bash's exec "$SHELL", PowerShell doesn't need to restart the shell.
# The API key is already set in $env:CODEPLAIN_API_KEY for this session,
# and persisted via [Environment]::SetEnvironmentVariable for new sessions.
# Refresh PATH to pick up uv tool installations.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
