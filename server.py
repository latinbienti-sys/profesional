#!/usr/bin/env python3
"""Servidor web dinámico para el dashboard LATINBIEN.
Se alimenta en tiempo real desde Google Sheets (datos de latinbien.com).
Inicia con: python server.py
Luego abre: http://localhost:8000"""

import csv, os, json, urllib.request, io, sys, re
from collections import defaultdict, Counter
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Configuración ─────────────────────────────────────────────
HOST = '0.0.0.0'
PORT = 8000
TSV_URL = "https://docs.google.com/spreadsheets/d/1kKq4y9ZtjmdacmEgQtMX64_puRNClibBOUd0in5TB6I/export?format=tsv&gid=1961588350"

# ── Procesamiento de datos (misma lógica que generar_html.py) ─
def fetch_and_process():
    """Descarga TSV, filtra y retorna el JSON de datos."""
    try:
        resp = urllib.request.urlopen(TSV_URL, timeout=30)
        raw = resp.read().decode('utf-8')
    except Exception as e:
        raise Exception(f'Error descargando TSV: {e}')
    
    reader = csv.reader(io.StringIO(raw), delimiter='\t')
    rows = list(reader)
    if len(rows) < 2:
        raise Exception('TSV vacío o sin datos')

    headers = rows[0]
    col_idx = {h: i for i, h in enumerate(headers)}

    STATUS_OP = col_idx.get('Status Operativo', 42)
    CLIENTE = col_idx.get('Nombre del socio a mostrar en la Factura', 29)
    TOTAL = col_idx.get('Total con signo', 44)
    PAGADO = col_idx.get('Total pagado', 46)
    FECHA = col_idx.get('Fecha', 15)
    TRABAJADOR = col_idx.get('Trabajador Profesional', 48)
    ACTIVE_STATUSES = {'6. CVG - ENTREGADO', '4. SAV - APROBADO - ESPERA ENTREGA', '8. CANCELACION TOTAL'}

    def classify_worker(tipo):
        tl = tipo.lower().strip()
        if 'independ' in tl or 'informal' in tl: return 'Independiente'
        if 'publico' in tl: return 'Sector público'
        if 'privado' in tl: return 'Sector privado'
        if 'depend' in tl or 'bajo_dependencia' in tl: return 'Dependientes'
        return 'No clasificado'

    active_rows = []
    status_counter = Counter()
    for row in rows[1:]:
        if len(row) <= max(col_idx.values()): continue
        status = row[STATUS_OP].strip() if STATUS_OP < len(row) else ''
        status_counter[status] += 1
        if status not in ACTIVE_STATUSES: continue
        try:
            total_val = float(row[TOTAL].replace(',', '')) if TOTAL < len(row) and row[TOTAL].strip() else 0
        except: total_val = 0
        try:
            pagado_val = float(row[PAGADO].replace(',', '')) if PAGADO < len(row) and row[PAGADO].strip() else 0
        except: pagado_val = 0
        active_rows.append({
            'cliente': row[CLIENTE].strip() if CLIENTE < len(row) else 'N/A',
            'total': total_val, 'pagado': pagado_val,
            'fecha': row[FECHA].strip() if FECHA < len(row) else '',
            'trabajador': row[TRABAJADOR].strip().lower() if TRABAJADOR < len(row) and row[TRABAJADOR].strip() else 'desconocido',
        })

    # Agregar por cliente
    clients_dict = defaultdict(lambda: {'contratos': 0, 'facturado': 0.0, 'cobrado': 0.0, 'fechas': [], 'worker_types': Counter()})
    for r in active_rows:
        c = r['cliente']
        clients_dict[c]['contratos'] += 1
        clients_dict[c]['facturado'] += r['total']
        clients_dict[c]['cobrado'] += r['pagado']
        clients_dict[c]['fechas'].append(r['fecha'])
        clients_dict[c]['worker_types'][r['trabajador']] += 1

    client_list = []
    dist = Counter()
    seg_stats = defaultdict(lambda: {'clientes': 0, 'contratos': 0, 'facturado': 0.0, 'cobrado': 0.0})
    for c_name, c_data in sorted(clients_dict.items(), key=lambda x: -x[1]['contratos']):
        fechas_ordenadas = sorted([f for f in c_data['fechas'] if f])
        first_date = fechas_ordenadas[0] if fechas_ordenadas else ''
        last_date = fechas_ordenadas[-1] if fechas_ordenadas else ''
        prom = round(c_data['facturado'] / c_data['contratos'], 2) if c_data['contratos'] else 0
        wt = c_data['worker_types'].most_common(1)[0][0] if c_data['worker_types'] else 'No clasificado'
        worker = classify_worker(wt)
        dist[c_data['contratos']] += 1
        seg_stats[worker]['clientes'] += 1
        seg_stats[worker]['contratos'] += c_data['contratos']
        seg_stats[worker]['facturado'] += c_data['facturado']
        seg_stats[worker]['cobrado'] += c_data['cobrado']
        client_list.append({
            'cliente': c_name, 'contratos': c_data['contratos'],
            'facturado': round(c_data['facturado'], 2),
            'cobrado': round(c_data['cobrado'], 2),
            'saldo': round(c_data['facturado'] - c_data['cobrado'], 2),
            'prom': prom, 'worker_type': worker,
            'segmento': worker, 'first_date': first_date, 'last_date': last_date,
        })

    active_rows.sort(key=lambda r: r['fecha'] if r['fecha'] else '', reverse=True)
    last_200 = active_rows[:200]
    last200_seg = Counter()
    for r in last_200: last200_seg[classify_worker(r['trabajador'])] += 1

    vip_clients = [c for c in client_list if c['contratos'] >= 5]
    vip_clients.sort(key=lambda x: -x['contratos'])

    return {
        'status_summary': dict(status_counter.most_common()),
        'total_rows': len(rows) - 1,
        'client_count': len(client_list),
        'total_facturado': round(sum(c['facturado'] for c in client_list), 2),
        'total_cobrado': round(sum(c['cobrado'] for c in client_list), 2),
        'distribucion': [{'rango': k, 'cantidad': v} for k, v in sorted(dist.items())],
        'status_counts': {'Entregado': status_counter.get('6. CVG - ENTREGADO', 0),
                          'Aprobado': status_counter.get('4. SAV - APROBADO - ESPERA ENTREGA', 0),
                          'Cancelacion Total': status_counter.get('8. CANCELACION TOTAL', 0)},
        'clients': client_list,
        'segment_stats': {s: dict(v) for s, v in sorted(seg_stats.items(), key=lambda x: -x[1]['contratos'])},
        'last200': dict(last200_seg.most_common()),
        'vip': [{'cliente': c['cliente'], 'cont': c['contratos'],
                 'first': c['first_date'], 'last': c['last_date']} for c in vip_clients],
    }

