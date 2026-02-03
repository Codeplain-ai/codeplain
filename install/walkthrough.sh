#!/bin/bash

set -euo pipefail

# Brand Colors (use exported colors if available, otherwise define them)
YELLOW="${YELLOW:-\033[38;2;224;255;110m}"
GREEN="${GREEN:-\033[38;2;121;252;150m}"
BLUE="${BLUE:-\033[38;2;10;31;212m}"
BLACK="${BLACK:-\033[38;2;26;26;26m}"
WHITE="${WHITE:-\033[38;2;255;255;255m}"
GRAY="${GRAY:-\033[38;2;128;128;128m}"
BOLD="${BOLD:-\033[1m}"
NC="${NC:-\033[0m}"

# Detect if terminal has a light background (if not already detected by parent script)
if [ -z "${TERM_BACKGROUND:-}" ]; then
    detect_light_background() {
        # Allow explicit override via environment variable
        if [ "${CODEPLAIN_LIGHT_THEME:-}" = "1" ] || [ "${CODEPLAIN_LIGHT_THEME:-}" = "true" ]; then
            return 0  # Light theme
        fi
        if [ "${CODEPLAIN_DARK_THEME:-}" = "1" ] || [ "${CODEPLAIN_DARK_THEME:-}" = "true" ]; then
            return 1  # Dark theme
        fi

        # Check COLORFGBG environment variable (format: "fg;bg")
        if [ -n "${COLORFGBG:-}" ]; then
            local bg="${COLORFGBG##*;}"
            case "$bg" in
                7|15) return 0 ;;  # White/bright white background = light
                0|8) return 1 ;;   # Black/dark gray background = dark
            esac
        fi

        # Default to dark background
        return 1
    }

    if detect_light_background; then
        TERM_BACKGROUND="light"
    else
        TERM_BACKGROUND="dark"
    fi
fi

# Set accent colors based on terminal background
# Light background: use BLUE+BOLD for success messages and links, BLACK+BOLD for highlights
# Dark background: use GREEN for checkmarks, YELLOW for links and highlights
if [ "$TERM_BACKGROUND" = "light" ]; then
    CHECK_COLOR="${CHECK_COLOR:-${BLUE}${BOLD}}"  # #0A1FD4 Bold for success messages
    LINK_COLOR="${LINK_COLOR:-$BLUE}"
    HIGHLIGHT_COLOR="${HIGHLIGHT_COLOR:-${BLACK:-\033[38;2;26;26;26m}${BOLD}}"  # Black + Bold
else
    CHECK_COLOR="${CHECK_COLOR:-$GREEN}"
    LINK_COLOR="${LINK_COLOR:-$YELLOW}"
    HIGHLIGHT_COLOR="${HIGHLIGHT_COLOR:-$YELLOW}"
fi

# Onboarding Step 1: Introduction to Plain
clear
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e "  ${HIGHLIGHT_COLOR}quick intro to ***plain specification language${NC} - Step 1 of 5"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  ***plain is a language of spec-driven development that allows developers to express intent on any level of detail."
echo ""
echo -e "  write specs in ${HIGHLIGHT_COLOR}plain English${NC}, in markdown with additional syntax"
echo ""
echo -e "  render production-ready code with *codeplain."
echo ""
echo -e "  A ***plain file has these key sections:"
echo ""
echo -e "${GRAY}  ┌────────────────────────────────────────────────────────┐${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${YELLOW}***definitions***${NC}      - key concepts in your app     ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${YELLOW}***implementation reqs***${NC}  - implementation details       ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${YELLOW}***test reqs***${NC}       - testing requirements         ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${YELLOW}***functional specs***${NC} - what the app should do       ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  └────────────────────────────────────────────────────────┘${NC}"
echo ""
echo -e "  Let's see each section in a \"hello, world\" example."
echo ""
read -r -p "  press [Enter] to continue..." < /dev/tty

# Onboarding Step 2: Functional Specification
clear
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e "  ${HIGHLIGHT_COLOR}Plain Language 101${NC} - Step 2 of 5"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  ${WHITE}${BOLD}FUNCTIONAL SPECS${NC} - what should the app do?"
echo ""
echo -e "  This is where you describe ${GREEN}what your app should do${NC},"
echo -e "  written in plain English. No code, just requirements."
echo ""
echo -e "${GRAY}  ┌────────────────────────────────────────────────────────┐${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***definitions***${NC}                                    ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :App: is a console application.${NC}                    ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***implementation reqs***${NC}                                ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :Implementation: should be in Python.${NC}              ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :UnitTests: should use Unittest framework.${NC}         ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***test reqs***${NC}                                     ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :ConformanceTests: should use Unittest.${NC}            ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${HIGHLIGHT_COLOR}***functional specs***${NC}                               ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GREEN}${BOLD}- :App: should display \"hello, world\".${NC}               ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  └────────────────────────────────────────────────────────┘${NC}"
echo ""
echo -e "  ${GREEN}▲${NC} The ${HIGHLIGHT_COLOR}functional spec${NC} describes ${GREEN}what${NC} the app does."
echo -e "    Here, it simply displays \"hello, world\"."
echo ""
read -r -p "  press [Enter] to continue..." < /dev/tty

