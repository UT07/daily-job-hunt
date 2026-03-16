#!/bin/bash
# ============================================================
#  Job Automation — Environment Setup Script
#  Run this once to set up your environment
# ============================================================

set -e

echo "=== Job Automation Setup ==="
echo ""

# 1. Create virtual environment
echo "[1/4] Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
echo "[2/4] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 3. Check for LaTeX
echo "[3/4] Checking LaTeX installation..."
if command -v pdflatex &> /dev/null; then
    echo "  ✓ pdflatex found"
else
    echo "  ✗ pdflatex not found."
    echo "  Install it with:"
    echo "    macOS:  brew install --cask mactex-no-gui"
    echo "    Ubuntu: sudo apt install texlive-latex-base texlive-latex-extra texlive-fonts-recommended"
    echo "    Or install tectonic: cargo install tectonic"
fi

# 4. Set up API keys
echo "[4/4] Setting up API keys..."
echo ""
echo "You need to set these environment variables (add to ~/.zshrc or ~/.bashrc):"
echo ""
echo "  # REQUIRED — Claude API for matching/tailoring/cover letters"
echo '  export ANTHROPIC_API_KEY="sk-ant-..."'
echo ""
echo "  # At least ONE of these job scraper APIs:"
echo '  export SERPAPI_API_KEY="..."       # https://serpapi.com/ (~$50/mo, best coverage)'
echo '  export JSEARCH_API_KEY="..."       # https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch (free tier: 200/mo)'
echo '  export ADZUNA_APP_ID="..."         # https://developer.adzuna.com/ (free tier: 250/mo, great for Ireland)'
echo '  export ADZUNA_APP_KEY="..."'
echo ""
echo "=== Setup Complete ==="
echo ""
echo "To run the pipeline:"
echo "  source .venv/bin/activate"
echo "  python main.py                # Full run"
echo "  python main.py --dry-run      # Scrape + match only"
echo "  python main.py --scrape-only  # Just scrape"
