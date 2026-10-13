import sqlite3
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import json
from datetime import datetime, timedelta
import csv
import io
import os

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)
DB_PATH = 'mes.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT,
        role TEXT CHECK(role IN ('planner', 'operator', 'qe'))
    );
    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY,
        name TEXT
    );
    CREATE TABLE IF NOT EXISTS areas (
        id INTEGER PRIMARY KEY,
        site_id INTEGER,
        name TEXT,
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );
    CREATE TABLE IF NOT EXISTS lines (
        id INTEGER PRIMARY KEY,
        area_id INTEGER,
        name TEXT,
        FOREIGN KEY(area_id) REFERENCES areas(id)
    );
    CREATE TABLE IF NOT EXISTS cells (
        id INTEGER PRIMARY KEY,
        line_id INTEGER,
        name TEXT,
        FOREIGN KEY(line_id) REFERENCES lines(id)
    );
    CREATE TABLE IF NOT EXISTS machines (
        id INTEGER PRIMARY KEY,
        cell_id INTEGER,
        name TEXT,
        ideal_rate REAL,  -- units per hour
        FOREIGN KEY(cell_id) REFERENCES cells(id)
    );
    CREATE TABLE IF NOT EXISTS downtime_reasons (
        id INTEGER PRIMARY KEY,
        code TEXT UNIQUE,
        description TEXT,
        category TEXT CHECK(category IN ('availability', 'performance', 'quality'))
    );
    CREATE TABLE IF NOT EXISTS material_lots (
        id INTEGER PRIMARY KEY,
        material_code TEXT,
        lot_number TEXT UNIQUE,
        quantity REAL,
        received_at TEXT
    );
    CREATE TABLE IF NOT EXISTS production_orders (
        id INTEGER PRIMARY KEY,
        order_number TEXT UNIQUE,
        product_code TEXT,
        quantity_ordered INTEGER,
        quantity_good INTEGER DEFAULT 0,
        quantity_scrap INTEGER DEFAULT 0,
        status TEXT CHECK(status IN ('released', 'running', 'held', 'complete')) DEFAULT 'released',
        released_at TEXT,
        target_rate REAL,
        recipe TEXT,
        line_id INTEGER,
        current_machine_id INTEGER,
        FOREIGN KEY(line_id) REFERENCES lines(id),
        FOREIGN KEY(current_machine_id) REFERENCES machines(id)
    );
    CREATE TABLE IF NOT EXISTS order_events (
        id INTEGER PRIMARY KEY,
        order_id INTEGER,
        event_type TEXT,
        timestamp TEXT,
        operator_id INTEGER,
        notes TEXT,
        FOREIGN KEY(order_id) REFERENCES production_orders(id)
    );
    CREATE TABLE IF NOT EXISTS downtime_events (
        id INTEGER PRIMARY KEY,
        machine_id INTEGER,
        order_id INTEGER,
        reason_id INTEGER,
        start_time TEXT,
        end_time TEXT,
        operator_id INTEGER,
        FOREIGN KEY(machine_id) REFERENCES machines(id),
        FOREIGN KEY(order_id) REFERENCES production_orders(id),
        FOREIGN KEY(reason_id) REFERENCES downtime_reasons(id)
    );
    CREATE TABLE IF NOT EXISTS genealogy (
        id INTEGER PRIMARY KEY,
        serial_or_batch TEXT,
        order_id INTEGER,
        machine_id INTEGER,
        material_lot_id INTEGER,
        quantity_used REAL,
        operator_id INTEGER,
        timestamp TEXT,
        FOREIGN KEY(order_id) REFERENCES production_orders(id),
        FOREIGN KEY(machine_id) REFERENCES machines(id),
        FOREIGN KEY(material_lot_id) REFERENCES material_lots(id)
    );
    CREATE TABLE IF NOT EXISTS quality_checks (
        id INTEGER PRIMARY KEY,
        serial_or_batch TEXT,
        order_id INTEGER,
        check_type TEXT,
        result TEXT CHECK(result IN ('pass', 'fail')),
        waived_by INTEGER,
        waived_at TEXT,
        notes TEXT,
        timestamp TEXT,
        FOREIGN KEY(order_id) REFERENCES production_orders(id)
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY,
        table_name TEXT,
        record_id INTEGER,
        action TEXT,
        user_id INTEGER,
        timestamp TEXT,
        details TEXT
    );
    ''')
    conn.commit()
    conn.close()

def seed_db():
    conn = get_db()
    c = conn.cursor()
    # Check if already seeded
    if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0:
        conn.close()
        return
    
    # Users
    c.executemany('INSERT INTO users (name, role) VALUES (?, ?)', [
        ('Alice Planner', 'planner'),
        ('Bob Operator', 'operator'),
        ('Carol QE', 'qe')
    ])
    
    # ISA-95
    c.execute("INSERT INTO sites (name) VALUES ('Plant Alpha')")
    site_id = c.lastrowid
    c.execute("INSERT INTO areas (site_id, name) VALUES (?, 'Molding Area')", (site_id,))
    area_id = c.lastrowid
    c.execute("INSERT INTO lines (area_id, name) VALUES (?, 'Injection Line 1')", (area_id,))
    line_id = c.lastrowid
    c.execute("INSERT INTO cells (line_id, name) VALUES (?, 'Press Cell')", (line_id,))
    cell_id = c.lastrowid
    machines = [
        ('Press 1', 120),
        ('Press 2', 110),
        ('Press 3', 100)
    ]
    for name, rate in machines:
        c.execute("INSERT INTO machines (cell_id, name, ideal_rate) VALUES (?, ?, ?)", (cell_id, name, rate))
    
    # Downtime reasons
    reasons = [
        ('JAM', 'Material jam', 'availability'),
        ('NOMAT', 'No material', 'availability'),
        ('QUAL', 'Quality hold', 'quality'),
        ('CHANGE', 'Changeover', 'availability'),
        ('MAINT', 'Maintenance', 'availability'),
        ('SPEED', 'Speed loss', 'performance')
    ]
    for code, desc, cat in reasons:
        c.execute("INSERT INTO downtime_reasons (code, description, category) VALUES (?, ?, ?)", (code, desc, cat))
    
    # Material lots
    lots = [
        ('RESIN', 'R-19', 10000, (datetime.now() - timedelta(days=10)).isoformat()),
        ('RESIN', 'R-20', 8000, (datetime.now() - timedelta(days=5)).isoformat()),
        ('COLOR', 'C-05', 500, (datetime.now() - timedelta(days=3)).isoformat())
    ]
    for mat, lot, qty, rec in lots:
        c.execute("INSERT INTO material_lots (material_code, lot_number, quantity, received_at) VALUES (?, ?, ?, ?)", (mat, lot, qty, rec))
    
    # Seed historical orders for OEE (past week)
    now = datetime.now()
    for i in range(7):
        day = now - timedelta(days=7-i)
        order_num = f"HIST-{10000 + i}"
        c.execute('''INSERT INTO production_orders 
            (order_number, product_code, quantity_ordered, quantity_good, quantity_scrap, status, released_at, target_rate, recipe, line_id, current_machine_id)
            VALUES (?, 'PART-A', 2000, 1850, 50, 'complete', ?, 100, 'Standard recipe', ?, 3)''',
            (order_num, day.isoformat(), line_id))
        oid = c.lastrowid
        # Some events
        start = day.replace(hour=6)
        end = day.replace(hour=14)
        c.execute("INSERT INTO order_events (order_id, event_type, timestamp, operator_id) VALUES (?, 'start', ?, 2)", (oid, start.isoformat()))
        c.execute("INSERT INTO order_events (order_id, event_type, timestamp, operator_id) VALUES (?, 'complete', ?, 2)", (oid, end.isoformat()))
        # Some downtime
        if i % 2 == 0:
            c.execute("INSERT INTO downtime_events (machine_id, order_id, reason_id, start_time, end_time, operator_id) VALUES (3, ?, 1, ?, ?, 2)",
                      (oid, (start + timedelta(hours=2)).isoformat(), (start + timedelta(hours=2, minutes=15)).isoformat()))
        # Genealogy example
        c.execute("INSERT INTO genealogy (serial_or_batch, order_id, machine_id, material_lot_id, quantity_used, operator_id, timestamp) VALUES (?, ?, 3, 1, 50, 2, ?)",
                  (f"BATCH-{order_num}", oid, start.isoformat()))
    
    # Current order for demo
    c.execute('''INSERT INTO production_orders 
        (order_number, product_code, quantity_ordered, status, released_at, target_rate, recipe, line_id)
        VALUES ('10482', 'PART-B', 5000, 'released', ?, 100, 'High gloss resin', ?)''',
        (now.isoformat(), line_id))
    
    conn.commit()
    conn.close()

def log_audit(table, record_id, action, user_id, details):
    conn = get_db()
    conn.execute("INSERT INTO audit_log (table_name, record_id, action, user_id, timestamp, details) VALUES (?, ?, ?, ?, ?, ?)",
                 (table, record_id, action, user_id, datetime.now().isoformat(), json.dumps(details)))
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/operator')
def operator():
    return send_from_directory('static', 'operator.html')

@app.route('/planner')
def planner():
    return send_from_directory('static', 'planner.html')

# API endpoints
@app.route('/api/orders', methods=['GET'])
def get_orders():
    conn = get_db()
    orders = [dict(row) for row in conn.execute('SELECT * FROM production_orders ORDER BY id DESC').fetchall()]
    conn.close()
    return jsonify(orders)

@app.route('/api/orders', methods=['POST'])
def create_order():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO production_orders (order_number, product_code, quantity_ordered, target_rate, recipe, line_id, status, released_at)
                 VALUES (?, ?, ?, ?, ?, ?, 'released', ?)''',
              (data['order_number'], data['product_code'], data['quantity_ordered'], data.get('target_rate', 100), data.get('recipe', 'Standard'), data.get('line_id', 1), datetime.now().isoformat()))
    oid = c.lastrowid
    log_audit('production_orders', oid, 'create', data.get('user_id', 1), data)
    conn.commit()
    conn.close()
    return jsonify({'id': oid, 'status': 'released'})

