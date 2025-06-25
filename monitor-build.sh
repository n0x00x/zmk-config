#!/bin/bash
# ZMK Build Monitor - Automatically downloads firmware when build completes
# Usage: ./monitor-build.sh [--extract] [--notify]

REPO="n0x00x/zmk-config"
EXTRACT_FLAG=""
NOTIFY_FLAG=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --extract)
            EXTRACT_FLAG="yes"
            shift
            ;;
        --notify)
            NOTIFY_FLAG="yes"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--extract] [--notify]"
            exit 1
            ;;
    esac
done

# Color codes for pretty output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🔍 ZMK Build Monitor${NC}"
echo -e "${BLUE}Repository: $REPO${NC}"
echo ""

# Check if gh is authenticated
if ! gh auth status &>/dev/null; then
    echo -e "${RED}❌ GitHub CLI not authenticated. Please run 'gh auth login' first.${NC}"
    exit 1
fi

# Get latest run info
echo -e "${YELLOW}📡 Checking latest build status...${NC}"

# First check if we can access runs at all
if ! gh run list --repo $REPO --limit 1 &>/dev/null; then
    echo -e "${RED}❌ Unable to access repository or no runs found${NC}"
    echo -e "${YELLOW}💡 Make sure you have access to: $REPO${NC}"
    exit 1
fi

# Get ALL recent runs to find the absolute latest one
ALL_RUNS=$(gh run list --repo $REPO --limit 5 --json databaseId,status,conclusion,workflowName,createdAt,headSha 2>/dev/null)

if [[ -z "$ALL_RUNS" ]]; then
    echo -e "${RED}❌ No builds found in repository${NC}"
    echo -e "${YELLOW}💡 Push some changes to trigger a build first${NC}"
    exit 1
fi

# Get the absolute latest run (first in the list, regardless of status)
RUN_ID=$(echo "$ALL_RUNS" | jq -r '.[0].databaseId')
STATUS=$(echo "$ALL_RUNS" | jq -r '.[0].status')
CONCLUSION=$(echo "$ALL_RUNS" | jq -r '.[0].conclusion')
WORKFLOW=$(echo "$ALL_RUNS" | jq -r '.[0].workflowName')
CREATED=$(echo "$ALL_RUNS" | jq -r '.[0].createdAt')
HEAD_SHA=$(echo "$ALL_RUNS" | jq -r '.[0].headSha')

if [[ -z "$RUN_ID" || "$RUN_ID" == "null" ]]; then
    echo -e "${RED}❌ No builds found in repository${NC}"
    echo -e "${YELLOW}💡 Push some changes to trigger a build first${NC}"
    exit 1
fi

# Show recent runs for context
echo -e "${BLUE}Recent Builds:${NC}"
echo "$ALL_RUNS" | jq -r '.[] | "  \(.databaseId) | \(.status) | \(.createdAt) | \(.workflowName)"' | head -3
echo ""

echo -e "${BLUE}Monitoring Build:${NC}"
echo -e "  Run ID: $RUN_ID"
echo -e "  Commit: ${HEAD_SHA:0:7}"
echo -e "  Status: $STATUS"
if [[ "$CONCLUSION" != "null" ]]; then
    echo -e "  Conclusion: $CONCLUSION"
fi
echo -e "  Created: $CREATED"
echo ""

# If already completed, download immediately
if [[ "$STATUS" == "completed" ]]; then
    if [[ "$CONCLUSION" == "success" ]]; then
        echo -e "${GREEN}✅ Build already completed successfully!${NC}"
        echo -e "${GREEN}📥 Downloading firmware artifacts...${NC}"
        
        # Archive previous firmware if it exists
        if [[ -d "firmware" ]]; then
            TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
            echo -e "${YELLOW}📦 Archiving previous firmware to firmware-archive-$TIMESTAMP/...${NC}"
            mv firmware "firmware-archive-$TIMESTAMP"
        fi
        
        # Download artifacts
        if gh run download $RUN_ID --repo $REPO; then
            echo -e "${GREEN}🎉 Download complete!${NC}"
            
            # Add timestamp and build info to firmware directory
            if [[ -d "firmware" ]]; then
                BUILD_TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
                BUILD_INFO_FILE="firmware/build-info.txt"
                
                cat > "$BUILD_INFO_FILE" << EOF
ZMK Firmware Build Information
==============================
Build Date: $BUILD_TIMESTAMP
Run ID: $RUN_ID
Commit SHA: $HEAD_SHA
GitHub Actions: https://github.com/$REPO/actions/runs/$RUN_ID
Status: $CONCLUSION

Firmware Variants:
- conservative_left.uf2 & conservative_right.uf2 (Safe enhancements)
- selective_hrm_left.uf2 & selective_hrm_right.uf2 (Recommended)
- toggle_layers_left.uf2 & toggle_layers_right.uf2 (Advanced)
- hybrid_left.uf2 & hybrid_right.uf2 (Experimental)
- settings_reset.uf2 (Emergency reset)

