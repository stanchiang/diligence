"""Persist deck evidence and checkpointed local-model runs."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'diligence.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS decks (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT UNIQUE NOT NULL,
                    slide_count INTEGER NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS slides (
                    deck_id TEXT NOT NULL REFERENCES decks(id), number INTEGER NOT NULL,
                    extracted_text TEXT NOT NULL, image TEXT NOT NULL, PRIMARY KEY(deck_id,number));
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id),
                    status TEXT NOT NULL, stage TEXT NOT NULL, created TEXT NOT NULL,
                    updated TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute("UPDATE analyses SET status='interrupted', stage='Interrupted by restart; resume to continue' WHERE status IN ('queued','running')")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def decks(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM decks ORDER BY created DESC')]

    def deck(self, did):
        with self.connect() as db:
            row = db.execute('SELECT * FROM decks WHERE id=?', (did,)).fetchone()
        if row is None:
            raise ValueError('Deck not found.')
        return dict(row)

    def ingest(self, name, content):
        from pypdf import PdfReader
        import io
        if not isinstance(name, str) or Path(name).suffix.lower() != '.pdf':
            raise ValueError('Choose a PDF pitch deck.')
        if not content or len(content) > 25 * 1024 * 1024:
            raise ValueError('PDF must be 1 byte to 25 MB.')
        digest = hashlib.sha256(content).hexdigest()
        with self.connect() as db:
            existing = db.execute('SELECT * FROM decks WHERE sha256=?', (digest,)).fetchone()
        if existing:
            return dict(existing)
        renderer = shutil.which('pdftoppm')
        if not renderer:
            raise ValueError('Poppler is required. On macOS run: brew install poppler')
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError('Unlock this PDF before importing it.')
            count = len(reader.pages)
            if not 1 <= count <= 150:
                raise ValueError('Pitch decks must contain 1–150 slides.')
            texts = [p.extract_text() or '' for p in reader.pages]
            if any(len(t.encode('utf-8')) > 14000 for t in texts):
                raise ValueError('A slide has more than 14 KB of text; split that slide before importing.')
        except Exception as exc:
            raise ValueError('Cannot read PDF: ' + str(exc)[:200]) from exc
        did = uuid.uuid4().hex
        target = self.root / 'decks' / did
        target.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            tmp = Path(tmp)
            pdf = tmp / 'deck.pdf'
            pdf.write_bytes(content)
            # No shell invocation; filenames cannot inject commands. Images remain local.
            proc = subprocess.run([renderer, '-jpeg', '-jpegopt', 'quality=90', '-scale-to', '1800',
                                   str(pdf), str(tmp / 'slide')], capture_output=True, timeout=300)
            if proc.returncode:
                raise ValueError('Slide renderer failed: ' + proc.stderr.decode('utf-8', 'replace')[:300])
            images = sorted(tmp.glob('slide-*.jpg'), key=lambda p: int(p.stem.split('-')[-1]))
            if len(images) != count:
                raise ValueError('Slide rendering produced an incomplete deck.')
            image_names = []
            for i, p in enumerate(images, 1):
                dest = tmp / f'{i}.jpg'
                p.rename(dest)
                image_names.append(dest.name)
            os.replace(tmp, target)
        deck = dict(id=did, name=Path(name).name[:180], sha256=digest, slide_count=count, created=now())
        try:
            with self.connect() as db:
                db.execute('INSERT INTO decks VALUES (:id,:name,:sha256,:slide_count,:created)', deck)
                db.executemany('INSERT INTO slides VALUES (?,?,?,?)',
                    [(did, i+1, t, image_names[i]) for i, t in enumerate(texts)])
        except Exception:
            shutil.rmtree(target)
            raise
        return deck

    def slide(self, did, number):
        with self.connect() as db:
            row = db.execute('SELECT * FROM slides WHERE deck_id=? AND number=?', (did, number)).fetchone()
        if row is None:
            raise ValueError('Slide not found.')
        return dict(row)

    def image_path(self, did, number):
        slide = self.slide(did, number)
        return self.root / 'decks' / did / slide['image']

    def create_run(self, did, model):
        deck = self.deck(did)
        payload = dict(deck=deck, model=model, prompt_version='1.0', render_max_dimension=1800,
                       inference={'temperature': 0.15, 'reasoning_effort': 'none', 'max_output_tokens': 4500},
                       slides=[], batches=[], report=None, trace=[], error=None)
        rid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO analyses VALUES (?,?,?,?,?,?,?)',
                (rid, did, 'queued', 'Waiting for local Qwen', now(), now(), json.dumps(payload)))
        return self.run(rid)

    def save_run(self, rid, status, stage, payload):
        with self.connect() as db:
            db.execute('UPDATE analyses SET status=?,stage=?,updated=?,payload=? WHERE id=?',
                       (status, stage, now(), json.dumps(payload), rid))

    def run(self, rid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM analyses WHERE id=?', (rid,)).fetchone()
        if row is None:
            raise ValueError('Analysis not found.')
        r = dict(row)
        r['payload'] = json.loads(r['payload'])
        return r

    def runs(self, did):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,status,stage,created,updated FROM analyses WHERE deck_id=? ORDER BY created DESC', (did,))]
