#!/bin/bash
# Auto-restart training loop
# train.py now exits after 120s hang (faulthandler exit=True)
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') Starting train.py..."
    PYTHONUNBUFFERED=1 poke-rl/Scripts/python.exe -u train.py >> training_current.log 2>&1
    EXIT_CODE=$?
    echo "$(date '+%Y-%m-%d %H:%M:%S') train.py exited with code $EXIT_CODE. Restarting in 5s..."
    sleep 5
done