@app.route('/api/orders/<int:oid>/action', methods=['POST'])
def order_action(oid):
    data = request.json
    action = data['action']  # start, pause, resume, complete, scrap
    operator_id = data.get('operator_id', 2)
    conn = get_db()
    c = conn.cursor()
    order = dict(c.execute('SELECT * FROM production_orders WHERE id=?', (oid,)).fetchone() or {})
    if not order:
        return jsonify({'error': 'Order not found'}), 404
    
    now = datetime.now().isoformat()
    if action == 'start' and order['status'] == 'released':
        c.execute("UPDATE production_orders SET status='running', current_machine_id=? WHERE id=?", (data.get('machine_id', 3), oid))
        c.execute("INSERT INTO order_events (order_id, event_type, timestamp, operator_id) VALUES (?, 'start', ?, ?)", (oid, now, operator_id))
    elif action == 'pause' and order['status'] == 'running':
        c.execute("UPDATE production_orders SET status='held' WHERE id=?", (oid,))
        c.execute("INSERT INTO order_events (order_id, event_type, timestamp, operator_id, notes) VALUES (?, 'pause', ?, ?, ?)", (oid, now, operator_id, data.get('reason', '')))
        # Start downtime
        if data.get('reason_id'):
            c.execute("INSERT INTO downtime_events (machine_id, order_id, reason_id, start_time, operator_id) VALUES (?, ?, ?, ?, ?)",
                      (order.get('current_machine_id') or 3, oid, data['reason_id'], now, operator_id))
    elif action == 'resume' and order['status'] == 'held':
        c.execute("UPDATE production_orders SET status='running' WHERE id=?", (oid,))
        c.execute("INSERT INTO order_events (order_id, event_type, timestamp, operator_id) VALUES (?, 'resume', ?, ?)", (oid, now, operator_id))
        # End open downtime
        c.execute("UPDATE downtime_events SET end_time=? WHERE order_id=? AND end_time IS NULL", (now, oid))
    elif action == 'complete' and order['status'] in ('running', 'held'):
        good = data.get('quantity_good', order['quantity_good'])
        scrap = data.get('quantity_scrap', order['quantity_scrap'])
        c.execute("UPDATE production_orders SET status='complete', quantity_good=?, quantity_scrap=? WHERE id=?", (good, scrap, oid))
        c.execute("INSERT INTO order_events (order_id, event_type, timestamp, operator_id) VALUES (?, 'complete', ?, ?)", (oid, now, operator_id))
    elif action == 'scrap':
        qty = data.get('quantity', 1)
        reason = data.get('reason', 'SCRAP')
        c.execute("UPDATE production_orders SET quantity_scrap = quantity_scrap + ? WHERE id=?", (qty, oid))
        c.execute("INSERT INTO order_events (order_id, event_type, timestamp, operator_id, notes) VALUES (?, 'scrap', ?, ?, ?)", (oid, now, operator_id, f"Scrapped {qty}: {reason}"))
    else:
        conn.close()
        return jsonify({'error': 'Invalid action for current state'}), 400
    
    log_audit('production_orders', oid, action, operator_id, data)
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/downtime', methods=['POST'])
def record_downtime():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO downtime_events (machine_id, order_id, reason_id, start_time, end_time, operator_id) VALUES (?, ?, ?, ?, ?, ?)",
              (data['machine_id'], data.get('order_id'), data['reason_id'], data['start_time'], data.get('end_time'), data.get('operator_id', 2)))
    did = c.lastrowid
    log_audit('downtime_events', did, 'create', data.get('operator_id', 2), data)
    conn.commit()
    conn.close()
    return jsonify({'id': did})