# ── Generar HTML dinámico ────────────────────────────────────
def build_html(data):
    """Toma el JSON de datos y genera el HTML completo del dashboard.
    Reutiliza la plantilla HTML de generar_html.py."""
    # Leer el template de generar_html.py
    script_path = os.path.join(os.path.dirname(__file__), 'generar_html.py')
    with open(script_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    # Extraer la plantilla HTML (entre f''' y ''')
    m = re.search(r"^html = f'''(.+)'''", source, re.DOTALL | re.MULTILINE)
    if not m:
        # Buscar el final de la plantilla de otra forma
        start = source.find("html = f'''")
        if start < 0:
            start = source.find("html = '''")
        if start < 0:
            raise Exception('No se encontró la plantilla HTML en generar_html.py')
# Encontrar el cierre de '''
        prefix = source[start:start+11]  # "html = f'''" or "html = '''"
        skip = len(prefix)  # should be 11
        rest = source[start+skip:]
        end = rest.find("'''")
        if end < 0:
            raise Exception('No se encontró el cierre de la plantilla HTML')
        tpl = rest[:end]
    else:
        tpl = m.group(1)
    
    # Convertir JSON a string escapado para JS
    json_str = json.dumps(data, ensure_ascii=False)
    json_escaped = json_str.replace('\\', '\\\\').replace("'", "\\'")
    
    # Reemplazar {json_escaped} en la plantilla
    html = tpl.replace('{json_escaped}', json_escaped)
    
    # Convertir {{ → { y }} → } (escape de f-string)
    html = html.replace('{{', '{').replace('}}', '}')
    
    return html

# ── Cache ─────────────────────────────────────────────────────
DATA_CACHE = None
HTML_CACHE = None

def get_dashboard():
    global DATA_CACHE, HTML_CACHE
    try:
        data = fetch_and_process()
        DATA_CACHE = data
        HTML_CACHE = build_html(data)
        return HTML_CACHE
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        if HTML_CACHE:
            print('Sirviendo cache anterior...', file=sys.stderr)
            return HTML_CACHE
        raise

# ── Servidor HTTP ─────────────────────────────────────────────
class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/index.html'):
            try:
                html = get_dashboard()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(html.encode('utf-8'))
            except Exception as e:
                self.send_error(500, f'Error generando dashboard: {e}')
        else:
            self.send_error(404)
    
    def log_message(self, format, *args):
        print(f'[{self.address_string()}] {format % args}', file=sys.stderr)

if __name__ == '__main__':
    print('='*60)
    print('  LATINBIEN - Dashboard en Vivo')
    print('='*60)
    print()
    print('  Cargando datos desde Google Sheets...')
    try:
        data = fetch_and_process()
        DATA_CACHE = data
        HTML_CACHE = build_html(data)
        print(f'  OK - {data["client_count"]} clientes, {data["total_rows"]} facturas totales')
    except Exception as e:
        print(f'  ERROR: {e}')
        print('  El servidor iniciara igual, pero puede fallar en la primera solicitud.')
    
    print()
    print(f'  Abre esta URL en tu navegador:')
    print(f'  ----------------')
    print(f'  http://localhost:{PORT}/')
    print(f'  ----------------')
    print()
    print('  Los datos se actualizan desde la fuente en cada visita.')
    print('  Presiona Ctrl+C para detener el servidor.')
    print()
    
    server = HTTPServer((HOST, PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Servidor detenido.')
        server.server_close()