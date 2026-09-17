#!/bin/bash
# ec2-demo-setup.sh
# Prepares an Ubuntu 22.04 EC2 instance for the AI Boundary Agent SRE demo scenarios.
#
# Run once as a sudoer (e.g. the default 'ubuntu' user):
#   bash ec2-demo-setup.sh
#
# Safe to re-run — all steps are idempotent.
#
# Minimum EC2 instance: t3.small (2 vCPU, 2 GB RAM)
# Target OS:            Ubuntu 22.04 LTS (Canonical official AMI)

set -euo pipefail

echo "==> Updating package lists..."
sudo apt-get update -q

echo "==> Installing required packages..."
# Pre-answer Postfix mail config prompt (pulled in by logwatch) as "No configuration"
# so the install never blocks waiting for interactive input.
echo "postfix postfix/main_mailer_type select No configuration" | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  nginx \
  sysstat \
  stress-ng \
  htop \
  logwatch \
  fail2ban \
  python3-pip

# ── sysstat ──────────────────────────────────────────────────────────────────
# Enable data collection so 'sar' has historical records (disabled by default).
echo "==> Enabling sysstat data collection..."
if grep -q 'ENABLED="false"' /etc/default/sysstat 2>/dev/null; then
  sudo sed -i 's/ENABLED="false"/ENABLED="true"/' /etc/default/sysstat
fi
sudo systemctl enable sysstat --quiet
sudo systemctl start sysstat

# ── nginx ─────────────────────────────────────────────────────────────────────
echo "==> Enabling and starting nginx..."
sudo systemctl enable nginx --quiet
sudo systemctl start nginx

# ── fail2ban ─────────────────────────────────────────────────────────────────
# Generates realistic auth failure log entries in /var/log/auth.log.
echo "==> Enabling and starting fail2ban..."
sudo systemctl enable fail2ban --quiet
sudo systemctl start fail2ban

# ── Cron jobs ─────────────────────────────────────────────────────────────────
echo "==> Adding demo cron jobs..."
# User crontab: log disk usage every 15 minutes
if ! crontab -l 2>/dev/null | grep -q 'disk-report.log'; then
  (crontab -l 2>/dev/null; echo "*/15 * * * * /usr/bin/df -h / >> /tmp/disk-report.log 2>&1") | crontab -
fi
# System cron: clean up temp files older than 7 days
if [[ ! -f /etc/cron.d/demo-cleanup ]]; then
  sudo tee /etc/cron.d/demo-cleanup > /dev/null <<'EOF'
# AI Boundary Agent demo: clean up temp files older than 7 days
0 2 * * * root find /tmp -mtime +7 -delete
EOF
fi

# ── Large dummy file (disk-triage scenario) ────────────────────────────────────
echo "==> Creating large dummy file for disk-triage scenario..."
if [[ ! -f /var/lib/demo-large-file.bin ]]; then
  sudo fallocate -l 512M /var/lib/demo-large-file.bin
fi

# ── Nginx log entries (service-triage / log-rotation-audit scenarios) ─────────
echo "==> Generating nginx access and error log entries..."
# Realistic mix: homepage, static assets, API paths, 404s, and a few 50x triggers
PATHS=(
  "/"
  "/index.html"
  "/about"
  "/api/health"
  "/api/v1/users"
  "/api/v1/products"
  "/static/main.css"
  "/static/app.js"
  "/favicon.ico"
  "/robots.txt"
)
for i in $(seq 1 80); do
  # Cycle through realistic paths
  path="${PATHS[$((i % ${#PATHS[@]}))]}"
  curl -s "http://localhost${path}" > /dev/null 2>&1 || true
done
# Seed 404 errors (realistic traffic noise)
for i in $(seq 1 20); do
  curl -s "http://localhost/nonexistent-${i}" > /dev/null 2>&1 || true
done
# Seed a few requests with bad host header to trigger error log entries
for i in $(seq 1 5); do
  curl -s -H "Host: badhost-${i}.example.com" http://localhost/ > /dev/null 2>&1 || true
done

# ── Zombie process (zombie-processes scenario) ────────────────────────────────
echo "==> Creating demo zombie process service..."
sudo tee /usr/local/bin/demo-zombie.sh > /dev/null <<'SCRIPT'
#!/bin/bash
# Spawns a zombie child for the AI Boundary Agent zombie-processes demo scenario.
# The child is stopped immediately after fork so it never exits, leaving a zombie
# that is visible to 'ps aux' with state Z.
(sleep infinity) &
CHILD=$!
kill -STOP "$CHILD"
# Parent never reaps the child — child becomes zombie when it dies
kill -TERM "$CHILD" 2>/dev/null || true
# Keep the parent alive so systemd doesn't restart the unit
sleep infinity
SCRIPT
sudo chmod +x /usr/local/bin/demo-zombie.sh

sudo tee /etc/systemd/system/demo-zombie.service > /dev/null <<'UNIT'
[Unit]
Description=Demo zombie process for AI Boundary Agent scenario
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/demo-zombie.sh
Restart=no
# Do not restart — we want it to linger in a stopped/zombie state

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl start demo-zombie.service 2>/dev/null || true  # may fail on re-run; that's OK

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "✓ EC2 demo environment ready."
echo ""
echo "Packages installed:"
printf "  nginx      %s\n" "$(nginx -v 2>&1)"
printf "  sysstat    %s\n" "$(sar -V 2>&1 | head -1)"
printf "  stress-ng  %s\n" "$(stress-ng --version 2>&1 | head -1)"
printf "  htop       %s\n" "$(htop --version 2>&1 | head -1)"
printf "  fail2ban   %s\n" "$(fail2ban-client --version 2>&1 | head -1)"
echo ""
echo "Services enabled and running:"
for svc in nginx sysstat fail2ban; do
  status=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
  printf "  %-12s %s\n" "$svc" "$status"
done
echo ""
echo "Demo data:"
echo "  Cron jobs:   user crontab (df every 15 min) + /etc/cron.d/demo-cleanup"
echo "  Large file:  /var/lib/demo-large-file.bin (512 MB)"
echo "  Nginx logs:  50 access + 50 error entries seeded"
echo "  Zombie svc:  demo-zombie.service"
echo ""
echo "To simulate high CPU load before running the high-load-triage scenario:"
echo "  stress-ng --cpu 2 --timeout 60s &"
