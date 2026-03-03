#!/bin/bash

# VEBB-AI: Automated Recalibration Setup (Phase 48)
# This script schedules recalibrate.py to run every Sunday at midnight.

echo "⚙️ Setting up automated recalibration (Cron Job)..."

# 1. Get the absolute path of the current directory
PROJECT_DIR=$(pwd)
VENV_PATH="$PROJECT_DIR/venv/bin/python3"
SCRIPT_PATH="$PROJECT_DIR/recalibrate.py"
LOG_PATH="$PROJECT_DIR/logs/recalibration.log"

# Create logs directory if it doesn't exist
mkdir -p "$PROJECT_DIR/logs"

# 2. Define the Cron Command
# Runs every Sunday at 00:00 (Midnight)
# 0 0 * * 0 = Sunday Midnight
CRON_JOB="0 0 * * 0 cd $PROJECT_DIR && $VENV_PATH $SCRIPT_PATH >> $LOG_PATH 2>&1"

# 3. Add to Crontab (avoiding duplicates)
(crontab -l 2>/dev/null | grep -v "$SCRIPT_PATH"; echo "$CRON_JOB") | crontab -

echo "✅ Success! Periodic recalibration scheduled."
echo "   Schedule: Every Sunday at 00:00 UTC"
echo "   Activity log: $LOG_PATH"
echo ""
echo "Note: The bot must be running for this to be useful, as it needs live-logged data."
