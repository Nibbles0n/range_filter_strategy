#!/usr/bin/env bash
# daily_report.sh — Run the daily performance report generator
#
# Cron example (runs every day at 09:00 UTC):
#   0 9 * * * cd /home/picaso/.openclaw/workspace/range_filter_strategy && python3 generate_report.py >> reports.log 2>&1
#
# Or with full path:
#   0 9 * * * /usr/bin/python3 /home/picaso/.openclaw/workspace/range_filter_strategy/generate_report.py >> /home/picaso/.openclaw/workspace/range_filter_strategy/reports.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORTS_DIR="${SCRIPT_DIR}/reports"
LOG_FILE="${SCRIPT_DIR}/reports.log"

# Ensure reports/ dir exists
mkdir -p "${REPORTS_DIR}"

# Run the report generator
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Running daily report..." >> "${LOG_FILE}"
cd "${SCRIPT_DIR}"
python3 generate_report.py >> "${LOG_FILE}" 2>&1
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Done." >> "${LOG_FILE}"
