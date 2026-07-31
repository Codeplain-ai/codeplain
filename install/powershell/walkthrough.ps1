$ErrorActionPreference = 'Stop'

# Brand Colors (use exported colors if available, otherwise define them)
if (-not $env:YELLOW)     { $YELLOW      = "$([char]27)[38;2;224;255;110m" } else { $YELLOW      = $env:YELLOW }
if (-not $env:GREEN)      { $GREEN       = "$([char]27)[38;2;121;252;150m" } else { $GREEN       = $env:GREEN }
if (-not $env:WHITE)      { $WHITE       = "$([char]27)[38;2;255;255;255m" } else { $WHITE       = $env:WHITE }
if (-not $env:GRAY)       { $GRAY        = "$([char]27)[38;2;128;128;128m" } else { $GRAY        = $env:GRAY }
if (-not $env:BOLD)       { $BOLD        = "$([char]27)[1m"               } else { $BOLD        = $env:BOLD }
if (-not $env:NC)         { $NC          = "$([char]27)[0m"               } else { $NC          = $env:NC }

# Box-drawing and symbol characters built from code points so this file stays pure ASCII.
# Windows PowerShell 5.1 reads BOM-less files as ANSI (CP1252), where these glyphs decode
# to smart quotes that silently terminate the enclosing string and break parsing.
$TRI = [char]0x25B2
$TL  = [char]0x250C   # top-left corner
$TR  = [char]0x2510   # top-right corner
$BL  = [char]0x2514   # bottom-left corner
$BR  = [char]0x2518   # bottom-right corner
$V   = [char]0x2502   # vertical line
$HR  = ([string][char]0x2500) * 56   # horizontal rule
$BOX_TOP    = "${TL}${HR}${TR}"
$BOX_BOTTOM = "${BL}${HR}${BR}"

# Onboarding Step 1: Introduction to Plain
Clear-Host
Write-Host ""
Write-Host "  ${WHITE}${BOLD}***plain specification language intro${NC} (1/5)"
Write-Host ""
Write-Host "  ***plain is the language of spec-driven development that allows developers to express intent at any level of detail."
Write-Host ""
Write-Host "  Write specs in natural language extended with additional syntax based on markdown."
Write-Host ""
Write-Host "  A ***plain file has these key sections:"
Write-Host ""
Write-Host "${GRAY}  ${BOX_TOP}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}***definitions***${NC}         - key concepts in your app  ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}***implementation reqs***${NC} - implementation details    ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}***test reqs***${NC}           - testing requirements      ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}***functional specs***${NC}    - what the app should do    ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${BOX_BOTTOM}${NC}"
Write-Host ""
Write-Host "  Let's see each section in a `"hello, world`" example."
Write-Host ""
Read-Host "  Press ${WHITE}[Enter]${NC} to continue..."

# Onboarding Step 2: Definitions
Clear-Host
Write-Host ""
Write-Host "  ${WHITE}${BOLD}***plain specification language intro${NC} (2/5)"
Write-Host ""
Write-Host "  ${WHITE}${BOLD}DEFINITIONS${NC} - Definitions and descriptions of key concepts"
Write-Host ""
Write-Host "  Define ${WHITE}${BOLD}reusable concepts${NC} using the ${WHITE}${BOLD}:Concept:${NC} notation."
Write-Host "  These become building blocks you can reference anywhere."
Write-Host ""
Write-Host "${GRAY}  ${BOX_TOP}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}${BOLD}***definitions***${NC}                                     ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${WHITE}${BOLD}- :App: is a console application.${NC}                     ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***implementation reqs***${NC}                             ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :Implementation: should be in Python.${NC}               ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***test reqs***${NC}                                       ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :ConformanceTests: should use pytest.${NC}               ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***functional specs***${NC}                                ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :App: should display `"hello, world`".${NC}                ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${BOX_BOTTOM}${NC}"
Write-Host ""
Write-Host "  ${GREEN}${TRI}${NC} The ${WHITE}:App:${NC} concept is defined once and used throughout the specs."
Write-Host "    Concepts help keep your specs consistent and clear."
Write-Host ""
Read-Host "  Press ${WHITE}[Enter]${NC} to continue..."

