"""グループホーム日用品在庫管理ツール — PumpStack Lab."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, date
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from flask import Flask, jsonify, request, send_from_directory

APP_DIR = Path(__file__).resolve().parent
STATIC = APP_DIR / "static"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

INITIAL_ITEMS = [
    "トイレットペーパー",
    "ゴミ袋",
    "排水溝ネット",
    "洗濯洗剤ジェルボール",
    "食器洗剤（詰め替え）",
    "手洗い石鹸（固形）",
    "トイレ洗剤（詰め替え）",
    "浴室洗剤（詰め替え）",
    "クイックルワイパー（ドライ）",
    "うがい薬",
    "ゴム手袋（M）",
    "ゴム手袋（L）",
    "ふきん",
    "食器用スポンジ",
    "キッチンハイター（大）",
    "衣類用漂白剤",
    "洗濯槽クリーナー（本体）",
    "洗濯槽クリーナー（詰め替え）",
    "トイレ洗剤（本体）",
    "ドメスト（トイレ用）",
    "クレンザー",
    "カビキラー",
    "クイックルハンディ",
    "バス用スポンジ",
    "水切りネット（三角コーナー）",
    "パイプユニッシュ",
]

app = Flask(__name__, static_folder=str(STATIC), static_url_path="")


class PGWrapper:
    def __init__(self, dsn: str):
        self._conn = psycopg2.connect(dsn)

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params or ())
        return cur

    def executemany(self, sql: str, params_list):
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        execute_batch(cur, sql, params_list)
        cur.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()


def conn() -> PGWrapper:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL が未設定です")
    return PGWrapper(DATABASE_URL)


DB_READY = False


def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            current_stock INTEGER NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '個',
            sort_order INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            staff_name TEXT NOT NULL DEFAULT '',
            note TEXT,
            recorded_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS staff (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )""")
        # 初期アイテムを投入
        if c.execute("SELECT COUNT(*) FROM items").fetchone()["count"] == 0:
            now = datetime.now().isoformat(timespec="seconds")
            rows = [
                (str(uuid.uuid4()), name, 0, "個", i, 1, now)
                for i, name in enumerate(INITIAL_ITEMS)
            ]
            c.executemany(
                "INSERT INTO items (id,name,current_stock,unit,sort_order,active,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )


def ensure_db():
    global DB_READY
    if DB_READY:
        return
    init_db()
    DB_READY = True


def calc_weekly_avg(item_id: str, c: PGWrapper) -> float:
    """過去28日間の払出数量から週平均払出数を計算する。"""
    since = (datetime.now() - timedelta(days=28)).isoformat()
    row = c.execute(
        """SELECT COALESCE(SUM(quantity), 0) AS total
           FROM transactions
           WHERE item_id=%s AND type='out' AND recorded_at >= %s""",
        (item_id, since),
    ).fetchone()
    total = int(row["total"] or 0)
    return round(total / 4, 2)  # 4週分


def calc_alert_level(stock: int, weekly_avg: float) -> str:
    """0=正常 1=黄色（1ヶ月以内に切れる） 2=赤（2週間以内に切れる）"""
    if weekly_avg <= 0:
        return "normal"
    weeks_left = stock / weekly_avg
    if weeks_left <= 2:
        return "red"
    if weeks_left <= 4:
        return "yellow"
    return "normal"


# ── ルート ─────────────────────────────

@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/detail")
def detail():
    return send_from_directory(STATIC, "detail.html")


# ── API: 在庫一覧 ────────────────────────

@app.route("/api/items")
def list_items():
    ensure_db()
    with conn() as c:
        items = [dict(r) for r in c.execute(
            "SELECT * FROM items WHERE active=1 ORDER BY sort_order, name"
        )]
        result = []
        for item in items:
            weekly_avg = calc_weekly_avg(item["id"], c)
            alert = calc_alert_level(int(item["current_stock"]), weekly_avg)
            result.append({**item, "weekly_avg": weekly_avg, "alert": alert})
    return jsonify({"items": result})


@app.route("/api/items", methods=["POST"])
def create_item():
    ensure_db()
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    unit = (data.get("unit") or "個").strip()
    stock = int(data.get("current_stock") or 0)
    if not name:
        return jsonify({"error": "品名は必須です"}), 400
    now = datetime.now().isoformat(timespec="seconds")
    iid = str(uuid.uuid4())
    with conn() as c:
        max_order = c.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS v FROM items").fetchone()["v"]
        try:
            c.execute(
                "INSERT INTO items (id,name,current_stock,unit,sort_order,active,created_at) VALUES (%s,%s,%s,%s,%s,1,%s)",
                (iid, name, stock, unit, max_order, now),
            )
        except Exception:
            return jsonify({"error": "同じ品名が既に存在します"}), 409
    return jsonify({"id": iid})


@app.route("/api/items/<iid>", methods=["DELETE"])
def delete_item(iid):
    ensure_db()
    with conn() as c:
        c.execute("UPDATE items SET active=0 WHERE id=%s", (iid,))
    return jsonify({"ok": True})


# ── API: 受払登録 ────────────────────────

@app.route("/api/transactions", methods=["POST"])
def create_transaction():
    ensure_db()
    data = request.get_json(force=True)
    item_id = (data.get("item_id") or "").strip()
    tx_type = (data.get("type") or "").strip()  # "in" or "out"
    quantity = int(data.get("quantity") or 0)
    staff_name = (data.get("staff_name") or "").strip()
    note = (data.get("note") or "").strip()

    if not item_id or tx_type not in ("in", "out") or quantity <= 0:
        return jsonify({"error": "入力内容が不正です"}), 400

    now = datetime.now().isoformat(timespec="seconds")
    tid = str(uuid.uuid4())

    with conn() as c:
        item = c.execute("SELECT * FROM items WHERE id=%s AND active=1", (item_id,)).fetchone()
        if not item:
            return jsonify({"error": "品目が見つかりません"}), 404
        new_stock = int(item["current_stock"]) + (quantity if tx_type == "in" else -quantity)
        if new_stock < 0:
            return jsonify({"error": "在庫が不足しています"}), 400
        c.execute(
            "INSERT INTO transactions (id,item_id,type,quantity,staff_name,note,recorded_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (tid, item_id, tx_type, quantity, staff_name, note, now),
        )
        c.execute("UPDATE items SET current_stock=%s WHERE id=%s", (new_stock, item_id))
    return jsonify({"id": tid, "new_stock": new_stock})


@app.route("/api/transactions")
def list_transactions():
    ensure_db()
    item_id = request.args.get("item_id")
    limit = min(int(request.args.get("limit") or 200), 500)
    with conn() as c:
        if item_id:
            rows = list(c.execute(
                "SELECT t.*, i.name as item_name FROM transactions t JOIN items i ON t.item_id=i.id WHERE t.item_id=%s ORDER BY t.recorded_at DESC LIMIT %s",
                (item_id, limit),
            ))
        else:
            rows = list(c.execute(
                "SELECT t.*, i.name as item_name FROM transactions t JOIN items i ON t.item_id=i.id ORDER BY t.recorded_at DESC LIMIT %s",
                (limit,),
            ))
    return jsonify({"transactions": [dict(r) for r in rows]})


# ── API: スタッフ ────────────────────────

@app.route("/api/staff")
def list_staff():
    ensure_db()
    with conn() as c:
        rows = list(c.execute("SELECT * FROM staff WHERE active=1 ORDER BY name"))
    return jsonify({"staff": [dict(r) for r in rows]})


@app.route("/api/staff", methods=["POST"])
def create_staff():
    ensure_db()
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "担当者名は必須です"}), 400
    now = datetime.now().isoformat(timespec="seconds")
    sid = str(uuid.uuid4())
    with conn() as c:
        try:
            c.execute(
                "INSERT INTO staff (id,name,active,created_at) VALUES (%s,%s,1,%s)",
                (sid, name, now),
            )
        except Exception:
            return jsonify({"error": "同じ名前が既に存在します"}), 409
    return jsonify({"id": sid})


@app.route("/api/staff/<sid>", methods=["DELETE"])
def delete_staff(sid):
    ensure_db()
    with conn() as c:
        c.execute("UPDATE staff SET active=0 WHERE id=%s", (sid,))
    return jsonify({"ok": True})


if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=5070, debug=True)
