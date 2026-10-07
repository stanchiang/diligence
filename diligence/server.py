import argparse
import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from .agent import Pipeline, markdown
from .lm import LMStudio
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECK = ROOT / 'CHARGEBOTIC_Deck - V3 .pdf'


class App:
    def __init__(self, data, lm=None):
        self.store = Store(data)
        self.lm = lm or LMStudio()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.active_run = None
        self.importing = False
        self.setup_error = None

    def import_default(self):
        if not DEFAULT_DECK.exists():
            return
        self.importing = True
        with self.lock:
            try:
                self.store.ingest(DEFAULT_DECK.name, DEFAULT_DECK.read_bytes())
            except Exception as exc:
                self.setup_error = str(exc)
            finally:
                self.importing = False

    def model_status(self):
        try:
            models = self.lm.models()
            return {'connected': True, 'models': models, 'url': self.lm.base_url, 'error': None}
        except Exception as exc:
            return {'connected': False, 'models': [], 'url': self.lm.base_url, 'error': str(exc)}

    def start(self, did=None, model=None, resume=None):
        if not self.lock.acquire(blocking=False):
            raise ValueError('The local worker is busy. Wait for import or stop the active analysis.')
        try:
            if resume:
                run = self.store.run(resume)
                if run['status'] not in {'failed', 'interrupted', 'cancelled'}:
                    raise ValueError('Only stopped analyses can be resumed.')
                model = run['payload']['model']
            elif not isinstance(model, str) or 'qwen' not in model.lower() or len(model) > 200:
                raise ValueError('Choose your local Qwen vision model.')
            if model not in self.lm.models():
                raise ValueError('Selected Qwen model is not available in LM Studio.')
            if not resume:
                run = self.store.create_run(did, model)
            self.cancel.clear()
            self.active_run = run['id']
            self.store.save_run(run['id'], 'queued', 'Waiting for local Qwen', run['payload'])
            def work():
                try:
                    Pipeline(self.store, self.lm, self.cancel).run(run['id'])
                finally:
                    self.active_run = None
                    self.lock.release()
            threading.Thread(target=work, daemon=True).start()
            return {'id': run['id']}
        except Exception:
            self.lock.release()
            raise


class Handler(BaseHTTPRequestHandler):
    server_version = 'DiligenceLocal/1.0'

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        # HTTP paths/status only; deck contents and model responses are never logged.
        pass

    def send(self, data, status=200, content_type='application/json; charset=utf-8', filename=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def trusted_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.trusted_host():
            return self.send({'error': 'Use the loopback application address.'}, 403)
        try:
            parts = urlparse(self.path).path.strip('/').split('/')
            if parts == ['api', 'status']:
                return self.send({'token': self.app.token, 'lm': self.app.model_status(),
                                  'active_run': self.app.active_run, 'importing': self.app.importing,
                                  'setup_error': self.app.setup_error, 'default_available': DEFAULT_DECK.exists()})
            if parts == ['api', 'decks']:
                return self.send(self.app.store.decks())
            if len(parts) == 3 and parts[:2] == ['api', 'decks']:
                deck = self.app.store.deck(parts[2])
                return self.send({'deck': deck, 'runs': self.app.store.runs(parts[2])})
            if len(parts) == 5 and parts[:2] == ['api', 'decks'] and parts[3] == 'slides':
                return self.send(self.app.store.slide(parts[2], int(parts[4])))
            if len(parts) == 5 and parts[:2] == ['api', 'decks'] and parts[3] == 'images':
                return self.send(self.app.store.image_path(parts[2], int(parts[4])).read_bytes(), content_type='image/jpeg')
            if len(parts) == 3 and parts[:2] == ['api', 'runs']:
                return self.send(self.app.store.run(parts[2]))
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] in {'markdown', 'json'}:
                run = self.app.store.run(parts[2])
                if parts[3] == 'json':
                    return self.send(run, filename='business-explanation.json')
                return self.send(markdown(run), content_type='text/markdown; charset=utf-8', filename='business-explanation.md')
            name = 'index.html' if parts == [''] else '/'.join(parts)
            path = (ROOT / 'web' / name).resolve()
            if path.parent != (ROOT / 'web').resolve() or not path.is_file():
                return self.send({'error': 'Not found.'}, 404)
            return self.send(path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        except (ValueError, FileNotFoundError):
            self.send({'error': 'Not found.'}, 404)
        except Exception as exc:
            self.send({'error': str(exc)[:1000]}, 500)

    def do_POST(self):
        origin = self.headers.get('Origin')
        expected_origin = 'http://' + self.headers.get('Host', '')
        if not self.trusted_host() or (origin and origin != expected_origin):
            return self.send({'error': 'Local application origin required.', 'code': 'invalid_origin'}, 403)
        if not secrets.compare_digest(self.headers.get('X-Diligence-Token', ''), self.app.token):
            return self.send({'error': 'Local application token required.', 'code': 'stale_token'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 25*1024*1024:
                return self.send({'error': 'Request exceeds 25 MB.'}, 413)
            raw = self.rfile.read(size)
            parts = urlparse(self.path).path.strip('/').split('/')
            if parts == ['api', 'decks']:
                if not self.app.lock.acquire(blocking=False):
                    return self.send({'error': 'Worker busy; wait for the current operation.'}, 409)
                try:
                    return self.send(self.app.store.ingest(unquote(self.headers.get('X-Filename', 'pitch-deck.pdf')), raw), 201)
                finally:
                    self.app.lock.release()
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                raise ValueError('Request must be a JSON object.')
            if parts == ['api', 'runs']:
                return self.send(self.app.start(body.get('deck_id'), body.get('model')), 202)
            if len(parts) == 4 and parts[:2] == ['api', 'runs']:
                if parts[3] == 'resume':
                    return self.send(self.app.start(resume=parts[2]), 202)
                if parts[3] == 'cancel':
                    if self.app.active_run != parts[2]:
                        raise ValueError('This analysis is not running.')
                    self.app.cancel.set()
                    return self.send({'stopping': True, 'message': 'Stopping after the current local model request finishes.'})
            return self.send({'error': 'Not found.'}, 404)
        except (ValueError, TypeError) as exc:
            self.send({'error': str(exc)[:1000]}, 400)
        except Exception as exc:
            self.send({'error': str(exc)[:1000]}, 500)


def main():
    parser = argparse.ArgumentParser(description='Local pitch-deck understanding with Qwen and LM Studio')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--data', default=str(ROOT / 'data'))
    parser.add_argument('--no-default', action='store_true', help='Do not import the default Chargebotic deck')
    args = parser.parse_args()
    app = App(args.data)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.app = app
    app.store.recover_interrupted()
    if not args.no_default:
        threading.Thread(target=app.import_default, daemon=True).start()
    print(f'Diligence is running locally at http://127.0.0.1:{args.port}', flush=True)
    print('Decks, slide images, and interpretations remain under the local data directory.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        app.cancel.set()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
