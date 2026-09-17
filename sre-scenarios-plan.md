# Plan: SRE / Platform Engineering Scenarios for the Autonomous Agent

## Overview

The autonomous agent (`main_autonomous.py` + `agent_autonomous.py`) currently
ships with three trivial example goals in the README. This plan:

1. Expands the scenario catalogue to **12 production-realistic SRE / platform engineering goals**,
   including more interesting multi-signal and chaos-style scenarios.
2. Adds a `--scenario` CLI flag and `--list-scenarios` to `main_autonomous.py`.
3. Provides a full **EC2 prerequisites setup script** so the instance is ready to
   demonstrate every scenario without manual prep.
4. Updates `README.md` with the scenario reference table and setup instructions.

No changes to the core agent loop, tools, or Boundary integration are required.

---

## EC2 Prerequisites

### What is pre-installed on a stock Ubuntu 22.04 EC2 AMI

The following are available **without any setup**:

| Tool / Command | Available by default |
|---|---|
| `systemd`, `systemctl`, `journalctl` | ✅ Yes |
| `ps`, `top`, `free`, `df`, `du`, `uptime` | ✅ Yes |
| `ss`, `netstat`, `ip` | ✅ Yes (`iproute2` + `net-tools`) |
| `cron` / `crontab` | ✅ Yes (`cron` daemon) |
| `logrotate` | ✅ Yes |
| `last`, `lastb`, `who` | ✅ Yes (`util-linux`) |
| `/var/log/auth.log`, `/var/log/syslog` | ✅ Yes (`rsyslog`) |
| `lscpu`, `lsmem`, `uname`, `lsb_release` | ✅ Yes |
| `dpkg`, `apt` | ✅ Yes |
| `find`, `grep`, `awk`, `sed`, `sort`, `uniq` | ✅ Yes |

### What must be installed (one-time setup)

The following are **NOT** on a stock AMI and must be installed before the demo:

| Package | Purpose | Scenario(s) |
|---|---|---|
| `nginx` | Web server with access/error logs; used for service triage, log analysis | 5, 9 |
| `sysstat` | Provides `sar`, `iostat`, `mpstat` for historical I/O and CPU metrics | 2, 3 |
| `stress-ng` | Generates realistic CPU, memory, and I/O load for live demos | 3 |
| `htop` | Interactive process viewer; nicer output than `top` for demos | 1, 2 |
| `logwatch` | Log summarisation tool; used in log audit scenario | 9 |
| `python3-pip` | Needed to run a simple background HTTP workload generator | 9 |
| `fail2ban` | Generates auth failure log entries; makes auth-audit scenario realistic | 6 |

### One-time EC2 setup script

Run this once as the ubuntu user (or via `user-data` at launch) to prepare the instance.
Save as `ec2-demo-setup.sh` and run: `bash ec2-demo-setup.sh`

