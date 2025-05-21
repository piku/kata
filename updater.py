# This is a simple web server that only accepts a POST request with a Python file
# and updates kata.py, making it executable.

import os
import sys
import http.server
import socketserver
import urllib.parse
import logging
import shutil
import json
    
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class MyRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        logging.info(f"Received POST request with {content_length} bytes of data.")

        with open('kata.py', 'wb') as f:
            f.write(post_data)
            logging.info("Updated kata.py with new content.")
        os.chmod('kata.py', 0o755)
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write("OK".encode('utf-8'))

if __name__ == "__main__":
    PORT = 8000
    os.chdir(os.environ.get("HOME"))
    with socketserver.TCPServer(("", PORT), MyRequestHandler) as httpd:
        logging.info(f"Serving on port {PORT}")
        httpd.serve_forever()

