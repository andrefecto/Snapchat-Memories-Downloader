#!/bin/bash

# Simple script to update version and create tag in one step
# Usage: ./tag-version.sh 1.2.9

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check if version argument provided
if [ -z "$1" ]; then
    echo -e "${RED}Error: No version specified${NC}"
    echo "Usage: ./tag-version.sh <version>"
    echo "Example: ./tag-version.sh 1.2.9"
    exit 1
fi

VERSION=$1
TAG_NAME="v$VERSION"

# Validate version format
if ! [[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}Error: Invalid version format${NC}"
    echo "Version must be in format: X.Y.Z (e.g., 1.2.9)"
    exit 1
fi

echo -e "${BLUE}📦 Preparing version $TAG_NAME${NC}"

# Check if there are uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo -e "${RED}Error: You have uncommitted changes${NC}"
    echo "Please commit your changes first, then run this script."
    exit 1
fi

# Update version in HTML
echo -e "${BLUE}Updating version in docs/index.html...${NC}"
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s/const APP_VERSION = 'v[0-9]*\.[0-9]*\.[0-9]*';/const APP_VERSION = '$TAG_NAME';/" docs/index.html
else
    sed -i "s/const APP_VERSION = 'v[0-9]*\.[0-9]*\.[0-9]*';/const APP_VERSION = '$TAG_NAME';/" docs/index.html
fi

# Check if version changed
if git diff --quiet docs/index.html; then
    echo -e "${YELLOW}⚠ Version already up to date (already $TAG_NAME)${NC}"
    echo -e "${BLUE}Creating tag anyway...${NC}"
else
    echo -e "${GREEN}✓ Updated version to $TAG_NAME${NC}"
    
    # Amend the last commit with version update
    git add docs/index.html
    echo -e "${BLUE}Amending last commit with version update...${NC}"
    git commit --amend --no-edit
    echo -e "${GREEN}✓ Amended commit${NC}"
fi

# Create annotated tag
echo -e "${BLUE}Creating tag $TAG_NAME...${NC}"
git tag -a "$TAG_NAME" -m "$TAG_NAME - Version update"
echo -e "${GREEN}✓ Created tag $TAG_NAME${NC}"

# Push
echo -e "${BLUE}Pushing to remote...${NC}"
git push origin main
git push origin "$TAG_NAME"
echo -e "${GREEN}✓ Pushed successfully!${NC}"

echo ""
echo -e "${GREEN}🎉 Done! Version $TAG_NAME deployed.${NC}"