```bash
#!/bin/bash
# ec2-demo-setup.sh
# Prepares an Ubuntu 22.04 EC2 instance for the AI boundary agent SRE demo scenarios.
# Run once as a sudoer (e.g. the default 'ubuntu' user).

set -euo pipefail

echo "==> Updating package lists..."
sudo apt-get update -q

echo "==> Installing required packages..."
sudo apt-get install -y \
  nginx \
  sysstat \
  stress-ng \
  htop \
  logwatch \
  fail2ban \
  python3-pip

echo "==> Enabling sysstat data collection (needed for 'sar' historical data)..."
sudo sed -i 's/ENABLED="false"/ENABLED="true"/' /etc/default/sysstat
sudo systemctl enable sysstat
sudo systemctl start sysstat

echo "==> Starting and enabling nginx..."
sudo systemctl enable nginx
sudo systemctl start nginx

echo "==> Enabling and starting fail2ban (generates auth log entries)..."
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

echo "==> Creating a sample cron job for the current user..."
(crontab -l 2>/dev/null; echo "*/15 * * * * /usr/bin/df -h / >> /tmp/disk-report.log 2>&1") | crontab -

echo "==> Adding a system-level cron job example..."
sudo tee /etc/cron.d/demo-cleanup > /dev/null <<'EOF'
# Demo: clean up temp files older than 7 days
0 2 * * * root find /tmp -mtime +7 -delete
EOF

echo "==> Placing a large dummy file to make disk-triage interesting..."
sudo fallocate -l 512M /var/lib/demo-large-file.bin 2>/dev/null || true

echo "==> Generating some nginx log entries..."
for i in $(seq 1 50); do
  curl -s http://localhost/ > /dev/null 2>&1 || true
  curl -s http://localhost/nonexistent > /dev/null 2>&1 || true
done

echo "==> Generating a zombie process for the zombie-process scenario..."
# Create a persistent zombie for demo purposes (a shell one-liner)
sudo tee /usr/local/bin/demo-zombie.sh > /dev/null <<'EOF'
#!/bin/bash
# Spawns a zombie child process that persists until demo-zombie service is stopped
(sleep infinity) & CHILD=$!
kill -STOP $CHILD
wait $CHILD &
sleep infinity
EOF
sudo chmod +x /usr/local/bin/demo-zombie.sh
sudo tee /etc/systemd/system/demo-zombie.service > /dev/null <<'EOF'
[Unit]
Description=Demo zombie process (for AI agent scenario)

[Service]
ExecStart=/usr/local/bin/demo-zombie.sh
Restart=no

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl start demo-zombie.service

echo ""
echo "✓ EC2 demo environment ready."
echo ""
echo "Installed:"
echo "  nginx      - $(nginx -v 2>&1)"
echo "  sysstat    - $(sar -V 2>&1 | head -1)"
echo "  stress-ng  - $(stress-ng --version 2>&1 | head -1)"
echo "  htop       - $(htop --version 2>&1 | head -1)"
echo "  fail2ban   - $(fail2ban-client --version 2>&1 | head -1)"
echo ""
echo "Services running: nginx, sysstat, fail2ban, demo-zombie"
echo "Cron jobs:        user crontab + /etc/cron.d/demo-cleanup"
echo "Large file:       /var/lib/demo-large-file.bin (512MB)"
```

---

## Scenario Catalogue (12 scenarios)

### Category: Observability & Health Checks

#### `health-check`
**Goal:** `"Run a full system health check: CPU load average, memory pressure, disk usage across all mount points, and top 5 resource-consuming processes"`

**Commands the agent will use:** `uptime`, `free -h`, `df -h`, `ps aux --sort=-%cpu | head -6`

**Prerequisites:** None — all tools are default on Ubuntu 22.04.

---

#### `zombie-processes`
**Goal:** `"Find all zombie or uninterruptible-sleep (D-state) processes, report their PIDs, names, and parent processes"`

**Commands the agent will use:** `ps aux | awk '$8 ~ /^[ZD]/'`, `ps -o pid,ppid,stat,comm -p <PID>`

**Prerequisites:** `demo-zombie.service` from setup script (creates a visible zombie).

---

#### `io-performance`
**Goal:** `"Analyse current disk I/O activity: report top I/O-consuming processes, average read/write throughput, and any processes in I/O wait"`

**Commands the agent will use:** `iostat -xz 1 3` (from `sysstat`), `iotop -b -n 3` (if installed), `ps aux | awk '$8=="D"'`

**Prerequisites:** `sysstat` package (provides `iostat`).

---

### Category: Incident Triage

#### `disk-triage`
**Goal:** `"Investigate disk usage: find the top 10 largest files and directories, identify any partitions over 80% full, and check for recently created large files"`

**Commands the agent will use:** `df -h`, `du -sh /* 2>/dev/null | sort -rh | head 10`, `find / -size +100M -not -path '*/proc/*' 2>/dev/null`

**Prerequisites:** `ec2-demo-setup.sh` creates a 512MB `/var/lib/demo-large-file.bin` to make this interesting.

---

#### `memory-leak-triage`
**Goal:** `"Identify the top 5 processes consuming the most memory, report their RSS and VSZ, and check if any process is near the OOM threshold"`

**Commands the agent will use:** `ps aux --sort=-%mem | head -6`, `cat /proc/meminfo`, `dmesg | grep -i 'oom\|killed'`

**Prerequisites:** None — all commands are available by default.

---

#### `auth-audit`
**Goal:** `"Audit authentication security: find the last 10 failed login attempts, list all currently logged-in users, and check for any accounts with recent sudo usage"`

**Commands the agent will use:** `grep -i 'failed\|invalid' /var/log/auth.log | tail -20`, `who`, `last -n 20`, `grep sudo /var/log/auth.log | tail -10`

