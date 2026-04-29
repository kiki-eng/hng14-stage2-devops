"""
Live Metrics Dashboard — Flask app that serves a real-time dashboard refreshing
every 3 seconds.  Shows banned IPs, global req/s, top 10 source IPs,
CPU/memory usage, effective mean/stddev, and uptime.
"""

import time
import psutil
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# These are set by main.py before the server starts
_state = {}


def init_dashboard(detector, baseline, blocker, monitor, start_time):
    _state["detector"] = detector
    _state["baseline"] = baseline
    _state["blocker"] = blocker
    _state["monitor"] = monitor
    _state["start_time"] = start_time


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HNG Anomaly Detection Dashboard</title>
<style>
  :root {
    --bg: #0f172a;
    --surface: #1e293b;
    --border: #334155;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --danger: #ef4444;
    --success: #22c55e;
    --warning: #f59e0b;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
  header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 20px 0; border-bottom: 1px solid var(--border); margin-bottom: 24px;
  }
  header h1 { font-size: 1.5rem; font-weight: 700; }
  header h1 span { color: var(--accent); }
  .status-badge {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; font-weight: 500;
  }
  .status-live { background: rgba(34,197,94,0.15); color: var(--success); }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px;
  }
  .card-label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 8px; }
  .card-value { font-size: 1.8rem; font-weight: 700; font-variant-numeric: tabular-nums; }
  .card-sub { font-size: 0.8rem; color: var(--muted); margin-top: 4px; }
  .section { margin-bottom: 24px; }
  .section-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--border); }
  th { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
  td { font-size: 0.9rem; font-variant-numeric: tabular-nums; }
  .ip-cell { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; }
  .ban-tag {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 0.75rem; font-weight: 600;
  }
  .ban-active { background: rgba(239,68,68,0.2); color: var(--danger); }
  .ban-permanent { background: rgba(239,68,68,0.4); color: #fca5a5; }
  .bar-bg { background: var(--border); border-radius: 4px; height: 6px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
  .bar-cpu { background: var(--accent); }
  .bar-mem { background: var(--warning); }
  .baseline-chart { display: flex; align-items: flex-end; gap: 3px; height: 80px; padding-top: 8px; }
  .baseline-bar {
    flex: 1; min-width: 4px; background: var(--accent); border-radius: 2px 2px 0 0;
    opacity: 0.7; transition: height 0.3s;
  }
  .baseline-bar:hover { opacity: 1; }
  #last-update { color: var(--muted); font-size: 0.8rem; }
  .empty-state { text-align: center; padding: 30px; color: var(--muted); }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1><span>HNG</span> Anomaly Detection Engine</h1>
    <div>
      <span class="status-badge status-live"><span class="status-dot"></span> Live</span>
      <span id="last-update" style="margin-left: 12px;"></span>
    </div>
  </header>

  <div class="grid" id="metrics-grid"></div>

  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
    <div class="card section">
      <div class="section-title">🔴 Banned IPs</div>
      <div id="banned-table"></div>
    </div>
    <div class="card section">
      <div class="section-title">📊 Top 10 Source IPs</div>
      <div id="top-ips-table"></div>
    </div>
  </div>

  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
    <div class="card section">
      <div class="section-title">📈 Baseline History</div>
      <div id="baseline-chart" class="baseline-chart"></div>
      <div id="baseline-legend" style="font-size:0.75rem;color:var(--muted);margin-top:8px;"></div>
    </div>
    <div class="card section">
      <div class="section-title">🕐 Hourly Baseline Slots</div>
      <div id="hourly-table"></div>
    </div>
  </div>
</div>

<script>
function fmt(n, d=1) { return typeof n === 'number' ? n.toFixed(d) : '—'; }
function ago(ts) {
  const s = Math.floor(Date.now()/1000 - ts);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm ago';
}
function fmtDuration(s) {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = Math.floor(s%60);
  return (h ? h+'h ' : '') + (m ? m+'m ' : '') + sec+'s';
}

async function refresh() {
  try {
    const r = await fetch('/api/metrics');
    const d = await r.json();

    // Metric cards
    document.getElementById('metrics-grid').innerHTML = `
      <div class="card">
        <div class="card-label">Global Req/s</div>
        <div class="card-value">${fmt(d.global_rate)}</div>
      </div>
      <div class="card">
        <div class="card-label">Effective Mean</div>
        <div class="card-value">${fmt(d.baseline.effective_mean,2)}</div>
        <div class="card-sub">σ = ${fmt(d.baseline.effective_stddev,2)}</div>
      </div>
      <div class="card">
        <div class="card-label">Banned IPs</div>
        <div class="card-value" style="color:${d.banned_count > 0 ? 'var(--danger)' : 'var(--success)'}">${d.banned_count}</div>
      </div>
      <div class="card">
        <div class="card-label">Lines Processed</div>
        <div class="card-value">${d.lines_processed.toLocaleString()}</div>
      </div>
      <div class="card">
        <div class="card-label">CPU Usage</div>
        <div class="card-value">${fmt(d.system.cpu_percent)}%</div>
        <div class="bar-bg" style="margin-top:8px"><div class="bar-fill bar-cpu" style="width:${d.system.cpu_percent}%"></div></div>
      </div>
      <div class="card">
        <div class="card-label">Memory Usage</div>
        <div class="card-value">${fmt(d.system.memory_percent)}%</div>
        <div class="bar-bg" style="margin-top:8px"><div class="bar-fill bar-mem" style="width:${d.system.memory_percent}%"></div></div>
      </div>
      <div class="card">
        <div class="card-label">Uptime</div>
        <div class="card-value" style="font-size:1.3rem">${fmtDuration(d.uptime)}</div>
      </div>
      <div class="card">
        <div class="card-label">Slack Messages</div>
        <div class="card-value">${d.slack_messages}</div>
        <div class="card-sub">${d.slack_errors} errors</div>
      </div>
    `;

    // Banned IPs table
    const banned = Object.entries(d.banned_ips);
    if (banned.length === 0) {
      document.getElementById('banned-table').innerHTML = '<div class="empty-state">No IPs currently banned</div>';
    } else {
      let html = '<table><tr><th>IP</th><th>Duration</th><th>Offense</th><th>Banned</th></tr>';
      for (const [ip, info] of banned) {
        const dur = info.duration_minutes > 0 ? info.duration_minutes + 'm' : 'PERMANENT';
        const cls = info.duration_minutes === 0 ? 'ban-permanent' : 'ban-active';
        html += `<tr>
          <td class="ip-cell">${ip}</td>
          <td><span class="ban-tag ${cls}">${dur}</span></td>
          <td>#${info.ban_count}</td>
          <td>${ago(info.ban_time)}</td>
        </tr>`;
      }
      html += '</table>';
      document.getElementById('banned-table').innerHTML = html;
    }

    // Top IPs table
    if (d.top_ips.length === 0) {
      document.getElementById('top-ips-table').innerHTML = '<div class="empty-state">No traffic yet</div>';
    } else {
      let html = '<table><tr><th>#</th><th>IP</th><th>Req/s</th></tr>';
      d.top_ips.forEach(([ip, rate], i) => {
        html += `<tr><td>${i+1}</td><td class="ip-cell">${ip}</td><td>${fmt(rate)}</td></tr>`;
      });
      html += '</table>';
      document.getElementById('top-ips-table').innerHTML = html;
    }

    // Baseline history chart
    const history = d.baseline.history || [];
    if (history.length > 0) {
      const maxMean = Math.max(...history.map(h => h.effective_mean), 1);
      let chartHtml = '';
      history.forEach(h => {
        const pct = (h.effective_mean / maxMean) * 100;
        chartHtml += `<div class="baseline-bar" style="height:${Math.max(pct,2)}%" title="mean=${fmt(h.effective_mean,2)} σ=${fmt(h.effective_stddev,2)} n=${h.sample_count}"></div>`;
      });
      document.getElementById('baseline-chart').innerHTML = chartHtml;
      const last = history[history.length - 1];
      document.getElementById('baseline-legend').textContent =
        `Latest: mean=${fmt(last.effective_mean,2)}, σ=${fmt(last.effective_stddev,2)}, samples=${last.sample_count}`;
    }

    // Hourly slots
    const hourly = d.baseline.hourly_stats || {};
    const hours = Object.keys(hourly).sort((a,b) => a-b);
    if (hours.length === 0) {
      document.getElementById('hourly-table').innerHTML = '<div class="empty-state">Not enough data yet</div>';
    } else {
      let html = '<table><tr><th>Hour</th><th>Mean</th><th>StdDev</th><th>Samples</th></tr>';
      hours.forEach(h => {
        const s = hourly[h];
        html += `<tr><td>${String(h).padStart(2,'0')}:00</td><td>${s.mean}</td><td>${s.stddev}</td><td>${s.samples}</td></tr>`;
      });
      html += '</table>';
      document.getElementById('hourly-table').innerHTML = html;
    }

    document.getElementById('last-update').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch(e) {
    console.error('Refresh failed:', e);
  }
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/metrics")
def metrics():
    det = _state.get("detector")
    bl = _state.get("baseline")
    blk = _state.get("blocker")
    mon = _state.get("monitor")
    start = _state.get("start_time", time.time())

    banned = blk.get_banned() if blk else {}
    banned_serializable = {}
    for ip, info in banned.items():
        banned_serializable[ip] = {
            "ban_time": info["ban_time"],
            "duration_minutes": info["duration_minutes"],
            "ban_count": info["ban_count"],
            "condition": info["condition"],
        }

    data = {
        "global_rate": det.get_global_rate() if det else 0,
        "top_ips": det.get_top_ips(10) if det else [],
        "banned_ips": banned_serializable,
        "banned_count": len(banned),
        "lines_processed": mon.lines_processed if mon else 0,
        "parse_errors": mon.parse_errors if mon else 0,
        "uptime": time.time() - start,
        "baseline": {
            "effective_mean": bl.effective_mean if bl else 0,
            "effective_stddev": bl.effective_stddev if bl else 0,
            "last_recalc": bl.last_recalc if bl else 0,
            "history": list(bl.history) if bl else [],
            "hourly_stats": bl.get_hourly_stats() if bl else {},
        },
        "system": {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_used_mb": round(psutil.virtual_memory().used / 1024 / 1024),
            "memory_total_mb": round(psutil.virtual_memory().total / 1024 / 1024),
        },
        "slack_messages": _state.get("notifier_msgs", 0),
        "slack_errors": _state.get("notifier_errs", 0),
    }
    return jsonify(data)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "uptime": time.time() - _state.get("start_time", time.time())})