@app.route('/api/genealogy', methods=['POST'])
def record_genealogy():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO genealogy (serial_or_batch, order_id, machine_id, material_lot_id, quantity_used, operator_id, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (data['serial_or_batch'], data['order_id'], data['machine_id'], data['material_lot_id'], data['quantity_used'], data.get('operator_id', 2), datetime.now().isoformat()))
    gid = c.lastrowid
    log_audit('genealogy', gid, 'create', data.get('operator_id', 2), data)
    conn.commit()
    conn.close()
    return jsonify({'id': gid})

@app.route('/api/quality', methods=['POST'])
def record_quality():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO quality_checks (serial_or_batch, order_id, check_type, result, notes, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
              (data['serial_or_batch'], data['order_id'], data['check_type'], data['result'], data.get('notes', ''), datetime.now().isoformat()))
    qid = c.lastrowid
    if data.get('waived_by'):
        c.execute("UPDATE quality_checks SET waived_by=?, waived_at=? WHERE id=?", (data['waived_by'], datetime.now().isoformat(), qid))
    log_audit('quality_checks', qid, 'create', data.get('operator_id', 2), data)
    conn.commit()
    conn.close()
    return jsonify({'id': qid})

@app.route('/api/recall/<serial>', methods=['GET'])
def recall(serial):
    conn = get_db()
    # Find genealogy, order, machine, materials, quality
    gen = [dict(row) for row in conn.execute('''
        SELECT g.*, m.name as machine_name, ml.lot_number, ml.material_code, po.order_number, po.product_code,
               u.name as operator_name
        FROM genealogy g
        LEFT JOIN machines m ON g.machine_id = m.id
        LEFT JOIN material_lots ml ON g.material_lot_id = ml.id
        LEFT JOIN production_orders po ON g.order_id = po.id
        LEFT JOIN users u ON g.operator_id = u.id
        WHERE g.serial_or_batch = ?
    ''', (serial,)).fetchall()]
    
    quality = [dict(row) for row in conn.execute('''
        SELECT q.*, u.name as waived_by_name
        FROM quality_checks q
        LEFT JOIN users u ON q.waived_by = u.id
        WHERE q.serial_or_batch = ?
    ''', (serial,)).fetchall()]
    
    # Related events if order known
    order_ids = list(set([g['order_id'] for g in gen if g['order_id']]))
    events = []
    downtimes = []
    if order_ids:
        placeholders = ','.join('?' * len(order_ids))
        events = [dict(row) for row in conn.execute(f'SELECT * FROM order_events WHERE order_id IN ({placeholders})', order_ids).fetchall()]
        downtimes = [dict(row) for row in conn.execute(f'''
            SELECT d.*, r.code, r.description FROM downtime_events d
            LEFT JOIN downtime_reasons r ON d.reason_id = r.id
            WHERE d.order_id IN ({placeholders})
        ''', order_ids).fetchall()]
    
    conn.close()
    return jsonify({
        'serial': serial,
        'genealogy': gen,
        'quality_checks': quality,
        'order_events': events,
        'downtimes': downtimes,
        'summary': f"Serial/batch {serial} used materials {', '.join(set(g['lot_number'] for g in gen if g['lot_number']))}, ran on {', '.join(set(g['machine_name'] for g in gen if g['machine_name']))}."
    })