# Onboarding Step 3: Implementation & Test Reqs
Clear-Host
Write-Host ""
Write-Host "  ${WHITE}${BOLD}***plain specification language intro${NC} (3/5)"
Write-Host ""
Write-Host "  ${WHITE}${BOLD}IMPLEMENTATION & TEST REQS${NC} - How to implement and test"
Write-Host ""
Write-Host "  Specify ${WHITE}${BOLD}implementation details${NC} and ${WHITE}${BOLD}testing requirements${NC}."
Write-Host "  This guides how the code should be generated and verified."
Write-Host ""
Write-Host "${GRAY}  ${BOX_TOP}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***definitions***${NC}                                     ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :App: is a console application.${NC}                     ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}${BOLD}***implementation reqs***${NC}                             ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${WHITE}${BOLD}- :Implementation: should be in Python.${NC}               ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}${BOLD}***test reqs***${NC}                                       ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${WHITE}${BOLD}- :ConformanceTests: should use pytest.${NC}               ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***functional specs***${NC}                                ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :App: should display `"hello, world`".${NC}                ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${BOX_BOTTOM}${NC}"
Write-Host ""
Write-Host "  ${GREEN}${TRI}${NC} ${WHITE}${BOLD}Implementation reqs${NC} define the language and frameworks."
Write-Host "    ${WHITE}${BOLD}Test reqs${NC} ensure the generated code is verified."
Write-Host ""
Read-Host "  Press ${WHITE}[Enter]${NC} to continue..."

# Onboarding Step 4: Functional Specification
Clear-Host
Write-Host ""
Write-Host "  ${WHITE}${BOLD}***plain specification language intro${NC} (4/5)"
Write-Host ""
Write-Host "  ${WHITE}${BOLD}FUNCTIONAL SPECS${NC} - What should the app do?"
Write-Host ""
Write-Host "  This is where you describe ${WHITE}what your app should do${NC},"
Write-Host "  written in natural language. No code, just requirements."
Write-Host ""
Write-Host "${GRAY}  ${BOX_TOP}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***definitions***${NC}                                     ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :App: is a console application.${NC}                     ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***implementation reqs***${NC}                             ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :Implementation: should be in Python.${NC}               ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}***test reqs***${NC}                                       ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GRAY}- :ConformanceTests: should use pytest.${NC}               ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${GREEN}${BOLD}***functional specs***${NC}                                ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}  ${WHITE}${BOLD}- :App: should display `"hello, world`".${NC}                ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${V}${NC}                                                        ${GRAY}${V}${NC}"
Write-Host "${GRAY}  ${BOX_BOTTOM}${NC}"
Write-Host ""
Write-Host "  ${GREEN}${TRI}${NC} The ${WHITE}${BOLD}functional spec${NC} describes ${WHITE}${BOLD}what the app does${NC}."
Write-Host "    Here, it simply displays `"hello, world`"."
Write-Host ""
Read-Host "  Press ${WHITE}[Enter]${NC} to continue..."

# Onboarding Step 5: Rendering Code
Clear-Host
Write-Host ""
Write-Host "  ${WHITE}${BOLD}***plain specification language intro${NC} (5/5)"
Write-Host ""
Write-Host "  ${WHITE}${BOLD}RENDERING CODE${NC} - Generate your app"
Write-Host ""
Write-Host "  Once you have a ***plain file, generate code with:"
Write-Host ""
Write-Host "    ${WHITE}${BOLD}codeplain hello_world.plain${NC}"
Write-Host ""
Write-Host "  *codeplain will:"
Write-Host ""
Write-Host "    ${GRAY}1. Read your specification${NC}"
Write-Host "    ${GRAY}2. Generate implementation code${NC}"
Write-Host "    ${GRAY}3. Create and run tests to verify correctness${NC}"
Write-Host "    ${GRAY}4. Output production-ready code${NC}"
Write-Host ""
Write-Host "  The generated code is guaranteed to match your specs"
Write-Host "  and pass all defined tests."
Write-Host ""
Read-Host "  Press ${WHITE}[Enter]${NC} to finish..."
