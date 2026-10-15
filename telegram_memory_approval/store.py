"""Durable state machine. Terminal rows are never automatically retried."""
import json
import sqlite3
import uuid


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=30)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS proposals (
            token TEXT PRIMARY KEY, native_id TEXT UNIQUE NOT NULL,
            hash TEXT NOT NULL, record TEXT NOT NULL, owner INTEGER NOT NULL,
            chat INTEGER NOT NULL, topic INTEGER, message INTEGER,
            state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')""")
        self.db.commit()

    def recover(self):
        # Called only by the process holding the profile runtime lock.
        with self.db:
            self.db.execute("UPDATE proposals SET state='uncertain', detail='Restart during side effect; reconcile manually' WHERE state IN ('sending','applying')")

    def insert(self, record, digest, owner, chat, topic):
        token = uuid.uuid4().hex
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO proposals(token,native_id,hash,record,owner,chat,topic,state) VALUES(?,?,?,?,?,?,?,'sending')",
                (token, record["id"], digest, json.dumps(record, ensure_ascii=False), owner, chat, topic))
        return token if cursor.rowcount else None

    def get(self, token):
        row = self.db.execute("SELECT * FROM proposals WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None

    def get_native(self, native_id):
        row = self.db.execute("SELECT * FROM proposals WHERE native_id=?", (native_id,)).fetchone()
        return dict(row) if row else None

    def transition(self, token, before, after, *, message=None, detail=''):
        with self.db:
            cursor = self.db.execute("UPDATE proposals SET state=?, message=COALESCE(?,message),detail=? WHERE token=? AND state=?",
                                     (after, message, detail, token, before))
        return cursor.rowcount == 1

    def close(self):
        self.db.close()
