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
ODOO_USER = os.environ.get('ODOO_USER', 'latinbienti@latinbien.com')
ODOO_PASS = os.environ.get('ODOO_PASS', 'z+cakaSe2805*')

# Statuses que incluimos (Entregado, Aprobado, Cancelación Total)
TARGET_STATUSES = ['6', '4', '8']

# ── Cliente Odoo ────────────────────────────────────────────────
def odoo_connect():
    """Autentica y retorna uid + models proxy (XML-RPC) + session (JSON-RPC opcional)."""
    common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
    if not uid:
        raise Exception('Error de autenticación en Odoo')
    models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
    return uid, models

def json_connect():
    """Autentica vía JSON-RPC y retorna session + uid.
    JSON-RPC no sufre el bug de website_sale_wishlist en _check_credentials."""
    import requests
    sess = requests.Session()
    resp = sess.post(f'{ODOO_URL}/web/session/authenticate', json={
        'jsonrpc': '2.0', 'method': 'call',
        'params': {'db': ODOO_DB, 'login': ODOO_USER, 'password': ODOO_PASS},
        'id': 1
    })
    res = resp.json()
    if 'error' in res:
        raise Exception(f'Error JSON-RPC auth: {res["error"]}')
    uid = res['result']['uid']
    return sess, uid

def json_execute(sess, model, method, args=None, kwargs=None):
    """Ejecuta una llamada a Odoo vía JSON-RPC."""
    import requests
    payload = {
        'jsonrpc': '2.0', 'method': 'call',
        'params': {
            'model': model,
            'method': method,
            'args': args or [],
            'kwargs': kwargs or {},
        },
        'id': 2
    }
    resp = sess.post(f'{ODOO_URL}/web/dataset/call_kw', json=payload)
    res = resp.json()
    if 'error' in res:
        raise Exception(f'JSON-RPC error: {res["error"]}')
    return res['result']

def fetch_data():
    """Trae todas las facturas desde Odoo con los status indicados.
    Usa JSON-RPC en lugar de XML-RPC para evitar el bug de website_sale_wishlist."""
    sess, uid = json_connect()
    
    domain = [
        ['x_status_operativos', 'in', TARGET_STATUSES],
        ['move_type', '=', 'out_invoice'],
    ]
    
    fields = [
        'id', 'name', 'partner_id', 'amount_total', 'amount_residual',
        'invoice_date', 'x_status_operativos', 'x_work_profesional',
    ]
    
    ids = json_execute(sess, 'account.move', 'search', [domain])
    
    # Leer en lotes para evitar timeouts
    batch_size = 500
    all_recs = []
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i+batch_size]
        recs = json_execute(sess, 'account.move', 'read', [batch, fields])
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
            'trabajador': str(r.get('x_work_profesional') or ''),
            'status': status_key,
        })
    
    # Clasificar trabajador: muestra el valor real de Odoo, solo corrige typo
    def normalize_worker(tipo):
        tl = tipo.lower().strip() if tipo else ''
        if not tl or tl == 'vacio' or tl == 'false':
            return 'Sin clasificar'
        if tl == 'infdependiente_informal':
            return 'independiente_informal'
        return tl
    
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
             if c_data['worker_types'] else 'Sin clasificar'
        worker = normalize_worker(wt)
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
        last200_seg[normalize_worker(r['trabajador'])] += 1
    
    # VIP (5+ contratos)
    vip_clients = [c for c in client_list if c['contratos'] >= 5]
    vip_clients.sort(key=lambda x: -x['contratos'])
    
    # Total rows (all statuses, not just our selection)
    all_ids = json_execute(sess, 'account.move', 'search_count', [[['move_type', '=', 'out_invoice']]])
    
    # Preparar facturas individuales para filtro por fecha
    invoices = []
    for r in rows:
        invoices.append({
            'fecha': r['fecha'],
            'total': r['total'],
            'pagado': r['pagado'],
            'cliente': r['cliente'],
            'status': r['status'],
            'trabajador': r['trabajador'],
        })
    
    # Plan de pagos
    payment_plan = fetch_payment_plan(sess)
    
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
        'invoices': invoices,  # Para filtro por fecha preciso
        'segment_stats': {s: dict(v)
                          for s, v in sorted(seg_stats.items(),
                                             key=lambda x: -x[1]['contratos'])},
        'last200': dict(last200_seg.most_common()),
        'vip': [{'cliente': c['cliente'], 'cont': c['contratos'],
                 'first': c['first_date'], 'last': c['last_date']}
                for c in vip_clients],
        'payment_plan': payment_plan,
    }