# Onboarding Step 3: Definitions
clear
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e "  ${HIGHLIGHT_COLOR}Plain Language 101${NC} - Step 3 of 5"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  ${WHITE}${BOLD}DEFINITIONS${NC} - identify key concepts"
echo ""
echo -e "  Define ${GREEN}reusable concepts${NC} with the ${HIGHLIGHT_COLOR}:ConceptName:${NC} syntax."
echo -e "  These become building blocks you can reference anywhere."
echo ""
echo -e "${GRAY}  ┌────────────────────────────────────────────────────────┐${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${HIGHLIGHT_COLOR}***definitions***${NC}                                    ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GREEN}${BOLD}- :App: is a console application.${NC}                    ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***implementation reqs***${NC}                                ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :Implementation: should be in Python.${NC}              ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :UnitTests: should use Unittest framework.${NC}         ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***test reqs***${NC}                                     ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :ConformanceTests: should use Unittest.${NC}            ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***functional specs***${NC}                               ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :App: should display \"hello, world\".${NC}               ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  └────────────────────────────────────────────────────────┘${NC}"
echo ""
echo -e "  ${GREEN}▲${NC} The ${HIGHLIGHT_COLOR}:App:${NC} concept is defined once and used throughout."
echo -e "    Concepts help keep your specs consistent and clear."
echo ""
read -r -p "  press [Enter] to continue..." < /dev/tty

# Onboarding Step 4: Implementation & Test Reqs
clear
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e "  ${HIGHLIGHT_COLOR}Plain Language 101${NC} - Step 4 of 5"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  ${WHITE}${BOLD}IMPLEMENTATION & TEST REQS${NC} - how to implement and test"
echo ""
echo -e "  Specify ${GREEN}implementation details${NC} and ${GREEN}testing requirements${NC}."
echo -e "  This guides how the code should be generated and verified."
echo ""
echo -e "${GRAY}  ┌────────────────────────────────────────────────────────┐${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***definitions***${NC}                                    ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :App: is a console application.${NC}                    ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${YELLOW}${BOLD}***implementation reqs***${NC}                                ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GREEN}${BOLD}- :Implementation: should be in Python.${NC}              ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GREEN}${BOLD}- :UnitTests: should use Unittest framework.${NC}         ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${YELLOW}${BOLD}***test reqs***${NC}                                     ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GREEN}${BOLD}- :ConformanceTests: should use Unittest.${NC}            ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}***functional specs***${NC}                               ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}  ${GRAY}- :App: should display \"hello, world\".${NC}               ${GRAY}│${NC}"
echo -e "${GRAY}  │${NC}                                                        ${GRAY}│${NC}"
echo -e "${GRAY}  └────────────────────────────────────────────────────────┘${NC}"
echo ""
echo -e "  ${GREEN}▲${NC} ${YELLOW}Implementation reqs${NC} define the language and frameworks."
echo -e "    ${YELLOW}Test reqs${NC} ensure the generated code is verified."
echo ""
read -r -p "  press [Enter] to continue..." < /dev/tty

# Onboarding Step 5: Rendering Code
clear
echo ""
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo -e "  ${HIGHLIGHT_COLOR}Plain Language 101${NC} - Step 5 of 5"
echo -e "${GRAY}────────────────────────────────────────────${NC}"
echo ""
echo -e "  ${WHITE}${BOLD}RENDERING CODE${NC} - generate your app"
echo ""
echo -e "  Once you have a Plain file, generate code with:"
echo ""
echo -e "    ${HIGHLIGHT_COLOR}codeplain hello_world.plain${NC}"
echo ""
echo -e "  *codeplain will:"
echo ""
echo -e "    ${GREEN}1.${NC} Read your specification"
echo -e "    ${GREEN}2.${NC} Generate implementation code"
echo -e "    ${GREEN}3.${NC} Create and run tests to verify correctness"
echo -e "    ${GREEN}4.${NC} Output production-ready code"
echo ""
echo -e "  The generated code is guaranteed to match your specs"
echo -e "  and pass all defined tests."
echo ""
read -r -p "  press [Enter] to finish..." < /dev/tty
