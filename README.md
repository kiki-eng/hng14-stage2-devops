# HNG Anomaly Detection Engine / DDoS Detection Tool

A real-time anomaly detection daemon that monitors HTTP traffic to a Nextcloud instance, learns normal traffic patterns through adaptive baselining, and automatically responds to DDoS attacks and suspicious activity via iptables blocking and Slack alerts.

## Live Endpoints

| Resource | URL |
|---|---|
| **Metrics Dashboard** | `http://<DASHBOARD_DOMAIN>` |
| **Nextcloud** | `http://<SERVER_IP>` |
| **GitHub Repo** | `https://github.com/<YOUR_USERNAME>/<YOUR_REPO>` |

> Replace `<DASHBOARD_DOMAIN>`, `<SERVER_IP>`, and GitHub link before submission.

---

## Language Choice: Python

Python was chosen for several reasons:

1. **Rapid prototyping** — the tight deadline demanded a language with minimal boilerplate
2. **Excellent standard library** — `collections.deque`, `threading`, `json`, `subprocess` cover every core requirement without third-party rate-limiting libraries
3. **Ecosystem** — Flask provides a lightweight dashboard server; `psutil` gives cross-platform system metrics
4. **Readability** — the detection logic is inherently algorithmic; Python's clarity makes the z-score calculations, baseline management, and sliding window eviction logic easy to audit and extend

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Internet                              │
└──────────────┬───────────────────────────────────────────────┘
               │  HTTP :80
┌──────────────▼───────────────────────────────────────────────┐
│  Nginx (reverse proxy)                                       │
│  • JSON access log → /var/log/nginx/hng-access.log           │
│  • X-Forwarded-For → real client IP                          │
│  • Named volume: HNG-nginx-logs                              │
└──────────────┬───────────────────────────────────────────────┘
               │  proxy_pass :80
┌──────────────▼──────────┐   ┌────────────────────────────────┐
│  Nextcloud              │   │  Anomaly Detector Daemon       │
│  (kefaslungu/hng-       │   │  (Python, host network)        │
│   nextcloud)            │   │                                │
│  Mounts HNG-nginx-logs  │   │  ┌─ monitor.py (tail log)     │
│  read-only              │   │  ├─ baseline.py (30min roll)   │
│                         │   │  ├─ detector.py (z-score/5x)   │
│                         │   │  ├─ blocker.py (iptables)      │
└─────────────────────────┘   │  ├─ unbanner.py (backoff)      │
                              │  ├─ notifier.py (Slack)        │
                              │  └─ dashboard.py (Flask :8080) │
                              │                                │
                              │  Mounts HNG-nginx-logs ro      │
                              │  cap_add: NET_ADMIN, NET_RAW   │
                              └────────────────────────────────┘
```

---

## How the Sliding Window Works

### Deque Structure

Each sliding window is a `collections.deque` that stores raw **timestamps** (epoch floats) of individual events within the last 60 seconds.

```
Global window:  deque([1714400001.23, 1714400001.45, 1714400002.10, ...])
Per-IP windows: {"1.2.3.4": deque([...]), "5.6.7.8": deque([...])}
```

There are **two separate pairs** of windows:
- **Request windows** — one global, one per-IP — tracking all HTTP requests
- **Error windows** — one global, one per-IP — tracking only 4xx/5xx responses

### Eviction Logic

On every new event insertion **and** on every rate query, the window runs:

```python
cutoff = now - window_seconds   # now - 60
while self._deque and self._deque[0] < cutoff:
    self._deque.popleft()       # O(1) removal from left
```

Because timestamps arrive in monotonically increasing order and `deque.popleft()` is O(1), eviction is always efficient regardless of traffic volume.

### Rate Calculation

```python
rate = len(self._deque) / window_seconds
```

This gives a true **requests-per-second** average over the last 60 seconds — not a per-minute counter divided by 60.

---

## How the Rolling Baseline Works

### Window Size and Recalculation

| Parameter | Value |
|---|---|
| Window size | 30 minutes of per-second counts |
| Recalculation interval | Every 60 seconds |
| Minimum samples | 30 (before baseline activates) |
| Floor mean | 2.0 req/s (prevents division-by-zero on quiet periods) |
| Floor stddev | 1.0 (prevents zero-variance false positives) |

### Per-Second Counting

The detector maintains an epoch-second counter. Every time the clock ticks to a new second, the previous second's total request count is pushed to the baseline's deque:

```python
baseline.record_second(epoch_second, count, error_count)
```

### Per-Hour Slots

The baseline maintains separate `HourlySlot` accumulators keyed by hour-of-day (0–23). Each slot tracks running `total`, `sum_sq`, and `n` to compute mean and stddev in O(1).

During recalculation, the **current hour's slot is preferred** if it has ≥ `min_samples` data points. This means the baseline adapts to diurnal traffic patterns — quiet overnight hours won't inflate daytime thresholds.

### Recalculation Flow

```
Every 60 seconds:
  1. Evict entries older than 30 minutes from the deque
  2. Check current hour's HourlySlot
     - If slot has ≥ 30 samples → use slot's mean and stddev
     - Otherwise → compute from full 30-minute deque
  3. Apply floor values: mean = max(raw_mean, 2.0), stddev = max(raw_std, 1.0)
  4. Publish effective_mean and effective_stddev
  5. Append to history deque (for dashboard graphing)
  6. Write audit log entry: BASELINE_RECALC
