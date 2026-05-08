#!/bin/bash
# CLIPPR Setup Script
# Run this once: bash setup.sh

echo ""
echo "╔═══════════════════════════════════╗"
echo "║   CLIPPR — AI Viral Clip Engine   ║"
echo "╚═══════════════════════════════════╝"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install it from python.org"
    exit 1
fi
echo "✅ Python 3 found"

# Check ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  ffmpeg not found."
    echo "   Mac:   brew install ffmpeg"
    echo "   Linux: sudo apt install ffmpeg"
    echo "   Windows: https://ffmpeg.org/download.html"
    exit 1
fi
echo "✅ ffmpeg found"

# Install Python packages
echo ""
echo "📦 Installing Python packages..."
pip3 install flask yt-dlp openai-whisper anthropic

echo ""
echo "✅ Setup complete!"
echo ""
echo "▶  To run:"
echo "   python3 app.py"
echo ""
echo "   Then open http://localhost:5000 in your browser."
echo ""
