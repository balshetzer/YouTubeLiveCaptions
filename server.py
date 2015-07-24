import http.server
import datetime

class handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        print(self.headers, end='')
        print(self.rfile.read(int(self.headers.get('Content-Length'))).decode('UTF-8'))
        date = (datetime.datetime.utcnow().isoformat()[:-3] + '\r\n').encode('UTF-8')
        self.send_response(200)
        self.end_headers()
        self.wfile.write(date)
        return

if __name__ == '__main__':
    address = ('', 8080)
    server = http.server.HTTPServer(address, handler)
    server.serve_forever()