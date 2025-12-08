#!/bin/bash
# Setup script for Snapchat Memories Downloader

echo "Setting up virtual environment..."

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install requirements
echo "Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "Setup complete!"
echo ""
echo "To use the downloader:"
echo "  1. Activate the virtual environment: source venv/bin/activate"
echo "  2. Run the script:"
echo "     - Test mode (first 3 files): python download_memories.py --test"
echo "     - Full download: python download_memories.py"
echo "  3. When done, deactivate: deactivate"
