#!/bin/bash
set -e

echo "Setting up Local Autonomous Coding Agent..."

# Setup virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install requirements
echo "Installing dependencies..."
pip install -r requirements.txt

# Instructions
echo ""
echo "Setup complete! ✅"
echo "To use the agent, first verify Ollama is running and has the deepseek-coder model:"
echo "  ollama run deepseek-coder:1.3b"
echo ""
echo "Then activate the environment and run:"
echo "  source venv/bin/activate"
echo "  python main.py \"Create a hello world python script and run it\""
