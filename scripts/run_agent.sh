#!/bin/bash
cd /app
echo "=========================================="
echo "[$(date)] Starting scheduled execution"
echo "=========================================="
python main.py --real
echo "[$(date)] Execution completed"
echo ""
