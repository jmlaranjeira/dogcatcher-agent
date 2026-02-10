#!/bin/bash
set -e

echo "Dogcatcher Agent starting..."
echo "Schedule: ${DOGCATCHER_SCHEDULE:-0 * * * *}"

# Run once on startup
echo "Running initial execution..."
python main.py --real || echo "Initial run failed, but continuing with scheduler"

echo ""
echo "Starting scheduler (cron: ${DOGCATCHER_SCHEDULE:-0 * * * *})"
echo "  Next executions will run automatically."
echo "  Use 'docker logs dogcatcher-agent -f' to monitor."
echo ""

# Export environment variables for cron
printenv | sed 's/=\(.*\)/="\1"/' > /app/.env.cron

# Create wrapper script that loads env vars before running the agent
cat > /app/run_with_env.sh << 'WRAPPER'
#!/bin/bash
set -a
source /app/.env.cron
set +a
cd /app
echo "=========================================="
echo "[$(date)] Starting scheduled execution"
echo "=========================================="
python main.py --real
echo "[$(date)] Execution completed"
echo ""
WRAPPER
chmod +x /app/run_with_env.sh

# Set up crontab
echo "${DOGCATCHER_SCHEDULE:-0 * * * *} /app/run_with_env.sh >> /proc/1/fd/1 2>&1" | crontab -

# Run cron in foreground
exec cron -f
