#!/usr/bin/env python3
"""Generate index.html with all corrected data embedded (no fetch needed)."""
import csv, os, json, urllib.request
from collections import defaultdict, Counter

TSV_URL = "https://docs.google.com/spreadsheets/d/1kKq4y9ZtjmdacmEgQtMX64_puRNClibBOUd0in5TB6I/export?format=tsv&gid=1961588350"
TSV_PATH = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), 'latinbien_raw.tsv')

# Download TSV
print("Downloading TSV...")
urllib.request.urlretrieve(TSV_URL, TSV_PATH)

with open(TSV_PATH, 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f, delimiter='\t')
    rows = list(reader)

print(f"Total rows: {len(rows)}")
headers = rows[0]
col_idx = {h: i for i, h in enumerate(headers)}

STATUS_OP = col_idx.get('Status Operativo', 42)
CLIENTE = col_idx.get('Nombre del socio a mostrar en la Factura', 29)
TOTAL = col_idx.get('Total con signo', 44)
PAGADO = col_idx.get('Total pagado', 46)
FECHA = col_idx.get('Fecha', 15)
TRABAJADOR = col_idx.get('Trabajador Profesional', 48)
SUCURSAL = col_idx.get('Sucursal', 43)

ACTIVE_STATUSES = {'6. CVG - ENTREGADO', '4. SAV - APROBADO - ESPERA ENTREGA', '8. CANCELACION TOTAL'}

def classify_worker(tipo):
    tipo_lower = tipo.lower().strip()
    # Check 'independ' BEFORE 'depend' since 'independ' contains 'depend'
    if 'independ' in tipo_lower or 'informal' in tipo_lower:
        return 'Independiente'
    if 'publico' in tipo_lower: return 'Sector público'
    if 'privado' in tipo_lower: return 'Sector privado'
    if 'depend' in tipo_lower: return 'Dependientes'
    if 'bajo_dependencia' in tipo_lower:
        return 'Dependientes'
    return 'No clasificado'

# Process active rows
active_rows = []
status_counter = Counter()

for row in rows[1:]:
    if len(row) <= max(col_idx.values()):
        continue
    status = row[STATUS_OP].strip() if STATUS_OP < len(row) else ''
    status_counter[status] += 1
    if status in ACTIVE_STATUSES:
        try:
            total_val = float(row[TOTAL].replace(',', '')) if TOTAL < len(row) and row[TOTAL].strip() else 0
        except:
            total_val = 0
        try:
            pagado_val = float(row[PAGADO].replace(',', '')) if PAGADO < len(row) and row[PAGADO].strip() else 0
        except:
            pagado_val = 0
        active_rows.append({
            'cliente': row[CLIENTE].strip() if CLIENTE < len(row) else 'N/A',
            'total': total_val,
            'pagado': pagado_val,
            'fecha': row[FECHA].strip() if FECHA < len(row) else '',
            'trabajador': row[TRABAJADOR].strip().lower() if TRABAJADOR < len(row) and row[TRABAJADOR].strip() else 'desconocido',
        })

print(f"Active rows: {len(active_rows)}")

# Aggregate per client
clients_dict = defaultdict(lambda: {'contratos': 0, 'facturado': 0.0, 'cobrado': 0.0, 'fechas': [], 'worker_types': Counter()})
for r in active_rows:
    c = r['cliente']
    clients_dict[c]['contratos'] += 1
    clients_dict[c]['facturado'] += r['total']
    clients_dict[c]['cobrado'] += r['pagado']
    if r['fecha']:
        clients_dict[c]['fechas'].append(r['fecha'])
    clients_dict[c]['worker_types'][r['trabajador']] += 1

# Primary worker type
client_list = []
for c, d in clients_dict.items():
    primary_wt = d['worker_types'].most_common(1)[0][0] if d['worker_types'] else 'desconocido'
    segmento = classify_worker(primary_wt)
    fechas_sorted = sorted(d['fechas'])
    client_list.append({
        'cliente': c,
        'contratos': d['contratos'],
        'facturado': round(d['facturado'], 2),
        'cobrado': round(d['cobrado'], 2),
        'saldo': round(d['facturado'] - d['cobrado'], 2),
        'prom': round(d['facturado'] / d['contratos'], 2) if d['contratos'] else 0,
        'worker_type': primary_wt,
        'segmento': segmento,
        'first_date': fechas_sorted[0] if fechas_sorted else '',
        'last_date': fechas_sorted[-1] if fechas_sorted else '',
    })

client_list.sort(key=lambda x: -x['contratos'])

# Distribution
dist = Counter()
for c in client_list:
    dist[c['contratos']] += 1
dist_labels = sorted(dist.keys())

# Segment stats
seg_stats = defaultdict(lambda: {'clientes': 0, 'contratos': 0, 'facturado': 0.0, 'cobrado': 0.0})
for c in client_list:
    s = c['segmento']
    seg_stats[s]['clientes'] += 1
    seg_stats[s]['contratos'] += c['contratos']
    seg_stats[s]['facturado'] += c['facturado']
    seg_stats[s]['cobrado'] += c['cobrado']

# Last 200 analysis
active_rows.sort(key=lambda r: r['fecha'] if r['fecha'] else '', reverse=True)
last_200 = active_rows[:200]
last200_seg = Counter()
for r in last_200:
    last200_seg[classify_worker(r['trabajador'])] += 1

# VIP clients (5+ contracts)
vip_clients = [c for c in client_list if c['contratos'] >= 5]
vip_clients.sort(key=lambda x: -x['contratos'])

# Build embedded data
data_js = {
    'status_summary': dict(status_counter.most_common()),
    'total_rows': len(rows) - 1,
    'client_count': len(client_list),
    'total_facturado': sum(c['facturado'] for c in client_list),
    'total_cobrado': sum(c['cobrado'] for c in client_list),
    'distribucion': [{'rango': k, 'cantidad': v} for k, v in sorted(dist.items())],
    'status_counts': {
        'Entregado': status_counter.get('6. CVG - ENTREGADO', 0),
        'Aprobado': status_counter.get('4. SAV - APROBADO - ESPERA ENTREGA', 0),
        'Cancelacion Total': status_counter.get('8. CANCELACION TOTAL', 0),
    },
    'clients': client_list,
    'segment_stats': {s: dict(v) for s, v in sorted(seg_stats.items(), key=lambda x: -x[1]['contratos'])},
    'last200': dict(last200_seg.most_common()),
    'vip': [{
        'cliente': c['cliente'],
        'cont': c['contratos'],
        'first': c['first_date'],
        'last': c['last_date'],
    } for c in vip_clients],
}

json_str = json.dumps(data_js, ensure_ascii=True)
json_escaped = json_str.replace('\\', '\\\\').replace("'", "\\'").replace('</', '<\\/')
# For debugging: first/last 2KB of the raw JSON
json_error_preview = json_str[:2000] + '\n\n... [TRUNCATED] ...\n\n' + json_str[-2000:]
json_error_preview = json_error_preview.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
print(f"JSON data size: {len(json_str)} chars")

# Date range for VIP
all_fechas = []
for c in client_list:
    if c['first_date']: all_fechas.append(c['first_date'])
    if c['last_date']: all_fechas.append(c['last_date'])
min_date = min(all_fechas) if all_fechas else '2023-01-01'
max_date = max(all_fechas) if all_fechas else '2026-12-31'

# Relationship distribution for VIP
relacion_labels = ['1-3 meses', '3-6 meses', '6-12 meses', '12-24 meses', '24+ meses']