**Prerequisites:** `fail2ban` from setup script generates realistic auth failure log entries.

---

#### `service-triage`
**Goal:** `"Triage the nginx web server: check its systemd service status, inspect the last 50 error log lines, list open ports and connections, and report its current memory and CPU usage"`

**Commands the agent will use:** `systemctl status nginx`, `tail -50 /var/log/nginx/error.log`, `ss -tlnp | grep nginx`, `ps aux | grep nginx`

**Prerequisites:** `nginx` must be installed and running (setup script handles this).

---

#### `high-load-triage`
**Goal:** `"The host is under unexplained high CPU load — identify the top CPU-consuming processes, check system load average trend, inspect recent kernel messages, and report findings"`

**Commands the agent will use:** `uptime`, `top -bn1 | head -20`, `ps aux --sort=-%cpu | head -10`, `dmesg | tail -20`, `vmstat 1 5`

**Prerequisites:** Optionally run `stress-ng --cpu 2 --timeout 60s &` before the demo to generate visible CPU load. `stress-ng` is installed by setup script.

---

### Category: Platform / Infrastructure Ops

#### `failed-services`
**Goal:** `"Audit all systemd services: list every service that is failed or degraded, report the last error for each, and identify any services that restarted more than 3 times"`

**Commands the agent will use:** `systemctl --failed --no-pager`, `journalctl -u <service> -n 20 --no-pager`, `systemctl show <service> --property=NRestarts`

**Prerequisites:** `demo-zombie.service` may be in a stopped/failed state after demo. Optionally manually stop `fail2ban` to show a real failed service.

---

#### `system-inventory`
**Goal:** `"Generate a complete system inventory: OS version, kernel, CPU model and core count, total memory, disk layout, installed package count, and system uptime"`

**Commands the agent will use:** `lsb_release -a`, `uname -r`, `lscpu | grep -E 'Model|CPU\(s\)'`, `free -h`, `lsblk`, `dpkg -l | wc -l`, `uptime`

**Prerequisites:** None — all commands are available by default.

---

#### `cron-audit`
**Goal:** `"Audit all scheduled jobs on this host: list user crontabs, system cron jobs in /etc/cron.d and /etc/cron.daily, and check the cron daemon logs for the last 10 executions"`

**Commands the agent will use:** `crontab -l`, `ls -la /etc/cron.*`, `cat /etc/cron.d/*`, `grep CRON /var/log/syslog | tail -10`

**Prerequisites:** Setup script installs a user crontab entry and `/etc/cron.d/demo-cleanup`.

---

#### `log-rotation-audit`
**Goal:** `"Audit log management: check logrotate configuration for nginx and syslog, verify log sizes, find any log files larger than 50MB, and confirm logrotate last run date"`

**Commands the agent will use:** `cat /etc/logrotate.d/nginx`, `ls -lh /var/log/nginx/`, `find /var/log -size +50M`, `cat /var/lib/logrotate/status | head -20`

**Prerequisites:** `nginx` installed and running (generates log files). Setup script generates 50 nginx log entries to populate access.log.

---

#### `onboarding-audit`
**Goal:** `"Run a new SRE onboarding audit: list all non-system user accounts, check which users have sudo privileges, review SSH authorized_keys for each user, and report the last login time for each"`

**Commands the agent will use:** `awk -F: '$3 >= 1000 {print $1}' /etc/passwd`, `grep -v '^#' /etc/sudoers 2>/dev/null | grep -v '^$'`, `ls -la /home/*/.ssh/authorized_keys 2>/dev/null`, `lastlog | grep -v 'Never'`

**Prerequisites:** None — all commands are default. Reinforces the pitch claim that SRE onboarding drops from ~1 day to ~10 minutes.

---

## Sub-Task 1 — Add `src/agent/scenarios.py` scenario catalogue

**Status:** [ ] pending

**Intent**
Centralise all 13 SRE scenario definitions in one importable module. No
coupling to the rest of the codebase — pure data. The CLI and README both
reference this module as the single source of truth.

**Expected Outcomes**
- `src/agent/scenarios.py` exists with all 13 scenarios defined.
- Each scenario is a `dataclass` with `key`, `name`, `category`, `goal`, and `notes` fields.
- `SCENARIOS: dict[str, Scenario]` maps key → scenario for O(1) lookup.
- `list_scenarios()` returns `dict[str, list[Scenario]]` grouped by category.

