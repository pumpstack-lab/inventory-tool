"""pytest共通フィクスチャ。"""
import os, sys, uuid, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── インメモリDB（SQLite）でPGWrapperを差し替え ──────────────────────────────
import sqlite3

def _pg2sq(sql: str) -> str:
    """psycopg2の %s → SQLite の ? に変換（テスト用）。"""
    return sql.replace("%s", "?")

class _SQLiteWrapper:
    def __init__(self, conn):
        self._conn = conn

    def fetchone(self, sql, params=()):
        cur = self._conn.execute(_pg2sq(sql), params or ())
        row = cur.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql, params=()):
        rows = self._conn.execute(_pg2sq(sql), params or ()).fetchall()
        return [dict(r) for r in rows]

    def run(self, sql, params=()):
        self._conn.execute(_pg2sq(sql), params or ())

    def executemany(self, sql, params_list):
        self._conn.executemany(_pg2sq(sql), params_list)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()


@pytest.fixture
def client(monkeypatch):
    import app as M

    # テストごとにクリーンなインメモリDB
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    # FOR UPDATE はSQLiteで無効なので除去
    original_pg2sq = _pg2sq

    def _pg2sq_ext(sql):
        return original_pg2sq(sql).replace(" FOR UPDATE", "")

    def _normalize(row):
        """SQLiteのCOUNT(*)キーをPostgreSQL互換の'count'に正規化。"""
        if row is None:
            return None
        d = dict(row)
        for k in list(d.keys()):
            if k.upper().startswith("COUNT") or k.upper().startswith("COALESCE"):
                # COALESCE(MAX(...),0)+1 など集計関数も'v'などのエイリアスが効く場合もある
                pass
        # COUNT(*) → count
        if "COUNT(*)" in d:
            d["count"] = d.pop("COUNT(*)")
        return d

    class Wrapper(_SQLiteWrapper):
        def fetchone(self, sql, params=()):
            cur = db.execute(_pg2sq_ext(sql), params or ())
            row = cur.fetchone()
            return _normalize(dict(row)) if row else None

        def fetchall(self, sql, params=()):
            return [_normalize(dict(r)) for r in db.execute(_pg2sq_ext(sql), params or ()).fetchall()]

        def run(self, sql, params=()):
            db.execute(_pg2sq_ext(sql), params or ())

        def executemany(self, sql, params_list):
            db.executemany(_pg2sq_ext(sql), params_list)

        def __exit__(self, exc_type, *_):
            if exc_type:
                db.rollback()
            else:
                db.commit()

    monkeypatch.setattr(M, "conn", lambda: Wrapper(db))
    monkeypatch.setattr(M, "DB_READY", False)
    # ensure_items_columns は information_schema を使うためSQLiteでは動かない → ノーオプで差し替え
    monkeypatch.setattr(M, "ensure_items_columns", lambda c: None)
    M.app.config["TESTING"] = True

    with M.app.test_client() as c:
        with M.app.app_context():
            M.ensure_db()
        yield c
