import os
import json
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
import psycopg2
import psycopg2.extras

PORT = int(os.environ.get('PORT', 8082))
DATABASE_URL = os.environ.get('DATABASE_URL', '')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Admin@Cab2025')

ALLOWED_DOMAINS = ['cuemath.com']
EXTERNAL_EMAILS = os.environ.get('EXTERNAL_EMAILS', '').split(',')  # agency emails


def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS cab_routes (
            id SERIAL PRIMARY KEY,
            route_name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS cab_roster (
            id SERIAL PRIMARY KEY,
            employee_name TEXT NOT NULL,
            employee_code TEXT NOT NULL,
            mobile TEXT NOT NULL,
            email TEXT,
            route_id INTEGER REFERENCES cab_routes(id),
            pick_location TEXT NOT NULL,
            pick_time TEXT NOT NULL,
            drop_time TEXT NOT NULL,
            report_time TEXT NOT NULL,
            shift_date TEXT NOT NULL,
            driver_name TEXT,
            driver_mobile TEXT,
            cab_type TEXT,
            car_number TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()


def verify_google_token(token):
    try:
        url = f'https://oauth2.googleapis.com/tokeninfo?id_token={token}'
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
        email = data.get('email', '')
        domain = email.split('@')[-1] if '@' in email else ''
        name = data.get('name', email)
        if domain in ALLOWED_DOMAINS or email in EXTERNAL_EMAILS:
            return {'email': email, 'name': name, 'domain': domain}
    except Exception:
        pass
    return None


def send_email(to_email, to_name, subject, html):
    if not BREVO_API_KEY:
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
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200):
        body = html.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/' or path == '/index.html':
            with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == '/api/config':
            self.send_json({'google_client_id': GOOGLE_CLIENT_ID})

        elif path == '/api/routes':
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT * FROM cab_routes ORDER BY route_name')
            routes = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
            self.send_json(routes)

        elif path == '/api/roster':
            params = urllib.parse.parse_qs(parsed.query)
            date = params.get('date', [None])[0]
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if date:
                cur.execute('''
                    SELECT r.*, cr.route_name FROM cab_roster r
                    LEFT JOIN cab_routes cr ON r.route_id = cr.id
                    WHERE r.shift_date = %s ORDER BY cr.route_name, r.pick_time
                ''', (date,))
            else:
                cur.execute('''
                    SELECT r.*, cr.route_name FROM cab_roster r
                    LEFT JOIN cab_routes cr ON r.route_id = cr.id
                    ORDER BY r.shift_date DESC, cr.route_name, r.pick_time
                ''')
            roster = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
            self.send_json(roster)

        elif path == '/api/my-plan':
            params = urllib.parse.parse_qs(parsed.query)
            email = params.get('email', [None])[0]
            if not email:
                self.send_json([], 200)
                return
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('''
                SELECT r.*, cr.route_name FROM cab_roster r
                LEFT JOIN cab_routes cr ON r.route_id = cr.id
                WHERE LOWER(r.email) = LOWER(%s)
                ORDER BY r.shift_date DESC LIMIT 30
            ''', (email,))
            plan = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
            self.send_json(plan)

        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_body()

        if path == '/api/verify-token':
            user = verify_google_token(body.get('token', ''))
            if user:
                self.send_json({'success': True, 'user': user})
            else:
                self.send_json({'success': False}, 401)

        elif path == '/api/admin/login':
            if body.get('password') == ADMIN_PASSWORD:
                self.send_json({'success': True})
            else:
                self.send_json({'success': False, 'error': 'Wrong password'}, 401)

        elif path == '/api/admin/route':
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO cab_routes (route_name, description) VALUES (%s, %s) RETURNING id',
                (body['route_name'], body.get('description', ''))
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()
            self.send_json({'success': True, 'id': new_id})

        elif path == '/api/admin/route/delete':
            conn = get_db()
            cur = conn.cursor()
            cur.execute('DELETE FROM cab_routes WHERE id = %s', (body['id'],))
            conn.commit()
            cur.close()
            conn.close()
            self.send_json({'success': True})

        elif path == '/api/admin/roster':
            conn = get_db()
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO cab_roster
                (employee_name, employee_code, mobile, email, route_id, pick_location, pick_time, drop_time, report_time, shift_date, driver_name, driver_mobile, cab_type, car_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            ''', (
                body['employee_name'], body['employee_code'], body['mobile'],
                body.get('email', ''), body['route_id'], body['pick_location'],
                body['pick_time'], body['drop_time'], body['report_time'], body['shift_date'],
                body.get('driver_name', ''), body.get('driver_mobile', ''),
                body.get('cab_type', ''), body.get('car_number', '')
            ))
            new_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()
            self.send_json({'success': True, 'id': new_id})

        elif path == '/api/admin/roster/delete':
            conn = get_db()
            cur = conn.cursor()
            cur.execute('DELETE FROM cab_roster WHERE id = %s', (body['id'],))
            conn.commit()
            cur.close()
            conn.close()
            self.send_json({'success': True})

        elif path == '/api/admin/roster/edit':
            conn = get_db()
            cur = conn.cursor()
            cur.execute('''
                UPDATE cab_roster SET
                employee_name=%s, employee_code=%s, mobile=%s, email=%s,
                route_id=%s, pick_location=%s, pick_time=%s, drop_time=%s,
                report_time=%s, shift_date=%s,
                driver_name=%s, driver_mobile=%s, cab_type=%s, car_number=%s
                WHERE id=%s
            ''', (
                body['employee_name'], body['employee_code'], body['mobile'],
                body.get('email', ''), body['route_id'], body['pick_location'],
                body['pick_time'], body['drop_time'], body['report_time'],
                body['shift_date'], body.get('driver_name', ''),
                body.get('driver_mobile', ''), body.get('cab_type', ''),
                body.get('car_number', ''), body['id']
            ))
            conn.commit()
            cur.close()
            conn.close()
            self.send_json({'success': True})

        else:
            self.send_json({'error': 'Not found'}, 404)


if __name__ == '__main__':
    init_db()
    print(f'Server running on port {PORT}')
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