@app.route('/api/oee', methods=['GET'])
def get_oee():
    # Simple OEE for last 7 days or specific order
    period_days = int(request.args.get('days', 7))
    since = (datetime.now() - timedelta(days=period_days)).isoformat()
    conn = get_db()
    # Aggregate counts
    totals = conn.execute('''
        SELECT SUM(quantity_good) as good, SUM(quantity_scrap) as scrap, SUM(quantity_ordered) as ordered
        FROM production_orders WHERE released_at > ? AND status = 'complete'
    ''', (since,)).fetchone()
    good = totals['good'] or 0
    scrap = totals['scrap'] or 0
    total_produced = good + scrap
    
    # Approximate availability from downtime
    # For demo, compute rough
    downtime_min = conn.execute('''
        SELECT SUM((julianday(end_time) - julianday(start_time)) * 1440) as mins
        FROM downtime_events WHERE start_time > ? AND end_time IS NOT NULL
    ''', (since,)).fetchone()['mins'] or 0
    
    # Assume 8h shifts, 7 days, 1 line
    planned_minutes = period_days * 8 * 60
    run_minutes = planned_minutes - downtime_min
    availability = (run_minutes / planned_minutes * 100) if planned_minutes > 0 else 0
    
    # Performance: assume ideal 100/hr, actual based on produced
    ideal_rate = 100
    actual_rate = (total_produced / (run_minutes / 60)) if run_minutes > 0 else 0
    performance = min(100, (actual_rate / ideal_rate * 100)) if ideal_rate > 0 else 0
    
    quality = (good / total_produced * 100) if total_produced > 0 else 0
    oee = (availability / 100) * (performance / 100) * (quality / 100) * 100
    
    # Pareto of loss codes
    pareto = [dict(row) for row in conn.execute('''
        SELECT r.code, r.description, r.category, COUNT(*) as count,
               SUM((julianday(d.end_time) - julianday(d.start_time)) * 1440) as total_mins
        FROM downtime_events d
        JOIN downtime_reasons r ON d.reason_id = r.id
        WHERE d.start_time > ? AND d.end_time IS NOT NULL
        GROUP BY r.id
        ORDER BY total_mins DESC
    ''', (since,)).fetchall()]
    
    conn.close()
    return jsonify({
        'availability': round(availability, 1),
        'performance': round(performance, 1),
        'quality': round(quality, 1),
        'oee': round(oee, 1),
        'good': good,
        'scrap': scrap,
        'pareto': pareto,
        'period_days': period_days
    })

