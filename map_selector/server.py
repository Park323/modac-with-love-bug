"""
Map Selector backend — serves static files and /map endpoint.

Run:
  python server.py

Then open:
  http://localhost:8080
"""

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/map":
            with open("mapinfo.json", encoding="utf-8") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data.encode())
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass  # suppress per-request logs


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = HTTPServer(("localhost", 8080), Handler)
    print("Map Selector → http://localhost:8080")
    server.serve_forever()
