#!/usr/bin/env python3
# This is a simple web server that only accepts a POST request with a Python file
# and updates kata.py on the server side. This is meant for development purposes only.
# It is not secure and should not be used in production.

from os import chmod, chdir, environ
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from logging import basicConfig, INFO, info
    
basicConfig(level=INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class MyRequestHandler(SimpleHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        info(f"Received POST request with {content_length} bytes of data.")

        with open('kata.py', 'wb') as f:
            f.write(post_data)
            info("Updated kata.py with new content.")
        chmod('kata.py', 0o755)
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write("OK".encode('utf-8'))

if __name__ == "__main__":
    chdir(environ.get("HOME"))
    with TCPServer(("", 8000), MyRequestHandler) as httpd:
        info(f"Serving on port {httpd.server_address[1]}")
        httpd.serve_forever()

