"""
scenarios.py
Catalogue of named SRE / platform engineering goal strings for the autonomous agent.

Each scenario is a short key that maps to a full goal string understood by the
autonomous planner. Pass the key via --scenario on the CLI rather than typing
the full goal string.

Usage:
    python main_autonomous.py --scenario health-check
    python main_autonomous.py --list-scenarios
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    category: str
    goal: str
    notes: str  # EC2 prerequisites / demo tips


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

_SCENARIOS_LIST: list[Scenario] = [

    # ── Observability & Health Checks ─────────────────────────────────────

    Scenario(
        key="health-check",
        name="Full System Health Check",
        category="Observability & Health Checks",
        goal=(
            "Run a full system health check: report CPU load average, memory pressure, "
            "disk usage across all mount points, and the top 5 resource-consuming processes"
        ),
        notes="No prerequisites — all tools are default on Ubuntu 22.04.",
    ),

    Scenario(
        key="zombie-processes",
        name="Zombie & D-State Process Hunt",
        category="Observability & Health Checks",
        goal=(
            "Find all zombie or uninterruptible-sleep (D-state) processes on this host, "
            "report their PIDs, names, and parent processes"
        ),
        notes="Run ec2-demo-setup.sh first — it creates a demo-zombie systemd service.",
    ),

    Scenario(
        key="io-performance",
        name="Disk I/O Performance Analysis",
        category="Observability & Health Checks",
        goal=(
            "Analyse current disk I/O activity: report top I/O-consuming processes, "
            "average read/write throughput, and any processes stuck in I/O wait"
        ),
        notes="Requires sysstat (iostat). Install via ec2-demo-setup.sh.",
    ),

    # ── Incident Triage ───────────────────────────────────────────────────

    Scenario(
        key="disk-triage",
        name="Disk Usage Triage",
        category="Incident Triage",
        goal=(
            "Investigate disk usage: find the top 10 largest files and directories, "
            "identify any partitions over 80% full, and check for recently created large files"
        ),
        notes="ec2-demo-setup.sh plants a 512MB dummy file to make this scenario interesting.",
    ),

    Scenario(
        key="memory-leak-triage",
        name="Memory Leak Triage",
        category="Incident Triage",
        goal=(
            "Identify the top 5 processes consuming the most memory, report their RSS and VSZ, "
            "and check if any process is near the OOM kill threshold"
        ),
        notes="No prerequisites — all commands available by default.",
    ),

    Scenario(
        key="auth-audit",
        name="Authentication Security Audit",
        category="Incident Triage",
        goal=(
            "Audit authentication security: find the last 10 failed login attempts, "
            "list all currently logged-in users, and check for accounts with recent sudo usage"
        ),
        notes="Install fail2ban via ec2-demo-setup.sh to seed realistic auth failure log entries.",
    ),

    Scenario(
        key="service-triage",
        name="Nginx Service Triage",
        category="Incident Triage",
        goal=(
            "Triage the nginx web server: check its systemd service status, inspect the last "
            "50 error log lines, list open ports and active connections, and report its current "
            "memory and CPU usage"
        ),
        notes="Requires nginx. Install and start via ec2-demo-setup.sh.",
    ),

    Scenario(
        key="high-load-triage",
        name="High CPU Load Triage",
        category="Incident Triage",
        goal=(
            "The host is under unexplained high CPU load — identify the top CPU-consuming "
            "processes, check the system load average trend, inspect recent kernel messages, "
            "and report findings"
        ),
        notes=(
            "For a live demo, run 'stress-ng --cpu 2 --timeout 60s &' before invoking the agent. "
            "Install stress-ng via ec2-demo-setup.sh."
        ),
    ),

    # ── Platform / Infrastructure Ops ─────────────────────────────────────

    Scenario(
        key="failed-services",
        name="Failed & Degraded Services Audit",
        category="Platform / Infrastructure Ops",
        goal=(
            "Audit all systemd services: list every service that is failed or degraded, "
            "report the last error message for each, and identify any service that has "
            "restarted more than 3 times"
        ),
        notes=(
            "For a visible failed service, manually stop fail2ban before the demo: "
            "'sudo systemctl stop fail2ban'."
        ),
    ),

    Scenario(
        key="system-inventory",
        name="System Inventory Report",
        category="Platform / Infrastructure Ops",
        goal=(
            "Generate a complete system inventory: OS version, kernel, CPU model and core count, "
            "total memory, disk layout, installed package count, and system uptime"
        ),
        notes="No prerequisites — all commands available by default.",
    ),

    Scenario(
        key="cron-audit",
        name="Scheduled Jobs Audit",
        category="Platform / Infrastructure Ops",
        goal=(
            "Audit all scheduled jobs on this host: list user crontabs, system cron jobs in "
            "/etc/cron.d and /etc/cron.daily, and check the cron daemon logs for the last "
            "10 executions"
        ),
        notes="ec2-demo-setup.sh adds a user crontab entry and /etc/cron.d/demo-cleanup.",
    ),

    Scenario(
        key="log-rotation-audit",
        name="Log Rotation Audit",
        category="Platform / Infrastructure Ops",
        goal=(
            "Audit log management: check logrotate configuration for nginx and syslog, "
            "verify current log sizes, find any log files larger than 50MB, and confirm "
            "the logrotate last-run date"
        ),
        notes=(
            "Requires nginx. ec2-demo-setup.sh generates 50 nginx log entries to populate "
            "access.log and error.log."
        ),
    ),

    Scenario(
        key="onboarding-audit",
        name="SRE Onboarding Audit",
        category="Platform / Infrastructure Ops",
        goal=(
            "Run a new SRE onboarding audit: list all non-system user accounts, check which "
            "users have sudo privileges, review SSH authorized_keys for each user, and report "
            "the last login time for each"
        ),
        notes=(
            "No prerequisites — all commands are default. Demonstrates that SRE onboarding "
            "access verification drops from ~1 day to ~10 minutes."
        ),
    ),

    # ── Nginx Operations ──────────────────────────────────────────────────

    Scenario(
        key="nginx-access-analysis",
        name="Nginx Traffic Pattern Analysis",
        category="Nginx Operations",
        goal=(
            "Analyse nginx access logs: find the top 10 most requested URLs, "
            "top 5 client IP addresses, HTTP 4xx and 5xx error rate, and total "
            "request count in the last hour"
        ),
        notes=(
            "Requires nginx with populated access.log. "
            "ec2-demo-setup.sh seeds 50 log entries across multiple URLs and error codes."
        ),
    ),

    Scenario(
        key="nginx-config-audit",
        name="Nginx Configuration Audit",
        category="Nginx Operations",
        goal=(
            "Audit the nginx configuration: list all enabled server blocks, "
            "check for syntax errors with nginx -t, verify the full config dump "
            "with nginx -T, and report which ports nginx is currently listening on"
        ),
        notes="Requires nginx installed and running. No additional setup needed.",
    ),

    Scenario(
        key="nginx-incident-response",
        name="Nginx Incident Response",
        category="Nginx Operations",
        goal=(
            "Nginx may be having issues — perform a full incident response: "
            "check the service health and uptime, inspect the last 100 error log lines "
            "for recurring patterns, verify port 80 is accepting connections with curl, "
            "check for recent modifications to the nginx config, and provide a "
            "root cause hypothesis"
        ),
        notes=(
            "Requires nginx. For a realistic demo, introduce a fault first: "
            "'sudo nginx -s stop' then run this scenario — the agent will detect "
            "the outage, inspect logs, and report the service is down."
        ),
    ),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, Scenario] = {s.key: s for s in _SCENARIOS_LIST}


def list_scenarios() -> dict[str, list[Scenario]]:
    """Return all scenarios grouped by category, preserving definition order."""
    grouped: dict[str, list[Scenario]] = {}
    for scenario in _SCENARIOS_LIST:
        grouped.setdefault(scenario.category, []).append(scenario)
    return grouped
