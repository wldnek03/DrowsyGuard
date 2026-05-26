# dashboard.py
# DrowsyGuard 웹 대시보드
# main.py가 저장한 ~/drowsy_log.csv를 읽어서 차트로 시각화
# 실행: python3 dashboard.py → 스마트폰/노트북에서 http://192.168.10.73:8080 접속

import csv
import os
from collections import defaultdict
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

LOG_PATH = os.path.expanduser('~/drowsy_log.csv')


# ═══════════════════════════════════════════════════
# 데이터 로딩 / 가공
# ═══════════════════════════════════════════════════

def load_sessions():
    """CSV 파일에서 모든 세션 데이터 읽기"""
    if not os.path.exists(LOG_PATH):
        return []
    sessions = []
    with open(LOG_PATH, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                sessions.append({
                    'date':          row['date'],
                    'start':         row['start'],
                    'end':           row['end'],
                    'duration_min':  float(row.get('duration_min', 0) or 0),
                    'caution_count': int(row.get('caution_count', 0) or 0),
                    'danger_count':  int(row.get('danger_count', 0) or 0),
                    'danger_min':    float(row.get('danger_min', 0) or 0),
                    'yawn_count':    int(row.get('yawn_count', 0) or 0),
                })
            except (ValueError, KeyError):
                continue
    return sessions


def get_today_stats(sessions):
    today = datetime.now().strftime('%Y-%m-%d')
    today_sessions = [s for s in sessions if s['date'] == today]
    return {
        'session_count': len(today_sessions),
        'total_min':     round(sum(s['duration_min']  for s in today_sessions), 1),
        'caution_total': sum(s['caution_count'] for s in today_sessions),
        'danger_total':  sum(s['danger_count']  for s in today_sessions),
        'yawn_total':    sum(s['yawn_count']    for s in today_sessions),
    }


def get_daily_stats(sessions, days=7):
    """최근 N일치 일별 집계 (오늘 기준 역순)"""
    today = datetime.now().date()
    day_map = defaultdict(lambda: {'min': 0.0, 'danger': 0, 'caution': 0, 'yawn': 0})
    for s in sessions:
        try:
            d = datetime.strptime(s['date'], '%Y-%m-%d').date()
        except ValueError:
            continue
        if (today - d).days >= days:
            continue
        day_map[s['date']]['min']     += s['duration_min']
        day_map[s['date']]['danger']  += s['danger_count']
        day_map[s['date']]['caution'] += s['caution_count']
        day_map[s['date']]['yawn']    += s['yawn_count']

    labels, mins, dangers, cautions, yawns = [], [], [], [], []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        key = d.strftime('%Y-%m-%d')
        labels.append(d.strftime('%m/%d'))
        mins.append(round(day_map[key]['min'], 1))
        dangers.append(day_map[key]['danger'])
        cautions.append(day_map[key]['caution'])
        yawns.append(day_map[key]['yawn'])
    return {
        'labels':   labels,
        'minutes':  mins,
        'danger':   dangers,
        'caution':  cautions,
        'yawn':     yawns,
    }


def get_hourly_stats(sessions):
    """시간대별(0~23시) 위험 발생 횟수 집계"""
    hours = [0] * 24
    for s in sessions:
        try:
            h = int(s['start'].split(':')[0])
            hours[h] += s['danger_count']
        except (ValueError, IndexError):
            continue
    return {
        'labels': [f'{h:02d}시' for h in range(24)],
        'values': hours,
    }


def get_recent_sessions(sessions, limit=10):
    """최근 세션 N개 (역순)"""
    recent = sessions[-limit:][::-1]
    return recent


# ═══════════════════════════════════════════════════
# 라우트
# ═══════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/api/dashboard')
def api_dashboard():
    sessions = load_sessions()
    return jsonify({
        'today':  get_today_stats(sessions),
        'daily':  get_daily_stats(sessions, days=7),
        'hourly': get_hourly_stats(sessions),
        'recent': get_recent_sessions(sessions, limit=10),
        'total':  len(sessions),
    })


# ═══════════════════════════════════════════════════
# HTML 템플릿
# ═══════════════════════════════════════════════════

HTML = r"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>실시간 다중 신호 기반 집중력 관리 시스템</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #2c3e50;
            margin: 0;
            padding: 20px;
            min-height: 100vh;
        }
        .container { max-width: 1100px; margin: 0 auto; }
        header {
            color: white;
            text-align: center;
            margin-bottom: 30px;
        }
        header h1 { margin: 0; font-size: 28px; }
        header p { margin: 8px 0 0 0; opacity: 0.9; font-size: 14px; }
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 14px;
            margin-bottom: 24px;
        }
        .card {
            background: white;
            padding: 18px;
            border-radius: 14px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
            text-align: center;
        }
        .card-icon { font-size: 24px; margin-bottom: 4px; }
        .card-label { color: #7f8c8d; font-size: 12px; margin-bottom: 4px; }
        .card-value { font-size: 28px; font-weight: 700; color: #2c3e50; }
        .card-unit { color: #95a5a6; font-size: 13px; margin-left: 3px; }
        .panel {
            background: white;
            padding: 22px;
            border-radius: 14px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
            margin-bottom: 20px;
        }
        .panel h2 {
            margin: 0 0 16px 0;
            color: #2c3e50;
            font-size: 17px;
        }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th { background: #f8f9fa; padding: 10px; text-align: left; font-weight: 600; color: #555; }
        td { padding: 10px; border-bottom: 1px solid #ecf0f1; }
        .danger-text { color: #e74c3c; font-weight: 600; }
        .caution-text { color: #f39c12; font-weight: 600; }
        .yawn-text { color: #9b59b6; font-weight: 600; }
        .refresh-info { text-align: center; color: white; opacity: 0.7; font-size: 12px; margin-top: 16px; }
        @media (max-width: 600px) {
            body { padding: 12px; }
            .card-value { font-size: 22px; }
            header h1 { font-size: 22px; }
        }
    </style>
</head>
<body>
<div class="container">

    <header>
        <h1>📚 실시간 다중 신호 기반 집중력 관리 시스템</h1>
        <p>학습 세션 데이터 대시보드</p>
    </header>

    <div class="cards" id="todayCards">
        <div class="card"><div class="card-icon">📖</div><div class="card-label">오늘 공부</div><div class="card-value" id="card-min">-</div></div>
        <div class="card"><div class="card-icon">🎯</div><div class="card-label">하루 공부 횟수</div><div class="card-value" id="card-sess">-</div></div>
        <div class="card"><div class="card-icon">⚠️</div><div class="card-label">위험</div><div class="card-value danger-text" id="card-danger">-</div></div>
        <div class="card"><div class="card-icon">💛</div><div class="card-label">주의</div><div class="card-value caution-text" id="card-caution">-</div></div>
    </div>

    <div class="panel">
        <h2>📊 일별 공부 시간 (최근 7일)</h2>
        <canvas id="dailyChart" height="80"></canvas>
    </div>

    <div class="panel">
        <h2>🕐 시간대별 위험 발생</h2>
        <canvas id="hourlyChart" height="80"></canvas>
    </div>

    <div class="panel">
        <h2>📋 최근 세션</h2>
        <div style="overflow-x:auto;">
        <table id="sessionTable">
            <thead><tr><th>날짜</th><th>시작</th><th>시간(분)</th><th>주의</th><th>위험</th></tr></thead>
            <tbody></tbody>
        </table>
        </div>
    </div>

    <p class="refresh-info">⏱ 30초마다 자동 새로고침 | 총 세션 <span id="totalCount">0</span>개</p>
</div>

<script>
let dailyChart = null, hourlyChart = null;

async function loadData() {
    try {
        const res = await fetch('/api/dashboard');
        const data = await res.json();
        renderCards(data.today);
        renderDaily(data.daily);
        renderHourly(data.hourly);
        renderRecent(data.recent);
        document.getElementById('totalCount').textContent = data.total;
    } catch (e) {
        console.error('데이터 로드 실패', e);
    }
}

function renderCards(t) {
    document.getElementById('card-min').innerHTML = t.total_min + '<span class="card-unit">분</span>';
    document.getElementById('card-sess').textContent = t.session_count;
    document.getElementById('card-danger').textContent = t.danger_total;
    document.getElementById('card-caution').textContent = t.caution_total;
}

function renderDaily(d) {
    const ctx = document.getElementById('dailyChart').getContext('2d');
    if (dailyChart) dailyChart.destroy();
    dailyChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: d.labels,
            datasets: [
                {
                    label: '공부 시간(분)', data: d.minutes,
                    backgroundColor: 'rgba(102,126,234,0.7)', borderRadius: 6,
                    yAxisID: 'y',
                },
                {
                    label: '위험 횟수', data: d.danger, type: 'line',
                    borderColor: '#e74c3c', backgroundColor: '#e74c3c',
                    tension: 0.3, yAxisID: 'y1',
                },
            ],
        },
        options: {
            responsive: true,
            scales: {
                y:  { beginAtZero: true, position: 'left',  title: { display: true, text: '분' } },
                y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: '횟수' } },
            },
        },
    });
}

function renderHourly(h) {
    const ctx = document.getElementById('hourlyChart').getContext('2d');
    if (hourlyChart) hourlyChart.destroy();
    const colors = h.values.map(v => v > 0 ? 'rgba(231,76,60,0.7)' : 'rgba(189,195,199,0.4)');
    hourlyChart = new Chart(ctx, {
        type: 'bar',
        data: { labels: h.labels, datasets: [{ label: '위험 횟수', data: h.values, backgroundColor: colors, borderRadius: 4 }] },
        options: { responsive: true, scales: { y: { beginAtZero: true } } },
    });
}

function renderRecent(rows) {
    const tbody = document.querySelector('#sessionTable tbody');
    tbody.innerHTML = '';
    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#95a5a6;">아직 세션 기록이 없어요</td></tr>';
        return;
    }
    rows.forEach(r => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${r.date}</td>
            <td>${r.start}</td>
            <td>${r.duration_min.toFixed(1)}</td>
            <td class="caution-text">${r.caution_count}</td>
            <td class="danger-text">${r.danger_count} (${r.danger_min.toFixed(1)}분)</td>
        `;
        tbody.appendChild(tr);
    });
}

loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""


if __name__ == '__main__':
    print('=' * 60)
    print('DrowsyGuard 웹 대시보드 시작')
    print(f'  CSV 파일: {LOG_PATH}')
    print(f'  접속 주소: http://<라즈베리파이IP>:8080')
    print('=' * 60)
    app.run(host='0.0.0.0', port=8080, debug=False)
