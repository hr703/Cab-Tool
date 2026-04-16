import os, json, urllib.parse, urllib.request, hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import psycopg2, psycopg2.extras

PORT = int(os.environ.get('PORT', 8082))
DATABASE_URL = os.environ.get('DATABASE_URL', '')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
MSG91_AUTH_KEY = os.environ.get('MSG91_AUTH_KEY', '')
MSG91_SENDER = os.environ.get('MSG91_SENDER', '')
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', 'hr@cuemath.com,ajay.yadav@cuemath.com').split(',')]
ALLOWED_DOMAIN = 'cuemath.com'


def get_db():
    if not DATABASE_URL:
        raise Exception('DATABASE_URL not set')
    return psycopg2.connect(DATABASE_URL)


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS routes (
            id SERIAL PRIMARY KEY,
            route_name TEXT NOT NULL,
            description TEXT DEFAULT '',
            pick_location TEXT DEFAULT '',
            drop_location TEXT DEFAULT '',
            pick_time TEXT DEFAULT '',
            drop_time TEXT DEFAULT '',
            report_time TEXT DEFAULT '',
            cab_cost NUMERIC DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS vendors (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            email TEXT DEFAULT '',
            mobile TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS night_employees (
            id SERIAL PRIMARY KEY,
            emp_name TEXT NOT NULL,
            emp_code TEXT DEFAULT '',
            email TEXT NOT NULL,
            mobile TEXT DEFAULT '',
            department TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS roster (
            id SERIAL PRIMARY KEY,
            employee_id INTEGER REFERENCES night_employees(id) ON DELETE CASCADE,
            route_id INTEGER REFERENCES routes(id) ON DELETE CASCADE,
            shift_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(employee_id, shift_date)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS cab_assignments (
            id SERIAL PRIMARY KEY,
            roster_id INTEGER REFERENCES roster(id) ON DELETE CASCADE UNIQUE,
            vendor_id INTEGER REFERENCES vendors(id),
            driver_name TEXT DEFAULT '',
            driver_mobile TEXT DEFAULT '',
            vehicle_type TEXT DEFAULT '',
            vehicle_number TEXT DEFAULT '',
            assigned_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS security_assignments (
            id SERIAL PRIMARY KEY,
            roster_id INTEGER REFERENCES roster(id) ON DELETE CASCADE UNIQUE,
            vendor_id INTEGER REFERENCES vendors(id),
            guard_name TEXT DEFAULT '',
            guard_mobile TEXT DEFAULT '',
            assigned_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()


def verify_google_token(token):
    try:
        url = 'https://oauth2.googleapis.com/tokeninfo?id_token=' + token
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
        email = data.get('email', '').lower()
        domain = email.split('@')[-1] if '@' in email else ''
        name = data.get('name', email)
        if domain == ALLOWED_DOMAIN:
            role = 'admin' if email in ADMIN_EMAILS else 'employee'
            return {'email': email, 'name': name, 'role': role}
    except Exception as ex:
        print('Google token error:', ex)
    return None


def send_email(to_email, to_name, subject, html):
    if not BREVO_API_KEY or not to_email:
        return
    payload = json.dumps({
        "sender": {"name": "Cuemath Cab Service", "email": "hr@cuemath.com"},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "htmlContent": html
    }).encode()
    req = urllib.request.Request(
        'https://api.brevo.com/v3/smtp/email',
        data=payload,
        headers={'api-key': BREVO_API_KEY, 'Content-Type': 'application/json'}
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print('Email error:', e)


def send_whatsapp(mobile, message):
    if not MSG91_AUTH_KEY or not mobile:
        return
    mobile = ''.join(filter(str.isdigit, mobile))
    if len(mobile) == 10:
        mobile = '91' + mobile
    payload = json.dumps({
        "sender": MSG91_SENDER,
        "mobiles": mobile,
        "message": message
    }).encode()
    req = urllib.request.Request(
        'https://api.msg91.com/api/sendhttp.php',
        data=payload,
        headers={'authkey': MSG91_AUTH_KEY, 'Content-Type': 'application/json'}
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print('WhatsApp error:', e)


def notify_assignment(roster_id, conn):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('''
        SELECT r.shift_date,
               ne.emp_name, ne.email, ne.mobile,
               ro.route_name, ro.pick_location, ro.drop_location, ro.pick_time, ro.drop_time,
               ca.driver_name, ca.driver_mobile, ca.vehicle_type, ca.vehicle_number,
               sa.guard_name, sa.guard_mobile
        FROM roster r
        JOIN night_employees ne ON r.employee_id = ne.id
        JOIN routes ro ON r.route_id = ro.id
        LEFT JOIN cab_assignments ca ON ca.roster_id = r.id
        LEFT JOIN security_assignments sa ON sa.roster_id = r.id
        WHERE r.id = %s
    ''', (roster_id,))
    e = cur.fetchone()
    cur.close()
    if not e:
        return

    lines = [
        '<b>Cab Booking Confirmed!</b>',
        f'Date: {e["shift_date"]}',
        f'Route: {e["route_name"]}',
        f'Pick Up: {e["pick_location"]} | {e["pick_time"]}',
        f'Drop: {e["drop_location"]} | {e["drop_time"]}',
    ]
    if e['driver_name']:
        lines.append(f'Driver: {e["driver_name"]} | {e["driver_mobile"]}')
        lines.append(f'Vehicle: {e["vehicle_type"]} | {e["vehicle_number"]}')
    if e['guard_name']:
        lines.append(f'Guard: {e["guard_name"]} | {e["guard_mobile"]}')

    html_body = '<br>'.join(lines)
    html = f'<div style="font-family:sans-serif;font-size:15px;line-height:2">{html_body}</div>'
    plain = '\n'.join(l.replace('<b>', '').replace('</b>', '') for l in lines)

    send_email(e['email'], e['emp_name'], 'Cab Booking Confirmed', html)
    send_whatsapp(e['mobile'], plain)
    for admin_email in ADMIN_EMAILS:
        send_email(admin_email, 'Admin', f'Cab assigned: {e["emp_name"]} | {e["shift_date"]}', html)


def get_roster_full(cur, where_clause='', params=()):
    cur.execute(f'''
        SELECT r.id, r.shift_date, r.employee_id, r.route_id,
               ne.emp_name, ne.emp_code, ne.email, ne.mobile, ne.department,
               ro.route_name, ro.pick_location, ro.drop_location, ro.pick_time, ro.drop_time, ro.cab_cost,
               ca.id as cab_id, ca.driver_name, ca.driver_mobile, ca.vehicle_type, ca.vehicle_number, ca.vendor_id as cab_vendor_id,
               sa.id as sec_id, sa.guard_name, sa.guard_mobile, sa.vendor_id as sec_vendor_id
        FROM roster r
        JOIN night_employees ne ON r.employee_id = ne.id
        JOIN routes ro ON r.route_id = ro.id
        LEFT JOIN cab_assignments ca ON ca.roster_id = r.id
        LEFT JOIN security_assignments sa ON sa.roster_id = r.id
        {where_clause}
        ORDER BY r.shift_date, ro.route_name, ne.emp_name
    ''', params)
    rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        for k in d:
            if d[k] is None:
                d[k] = ''
            elif hasattr(d[k], '__float__'):
                d[k] = float(d[k])
        result.append(d)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html if isinstance(html, bytes) else html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path in ('/', '/index.html'):
            with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                self.send_html(f.read())
            return

        if path == '/api/config':
            self.send_json({'google_client_id': GOOGLE_CLIENT_ID})
            return

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        try:
            if path == '/api/admin/routes':
                cur.execute('SELECT * FROM routes ORDER BY route_name')
                self.send_json([dict(r) for r in cur.fetchall()])

            elif path == '/api/admin/employees':
                cur.execute('SELECT * FROM night_employees ORDER BY emp_name')
                self.send_json([dict(r) for r in cur.fetchall()])

            elif path == '/api/admin/vendors':
                cur.execute("SELECT id, name, type, email, mobile FROM vendors ORDER BY type, name")
                self.send_json([dict(r) for r in cur.fetchall()])

            elif path == '/api/admin/roster':
                date_from = params.get('date_from', [None])[0]
                date_to = params.get('date_to', [None])[0]
                if date_from and date_to:
                    rows = get_roster_full(cur, 'WHERE r.shift_date BETWEEN %s AND %s', (date_from, date_to))
                else:
                    rows = get_roster_full(cur)
                self.send_json(rows)

            elif path == '/api/vendor/roster':
                vendor_id = params.get('vendor_id', [None])[0]
                vendor_type = params.get('type', [None])[0]
                date_from = params.get('date_from', [None])[0]
                date_to = params.get('date_to', [None])[0]
                if date_from and date_to:
                    rows = get_roster_full(cur, 'WHERE r.shift_date BETWEEN %s AND %s', (date_from, date_to))
                else:
                    rows = get_roster_full(cur)
                self.send_json(rows)

            elif path == '/api/employee/plan':
                email = params.get('email', [None])[0]
                if email:
                    rows = get_roster_full(cur, 'WHERE LOWER(ne.email) = LOWER(%s)', (email,))
                    self.send_json(rows)
                else:
                    self.send_json([])

            else:
                self.send_json({'error': 'Not found'}, 404)

        finally:
            cur.close()
            conn.close()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_body()
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        try:
            if path == '/api/auth/google':
                user = verify_google_token(body.get('token', ''))
                if user:
                    self.send_json({'success': True, 'user': user})
                else:
                    self.send_json({'success': False, 'error': 'Unauthorized'}, 401)

            elif path == '/api/auth/vendor':
                name = body.get('name', '').strip()
                pw = body.get('password', '')
                ph = hash_pw(pw)
                cur.execute('SELECT id, name, type FROM vendors WHERE LOWER(name)=LOWER(%s) AND password_hash=%s', (name, ph))
                vendor = cur.fetchone()
                if vendor:
                    self.send_json({'success': True, 'vendor': dict(vendor)})
                else:
                    self.send_json({'success': False, 'error': 'Invalid credentials'}, 401)

            elif path == '/api/admin/routes':
                cur.execute('''
                    INSERT INTO routes (route_name, description, pick_location, drop_location, pick_time, drop_time, report_time, cab_cost)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
                ''', (body['route_name'], body.get('description',''), body.get('pick_location',''),
                      body.get('drop_location',''), body.get('pick_time',''), body.get('drop_time',''),
                      body.get('report_time',''), body.get('cab_cost', 0)))
                new_id = cur.fetchone()['id']
                conn.commit()
                self.send_json({'success': True, 'id': new_id})

            elif path == '/api/admin/routes/update':
                cur.execute('''
                    UPDATE routes SET route_name=%s, description=%s, pick_location=%s, drop_location=%s,
                    pick_time=%s, drop_time=%s, report_time=%s, cab_cost=%s WHERE id=%s
                ''', (body['route_name'], body.get('description',''), body.get('pick_location',''),
                      body.get('drop_location',''), body.get('pick_time',''), body.get('drop_time',''),
                      body.get('report_time',''), body.get('cab_cost', 0), body['id']))
                conn.commit()
                self.send_json({'success': True})

            elif path == '/api/admin/routes/delete':
                cur.execute('DELETE FROM routes WHERE id=%s', (body['id'],))
                conn.commit()
                self.send_json({'success': True})

            elif path == '/api/admin/employees':
                cur.execute('''
                    INSERT INTO night_employees (emp_name, emp_code, email, mobile, department)
                    VALUES (%s,%s,%s,%s,%s) RETURNING id
                ''', (body['emp_name'], body.get('emp_code',''), body['email'],
                      body.get('mobile',''), body.get('department','')))
                new_id = cur.fetchone()['id']
                conn.commit()
                self.send_json({'success': True, 'id': new_id})

            elif path == '/api/admin/employees/update':
                cur.execute('''
                    UPDATE night_employees SET emp_name=%s, emp_code=%s, email=%s, mobile=%s, department=%s
                    WHERE id=%s
                ''', (body['emp_name'], body.get('emp_code',''), body['email'],
                      body.get('mobile',''), body.get('department',''), body['id']))
                conn.commit()
                self.send_json({'success': True})

            elif path == '/api/admin/employees/delete':
                cur.execute('DELETE FROM night_employees WHERE id=%s', (body['id'],))
                conn.commit()
                self.send_json({'success': True})

            elif path == '/api/admin/vendors':
                ph = hash_pw(body['password'])
                cur.execute('''
                    INSERT INTO vendors (name, type, email, mobile, password_hash)
                    VALUES (%s,%s,%s,%s,%s) RETURNING id
                ''', (body['name'], body['type'], body.get('email',''), body.get('mobile',''), ph))
                new_id = cur.fetchone()['id']
                conn.commit()
                self.send_json({'success': True, 'id': new_id})

            elif path == '/api/admin/vendors/delete':
                cur.execute('DELETE FROM vendors WHERE id=%s', (body['id'],))
                conn.commit()
                self.send_json({'success': True})

            elif path == '/api/admin/roster':
                # Bulk add roster entries for a week
                entries = body.get('entries', [])
                added = 0
                for entry in entries:
                    try:
                        cur.execute('''
                            INSERT INTO roster (employee_id, route_id, shift_date)
                            VALUES (%s,%s,%s) ON CONFLICT (employee_id, shift_date) DO NOTHING
                        ''', (entry['employee_id'], entry['route_id'], entry['shift_date']))
                        added += 1
                    except Exception:
                        pass
                conn.commit()
                self.send_json({'success': True, 'added': added})

            elif path == '/api/admin/roster/delete':
                cur.execute('DELETE FROM roster WHERE id=%s', (body['id'],))
                conn.commit()
                self.send_json({'success': True})

            elif path == '/api/cab/assign':
                vendor_id = body.get('vendor_id')
                roster_id = body.get('roster_id')
                cur.execute('''
                    INSERT INTO cab_assignments (roster_id, vendor_id, driver_name, driver_mobile, vehicle_type, vehicle_number)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (roster_id) DO UPDATE SET
                        vendor_id=%s, driver_name=%s, driver_mobile=%s,
                        vehicle_type=%s, vehicle_number=%s, assigned_at=NOW()
                ''', (
                    roster_id, vendor_id,
                    body.get('driver_name',''), body.get('driver_mobile',''),
                    body.get('vehicle_type',''), body.get('vehicle_number',''),
                    vendor_id,
                    body.get('driver_name',''), body.get('driver_mobile',''),
                    body.get('vehicle_type',''), body.get('vehicle_number','')
                ))
                conn.commit()
                notify_assignment(roster_id, conn)
                self.send_json({'success': True})

            elif path == '/api/security/assign':
                vendor_id = body.get('vendor_id')
                roster_id = body.get('roster_id')
                cur.execute('''
                    INSERT INTO security_assignments (roster_id, vendor_id, guard_name, guard_mobile)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (roster_id) DO UPDATE SET
                        vendor_id=%s, guard_name=%s, guard_mobile=%s, assigned_at=NOW()
                ''', (
                    roster_id, vendor_id,
                    body.get('guard_name',''), body.get('guard_mobile',''),
                    vendor_id,
                    body.get('guard_name',''), body.get('guard_mobile','')
                ))
                conn.commit()
                notify_assignment(roster_id, conn)
                self.send_json({'success': True})

            else:
                self.send_json({'error': 'Not found'}, 404)

        except Exception as err:
            conn.rollback()
            self.send_json({'success': False, 'error': str(err)}, 500)
        finally:
            cur.close()
            conn.close()


if __name__ == '__main__':
    try:
        init_db()
        print('Database initialized')
    except Exception as e:
        print(f'DB init warning: {e}')
    print(f'Server running on port {PORT}')
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
