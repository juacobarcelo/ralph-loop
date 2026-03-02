#!/bin/bash
set -e

git config --global --add safe.directory /workspace

if [ -n "$OPENAI_API_KEY" ] || [ -d "$HOME/.config/gh" ] || [ -n "$ANTHROPIC_API_KEY" ]; then
    exec ralph-loop "$@"
else
    echo "⚠️  Warning: No AI CLI auth detected. Proceeding anyway..."
    exec ralph-loop "$@"
fi
