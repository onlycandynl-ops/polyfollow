#!/bin/bash
# PolyFollow VPS Deploy Script
# Run this on your Hostinger VPS

set -e

echo "🚀 Deploying PolyFollow..."

# Install Python deps
pip3 install -r requirements.txt

# Create directories
mkdir -p logs data

# Copy .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  Edit .env with your Telegram credentials before running!"
    exit 1
fi

# Run with nohup (keeps running after SSH disconnect)
nohup python3 main.py > logs/output.log 2>&1 &
PID=$!
echo $PID > polyfollow.pid

echo "✅ PolyFollow running with PID $PID"
echo "📋 Tail logs: tail -f logs/polyfollow.log"
echo "🛑 Stop: kill \$(cat polyfollow.pid)"
