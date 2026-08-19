from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os
ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
print('Servindo em http://localhost:8001')
ThreadingHTTPServer(('0.0.0.0', 8001), SimpleHTTPRequestHandler).serve_forever()