```

---

## Detection Logic

### Per-IP Anomaly

An IP is flagged when **either** condition fires:

1. **Z-score test**: `(ip_rate - effective_mean) / effective_stddev > 3.0`
2. **Rate multiplier test**: `ip_rate > 5.0 × effective_mean`

### Error Surge Tightening

If an IP's 4xx/5xx rate exceeds `3× baseline_error_mean`, thresholds tighten:
- Z-score threshold drops from 3.0 → 2.0
- Rate multiplier drops from 5.0× → 3.0×

### Global Anomaly

Same z-score and rate-multiplier tests applied to the **global** request rate (all IPs combined). Triggers a Slack alert but **no blocking**.

### Alert Cooldown

To prevent alert floods, each IP and the global channel have a 30-second cooldown between consecutive alerts.

---

## Blocking and Auto-Unban

### iptables Blocking

Per-IP anomaly → `iptables -A INPUT -s <ip> -j DROP` (within 10 seconds of detection).

### Backoff Schedule

| Offense | Ban Duration |
|---|---|
| 1st | 10 minutes |
| 2nd | 30 minutes |
| 3rd | 2 hours |
| 4th+ | Permanent |

The `AutoUnbanner` thread checks for expired bans every 30 seconds and removes the iptables rule. Every unban triggers a Slack notification.

---

## Setup Instructions (Fresh VPS)

### Prerequisites

- Linux VPS with ≥ 2 vCPU, 2 GB RAM (Ubuntu 22.04+ recommended)
- Docker Engine ≥ 24.0
- Docker Compose v2
- A domain or subdomain pointed at the server's IP (for the dashboard)
- A Slack webhook URL

### Step 1: Install Docker

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

### Step 2: Clone the Repository

```bash
git clone https://github.com/<YOUR_USERNAME>/<YOUR_REPO>.git
cd <YOUR_REPO>
```

### Step 3: Configure Environment

```bash
cp .env.example .env
nano .env
```

Set these values:
```
POSTGRES_PASSWORD=<strong random password>
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx
```

### Step 4: (Optional) Configure Dashboard Domain

If you want the dashboard served on a subdomain, add an Nginx server block or set up a separate Nginx vhost pointing to port 8080. The detector dashboard runs on port 8080 by default.

### Step 5: Deploy

```bash
docker compose up -d --build
```

### Step 6: Verify

```bash
# Check all containers are running
docker compose ps

# Run integration tests
chmod +x integration.sh
./integration.sh

# Check detector logs
docker compose logs -f detector

# Check Nginx access log is being written
docker compose exec nginx cat /var/log/nginx/hng-access.log | head -5
```

### Step 7: Access

- **Nextcloud**: `http://<SERVER_IP>/`
- **Dashboard**: `http://<SERVER_IP>:8080/` or `http://<DASHBOARD_DOMAIN>/`

---

## Repository Structure

```
detector/
  main.py          # Entry point — orchestrates all components
  monitor.py       # Tails and parses Nginx JSON access log
  baseline.py      # Rolling 30-minute baseline with per-hour slots
  detector.py      # Anomaly detection (z-score, rate multiplier, error surge)
  blocker.py       # iptables DROP rule management with backoff
  unbanner.py      # Auto-unban daemon with Slack notifications
  notifier.py      # Slack webhook alert sender
  dashboard.py     # Flask live-metrics web UI
  config.yaml      # All configurable thresholds
  requirements.txt # Python dependencies
  Dockerfile       # Detector container image
nginx/
  nginx.conf       # Reverse proxy + JSON access log config
docs/
  architecture.png # System architecture diagram
screenshots/
  Tool-running.png
  Ban-slack.png
  Unban-slack.png
  Global-alert-slack.png
  Iptables-banned.png
  Audit-log.png
  Baseline-graph.png
README.md
docker-compose.yml
integration.sh
.env.example
```

---

## Screenshots

> Add screenshots to the `screenshots/` directory after deploying and triggering test traffic.

1. **Tool-running.png** — Daemon running, processing log lines
2. **Ban-slack.png** — Slack ban notification
3. **Unban-slack.png** — Slack unban notification
4. **Global-alert-slack.png** — Slack global anomaly notification
5. **Iptables-banned.png** — `sudo iptables -L -n` showing a blocked IP
6. **Audit-log.png** — Structured log with ban, unban, and baseline recalculation events
7. **Baseline-graph.png** — Baseline over time showing at least two hourly slots

---

## Blog Post

> [Link to your blog post here]

---

## License

MIT