# ── Plan de Pagos (Cuotas Fraccionadas) ──────────────────────────
def fetch_payment_plan(sess):
    """Obtiene resumen del plan de pagos fraccionado desde invoice.installment.line.
    Usa JSON-RPC para evitar bug de website_sale_wishlist."""
    from collections import defaultdict
    
    ids = json_execute(sess, 'invoice.installment.line', 'search', [[]])
    batch_size = 2000
    all_lines = []
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i+batch_size]
        recs = json_execute(sess, 'invoice.installment.line', 'read',
                            [batch, ['state', 'amount', 'payment_date', 'invoice_id']])
        all_lines.extend(recs)
    
    # Agrupar por estado
    state_totals = defaultdict(lambda: {'monto': 0.0, 'cantidad': 0})
    vencidos = []       # lista de (invoice_id, monto, fecha, factura_name)
    debidos = []        # lista de (invoice_id, monto, fecha, factura_name)
    proyeccion = defaultdict(float)  # payment_date -> monto (solo no pagados)
    
    # Análisis por ciclo (día del mes)
    from datetime import datetime, date
    hoy = date.today()
    # ciclo_data[dia][state] = {'cantidad': N, 'monto': X, 'dias_mora': [lista]}
    ciclo_data = defaultdict(lambda: defaultdict(lambda: {'cantidad': 0, 'monto': 0.0, 'dias_mora': []}))
    # ciclo_clientes[dia][partner_name][state] = {'cantidad': N, 'monto': X}
    ciclo_clientes = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {'cantidad': 0, 'monto': 0.0})))
    
    for line in all_lines:
        st = line.get('state', '')
        amt = float(line.get('amount') or 0)
        state_totals[st]['monto'] += amt
        state_totals[st]['cantidad'] += 1
        
        inv = line.get('invoice_id')
        inv_id = inv[0] if isinstance(inv, list) and len(inv) > 1 else None
        inv_name = inv[1] if isinstance(inv, list) and len(inv) > 1 else ''
        fecha = str(line.get('payment_date') or '')
        
        # Extraer día del mes para ciclo
        dia = 0
        try:
            if fecha and '-' in fecha:
                dia = int(fecha.split('-')[2])
        except (ValueError, IndexError):
            dia = 0
        
        if dia > 0:
            c = ciclo_data[dia][st]
            c['cantidad'] += 1
            c['monto'] += amt
            if st == 'vencido':
                try:
                    fd = datetime.strptime(fecha, '%Y-%m-%d').date()
                    d_mora = (hoy - fd).days
                    if d_mora > 0:
                        c['dias_mora'].append(d_mora)
                except (ValueError, TypeError):
                    pass
            # Guardar cliente a nivel de día (se asignará partner después)
            temp_cliente_key = inv_id  # lo vinculamos después con partner_map
            cc = ciclo_clientes[dia][temp_cliente_key][st]
            cc['cantidad'] += 1
            cc['monto'] += amt
        
        if st == 'vencido':
            vencidos.append({'invoice_id': inv_id, 'invoice_name': inv_name,
                             'monto': amt, 'fecha': fecha})
        elif st == 'draft':
            debidos.append({'invoice_id': inv_id, 'invoice_name': inv_name,
                            'monto': amt, 'fecha': fecha})
            if fecha:
                proyeccion[fecha] += amt
    
    # Obtener nombres de clientes desde las facturas involucradas
    inv_ids = set()
    for v in vencidos:
        if v['invoice_id']: inv_ids.add(v['invoice_id'])
    for d in debidos:
        if d['invoice_id']: inv_ids.add(d['invoice_id'])
    
    partner_map = {}
    if inv_ids:
        inv_list = list(inv_ids)
        inv_batches = [inv_list[i:i+500] for i in range(0, len(inv_list), 500)]
        for batch in inv_batches:
            inv_data = json_execute(sess, 'account.move', 'read', [batch, ['partner_id', 'name']])
            for inv in inv_data:
                pid = inv.get('partner_id')
                partner = pid[1] if isinstance(pid, list) and len(pid) > 1 else 'Desconocido'
                partner_map[inv['id']] = partner
    
    # Resolver inv_id -> partner name en ciclo_clientes (agregar por partner)
    ciclo_clientes_por_partner = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {'cantidad': 0, 'monto': 0.0})))
    for dia, invs in ciclo_clientes.items():
        for inv_id, states in invs.items():
            partner = partner_map.get(inv_id, 'Desconocido')
            for st, vals in states.items():
                cc = ciclo_clientes_por_partner[dia][partner][st]
                cc['cantidad'] += vals['cantidad']
                cc['monto'] += vals['monto']
    ciclo_clientes = ciclo_clientes_por_partner
    
    # Combinar datos de clientes en vencidos
    clientes_vencidos = defaultdict(lambda: {'monto': 0.0, 'cuotas': 0, 'facturas': []})
    for v in vencidos:
        cliente = partner_map.get(v['invoice_id'], 'Desconocido')
        clientes_vencidos[cliente]['monto'] += v['monto']
        clientes_vencidos[cliente]['cuotas'] += 1
        if v['invoice_name'] not in clientes_vencidos[cliente]['facturas']:
            clientes_vencidos[cliente]['facturas'].append(v['invoice_name'])
    
    clientes_vencidos_list = [{'cliente': k, 'monto': round(v['monto'], 2),
                                'cuotas': v['cuotas'], 'facturas': v['facturas']}
                              for k, v in sorted(clientes_vencidos.items(),
                                                 key=lambda x: -x[1]['monto'])]
    
    # Proyección ordenada
    proyeccion_list = [{'fecha': k, 'monto': round(v, 2)}
                       for k, v in sorted(proyeccion.items())]
    
    # Construir ciclo_analysis para rangos 03-18 y 10-25
    def build_ciclo_range(dia_min, dia_max):
        """Construye datos para un rango de días de ciclo, incluyendo clientes."""
        result = {}
        for d in range(dia_min, dia_max + 1):
            entry = {}
            for st in ['draft', 'vencido', 'paid']:
                c = ciclo_data.get(d, {}).get(st, {'cantidad': 0, 'monto': 0.0, 'dias_mora': []})
                dm = c.get('dias_mora', [])
                entry[st] = {
                    'cantidad': c['cantidad'],
                    'monto': round(c['monto'], 2),
                    'dias_mora_prom': round(sum(dm)/len(dm), 1) if dm else 0,
                    'max_dias_mora': max(dm) if dm else 0
                }
            # Clientes del día (top 30 por monto draft+vencido)
            clients_by_day = ciclo_clientes.get(d, {})
            clients_list = []
            for partner, states in clients_by_day.items():
                draft_monto = states.get('draft', {}).get('monto', 0)
                venc_monto = states.get('vencido', {}).get('monto', 0)
                total = draft_monto + venc_monto
                if total > 0:
                    clients_list.append({
                        'cliente': partner,
                        'monto_draft': round(draft_monto, 2),
                        'cant_draft': states.get('draft', {}).get('cantidad', 0),
                        'monto_vencido': round(venc_monto, 2),
                        'cant_vencido': states.get('vencido', {}).get('cantidad', 0),
                        'monto_pagado': round(states.get('paid', {}).get('monto', 0), 2),
                        'cant_pagado': states.get('paid', {}).get('cantidad', 0),
                    })
            clients_list.sort(key=lambda x: -(x['monto_draft'] + x['monto_vencido']))
            entry['clientes'] = clients_list[:30]
            result[str(d)] = entry
        return result
    
    ciclo_analysis = {
        '03-18': build_ciclo_range(3, 18),
        '10-25': build_ciclo_range(10, 25),
    }
    
    return {
        'state_totals': {k: {'monto': round(v['monto'], 2), 'cantidad': v['cantidad']}
                         for k, v in sorted(state_totals.items())},
        'clientes_vencidos': clientes_vencidos_list[:100],  # Top 100
        'proyeccion': proyeccion_list,
        'total_vencido': round(state_totals['vencido']['monto'], 2),
        'total_debido': round(state_totals['draft']['monto'], 2),
        'total_pagado': round(state_totals['paid']['monto'], 2),
        'total_cuotas': len(all_lines),
        'ciclo_analysis': ciclo_analysis,
    }
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
    
    # Primero desescapar {{ y }} del template (f-string literal braces)
    tpl_unescaped = tpl.replace('{{', '{').replace('}}', '}')
    # Luego insertar el JSON (para que no se dañe si contiene }} o {{)
    html = tpl_unescaped.replace('{json_escaped}', json_escaped)
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
    # Modo --generate: solo genera index.html y sale (para GitHub Actions)
    if '--generate' in sys.argv:
        OUTPUT = os.path.join(os.path.dirname(__file__), 'index.html')
        print(f'Generando {OUTPUT}...')
        try:
            data = fetch_data()
            html = build_html(data)
            with open(OUTPUT, 'w', encoding='utf-8') as f:
                f.write(html)
            print(f'OK - {data["client_count"]} clientes, '
                  f'{sum(data["status_counts"].values())} facturas emitidas')
            print(f'Escrito: {OUTPUT} ({os.path.getsize(OUTPUT)} bytes)')
        except Exception as e:
            print(f'ERROR: {e}')
            sys.exit(1)
        sys.exit(0)
    
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