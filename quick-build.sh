#!/bin/bash
# Quick Build - Commit current changes and monitor build automatically
# Usage: ./quick-build.sh [commit-message] [--extract] [--notify]

# Default commit message
DEFAULT_MESSAGE="Update keymap configuration"
COMMIT_MESSAGE="$DEFAULT_MESSAGE"
EXTRACT_FLAG=""
NOTIFY_FLAG=""

# Parse arguments
ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --extract)
            EXTRACT_FLAG="--extract"
            shift
            ;;
        --notify)
            NOTIFY_FLAG="--notify"
            shift
            ;;
        --help|-h)
            echo "Quick Build - Automated ZMK build and download"
            echo ""
            echo "Usage: $0 [commit-message] [--extract] [--notify]"
            echo ""
            echo "Options:"
            echo "  --extract    Extract firmware files after download"
            echo "  --notify     Send notification when complete (macOS only)"
            echo "  --help, -h   Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                                    # Use default commit message"
            echo "  $0 \"Add media keys\"                  # Custom commit message"
            echo "  $0 --extract --notify                # Auto-extract and notify"
            echo "  $0 \"Fix toggle layers\" --extract     # Custom message + extract"
            exit 0
            ;;
        *)
            if [[ -z "${ARGS[0]}" ]]; then
                COMMIT_MESSAGE="$1"
            fi
            shift
            ;;
    esac
done

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}🚀 ZMK Quick Build${NC}"
echo -e "${BLUE}Repository: n0x00x/zmk-config${NC}"
echo ""

# Check if we're in a git repository
if ! git rev-parse --git-dir &>/dev/null; then
    echo -e "${RED}❌ Not in a git repository${NC}"
    exit 1
fi

# Check for changes
if git diff --quiet && git diff --cached --quiet; then
    echo -e "${YELLOW}⚠️  No changes detected${NC}"
    echo -e "${BLUE}💡 Make some keymap changes first, then run this script${NC}"
    exit 0
fi

echo -e "${YELLOW}📝 Changes detected:${NC}"
git status --porcelain
echo ""

# Show current keymap
CURRENT_KEYMAP=$(readlink config/cradio.keymap 2>/dev/null)
if [[ -n "$CURRENT_KEYMAP" ]]; then
    echo -e "${BLUE}🎹 Current active keymap: ${GREEN}$CURRENT_KEYMAP${NC}"
    echo ""
fi

# Commit and push
echo -e "${YELLOW}📤 Committing and pushing changes...${NC}"
echo -e "${BLUE}Commit message: \"$COMMIT_MESSAGE\"${NC}"
echo ""

if git add . && git commit -m "$COMMIT_MESSAGE"; then
    echo -e "${GREEN}✅ Changes committed${NC}"
    
    if git push; then
        echo -e "${GREEN}✅ Changes pushed to GitHub${NC}"
        echo ""
        
        # Start monitoring
        echo -e "${BLUE}🔍 Starting build monitor...${NC}"
        echo -e "${YELLOW}💡 This will automatically download firmware when the build completes${NC}"
        echo -e "${YELLOW}💡 Press Ctrl+C to stop monitoring (build will continue on GitHub)${NC}"
        echo ""
        
        # Run monitor with flags
        ./monitor-build.sh $EXTRACT_FLAG $NOTIFY_FLAG
    else
        echo -e "${RED}❌ Failed to push changes${NC}"
        exit 1
    fi
else
    echo -e "${RED}❌ Failed to commit changes${NC}"
    exit 1
fi