@app.route('/api/reasons', methods=['GET'])
def get_reasons():
    conn = get_db()
    reasons = [dict(row) for row in conn.execute('SELECT * FROM downtime_reasons').fetchall()]
    conn.close()
    return jsonify(reasons)

@app.route('/api/machines', methods=['GET'])
def get_machines():
    conn = get_db()
    machines = [dict(row) for row in conn.execute('SELECT * FROM machines').fetchall()]
    conn.close()
    return jsonify(machines)

@app.route('/api/lots', methods=['GET'])
def get_lots():
    conn = get_db()
    lots = [dict(row) for row in conn.execute('SELECT * FROM material_lots').fetchall()]
    conn.close()
    return jsonify(lots)

@app.route('/api/erp/export', methods=['GET'])
def erp_export():
    # Completions and scrap as CSV
    conn = get_db()
    orders = conn.execute("SELECT order_number, product_code, quantity_ordered, quantity_good, quantity_scrap, status, released_at FROM production_orders WHERE status='complete'").fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['order_number', 'product_code', 'ordered', 'good', 'scrap', 'status', 'released_at'])
    for row in orders:
        writer.writerow(row)
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=erp_completions.csv'})

if __name__ == '__main__':
    init_db()
    seed_db()
    print("MES seeded. Starting server...")
    app.run(host='0.0.0.0', port=5000, debug=False)
