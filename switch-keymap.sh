#!/bin/bash
# ZMK Keymap Switching Script
# Usage: ./switch-keymap.sh [original|conservative|selective-hrm|toggle-layers|hybrid]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"
CURRENT_KEYMAP="$CONFIG_DIR/cradio.keymap"

# Available keymaps - using arrays for compatibility
KEYMAP_NAMES=("original" "conservative" "selective-hrm" "toggle-layers" "hybrid")
KEYMAP_FILES=("cradio-original.keymap" "cradio-conservative.keymap" "cradio-selective-hrm.keymap" "cradio-toggle-layers.keymap" "cradio-hybrid.keymap")

get_keymap_file() {
    local variant="$1"
    for i in "${!KEYMAP_NAMES[@]}"; do
        if [[ "${KEYMAP_NAMES[$i]}" == "$variant" ]]; then
            echo "${KEYMAP_FILES[$i]}"
            return
        fi
    done
    echo ""
}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

show_help() {
    echo -e "${BLUE}ZMK Keymap Switcher${NC}"
    echo ""
    echo "Usage: ./switch-keymap.sh [keymap-name]"
    echo ""
    echo "Available keymaps:"
    echo ""
    echo -e "${GREEN}  original${NC}        - Your original layout (backup)"
    echo -e "${YELLOW}  conservative${NC}    - Original + panic mode + sticky modifiers"
    echo -e "${GREEN}  selective-hrm${NC}   - SHIFT to thumb + reduced HRM (recommended)"
    echo -e "${BLUE}  toggle-layers${NC}   - Full Ben Vallack toggle system"
    echo -e "${YELLOW}  hybrid${NC}          - Dual-mode access with experimental features"
    echo ""
    echo "Examples:"
    echo "  ./switch-keymap.sh selective-hrm"
    echo "  ./switch-keymap.sh original"
    echo ""
    echo "Current keymap: $(get_current_keymap)"
}

get_current_keymap() {
    if [[ -L "$CURRENT_KEYMAP" ]]; then
        basename "$(readlink "$CURRENT_KEYMAP")" | sed 's/cradio-//; s/.keymap//'
    elif [[ -f "$CURRENT_KEYMAP" ]]; then
        # Check if it matches any of our variants
        current_hash=$(md5sum "$CURRENT_KEYMAP" 2>/dev/null | cut -d' ' -f1)
        for i in "${!KEYMAP_NAMES[@]}"; do
            variant="${KEYMAP_NAMES[$i]}"
            variant_file="$CONFIG_DIR/${KEYMAP_FILES[$i]}"
            if [[ -f "$variant_file" ]]; then
                variant_hash=$(md5sum "$variant_file" 2>/dev/null | cut -d' ' -f1)
                if [[ "$current_hash" == "$variant_hash" ]]; then
                    echo "$variant"
                    return
                fi
            fi
        done
        echo "unknown"
    else
        echo "missing"
    fi
}

switch_keymap() {
    local variant="$1"
    local source_file="$CONFIG_DIR/$(get_keymap_file "$variant")"
    
    if [[ ! -f "$source_file" ]]; then
        echo -e "${RED}Error: Source file '$source_file' not found${NC}"
        return 1
    fi
    
    # Create backup of current keymap if it's not a symlink
    if [[ -f "$CURRENT_KEYMAP" && ! -L "$CURRENT_KEYMAP" ]]; then
        echo -e "${YELLOW}Backing up current keymap...${NC}"
        cp "$CURRENT_KEYMAP" "$CONFIG_DIR/cradio-backup-$(date +%Y%m%d-%H%M%S).keymap"
    fi
    
    # Remove existing keymap (whether file or symlink)
    rm -f "$CURRENT_KEYMAP"
    
    # Create symlink to new keymap
    ln -s "$(basename "$source_file")" "$CURRENT_KEYMAP"
    
    echo -e "${GREEN}✓ Switched to '$variant' keymap${NC}"
    echo -e "${BLUE}  Active file: $(basename "$source_file")${NC}"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo "  1. Commit this change if you want to build the firmware"
    echo "  2. Push to GitHub to trigger automatic build"
    echo "  3. Download firmware from GitHub Actions"
    echo ""
    echo -e "${BLUE}To switch back: ./switch-keymap.sh original${NC}"
}

list_keymaps() {
    echo -e "${BLUE}Available keymaps:${NC}"
    echo ""
    
    current=$(get_current_keymap)
    
    for i in "${!KEYMAP_NAMES[@]}"; do
        variant="${KEYMAP_NAMES[$i]}"
        file="${KEYMAP_FILES[$i]}"
        path="$CONFIG_DIR/$file"
        
        if [[ -f "$path" ]]; then
            local status="${GREEN}✓${NC}"
            local current_marker=""
            if [[ "$variant" == "$current" ]]; then
                current_marker=" ${YELLOW}(current)${NC}"
            fi
            echo -e "  $status $variant$current_marker"
        else
            echo -e "  ${RED}✗${NC} $variant ${RED}(missing)${NC}"
        fi
    done
    echo ""
    echo -e "Current keymap: ${GREEN}$current${NC}"
}

# Main script logic
case "${1:-}" in
    "help"|"-h"|"--help"|"")
        show_help
        ;;
    "list"|"-l"|"--list")
        list_keymaps
        ;;
    "status")
        echo "Current keymap: $(get_current_keymap)"
        ;;
    *)
        VARIANT="$1"
        if [[ -z "$(get_keymap_file "$VARIANT")" ]]; then
            echo -e "${RED}Error: Unknown keymap '$VARIANT'${NC}"
            echo ""
            show_help
            exit 1
        fi
        
        current=$(get_current_keymap)
        if [[ "$current" == "$VARIANT" ]]; then
            echo -e "${YELLOW}Already using '$VARIANT' keymap${NC}"
            exit 0
        fi
        
        switch_keymap "$VARIANT"
        ;;
esac