**Todo List**
1. Create `src/agent/scenarios.py`.
2. Define `Scenario` dataclass: `key`, `name`, `category`, `goal`, `notes` (prerequisites note).
3. Populate all 13 scenarios as listed in the catalogue above.
4. Build `SCENARIOS` dict and `list_scenarios()` helper.

**Relevant Context**
- `agent_autonomous.py` goal strings are currently only in README examples.
- `main_autonomous.py` will import this module in Sub-Task 2.

---

## Sub-Task 2 — Add `--scenario` / `--list-scenarios` flags to `main_autonomous.py`

**Status:** [ ] pending

**Intent**
Let the user pick a named scenario with a short key instead of typing the full
goal string. `--list-scenarios` prints a formatted table so operators can
discover scenarios at the terminal.

**Expected Outcomes**
- `python main_autonomous.py --scenario health-check` runs the health-check goal.
- `python main_autonomous.py --list-scenarios` prints a Rich table and exits.
- Positional `GOAL` argument still works unchanged (backward-compatible).
- `--scenario` and positional `GOAL` are mutually exclusive.
- Unknown scenario key prints a helpful error with list of valid keys.

**Todo List**
1. Import `scenarios` module in `main_autonomous.py`.
2. Make positional `goal` argument optional (`nargs="?"`).
3. Add `--scenario KEY` and `--list-scenarios` to the argparse parser.
4. Add mutual exclusion: error if both `--scenario` and positional goal are provided.
5. Implement `--list-scenarios` using a Rich `Table` (Category / Key / Name / Notes columns).
6. Implement `--scenario KEY` resolution with error on unknown key.
7. Display the scenario name in the startup banner when a named scenario is used.

**Relevant Context**
- `main_autonomous.py` already uses `rich.console.Console`, `Rule`, `Table` — reuse these.
- `argparse` mutual exclusion: use `add_mutually_exclusive_group()` or manual guard.
- See `_resolve_auth_method()` for the existing argparse + env-var pattern.

---

## Sub-Task 3 — Add `ec2-demo-setup.sh` to the repository

**Status:** [ ] pending

**Intent**
Make it trivial for anyone to prepare their EC2 instance for every scenario.
The script is idempotent and safe to re-run.

**Expected Outcomes**
- `ec2-demo-setup.sh` exists at the repo root.
- Script is executable (`chmod +x`).
- Script installs all required packages, enables services, seeds demo data.
- Output clearly reports what was installed and what is running.

**Todo List**
1. Write `ec2-demo-setup.sh` with the full content from the "EC2 Prerequisites" section above.
2. Ensure idempotency: re-running should not fail if packages are already installed.
3. Mark executable in git: `git add --chmod=+x ec2-demo-setup.sh`.

**Relevant Context**
- Target: Ubuntu 22.04 LTS EC2 (Canonical official AMI, `t3.small` or larger recommended).
- The `ubuntu` user has passwordless sudo by default on AWS Ubuntu AMIs.
- Minimum instance size: `t3.small` (2 vCPU, 2 GB RAM) — `stress-ng` needs headroom.

---

## Sub-Task 4 — Update `README.md` with scenario reference and setup guide

**Status:** [ ] pending

**Intent**
The README is the first thing a demo audience sees. Add the full scenario
catalogue and the EC2 setup instructions so anyone can reproduce the demo.

**Expected Outcomes**
- New "EC2 Demo Setup" section with setup steps and script reference.
- New "SRE Scenario Reference" section with a table of all 12 scenarios.
- "Running the Autonomous Agent" section updated with `--scenario` and `--list-scenarios` examples.
- Existing architecture, prerequisites, and branch strategy content preserved.

**Todo List**
1. Add "EC2 Demo Setup" section (one-liner: `bash ec2-demo-setup.sh`, plus what it installs).
2. Add "SRE Scenario Reference" table: Category / Key / Goal / Prerequisites.
3. Update CLI examples to include `--scenario` and `--list-scenarios` invocations.
4. Keep all existing sections intact.

**Relevant Context**
- Current README "Running the Autonomous Agent" section starts around line 103.
- All 12 scenario keys must match exactly between README, `scenarios.py`, and CLI.