Flash Instructions:
1. Put keyboard in bootloader mode
2. Copy .uf2 file to the mounted drive
3. Keyboard will restart with new firmware
EOF
                
                echo -e "${BLUE}📋 Created build info file: $BUILD_INFO_FILE${NC}"
            fi
            
            # Extract if requested
            if [[ "$EXTRACT_FLAG" == "yes" ]]; then
                echo -e "${YELLOW}📂 Extracting and organizing firmware files...${NC}"
                if [[ -d "firmware" ]]; then
                    echo -e "${BLUE}Available firmware files:${NC}"
                    ls -la firmware/*.uf2
                    echo -e "${GREEN}📁 All firmware variants ready in ./firmware/ directory${NC}"
                    echo -e "${BLUE}📋 Build info: firmware/build-info.txt${NC}"
                fi
            fi
            
            # Notify if requested (macOS)
            if [[ "$NOTIFY_FLAG" == "yes" && "$OSTYPE" == "darwin"* ]]; then
                osascript -e 'display notification "ZMK firmware download complete!" with title "Build Monitor"'
            fi
        else
            echo -e "${RED}❌ Download failed${NC}"
            exit 1
        fi
        exit 0
    else
        echo -e "${RED}❌ Build failed with conclusion: $CONCLUSION${NC}"
        exit 1
    fi
fi

# Monitor in-progress build
if [[ "$STATUS" == "in_progress" || "$STATUS" == "queued" ]]; then
    echo -e "${YELLOW}⏳ Build in progress... monitoring for completion${NC}"
    echo -e "${BLUE}💡 Tip: You can press Ctrl+C to stop monitoring${NC}"
    echo ""
    
    CHECK_COUNT=0
    while true; do
        sleep 30
        CHECK_COUNT=$((CHECK_COUNT + 1))
        
        # Get updated status
        CURRENT_RUN=$(gh run view $RUN_ID --repo $REPO --json status,conclusion 2>/dev/null)
        
        if [[ -z "$CURRENT_RUN" ]]; then
            echo -e "${RED}❌ Lost connection to build. Please check GitHub Actions manually.${NC}"
            exit 1
        fi
        
        CURRENT_STATUS=$(echo "$CURRENT_RUN" | jq -r '.status')
        CURRENT_CONCLUSION=$(echo "$CURRENT_RUN" | jq -r '.conclusion')
        
        echo -e "${YELLOW}⏳ Check #$CHECK_COUNT - Status: $CURRENT_STATUS${NC}"
        
        if [[ "$CURRENT_STATUS" == "completed" ]]; then
            if [[ "$CURRENT_CONCLUSION" == "success" ]]; then
                echo ""
                echo -e "${GREEN}✅ Build completed successfully!${NC}"
                echo -e "${GREEN}📥 Downloading firmware artifacts...${NC}"
                
                # Archive previous firmware if it exists
                if [[ -d "firmware" ]]; then
                    TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
                    echo -e "${YELLOW}📦 Archiving previous firmware to firmware-archive-$TIMESTAMP/...${NC}"
                    mv firmware "firmware-archive-$TIMESTAMP"
                fi
                
                # Download artifacts
                if gh run download $RUN_ID --repo $REPO; then
                    echo -e "${GREEN}🎉 Download complete!${NC}"
                    
                    # Add timestamp and build info to firmware directory
                    if [[ -d "firmware" ]]; then
                        BUILD_TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
                        BUILD_INFO_FILE="firmware/build-info.txt"
                        
                        cat > "$BUILD_INFO_FILE" << EOF
ZMK Firmware Build Information
==============================
Build Date: $BUILD_TIMESTAMP
Run ID: $RUN_ID
Commit SHA: $HEAD_SHA
GitHub Actions: https://github.com/$REPO/actions/runs/$RUN_ID
Status: $CURRENT_CONCLUSION

Firmware Variants:
- conservative_left.uf2 & conservative_right.uf2 (Safe enhancements)
- selective_hrm_left.uf2 & selective_hrm_right.uf2 (Recommended)
- toggle_layers_left.uf2 & toggle_layers_right.uf2 (Advanced)
- hybrid_left.uf2 & hybrid_right.uf2 (Experimental)
- settings_reset.uf2 (Emergency reset)

Flash Instructions:
1. Put keyboard in bootloader mode
2. Copy .uf2 file to the mounted drive
3. Keyboard will restart with new firmware
EOF
                        
                        echo -e "${BLUE}📋 Created build info file: $BUILD_INFO_FILE${NC}"
                    fi
                    
                    # Extract if requested
                    if [[ "$EXTRACT_FLAG" == "yes" ]]; then
                        echo -e "${YELLOW}📂 Extracting and organizing firmware files...${NC}"
                        if [[ -d "firmware" ]]; then
                            echo -e "${BLUE}Available firmware files:${NC}"
                            ls -la firmware/*.uf2
                            echo ""
                            echo -e "${GREEN}📁 All firmware variants ready in ./firmware/ directory${NC}"
                            echo -e "${BLUE}📋 Build info: firmware/build-info.txt${NC}"
                            echo -e "${BLUE}💡 Flash the variant you want to test:${NC}"
                            echo -e "   • conservative_left.uf2 & conservative_right.uf2 (safe start)"
                            echo -e "   • selective_hrm_left.uf2 & selective_hrm_right.uf2 (recommended)"
                            echo -e "   • toggle_layers_left.uf2 & toggle_layers_right.uf2 (advanced)"
                            echo -e "   • hybrid_left.uf2 & hybrid_right.uf2 (experimental)"
                        fi
                    fi
                    
                    # Notify if requested (macOS)
                    if [[ "$NOTIFY_FLAG" == "yes" && "$OSTYPE" == "darwin"* ]]; then
                        osascript -e 'display notification "ZMK firmware download complete!" with title "Build Monitor"'
                    fi
                else
                    echo -e "${RED}❌ Download failed${NC}"
                    exit 1
                fi
                break
            else
                echo -e "${RED}❌ Build failed with conclusion: $CURRENT_CONCLUSION${NC}"
                echo -e "${YELLOW}💡 Check GitHub Actions for error details: https://github.com/$REPO/actions${NC}"
                exit 1
            fi
        fi
    done
else
    echo -e "${YELLOW}⚠️  Build status is '$STATUS' - not currently running${NC}"
    echo -e "${BLUE}💡 Push some changes to trigger a new build${NC}"
fi