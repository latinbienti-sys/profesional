#!/usr/bin/env python3
"""Servidor web dinámico — Dashboard LATINBIEN en tiempo real.
Se conecta DIRECTAMENTE a Odoo 16 (latinbien.com) vía API XML-RPC.
Sin Google Sheets, sin pasos manuales. Datos vivos desde la facturación.

Uso: python server.py
Luego abre: http://localhost:8000/"""

import os, sys, json, re
import xmlrpc.client
from collections import defaultdict, Counter
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Configuración Odoo ──────────────────────────────────────────
ODOO_URL = 'https://latinbien.com'
ODOO_DB = 'erp_production'
ODOO_USER = 'latinbienti@latinbien.com'
ODOO_PASS = 'z+cakaSe2805*'

# Statuses que incluimos (Entregado, Aprobado, Cancelación Total)
TARGET_STATUSES = ['6', '4', '8']

# ── Cliente Odoo ────────────────────────────────────────────────
def odoo_connect():
    """Autentica y retorna uid + models proxy."""
    common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
    if not uid:
        raise Exception('Error de autenticación en Odoo')
    models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
    return uid, models

def fetch_data():
    """Trae todas las facturas desde Odoo con los status indicados."""
    uid, models = odoo_connect()
    
    domain = [
        ['x_status_operativos', 'in', TARGET_STATUSES],
        ['move_type', '=', 'out_invoice'],
    ]
    
    fields = [
        'id', 'name', 'partner_id', 'amount_total', 'amount_residual',
        'invoice_date', 'x_status_operativos', 'x_work_profesional',
    ]
    
    ids = models.execute_kw(ODOO_DB, uid, ODOO_PASS, 'account.move',
                            'search', [domain])
    
    # Leer en lotes para evitar timeouts
    batch_size = 500
    all_recs = []
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i+batch_size]
        recs = models.execute_kw(ODOO_DB, uid, ODOO_PASS, 'account.move',
                                 'read', [batch, fields])
        all_recs.extend(recs)
    
    # Procesar: extraer datos relevantes
    rows = []
    status_counter = Counter()
    for r in all_recs:
        status_key = f'{r["x_status_operativos"]}. ' + {
            '6': 'CVG - ENTREGADO',
            '4': 'SAV - APROBADO - ESPERA ENTREGA',
            '8': 'CANCELACION TOTAL',
        }.get(str(r['x_status_operativos']), str(r['x_status_operativos']))
        status_counter[status_key] += 1
        
        partner_name = ''
        if isinstance(r.get('partner_id'), list) and len(r['partner_id']) > 1:
            partner_name = r['partner_id'][1]
        
        total = float(r['amount_total'] or 0)
        residual = float(r['amount_residual'] or 0)
        pagado = total - residual
        
        rows.append({
            'cliente': partner_name,
            'total': total,
            'pagado': max(pagado, 0),
            'fecha': str(r.get('invoice_date') or ''),
            'trabajador': str(r.get('x_work_profesional') or 'desconocido'),
            'status': status_key,
        })
    
    # Clasificar trabajador
    def classify_worker(tipo):
        tl = tipo.lower().strip()
        if 'independ' in tl or 'informal' in tl: return 'Independiente'
        if 'publico' in tl: return 'Sector público'
        if 'privado' in tl: return 'Sector privado'
        if 'depend' in tl or 'bajo_dependencia' in tl: return 'Dependientes'
        return 'No clasificado'
    
    # Agregar por cliente
    clients_dict = defaultdict(lambda: {
        'contratos': 0, 'facturado': 0.0, 'cobrado': 0.0,
        'fechas': [], 'worker_types': Counter()
    })
    for r in rows:
        c = r['cliente']
        clients_dict[c]['contratos'] += 1
        clients_dict[c]['facturado'] += r['total']
        clients_dict[c]['cobrado'] += r['pagado']
        clients_dict[c]['fechas'].append(r['fecha'])
        clients_dict[c]['worker_types'][r['trabajador']] += 1
    
    client_list = []
    dist = Counter()
    seg_stats = defaultdict(lambda: {'clientes': 0, 'contratos': 0,
                                      'facturado': 0.0, 'cobrado': 0.0})
    for c_name, c_data in sorted(clients_dict.items(),
                                  key=lambda x: -x[1]['contratos']):
        fechas_ordenadas = sorted([f for f in c_data['fechas'] if f])
        first_date = fechas_ordenadas[0] if fechas_ordenadas else ''
        last_date = fechas_ordenadas[-1] if fechas_ordenadas else ''
        prom = round(c_data['facturado'] / c_data['contratos'], 2) \
               if c_data['contratos'] else 0
        wt = c_data['worker_types'].most_common(1)[0][0] \
             if c_data['worker_types'] else 'No clasificado'
        worker = classify_worker(wt)
        dist[c_data['contratos']] += 1
        seg_stats[worker]['clientes'] += 1
        seg_stats[worker]['contratos'] += c_data['contratos']
        seg_stats[worker]['facturado'] += c_data['facturado']
        seg_stats[worker]['cobrado'] += c_data['cobrado']
        client_list.append({
            'cliente': c_name,
            'contratos': c_data['contratos'],
            'facturado': round(c_data['facturado'], 2),
            'cobrado': round(c_data['cobrado'], 2),
            'saldo': round(c_data['facturado'] - c_data['cobrado'], 2),
            'prom': prom,
            'worker_type': worker,
            'segmento': worker,
            'first_date': first_date,
            'last_date': last_date,
        })
    
    # Últimos 200
    rows.sort(key=lambda r: r['fecha'] if r['fecha'] else '', reverse=True)
    last_200 = rows[:200]
    last200_seg = Counter()
    for r in last_200:
        last200_seg[classify_worker(r['trabajador'])] += 1
    
    # VIP (5+ contratos)
    vip_clients = [c for c in client_list if c['contratos'] >= 5]
    vip_clients.sort(key=lambda x: -x['contratos'])
    
    # Total rows (all statuses, not just our selection)
    all_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASS, 'account.move',
                                 'search_count', [[['move_type', '=', 'out_invoice']]])
    
    return {
        'status_summary': dict(status_counter.most_common()),
        'total_rows': all_ids,
        'client_count': len(client_list),
        'total_facturado': round(sum(c['facturado'] for c in client_list), 2),
        'total_cobrado': round(sum(c['cobrado'] for c in client_list), 2),
        'distribucion': [{'rango': k, 'cantidad': v}
                         for k, v in sorted(dist.items())],
        'status_counts': {
            'Entregado': status_counter.get('6. CVG - ENTREGADO', 0),
            'Aprobado': status_counter.get('4. SAV - APROBADO - ESPERA ENTREGA', 0),
            'Cancelacion Total': status_counter.get('8. CANCELACION TOTAL', 0),
        },
        'clients': client_list,
        'segment_stats': {s: dict(v)
                          for s, v in sorted(seg_stats.items(),
                                             key=lambda x: -x[1]['contratos'])},
        'last200': dict(last200_seg.most_common()),
        'vip': [{'cliente': c['cliente'], 'cont': c['contratos'],
                 'first': c['first_date'], 'last': c['last_date']}
                for c in vip_clients],
    }