# Generate HTML
# I'll write the HTML with the JSON embedded
html = f'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Análisis de Cartera - LATINBIEN</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --primary: #213C83; --primary-dark: #1a2f66; --primary-light: #3D6194;
            --accent: #F98B10; --success: #10b981; --danger: #ef4444;
            --bg-gray: #f0f2f5; --text-dark: #1a1a2e; --white: #ffffff;
        }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: var(--bg-gray); color: var(--text-dark); padding: 30px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{
            background: linear-gradient(135deg, var(--primary) 0%, #0f2a5a 100%);
            color: white; padding: 30px 40px; border-radius: 16px; margin-bottom: 28px;
            box-shadow: 0 8px 25px rgba(33,60,131,0.3); position: relative; overflow: hidden;
        }}
        .header::before {{
            content: ''; position: absolute; top: -50%; right: -20%;
            width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(249,139,16,0.08) 0%, transparent 70%);
            border-radius: 50%;
        }}
        .header-content {{ display: flex; align-items: center; justify-content: space-between; gap: 25px; position: relative; z-index: 1; }}
        .header-logo {{ display: flex; align-items: center; gap: 18px; }}
        .header-logo img {{ height: 50px; width: auto; }}
        .header-logo .divider {{ width: 2px; height: 40px; background: rgba(255,255,255,0.2); }}
        .header-text h1 {{ font-size: 26px; font-weight: 800; letter-spacing: -0.5px; line-height: 1.2; }}
        .header-text h1 span {{ font-weight: 300; opacity: 0.85; }}
        .header-text p {{ font-size: 13px; opacity: 0.8; margin-top: 3px; }}
        .header-meta {{ display: flex; gap: 12px; flex-wrap: wrap; }}
        .header-meta .meta-item {{ background: rgba(255,255,255,0.12); padding: 7px 14px; border-radius: 10px; text-align: center; min-width: 90px; }}
        .header-meta .meta-item strong {{ display: block; font-size: 18px; color: var(--accent); }}
        .header-meta .meta-item span {{ font-size: 9px; opacity: 0.75; text-transform: uppercase; letter-spacing: 0.3px; }}
        .header-filter {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 12px; background: rgba(255,255,255,0.08); border-radius: 10px; margin-top: 6px; }}
        .header-filter label {{ font-size: 11px; color: rgba(255,255,255,0.7); font-weight: 600; }}
        .header-filter input[type=date] {{ padding: 4px 8px; border: 1px solid rgba(255,255,255,0.2); border-radius: 6px; font-size: 11px; background: rgba(255,255,255,0.9); }}
        .header-filter .btn-filtrar {{ padding: 4px 14px; background: var(--accent); color: white; border: none; border-radius: 6px; font-size: 11px; font-weight: 600; cursor: pointer; }}
        .header-filter .btn-filtrar:hover {{ background: #e07d00; }}
        .header-filter .btn-reset {{ padding: 4px 10px; background: rgba(255,255,255,0.15); color: rgba(255,255,255,0.8); border: 1px solid rgba(255,255,255,0.2); border-radius: 6px; font-size: 11px; cursor: pointer; }}
        .header-filter .btn-reset:hover {{ background: rgba(255,255,255,0.25); }}
        .header-filter .btn-refresh {{ padding: 4px 14px; background: #10b981; color: white; border: none; border-radius: 6px; font-size: 11px; font-weight: 600; cursor: pointer; margin-left: 10px; transition: background 0.2s; }}
        .header-filter .btn-refresh:hover {{ background: #059669; }}
        .header-filter .btn-refresh.loading {{ opacity: 0.6; pointer-events: none; }}
        .header-filter .filtro-info {{ font-size: 10px; color: var(--accent); font-weight: 600; }}

        .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 22px; }}
        .kpi-card {{ background: var(--white); border-radius: 14px; padding: 16px 14px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); text-align: center; border-top: 4px solid var(--primary-light); transition: transform 0.2s; }}
        .kpi-card:hover {{ transform: translateY(-3px); box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
        .kpi-card .number {{ font-size: 24px; font-weight: 800; color: var(--primary); }}
        .kpi-card .number.money {{ font-size: 20px; }}
        .kpi-card .label {{ font-size: 10px; color: #666; margin-top: 3px; text-transform: uppercase; letter-spacing: 0.4px; }}
        .kpi-card.accent {{ border-top-color: var(--accent); }}
        .kpi-card.accent .number {{ color: var(--accent); }}
        .kpi-card.success {{ border-top-color: var(--success); }}
        .kpi-card.success .number {{ color: var(--success); }}
        .kpi-card.danger {{ border-top-color: var(--danger); }}
        .kpi-card.danger .number {{ color: var(--danger); }}

        .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 22px; }}
        .chart-card {{ background: var(--white); border-radius: 14px; padding: 18px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
        .chart-card h3 {{ font-size: 14px; font-weight: 700; margin-bottom: 10px; color: var(--primary); text-align: center; }}
        .chart-card .chart-container {{ position: relative; height: 350px; }}
        .chart-card.full-width {{ grid-column: 1 / -1; }}
        .chart-card.full-width .chart-container {{ height: 260px; }}

        .filtros-card {{ background: var(--white); border-radius: 14px; padding: 16px 20px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); margin-bottom: 22px; display: flex; flex-wrap: wrap; align-items: center; gap: 14px; }}
        .filtros-card label {{ font-size: 12px; font-weight: 600; color: var(--primary); }}
        .filtros-card input[type="date"], .filtros-card input[type="number"], .filtros-card select {{ padding: 6px 10px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 12px; transition: border-color 0.3s; }}
        .filtros-card input[type="number"] {{ width: 80px; }}
        .filtros-card input:focus, .filtros-card select:focus {{ outline: none; border-color: var(--primary-light); }}
        .filtros-card .filtro-group {{ display: flex; align-items: center; gap: 6px; }}
        .filtros-card .btn-filtrar {{ padding: 6px 16px; background: var(--primary); color: white; border: none; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; transition: background 0.2s; }}
        .filtros-card .btn-filtrar:hover {{ background: var(--primary-dark); }}
        .filtros-card .btn-reset {{ padding: 6px 12px; background: #f0f0f0; color: #666; border: none; border-radius: 8px; font-size: 12px; cursor: pointer; }}
        .filtros-card .btn-reset:hover {{ background: #e0e0e0; }}
        .filtros-card .filtro-info {{ font-size: 12px; color: #888; margin-left: auto; }}

        .table-card {{ background: var(--white); border-radius: 14px; padding: 20px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); margin-bottom: 28px; }}
        .table-card h3 {{ font-size: 15px; font-weight: 700; margin-bottom: 12px; color: var(--primary); }}
        .table-card .controls {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }}
        .table-card .search-box input {{ padding: 7px 14px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 13px; width: 260px; max-width: 100%; }}
        .table-card .search-box input:focus {{ outline: none; border-color: var(--primary-light); }}
        .table-card .info {{ font-size: 12px; color: #888; }}
        .table-card .pagination {{ display: flex; gap: 4px; align-items: center; }}
        .table-card .pagination button {{ padding: 5px 12px; border: 1px solid #ddd; background: var(--white); border-radius: 6px; cursor: pointer; font-size: 12px; transition: all 0.2s; }}
        .table-card .pagination button:hover:not(:disabled) {{ background: var(--primary); color: var(--white); border-color: var(--primary); }}
        .table-card .pagination button:disabled {{ opacity: 0.4; cursor: default; }}
        .table-card .page-info {{ font-size: 12px; color: #888; }}
        .table-wrapper {{ overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; }}
        table thead th {{ background: #f0f2f7; padding: 9px 11px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px; color: var(--primary); border-bottom: 2px solid #e0e4ed; cursor: pointer; user-select: none; white-space: nowrap; }}
        table thead th:hover {{ background: #e6e9f0; }}
        table thead th .sort-icon {{ margin-left: 3px; opacity: 0.3; }}
        table thead th.sorted .sort-icon {{ opacity: 1; color: var(--accent); }}
        table tbody td {{ padding: 7px 11px; border-bottom: 1px solid #f0f0f0; font-size: 12px; }}
        table tbody tr:hover {{ background: #f8f9fc; }}
        table tbody tr.top-client td {{ background: #fef8f0; }}
        table tbody tr.rank-1 {{ border-left: 4px solid #D4A017; }}
        table tbody tr.rank-2 {{ border-left: 4px solid #A8A8A8; }}
        table tbody tr.rank-3 {{ border-left: 4px solid #CD7F32; }}
        .text-right {{ text-align: right !important; }}
        .text-center {{ text-align: center !important; }}
        .badge {{ display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 10px; font-weight: 600; }}
        .badge-gold {{ background: #fff3cd; color: #856404; }}
        .badge-silver {{ background: #e9ecef; color: #495057; }}
        .badge-bronze {{ background: #ffe8d6; color: #8b4513; }}
        .badge-blue {{ background: #dbeafe; color: #1e40af; }}
        .badge-gray {{ background: #f3f4f6; color: #6b7280; }}
        .badge-green {{ background: #d1fae5; color: #065f46; }}
        .badge-red {{ background: #fde8e8; color: #dc2626; }}

        .footer {{ text-align: center; padding: 18px; color: #aaa; font-size: 11px; }}
        .footer strong {{ color: var(--primary); }}

        .tabs {{ display: flex; gap: 4px; margin-bottom: 20px; background: #e8eaf0; border-radius: 12px; padding: 4px; overflow-x: auto; }}
        .tab-btn {{ padding: 9px 20px; border: none; background: transparent; border-radius: 10px; font-size: 13px; font-weight: 600; color: #666; cursor: pointer; transition: all 0.2s; white-space: nowrap; }}
        .tab-btn:hover {{ color: var(--primary); }}
        .tab-btn.active {{ background: var(--white); color: var(--primary); box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}

        .segment-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; margin-bottom: 22px; }}
        .segment-card {{ background: var(--white); border-radius: 14px; padding: 18px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); border-left: 4px solid var(--primary); }}
        .segment-card h4 {{ font-size: 14px; font-weight: 700; color: var(--primary); margin-bottom: 8px; }}
        .segment-card .stat {{ font-size: 12px; color: #555; margin: 3px 0; }}
        .segment-card .stat strong {{ color: var(--text-dark); }}

        @media (max-width: 900px) {{
            .charts-row {{ grid-template-columns: 1fr; }}
            .header-content {{ flex-direction: column; text-align: center; }}
            .header-logo {{ justify-content: center; }}
            .header-meta {{ justify-content: center; }}
            .header {{ padding: 22px 18px; }}
            .header-text h1 {{ font-size: 20px; }}
            body {{ padding: 14px; }}
            .table-card .controls {{ flex-direction: column; align-items: stretch; }}
            .table-card .search-box input {{ width: 100%; }}
            .filtros-card {{ flex-direction: column; align-items: stretch; }}
            .kpi-row {{ grid-template-columns: repeat(2, 1fr); }}
        }}
    </style>
</head>
<body>
<div class="container" id="app">

    <div class="header">
        <div class="header-content">
            <div class="header-logo">
                <img src="https://latinbien.com/web/image/website/1/logo/LATINBIEN?unique=4695b15" alt="LATINBIEN"
                     onerror="this.style.display='none'">
                <div class="divider"></div>
                <div class="header-text">
                    <h1>LATINBIEN <span>| Análisis de Cartera</span></h1>
                    <p>Distribución de contratos activos + montos históricos + segmentación por tipo de trabajador</p>
                </div>
            </div>
            <div class="header-meta">
                <div class="meta-item"><strong id="hClientes">—</strong><span>Clientes act.</span></div>
                <div class="meta-item"><strong id="hContratos">—</strong><span>Contratos act.</span></div>
                <div class="meta-item"><strong id="hFacturado">—</strong><span>Facturado</span></div>
                <div class="meta-item"><strong id="hPendiente">—</strong><span>Pendiente</span></div>
            </div>
        </div>
        <div class="header-filter">
            <label>📅 Filtrar por fecha:</label>
            <input type="date" id="filtroFechaDesde">
            <span style="color:rgba(255,255,255,0.5);font-size:11px">→</span>
            <input type="date" id="filtroFechaHasta">
            <button class="btn-filtrar" onclick="aplicarFiltroGlobal()">Aplicar</button>
            <button class="btn-reset" onclick="resetFiltroGlobal()">Limpiar</button>
            <span class="filtro-info" id="filtroInfoGlobal"></span>
            <button class="btn-refresh" onclick="actualizarDashboard(this)" title="Forzar actualización desde Odoo">🔄 Actualizar</button>
        </div>
    </div>

    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('resumen')">📊 Resumen</button>
        <button class="tab-btn" onclick="switchTab('montos')">💰 Montos</button>
        <button class="tab-btn" onclick="switchTab('segmentos')">👥 Segmentos</button>
        <button class="tab-btn" onclick="switchTab('temporal')">⏱ Temporal VIP</button>
        <button class="tab-btn" onclick="switchTab('tabla')">📋 Listado</button>
        <button class="tab-btn" onclick="switchTab('pagos')">💳 Plan de Pagos</button>
    </div>

    <!-- TAB 1: RESUMEN -->
    <div class="tab-content active" id="tab-resumen">
        <div class="kpi-row">
            <div class="kpi-card"><div class="number" id="kpi1">—</div><div class="label">Clientes 1 contrato</div></div>
            <div class="kpi-card"><div class="number" id="kpi2">—</div><div class="label">Clientes 2 contratos</div></div>
            <div class="kpi-card accent"><div class="number" id="kpi3">—</div><div class="label">Clientes 3+ contratos</div></div>
            <div class="kpi-card accent"><div class="number" id="kpi4">—</div><div class="label">Clientes 5+ (VIP)</div></div>
        </div>
        <div class="charts-row">
            <div class="chart-card"><h3>🏆 Top 20 Clientes</h3><div class="chart-container"><canvas id="chartTop20"></canvas></div></div>
            <div class="chart-card"><h3>📊 Distribución de Contratos (activos)</h3><div class="chart-container"><canvas id="chartDist"></canvas></div></div>
        </div>
        <div class="charts-row">
            <div class="chart-card full-width"><h3>📊 Facturas Emitidas por Status</h3><div class="chart-container"><canvas id="chartStatus"></canvas></div></div>
        </div>
    </div>

    <!-- TAB 2: MONTOS HISTÓRICOS -->
    <div class="tab-content" id="tab-montos">
        <div class="kpi-row">
            <div class="kpi-card"><div class="number" id="mkpiClientes">—</div><div class="label">Total Clientes</div></div>
            <div class="kpi-card"><div class="number" id="mkpiContratos">—</div><div class="label">Total Contratos</div></div>
            <div class="kpi-card accent"><div class="number money" id="mkpiFacturado">—</div><div class="label">Facturado</div></div>
            <div class="kpi-card success"><div class="number money" id="mkpiCobrado">—</div><div class="label">Cobrado</div></div>
            <div class="kpi-card danger"><div class="number money" id="mkpiPendiente">—</div><div class="label">Saldo Pendiente</div></div>
            <div class="kpi-card"><div class="number" id="mkpiPromContratos">—</div><div class="label">Prom Contratos</div></div>
            <div class="kpi-card accent"><div class="number money" id="mkpiPromMonto">—</div><div class="label">Prom $ x Cliente</div></div>
            <div class="kpi-card"><div class="number" id="mkpiMaxContratos">—</div><div class="label">Máx Contratos</div></div>
        </div>
        <div class="filtros-card">
            <label>🔍 Filtrar:</label>
            <div class="filtro-group"><span>Monto mín:</span><input type="number" id="filtroMontoMin" placeholder="0" step="100"></div>
            <div class="filtro-group"><span>Contratos mín:</span><input type="number" id="filtroContratosMin" placeholder="0" step="1"></div>
            <div class="filtro-group">
                <span>Estado:</span>
                <select id="filtroEstado"><option value="todos">Todos</option><option value="pagado">Pagado / Cancelado</option><option value="pendiente">Con saldo pendiente</option></select>
            </div>
            <button class="btn-filtrar" onclick="aplicarFiltrosMontos()">Aplicar</button>
            <button class="btn-reset" onclick="resetFiltrosMontos()">Limpiar</button>
            <span class="filtro-info" id="filtroInfoMontos">Mostrando todos</span>
        </div>
        <div class="charts-row">
            <div class="chart-card"><h3>💰 Top 20 por Monto Facturado</h3><div class="chart-container"><canvas id="chartTopMonto"></canvas></div></div>
            <div class="chart-card"><h3>📦 Top 20 por Contratos</h3><div class="chart-container"><canvas id="chartTopContratos"></canvas></div></div>
        </div>
        <div class="charts-row">
            <div class="chart-card"><h3>💵 Distribución por Monto</h3><div class="chart-container"><canvas id="chartDistMontos"></canvas></div></div>
            <div class="chart-card"><h3>🧩 Estado de Cartera</h3><div class="chart-container"><canvas id="chartCarteraPie"></canvas></div></div>
        </div>
    </div>

    <!-- TAB 3: SEGMENTOS -->
    <div class="tab-content" id="tab-segmentos">
        <div class="kpi-row" id="segmentKpis"></div>
        <div class="charts-row">
            <div class="chart-card"><h3>👥 Distribución por Segmento</h3><div class="chart-container"><canvas id="chartSegPie"></canvas></div></div>
            <div class="chart-card"><h3>💰 Facturado por Segmento</h3><div class="chart-container"><canvas id="chartSegFact"></canvas></div></div>
        </div>
        <div class="charts-row">
            <div class="chart-card full-width"><h3>📊 Últimos 200 Contratos por Tipo de Trabajador</h3><div class="chart-container"><canvas id="chartLast200"></canvas></div></div>
        </div>
        <div class="table-card" id="segmentDetalle">
            <h3>📋 Detalle por Segmento</h3>
            <div id="segmentTableWrap"></div>
        </div>
    </div>

    <!-- TAB 4: TEMPORAL VIP -->
    <div class="tab-content" id="tab-temporal">
        <div class="kpi-row">
            <div class="kpi-card accent"><div class="number" id="tmpVipCount">—</div><div class="label">Clientes VIP (5+ contratos)</div></div>
            <div class="kpi-card"><div class="number" id="tmpAvgSpan">—</div><div class="label">Promedio relación (meses)</div></div>
            <div class="kpi-card"><div class="number" id="tmpAvgFreq">—</div><div class="label">Frecuencia entre compras (días)</div></div>
            <div class="kpi-card accent"><div class="number" id="tmpMaxSpan">—</div><div class="label">Mayor antigüedad (meses)</div></div>
        </div>
        <div class="charts-row">
            <div class="chart-card"><h3>⏱ Línea de Tiempo — Clientes VIP</h3><div class="chart-container"><canvas id="chartTimeline"></canvas></div></div>
            <div class="chart-card"><h3>📆 Distribución por Tiempo de Relación</h3><div class="chart-container"><canvas id="chartRelacion"></canvas></div></div>
        </div>
        <div class="table-card">
            <h3>🏆 Top 10 VIP — Detalle Temporal</h3>
            <div class="table-wrapper">
                <table id="tablaTemporal">
                    <thead><tr><th>#</th><th>Cliente</th><th>Contratos</th><th>1ra Fecha</th><th>Últ Fecha</th><th>Días</th><th>Meses</th></tr></thead>
                    <tbody id="tbodyTemporal"></tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- TAB 5: LISTADO -->
    <div class="tab-content" id="tab-tabla">
        <div class="table-card">
            <h3>📋 Listado Completo de Clientes Activos</h3>
            <div class="controls">
                <div class="search-box"><input type="text" id="searchInput" placeholder="Buscar cliente..." oninput="filterTable()"></div>
                <span id="segFilterLabel" style="display:none;font-size:12px;color:var(--primary);font-weight:600;margin-left:8px"></span>
                <button id="clearSegFilter" style="display:none;font-size:11px;padding:3px 10px;border:1px solid #ccc;border-radius:5px;background:white;cursor:pointer;margin-left:4px" onclick="clearSegmentFilter()">✕ Limpiar filtro</button>
                <div class="info">Mostrando <span id="showing">0</span> de <span id="totalRows">0</span> clientes</div>
                <div class="pagination">
                    <button onclick="changePage(-1)" id="prevBtn" disabled>◀ Anterior</button>
                    <span class="page-info" id="pageInfo">Página 1 / 1</span>
                    <button onclick="changePage(1)" id="nextBtn" disabled>Siguiente ▶</button>
                </div>
            </div>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th onclick="sortTable(0)" class="sorted"># <span class="sort-icon">▲</span></th>
                            <th onclick="sortTable(1)">Cliente <span class="sort-icon">⇅</span></th>
                            <th onclick="sortTable(2)" class="sorted">Contratos <span class="sort-icon">▲</span></th>
                            <th onclick="sortTable(3)">Facturado <span class="sort-icon">⇅</span></th>
                            <th onclick="sortTable(4)">Cobrado <span class="sort-icon">⇅</span></th>
                            <th onclick="sortTable(5)">Saldo <span class="sort-icon">⇅</span></th>
                            <th onclick="sortTable(6)">Promedio <span class="sort-icon">⇅</span></th>
                            <th onclick="sortTable(7)">Segmento <span class="sort-icon">⇅</span></th>
                            <th onclick="sortTable(8)">Categoría <span class="sort-icon">⇅</span></th>
                        </tr>
                    </thead>
                    <tbody id="tableBody"></tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- TAB 6: PLAN DE PAGOS -->
    <div class="tab-content" id="tab-pagos">
        <div class="kpi-row">
            <div class="kpi-card danger"><div class="number money" id="pkTotalVencido">—</div><div class="label">Total Vencido</div></div>
            <div class="kpi-card accent"><div class="number money" id="pkTotalDebido">—</div><div class="label">Total Debido (al día)</div></div>
            <div class="kpi-card success"><div class="number money" id="pkTotalPagado">—</div><div class="label">Total Pagado</div></div>
        </div>
        <div class="charts-row">
            <div class="chart-card"><h3>📊 Estado de Cuotas</h3><div class="chart-container" style="height:250px"><canvas id="chartPagosState"></canvas></div></div>
            <div class="chart-card"><h3>📅 Proyección de Cobros (Debido)</h3><div class="chart-container" style="height:250px"><canvas id="chartProyeccion"></canvas></div></div>
        </div>
        <div class="table-card">
            <h3>⚠️ Clientes con Cuotas Vencidas</h3>
            <div style="margin:8px 0;font-size:13px;color:#666">Total vencido: <strong id="pkVencidoTotal">—</strong> — <span id="pkVencidosCount">—</span> clientes afectados</div>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Cliente</th>
                            <th class="text-right">Cuotas Vencidas</th>
                            <th class="text-right">Monto Vencido</th>
                            <th>Facturas</th>
                        </tr>
                    </thead>
                    <tbody id="tablaVencidos"></tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="footer">
        <strong>LATINBIEN</strong> — Latinoamericana de Bienes y Servicios, C.A. &nbsp;·&nbsp;
        Generado el <span id="fechaGeneracion"></span>
    </div>
</div>

<script>
// ================================================================
//  DATA — EMBEDDED (no fetch needed, works with file://)
// ================================================================
var DATA;
try {{ DATA = JSON.parse('{json_escaped}'); }} catch(e) {{ DATA = null; console.error('DATA parse error:', e); }}

if (!DATA) {{
    document.write('<div style="padding:40px;font-family:sans-serif"><h2 style="color:#c00;">Error al cargar datos</h2><p>No se pudo parsear el JSON embebido. Revisa la consola (F12).</p><p style="color:#888;font-size:13px">Revisa que el archivo se haya generado correctamente o prueba con Ctrl+F5.</p></div>');
    throw new Error('DATA parse failed');
}}

// Token: lo guardas en localStorage (solo en tu navegador), nunca en el código
var GITHUB_TOKEN = localStorage.getItem('gh_token') || '';

// Global date filter via URL hash — filtra FACTURAS (no clientes)
var filteredInvoices = null; // invoices después de aplicar filtro de fecha

function getFilteredClientes() {{
    const p = new URLSearchParams(window.location.hash.replace('#',''));
    const desde = p.get('desde');
    const hasta = p.get('hasta');
    const all = DATA.clients;
    if (!desde && !hasta) {{
        filteredInvoices = null;
        return all;
    }}
    // Filtrar facturas por fecha exacta
    const invs = DATA.invoices.filter(inv => {{
        if (!inv.fecha) return false;
        if (desde && inv.fecha < desde) return false;
        if (hasta && inv.fecha > hasta) return false;
        return true;
    }});
    filteredInvoices = invs;
    
    // Reconstruir clientes desde las facturas filtradas
    const clientMap = {{}};
    invs.forEach(inv => {{
        const nom = inv.cliente || '(sin nombre)';
        if (!clientMap[nom]) {{
            clientMap[nom] = {{ contratos: 0, facturado: 0, cobrado: 0, saldo: 0, prom: 0, workers: [] }};
        }}
        clientMap[nom].contratos += 1;
        clientMap[nom].facturado += inv.total;
        clientMap[nom].cobrado += inv.pagado;
        if (inv.trabajador) clientMap[nom].workers.push(inv.trabajador);
    }});
    const result = Object.keys(clientMap).map(nom => {{
        const d = clientMap[nom];
        // Worker más frecuente para este cliente
        const freq = {{}};
        d.workers.forEach(w => {{ freq[w] = (freq[w]||0) + 1; }});
        const topWorker = Object.keys(freq).sort((a,b) => freq[b]-freq[a])[0] || '';
        return {{
            cliente: nom,
            contratos: d.contratos,
            facturado: d.facturado,
            cobrado: d.cobrado,
            saldo: d.facturado - d.cobrado,
            prom: d.facturado / d.contratos,
            worker_type: topWorker,
            segmento: topWorker,
            first_date: '',
            last_date: '',
        }};
    }});
    result.sort((a,b) => b.contratos - a.contratos);
    return result;
}}

function aplicarFiltroGlobal() {{
    const d = document.getElementById('filtroFechaDesde').value;
    const h = document.getElementById('filtroFechaHasta').value;
    const p = new URLSearchParams();
    if (d) p.set('desde', d);
    if (h) p.set('hasta', h);
    window.location.hash = p.toString();
    location.reload();
}}

function resetFiltroGlobal() {{
    window.location.hash = '';
    location.reload();
}}

function actualizarDashboard(btn) {{
    if (!GITHUB_TOKEN) {{
        const token = prompt('Pega tu token de GitHub (solo Actions:Write):');
        if (!token) return;
        localStorage.setItem('gh_token', token);
        GITHUB_TOKEN = token;
    }}

    btn.classList.add('loading');
    btn.textContent = '⏳ Actualizando...';

    fetch('https://api.github.com/repos/latinbienti-sys/profesional/actions/workflows/update-dashboard.yml/dispatches', {{
        method: 'POST',
        headers: {{
            'Authorization': 'Bearer ' + GITHUB_TOKEN,
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        }},
        body: JSON.stringify({{ ref: 'main' }})
    }})
    .then(res => {{
        if (res.status === 204) {{
            btn.textContent = '✅ Actualizando...';
            setTimeout(() => {{ location.reload(); }}, 8000);
        }} else if (res.status === 401 || res.status === 403) {{
            localStorage.removeItem('gh_token');
            GITHUB_TOKEN = '';
            btn.textContent = '❌ Token inválido';
            btn.classList.remove('loading');
            setTimeout(() => {{ btn.textContent = '🔄 Actualizar'; }}, 3000);
        }} else {{
            btn.textContent = '❌ Error ' + res.status;
            btn.classList.remove('loading');
            setTimeout(() => {{ btn.textContent = '🔄 Actualizar'; }}, 3000);
        }}
    }})
    .catch(() => {{
        btn.textContent = '❌ Sin conexión';
        btn.classList.remove('loading');
        setTimeout(() => {{ btn.textContent = '🔄 Actualizar'; }}, 3000);
    }});
}}

const fullClientes = DATA.clients;
const hashParams = new URLSearchParams(window.location.hash.replace('#',''));
if (hashParams.get('desde')) document.getElementById('filtroFechaDesde').value = hashParams.get('desde');
if (hashParams.get('hasta')) document.getElementById('filtroFechaHasta').value = hashParams.get('hasta');

let clientes = getFilteredClientes();
const statusSummary = DATA.status_summary;
const segmentStats = DATA.segment_stats;
const last200 = DATA.last200;
const distribucion = DATA.distribucion;

// ================================================================
//  HELPERS
// ================================================================
function fmtMoney(v) {{ return '$' + Number(v||0).toLocaleString('en-US', {{minimumFractionDigits:2,maximumFractionDigits:2}}); }}
function fmtNum(v) {{ return Number(v||0).toLocaleString('en-US'); }}

function getCategory(c) {{
    if (c >= 10) return {{label:'VIP ★', cls:'badge-gold'}};
    if (c >= 7) return {{label:'Premium', cls:'badge-silver'}};
    if (c >= 5) return {{label:'Frecuente', cls:'badge-bronze'}};
    if (c >= 3) return {{label:'Regular', cls:'badge-blue'}};
    return {{label:'Ocasional', cls:'badge-gray'}};
}}

// ================================================================
//  SAFETY — wrap all in try-catch so page works even if Chart.js CDN fails
// ================================================================
function safeChart(canvasId, config) {{
    try {{
        if (typeof Chart === 'undefined') {{
            console.warn('Chart.js not loaded, skipping:', canvasId);
            return null;
        }}
        return new Chart(document.getElementById(canvasId), config);
    }} catch(e) {{
        console.error('Chart error on', canvasId, ':', e.message);
        return null;
    }}
}}

try {{
document.getElementById("fechaGeneracion").textContent =
    new Date().toLocaleDateString("es-ES", {{year:"numeric",month:"long",day:"numeric",hour:"2-digit",minute:"2-digit"}});

const totalFact = clientes.reduce((s,c) => s + c.facturado, 0);
const totalCob = clientes.reduce((s,c) => s + c.cobrado, 0);
const totalPen = totalFact - totalCob;

// Si hay filtro por fecha, mostrar facturas en vez de contratos
const hasFilter = window.location.hash.includes('desde') || window.location.hash.includes('hasta');
if (hasFilter && filteredInvoices) {{
    document.getElementById("hClientes").textContent = fmtNum(clientes.length);
    document.getElementById("hContratos").textContent = fmtNum(filteredInvoices.length) + ' facturas';
    document.getElementById("hFacturado").textContent = fmtMoney(totalFact);
    document.getElementById("hPendiente").textContent = fmtMoney(totalPen);
    document.getElementById("filtroInfoGlobal").textContent = filteredInvoices.length + ' facturas en rango';
}} else {{
    document.getElementById("hClientes").textContent = fmtNum(clientes.length);
    document.getElementById("hContratos").textContent = fmtNum(clientes.reduce((s,c) => s + c.contratos, 0));
    document.getElementById("hFacturado").textContent = fmtMoney(totalFact);
    document.getElementById("hPendiente").textContent = fmtMoney(totalPen);
}}

const c1 = clientes.filter(c => c.contratos === 1).length;
const c2 = clientes.filter(c => c.contratos === 2).length;
const c3 = clientes.filter(c => c.contratos >= 3).length;
const c5 = clientes.filter(c => c.contratos >= 5).length;
document.getElementById("kpi1").textContent = fmtNum(c1);
document.getElementById("kpi2").textContent = fmtNum(c2);
document.getElementById("kpi3").textContent = fmtNum(c3);
document.getElementById("kpi4").textContent = fmtNum(c5);

document.getElementById("mkpiClientes").textContent = fmtNum(clientes.length);
if (hasFilter && filteredInvoices) {{
    document.getElementById("mkpiContratos").textContent = fmtNum(filteredInvoices.length) + ' facturas';
    document.getElementById("filtroInfoMontos").textContent = filteredInvoices.length + ' facturas en rango';
}} else {{
    document.getElementById("mkpiContratos").textContent = fmtNum(clientes.reduce((s,c) => s + c.contratos, 0));
    document.getElementById("filtroInfoMontos").textContent = 'Mostrando todos';
}}
document.getElementById("mkpiFacturado").textContent = fmtMoney(totalFact);
document.getElementById("mkpiCobrado").textContent = fmtMoney(totalCob);
document.getElementById("mkpiPendiente").textContent = fmtMoney(totalPen);
document.getElementById("mkpiPromContratos").textContent = (clientes.reduce((s,c) => s + c.contratos, 0) / clientes.length).toFixed(1);
document.getElementById("mkpiPromMonto").textContent = fmtMoney(totalFact / clientes.length);
document.getElementById("mkpiMaxContratos").textContent = Math.max(...clientes.map(c => c.contratos));

// ================================================================
//  CHARTS — RESUMEN
// ================================================================
const brand = {{primary:'#213C83',primaryLight:'#3D6194',accent:'#F98B10',
    gradient:['#213C83','#2a4a96','#3458a8','#3D6194','#4a72a8','#5a82b8','#6a92c8','#7aa2d4','#8ab2e0','#9ac2ec']}};

// Top 20
const top20 = [...clientes].sort((a,b) => b.contratos - a.contratos).slice(0,20);
safeChart('chartTop20', {{
    type: 'bar',
    data: {{
        labels: top20.map(c => c.cliente.length > 28 ? c.cliente.substring(0,26)+'...' : c.cliente).reverse(),
        datasets: [{{data: top20.map(c => c.contratos).reverse(), backgroundColor: brand.gradient.slice(0,20).reverse(), borderWidth:0, borderRadius:4}}]
    }},
    options: {{
        indexAxis:'y', responsive:true, maintainAspectRatio:false,
        plugins:{{legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>ctx.parsed.x+' contratos'}}}}}},
        scales:{{x:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}, ticks:{{stepSize:2}}}}, y:{{grid:{{display:false}}, ticks:{{font:{{size:10}}}}}}}}
    }}
}});

// Distribution
safeChart('chartDist', {{
    type:'bar',
    data:{{
        labels:distribucion.map(d=>d.rango+' contratos'),
        datasets:[{{label:'Clientes', data:distribucion.map(d=>d.cantidad),
            backgroundColor:distribucion.map(d=>d.cantidad>=50?brand.primary:d.cantidad>=10?brand.primaryLight:'#b0c4de'), borderRadius:6}}]
    }},
    options:{{
        responsive:true, maintainAspectRatio:false,
        plugins:{{legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y+' clientes'}}}}}},
        scales:{{x:{{grid:{{display:false}}, ticks:{{font:{{size:10}}}}}}, y:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}}}}}
    }}
}});

// Status pie
const sc = DATA.status_counts;
const scTotal = sc.Entregado + sc.Aprobado + sc['Cancelacion Total'];
safeChart('chartStatus', {{
    type:'doughnut',
    data:{{
        labels:['Entregado ('+sc.Entregado+')', 'Cancelación Total ('+sc['Cancelacion Total']+')', 'Aprobado ('+sc.Aprobado+')'],
        datasets:[{{data:[sc.Entregado, sc['Cancelacion Total'], sc.Aprobado], backgroundColor:['#10b981','#ef4444','#3b82f6'], borderColor:'#fff', borderWidth:3}}]
    }},
    options:{{
        responsive:true, maintainAspectRatio:false,
        plugins:{{legend:{{position:'right', labels:{{font:{{size:13}}}}}}, tooltip:{{callbacks:{{label:ctx=>ctx.label+' — '+((ctx.parsed/scTotal)*100).toFixed(1)+'%'}}}}}}
    }}
}});

// ================================================================
//  CHARTS — MONTOS
// ================================================================
let chartsMontos = {{}};
function renderChartsMontos(data) {{
    const topMonto = [...data].sort((a,b) => b.facturado - a.facturado).slice(0,20);
    chartsMontos.topMonto = safeChart('chartTopMonto', {{
        type:'bar',
        data:{{
            labels:topMonto.map(c=>c.cliente.length>28?c.cliente.substring(0,26)+'...':c.cliente).reverse(),
            datasets:[
                {{label:'Facturado', data:topMonto.map(c=>c.facturado).reverse(), backgroundColor:'rgba(33,60,131,0.85)', borderRadius:3, barPercentage:0.6}},
                {{label:'Cobrado', data:topMonto.map(c=>c.cobrado).reverse(), backgroundColor:'rgba(16,185,129,0.7)', borderRadius:3, barPercentage:0.6}}
            ]
        }},
        options:{{indexAxis:'y', responsive:true, maintainAspectRatio:false,
            plugins:{{legend:{{position:'top', labels:{{font:{{size:10}}}}}}, tooltip:{{callbacks:{{label:ctx=>ctx.dataset.label+': '+fmtMoney(ctx.parsed.x)}}}}}},
            scales:{{x:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}, ticks:{{callback:v=>'$'+(v>=1000?(v/1000).toFixed(0)+'k':v)}}}}, y:{{grid:{{display:false}}, ticks:{{font:{{size:10}}}}}}}}
    }}}});

    const topCont = [...data].sort((a,b) => b.contratos - a.contratos).slice(0,20);
    chartsMontos.topContratos = safeChart('chartTopContratos', {{
        type:'bar',
        data:{{
            labels:topCont.map(c=>c.cliente.length>28?c.cliente.substring(0,26)+'...':c.cliente).reverse(),
            datasets:[{{data:topCont.map(c=>c.contratos).reverse(),
                backgroundColor:topCont.map(c=>c.contratos>=10?'#D4A017':c.contratos>=7?'#A8A8A8':c.contratos>=5?'#CD7F32':'#3D6194').reverse(), borderRadius:4}}]
        }},
        options:{{indexAxis:'y', responsive:true, maintainAspectRatio:false,
            plugins:{{legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>ctx.parsed.x+' contratos'}}}}}},
            scales:{{x:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}, ticks:{{stepSize:2}}}}, y:{{grid:{{display:false}}, ticks:{{font:{{size:10}}}}}}}}
    }}}});

    const brackets = [{{label:'< $100',min:0,max:100}},{{label:'$100-$500',min:100,max:500}},{{label:'$500-$1K',min:500,max:1000}},{{label:'$1K-$5K',min:1000,max:5000}},{{label:'$5K-$10K',min:5000,max:10000}},{{label:'$10K+',min:10000,max:Infinity}}];
    const bracketCounts = brackets.map(b => data.filter(c => c.facturado >= b.min && c.facturado < b.max).length);
    chartsMontos.distMontos = safeChart('chartDistMontos', {{
        type:'bar',
        data:{{labels:brackets.map(b=>b.label), datasets:[{{label:'Clientes', data:bracketCounts, backgroundColor:['#b0c4de','#6a92c8','#3D6194','#213C83','#F98B10','#d97706'], borderRadius:6}}]}},
        options:{{responsive:true, maintainAspectRatio:false, plugins:{{legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y+' clientes'}}}}}}, scales:{{x:{{grid:{{display:false}}}}, y:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}}}}}}}
    }});

    const pendientes = data.filter(c => c.saldo > 1).length;
    const pagados = data.filter(c => c.saldo <= 1).length;
    chartsMontos.carteraPie = safeChart('chartCarteraPie', {{
        type:'doughnut',
        data:{{labels:['Con saldo ('+fmtNum(pendientes)+')','Pagado ('+fmtNum(pagados)+')'], datasets:[{{data:[pendientes,pagados], backgroundColor:['#ef4444','#10b981'], borderColor:'#fff', borderWidth:3}}]}},
        options:{{responsive:true, maintainAspectRatio:false, plugins:{{legend:{{position:'right', labels:{{font:{{size:13}}}}}}, tooltip:{{callbacks:{{label:ctx=>ctx.label+' — '+((ctx.parsed/(pendientes+pagados))*100).toFixed(1)+'%'}}}}}}}}
    }});
}}
renderChartsMontos(clientes);

// ================================================================
//  FILTERS — MONTOS
// ================================================================
let filteredMontos = [];
function aplicarFiltrosMontos() {{
    const montoMin = parseFloat(document.getElementById('filtroMontoMin').value) || 0;
    const contMin = parseInt(document.getElementById('filtroContratosMin').value) || 0;
    const estado = document.getElementById('filtroEstado').value;
    filteredMontos = clientes.filter(c => {{
        if (c.facturado < montoMin) return false;
        if (c.contratos < contMin) return false;
        if (estado === 'pagado' && c.saldo > 1) return false;
        if (estado === 'pendiente' && c.saldo < 1) return false;
        return true;
    }});
    document.getElementById('filtroInfoMontos').textContent = filteredMontos.length === clientes.length ? 'Mostrando todos' : 'Mostrando '+filteredMontos.length+' de '+clientes.length+' clientes';
    Object.values(chartsMontos).forEach(ch => {{ if (ch) ch.destroy(); }});
    renderChartsMontos(filteredMontos);
}}
function resetFiltrosMontos() {{
    document.getElementById('filtroMontoMin').value = '';
    document.getElementById('filtroContratosMin').value = '';
    document.getElementById('filtroEstado').value = 'todos';
    filteredMontos = [];
    document.getElementById('filtroInfoMontos').textContent = 'Mostrando todos';
    Object.values(chartsMontos).forEach(ch => {{ if (ch) ch.destroy(); }});
    renderChartsMontos(clientes);
}}

// ================================================================
//  SEGMENTOS
// ================================================================
(function() {{
    const segKeys = Object.keys(segmentStats);
    const segColors = {{'bajo_dependencia':'#213C83','independiente':'#F98B10','dependiente_publico':'#10b981','independiente_formal':'#F59E0B','dependiente_privado':'#ef4444','independiente_informal':'#f97316','Sin clasificar':'#9ca3af'}};

    // KPI cards
    const kpiRow = document.getElementById('segmentKpis');
    kpiRow.innerHTML = segKeys.map(s => `
        <div class="kpi-card" style="border-top-color:${{segColors[s]||'#999'}};cursor:pointer" onclick="filterBySegment('${{s}}')" title="Ver facturas de ${{s}}">
            <div class="number">${{segmentStats[s].contratos}}</div>
            <div class="label">${{s}}</div>
            <div style="font-size:10px;color:#888;margin-top:4px">${{segmentStats[s].clientes}} clientes - ${{fmtMoney(segmentStats[s].facturado)}}</div>
        </div>
    `).join('');

    // Pie chart (facturas)
    const totalFacturas = segKeys.reduce((a,s)=>a+segmentStats[s].contratos, 0);
    safeChart('chartSegPie', {{
        type:'doughnut',
        data:{{
            labels:segKeys.map(s=>s+' ('+segmentStats[s].contratos+')'),
            datasets:[{{data:segKeys.map(s=>segmentStats[s].contratos), backgroundColor:segKeys.map(s=>segColors[s]||'#9ca3af'), borderColor:'#fff', borderWidth:3}}]
        }},
        options:{{responsive:true, maintainAspectRatio:false, plugins:{{legend:{{position:'right', labels:{{font:{{size:12}}}}}}, tooltip:{{callbacks:{{label:ctx=>ctx.label+' — '+((ctx.parsed/totalFacturas)*100).toFixed(1)+'%'}}}}}}}}
    }});

    // Facturado bar
    safeChart('chartSegFact', {{
        type:'bar',
        data:{{
            labels:segKeys,
            datasets:[{{label:'Facturado', data:segKeys.map(s=>segmentStats[s].facturado), backgroundColor:segKeys.map(s=>segColors[s]||'#9ca3af'), borderRadius:6}}]
        }},
        options:{{
            responsive:true, maintainAspectRatio:false,
            plugins:{{legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>fmtMoney(ctx.parsed.y)}}}}}},
            scales:{{x:{{grid:{{display:false}}}}, y:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}, ticks:{{callback:v=>'$'+(v>=1000?(v/1000).toFixed(0)+'k':v)}}}}}}
        }}
    }});

    // Last 200 chart
    const l200Keys = Object.keys(last200);
    safeChart('chartLast200', {{
        type:'bar',
        data:{{
            labels:l200Keys,
            datasets:[{{label:'Contratos', data:l200Keys.map(k=>last200[k]), backgroundColor:l200Keys.map(k=>segColors[k]||'#9ca3af'), borderRadius:6}}]
        }},
        options:{{
            responsive:true, maintainAspectRatio:false,
            plugins:{{legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y+' contratos ('+((ctx.parsed.y/200)*100).toFixed(1)+'%)'}}}}}},
            scales:{{x:{{grid:{{display:false}}}}, y:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}}}}}
        }}
    }});

    // Detalle table
    const wrap = document.getElementById('segmentTableWrap');
    wrap.innerHTML = `<table>
        <thead><tr><th>Segmento</th><th>Clientes</th><th>Contratos</th><th>Facturado</th><th>Cobrado</th><th>% Cartera</th><th></th></tr></thead>
        <tbody>${{segKeys.map(s => {{
            const st = segmentStats[s];
            const pct = ((st.clientes / clientes.length) * 100).toFixed(1);
            return `<tr style="cursor:pointer" onclick="filterBySegment('${{s}}')" title="Ver clientes de ${{s}}">
                <td><strong>${{s}}</strong></td><td class="text-right">${{st.clientes}}</td><td class="text-right">${{st.contratos}}</td>
                <td class="text-right">${{fmtMoney(st.facturado)}}</td><td class="text-right">${{fmtMoney(st.cobrado)}}</td>
                <td class="text-right">${{pct}}%</td>
                <td style="font-size:11px;color:var(--primary)">Ver →</td></tr>`;
        }}).join('')}}</tbody>
    </table>`;
}})();

// ================================================================
//  TEMPORAL VIP
// ================================================================
const vipClients = DATA.vip;
let filteredVip = [...vipClients];
let timelineChart = null;

// Compute temporal metrics
function calcTemporal(vip) {{
    if (!vip.length) return {{avgSpan:0, avgFreq:0, maxSpan:0}};
    let spans = [], freqs = [];
    vip.forEach(c => {{
        if (c.first && c.last) {{
            const d1 = new Date(c.first), d2 = new Date(c.last);
            const spanD = Math.round((d2 - d1) / (86400000));
            const spanM = Math.round(spanD / 30.44 * 10) / 10;
            spans.push(spanM);
            if (c.cont > 1) freqs.push(Math.round(spanD / (c.cont - 1)));
        }}
    }});
    return {{
        avgSpan: spans.length ? spans.reduce((a,b)=>a+b,0)/spans.length : 0,
        avgFreq: freqs.length ? freqs.reduce((a,b)=>a+b,0)/freqs.length : 0,
        maxSpan: spans.length ? Math.max(...spans) : 0
    }};
}}

function buildTimelineChartData(data) {{
    if (!data.length) return {{labels:[], offsets:[], spans:[], base:new Date()}};
    const sorted = [...data].sort((a,b) => (a.first||'').localeCompare(b.first||''));
    const base = new Date(sorted[0].first);
    return {{
        labels: sorted.map(c => c.cliente.length > 25 ? c.cliente.substring(0,23)+'...' : c.cliente).reverse(),
        offsets: sorted.map(c => Math.round((new Date(c.first) - base) / 86400000)).reverse(),
        spans: sorted.map(c => Math.round((new Date(c.last) - new Date(c.first)) / 86400000)).reverse(),
        base
    }};
}}

function renderTimeline(data) {{
    const tl = buildTimelineChartData(data);
    const ctx = document.getElementById('chartTimeline');
    if (timelineChart) timelineChart.destroy();
    timelineChart = safeChart('chartTimeline', {{
        type:'bar',
        data:{{
            labels: tl.labels,
            datasets: [
                {{label:'Offset', data: tl.offsets, backgroundColor:'rgba(33,60,131,0.15)', borderRadius:0, barPercentage:0.7}},
                {{label:'Período activo', data: tl.spans, backgroundColor:'#F98B10', borderRadius:4}}
            ]
        }},
        options:{{
            indexAxis:'y', responsive:true, maintainAspectRatio:false, stacked:true,
            plugins:{{legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>ctx.datasetIndex===0?'Inicio: '+data[data.length-1-ctx.dataIndex].first:data[data.length-1-ctx.dataIndex].first+' → '+data[data.length-1-ctx.dataIndex].last}}}}}},
            scales:{{x:{{stacked:true, title:{{display:true, text:'Días desde '+tl.base.toISOString().split('T')[0], font:{{size:10}}}}, grid:{{color:'rgba(0,0,0,0.05)'}}}}, y:{{stacked:true, grid:{{display:false}}, ticks:{{font:{{size:9}}}}}}}}
    }}}});
}}

function renderTemporalTable(data) {{
    const tbody = document.getElementById('tbodyTemporal');
    if (!data.length) {{
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#999;">Sin datos</td></tr>';
        return;
    }}
    tbody.innerHTML = data.map((c,i) => {{
        const d1 = new Date(c.first), d2 = new Date(c.last);
        const spanD = Math.round((d2 - d1) / 86400000);
        const spanM = Math.round(spanD / 30.44 * 10) / 10;
        return `<tr class="${{i<3?'top-client':''}} ${{i===0?'rank-1':i===1?'rank-2':i===2?'rank-3':''}}">
            <td><strong>${{i+1}}</strong></td><td>${{c.cliente}}</td>
            <td class="text-right"><strong>${{c.cont}}</strong></td>
            <td>${{c.first}}</td><td>${{c.last}}</td>
            <td class="text-right">${{spanD}}</td><td class="text-right">${{spanM.toFixed(1)}}</td>
        </tr>`;
    }}).join('');
}}

function updateTemporalUI(data) {{
    const tmp = calcTemporal(data);
    document.getElementById('tmpVipCount').textContent = data.length;
    document.getElementById('tmpAvgSpan').textContent = tmp.avgSpan.toFixed(1)+' m';
    document.getElementById('tmpAvgFreq').textContent = Math.round(tmp.avgFreq)+' d';
    document.getElementById('tmpMaxSpan').textContent = tmp.maxSpan.toFixed(1)+' m';
    renderTimeline(data);
    renderTemporalTable(data);
}}

// Relationship chart
const relacionData = [
    {{label:'1-3 meses', count: vipClients.filter(c => {{const d=(new Date(c.last)-new Date(c.first))/86400000/30.44; return d>=1 && d<3;}}).length}},
    {{label:'3-6 meses', count: vipClients.filter(c => {{const d=(new Date(c.last)-new Date(c.first))/86400000/30.44; return d>=3 && d<6;}}).length}},
    {{label:'6-12 meses', count: vipClients.filter(c => {{const d=(new Date(c.last)-new Date(c.first))/86400000/30.44; return d>=6 && d<12;}}).length}},
    {{label:'12-24 meses', count: vipClients.filter(c => {{const d=(new Date(c.last)-new Date(c.first))/86400000/30.44; return d>=12 && d<24;}}).length}},
    {{label:'24+ meses', count: vipClients.filter(c => {{const d=(new Date(c.last)-new Date(c.first))/86400000/30.44; return d>=24;}}).length}}
];
safeChart('chartRelacion', {{
    type:'bar',
    data:{{labels:relacionData.map(d=>d.label), datasets:[{{label:'Clientes VIP', data:relacionData.map(d=>d.count), backgroundColor:['#b0c4de','#6a92c8','#3D6194','#213C83','#F98B10'], borderRadius:6}}]}},
    options:{{responsive:true, maintainAspectRatio:false, plugins:{{legend:{{display:false}}}}, scales:{{x:{{grid:{{display:false}}}}, y:{{beginAtZero:true, grid:{{color:'rgba(0,0,0,0.05)'}}, ticks:{{stepSize:1}}}}}}}}
}});

updateTemporalUI(vipClients);

// ================================================================
//  TABLE
// ================================================================
let currentPage = 1;
let sortColumn = 2;
let sortDesc = true;
const PAGE_SIZE = 25;
let filteredData = [...clientes];
let segmentFilter = '';

function filterTable() {{
    const term = document.getElementById('searchInput').value.toUpperCase();
    filteredData = clientes.filter(c => {{
        if (segmentFilter && c.segmento !== segmentFilter) return false;
        if (term && !c.cliente.toUpperCase().includes(term)) return false;
        return true;
    }});
    currentPage = 1;
    renderTable();
}}

function filterBySegment(seg) {{
    segmentFilter = seg;
    const searchInput = document.getElementById('searchInput');
    searchInput.value = '';
    document.getElementById('clearSegFilter').style.display = seg ? 'inline-block' : 'none';
    document.getElementById('segFilterLabel').textContent = seg ? 'Segmento: ' + seg : '';
    document.getElementById('segFilterLabel').style.display = seg ? 'inline-block' : 'none';
    filterTable();
    switchTab('tabla');
}}

function clearSegmentFilter() {{
    filterBySegment('');
}}

function sortTable(col) {{
    if (sortColumn === col) sortDesc = !sortDesc;
    else {{ sortColumn = col; sortDesc = col === 2 || col === 3; }}
    document.querySelectorAll('thead th').forEach((th,i) => {{
        th.classList.toggle('sorted', i === col);
        const icon = th.querySelector('.sort-icon');
        if (icon) icon.textContent = i === col ? (sortDesc ? '▼' : '▲') : '⇅';
    }});
    const getVal = (c, col) => {{
        switch(col) {{
            case 0: return clientes.indexOf(c);
            case 1: return c.cliente;
            case 2: return c.contratos;
            case 3: return c.facturado;
            case 4: return c.cobrado;
            case 5: return c.saldo;
            case 6: return c.prom;
            case 7: return c.segmento || '';
            case 8: return c.contratos + c.facturado;
            default: return c.contratos;
        }}
    }};
    filteredData.sort((a,b) => {{
        const va = getVal(a,col), vb = getVal(b,col);
        if (typeof va === 'string') return sortDesc ? vb.localeCompare(va) : va.localeCompare(vb);
        return sortDesc ? vb - va : va - vb;
    }});
    currentPage = 1;
    renderTable();
}}

function changePage(delta) {{
    const totalPages = Math.ceil(filteredData.length / PAGE_SIZE);
    const np = currentPage + delta;
    if (np < 1 || np > totalPages) return;
    currentPage = np;
    renderTable();
}}

function renderTable() {{
    const totalPages = Math.ceil(filteredData.length / PAGE_SIZE);
    const start = (currentPage - 1) * PAGE_SIZE;
    const end = Math.min(start + PAGE_SIZE, filteredData.length);
    const pageData = filteredData.slice(start, end);
    document.getElementById('totalRows').textContent = filteredData.length;
    document.getElementById('showing').textContent = filteredData.length;
    document.getElementById('pageInfo').textContent = 'Página '+currentPage+' / '+Math.max(1,totalPages);
    document.getElementById('prevBtn').disabled = currentPage <= 1;
    document.getElementById('nextBtn').disabled = currentPage >= totalPages;
    const tbody = document.getElementById('tableBody');
    if (!pageData.length) {{
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:30px;color:#999;">Sin clientes</td></tr>';
        return;
    }}
    tbody.innerHTML = pageData.map((c,i) => {{
        const ri = start + i + 1;
        const cat = getCategory(c.contratos);
        const r = ri===1?'rank-1':ri===2?'rank-2':ri===3?'rank-3':'';
        const t = ri<=3?'top-client':'';
        return `<tr class="${{r}} ${{t}}">
            <td><strong>${{ri}}</strong></td>
            <td>${{c.cliente}}</td>
            <td class="text-right"><strong>${{c.contratos}}</strong></td>
            <td class="text-right">${{fmtMoney(c.facturado)}}</td>
            <td class="text-right">${{fmtMoney(c.cobrado)}}</td>
            <td class="text-right">${{fmtMoney(c.saldo)}}</td>
            <td class="text-right">${{fmtMoney(c.prom)}}</td>
            <td><span class="badge badge-blue">${{c.segmento||'N/A'}}</span></td>
            <td><span class="badge ${{cat.cls}}">${{cat.label}}</span></td>
        </tr>`;
    }}).join('');
}}

// ================================================================
//  TABS
// ================================================================
function switchTab(tab) {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const tabMap = {{resumen:'Resumen', montos:'Montos', segmentos:'Segmentos', temporal:'Temporal', tabla:'Listado', pagos:'Plan de Pagos'}};
    const btn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes(tabMap[tab]));
    if (btn) btn.classList.add('active');
    document.getElementById('tab-'+tab).classList.add('active');
    setTimeout(() => {{
        document.querySelectorAll('canvas').forEach(c => {{
            const ch = typeof Chart !== 'undefined' ? Chart.getChart(c) : null;
            if (ch) ch.resize();
        }});
    }}, 100);
}}

// ================================================================
//  PLAN DE PAGOS
// ================================================================
try {{
    const pp = DATA.payment_plan;
    if (pp) {{
        document.getElementById('pkTotalVencido').textContent = fmtMoney(pp.total_vencido);
        document.getElementById('pkTotalDebido').textContent = fmtMoney(pp.total_debido);
        document.getElementById('pkTotalPagado').textContent = fmtMoney(pp.total_pagado);
        
        const vencidos = pp.clientes_vencidos || [];
        document.getElementById('pkVencidoTotal').textContent = fmtMoney(pp.total_vencido);
        document.getElementById('pkVencidosCount').textContent = vencidos.length;
        var tbody = document.getElementById('tablaVencidos');
        if (tbody) {{
            tbody.innerHTML = vencidos.slice(0,50).map(function(v) {{
                var factStr = v.facturas.slice(0,3).join(', ');
                if (v.facturas.length > 3) factStr += '...';
                return '<tr><td><strong>' + v.cliente + '</strong></td><td class=\"text-right\">' + v.cuotas + '</td><td class=\"text-right\" style=\"color:#ef4444;font-weight:600\">' + fmtMoney(v.monto) + '</td><td style=\"font-size:11px;color:#666\">' + factStr + '</td></tr>';
            }}).join('');
        }}
    }}
}} catch(e) {{ console.error('Payment plan error:', e); }}

renderTable();
switchTab('resumen');
}} catch(e) {{
    console.error('Page init error:', e.message, e.stack);
    var errEl = document.getElementById('fechaGeneracion');
    if (errEl) errEl.textContent = 'Error: ' + e.message;
    var msg = document.createElement('div');
    msg.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#c00;color:#fff;padding:12px 20px;font-family:sans-serif;font-size:14px;z-index:9999';
    msg.textContent = '⚠ Error inicial: ' + e.message + ' (revisa consola F12)';
    document.body.appendChild(msg);
}}
</script>
</body>
</html>'''

output_path = os.path.join(os.path.dirname(__file__), 'index.html')
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"Written to {output_path}")
print(f"File size: {os.path.getsize(output_path)} bytes")
print("Done!")