# ── Generar HTML ────────────────────────────────────────────────
def build_html(data):
    script_path = os.path.join(os.path.dirname(__file__), 'generar_html.py')
    with open(script_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    start = source.find("html = f'''")
    if start < 0:
        raise Exception('No se encontró la plantilla HTML')
    
    prefix = source[start:start+11]
    rest = source[start+len(prefix):]
    end = rest.find("'''")
    if end < 0:
        raise Exception('No se encontró el cierre de la plantilla')
    tpl = rest[:end]
    
    json_str = json.dumps(data, ensure_ascii=False)
    json_escaped = json_str.replace('\\', '\\\\').replace("'", "\\'")
    
    html = tpl.replace('{json_escaped}', json_escaped)
    html = html.replace('{{', '{').replace('}}', '}')
    return html

# ── Cache ────────────────────────────────────────────────────────
DATA_CACHE = None
HTML_CACHE = None

def get_dashboard():
    global DATA_CACHE, HTML_CACHE
    try:
        data = fetch_data()
        DATA_CACHE = data
        HTML_CACHE = build_html(data)
        return HTML_CACHE
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        if HTML_CACHE:
            print('Sirviendo cache anterior...', file=sys.stderr)
            return HTML_CACHE
        raise

# ── Servidor HTTP ────────────────────────────────────────────────
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
                self.send_error(500, f'Error: {e}')
        else:
            self.send_error(404)
    
    def log_message(self, format, *args):
        print(f'[{self.address_string()}] {format % args}', file=sys.stderr)

if __name__ == '__main__':
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    
    print('='*55)
    print('  LATINBIEN - Dashboard en Tiempo Real')
    print('  Conectado directo a Odoo 16')
    print('='*55)
    print()
    print('  Cargando datos desde Odoo...')
    try:
        data = fetch_data()
        DATA_CACHE = data
        HTML_CACHE = build_html(data)
        print(f'  OK - {data["client_count"]} clientes, '
              f'{sum(data["status_counts"].values())} facturas emitidas')
    except Exception as e:
        print(f'  ERROR: {e}')
        sys.exit(1)
    
    print()
    print(f'  Abre esta URL en tu navegador:')
    print(f'  http://localhost:{PORT}/')
    print()
    print('  Los datos vienen DIRECTAMENTE desde latinbien.com')
    print('  Cada visita obtiene los datos mas recientes.')
    print('  Presiona Ctrl+C para detener.')
    print()
    
    server = HTTPServer(('0.0.0.0', PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Servidor detenido.')
        server.server_close()