"""グループホーム日用品在庫管理ツール — PumpStack Lab."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
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


# 問題4: 未処理例外をJSONで返す（フロントのalert(data.error)が空になる問題を解消）
@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    app.logger.error(traceback.format_exc())
    return jsonify({"error": "サーバーエラーが発生しました"}), 500


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Not found"}), 404


# ── DB接続 ───────────────────────────────

class PGWrapper:
    def __init__(self, dsn: str):
        self._conn = psycopg2.connect(dsn)

    def execute(self, sql: str, params=()):
        # bug5/7: カーソルを毎回閉じて確実にリソースを解放
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params or ())
        return cur  # fetchone/fetchall後は呼び出し元でcloseを想定

    def fetchone(self, sql: str, params=()):
        # bug17: try/finallyでDBエラー時もカーソルを確実に閉じる
        cur = self.execute(sql, params)
        try:
            return cur.fetchone()
        finally:
            cur.close()

    def fetchall(self, sql: str, params=()):
        cur = self.execute(sql, params)
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def run(self, sql: str, params=()):
        """結果を返さないDML用。"""
        cur = self._conn.cursor()
        try:
            cur.execute(sql, params or ())
        finally:
            cur.close()

    def executemany(self, sql: str, params_list):
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        execute_batch(cur, sql, params_list)
        cur.close()

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
        c.run("""CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            current_stock INTEGER NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '個',
            default_weekly_avg REAL DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )""")
        c.run("""CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            staff_name TEXT NOT NULL DEFAULT '',
            note TEXT,
            recorded_at TEXT NOT NULL
        )""")
        c.run("""CREATE TABLE IF NOT EXISTS staff (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )""")
        ensure_items_columns(c)
        count = c.fetchone("SELECT COUNT(*) FROM items")["count"]
        if count == 0:
            now = datetime.now().isoformat(timespec="seconds")
            rows = [
                (str(uuid.uuid4()), name, 0, "個", 0, i, 1, now)
                for i, name in enumerate(INITIAL_ITEMS)
            ]
            c.executemany(
                "INSERT INTO items (id,name,current_stock,unit,default_weekly_avg,sort_order,active,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )


def ensure_items_columns(c: PGWrapper) -> None:
    """既存DBにdefault_weekly_avgカラムがなければ追加。"""
    cols = {r["column_name"] for r in c.fetchall(
        "SELECT column_name FROM information_schema.columns WHERE table_name='items'"
    )}
    if "default_weekly_avg" not in cols:
        c.run("ALTER TABLE items ADD COLUMN IF NOT EXISTS default_weekly_avg REAL DEFAULT 0")


def ensure_db():
    global DB_READY
    if DB_READY:
        return
    init_db()
    DB_READY = True


# ── ビジネスロジック ─────────────────────

def bulk_weekly_avg(item_ids: list[str], c: PGWrapper) -> dict[str, float]:
    """bug12: 全品目の週平均を1クエリで取得してN+1を解消。"""
    if not item_ids:
        return {}
    since = (datetime.now() - timedelta(days=28)).isoformat()
    placeholders = ",".join(["%s"] * len(item_ids))
    rows = c.fetchall(
        f"""SELECT item_id, COALESCE(SUM(quantity), 0) AS total
            FROM transactions
            WHERE item_id IN ({placeholders}) AND type='out' AND recorded_at >= %s
            GROUP BY item_id""",
        (*item_ids, since),
    )
    return {r["item_id"]: round(int(r["total"]) / 4, 2) for r in rows}


def calc_alert_level(stock: int, weekly_avg: float) -> str:
    if stock == 0:
        return "red"
    if weekly_avg <= 0:
        return "normal"
    weeks_left = stock / weekly_avg
    if weeks_left <= 2:
        return "red"
    if weeks_left <= 4:
        return "yellow"
    return "normal"


# ── ルート ───────────────────────────────

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
        items = c.fetchall("SELECT * FROM items WHERE active=1 ORDER BY sort_order, name")
        item_ids = [item["id"] for item in items]
        # bug12: 1クエリで全品目の週平均を取得
        avg_map = bulk_weekly_avg(item_ids, c)
        result = []
        for item in items:
            history_avg = avg_map.get(item["id"], 0.0)
            # 履歴がなければdefault_weekly_avgにフォールバック（bug15: float()で型保証）
            weekly_avg = history_avg if history_avg > 0 else float(item.get("default_weekly_avg") or 0)
            alert = calc_alert_level(int(item["current_stock"]), weekly_avg)
            result.append({**dict(item), "weekly_avg": weekly_avg, "alert": alert})
    return jsonify({"items": result})


@app.route("/api/items", methods=["POST"])
def create_item():
    ensure_db()
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    unit = (data.get("unit") or "個").strip()
    stock = int(data.get("current_stock") or 0)
    default_avg = float(data.get("default_weekly_avg") or 0)
    # bug8: 在庫数の負数バリデーション
    if not name:
        return jsonify({"error": "品名は必須です"}), 400
    if stock < 0:
        return jsonify({"error": "在庫数は0以上で入力してください"}), 400
    if default_avg < 0:
        return jsonify({"error": "週平均は0以上で入力してください"}), 400
    now = datetime.now().isoformat(timespec="seconds")
    iid = str(uuid.uuid4())
    # bug11: with ブロック内でexcept→returnするとrollbackが走らないため
    #        事前に重複チェックしてから INSERT する
    with conn() as c:
        exists = c.fetchone("SELECT id FROM items WHERE name=%s", (name,))
        if exists:
            return jsonify({"error": "同じ品名が既に存在します"}), 409
        max_order = (c.fetchone("SELECT COALESCE(MAX(sort_order),0)+1 AS v FROM items") or {}).get("v", 0)
        c.run(
            "INSERT INTO items (id,name,current_stock,unit,default_weekly_avg,sort_order,active,created_at) VALUES (%s,%s,%s,%s,%s,%s,1,%s)",
            (iid, name, stock, unit, default_avg, max_order, now),
        )
    return jsonify({"id": iid})


@app.route("/api/items/<iid>", methods=["PATCH"])
def patch_item(iid):
    """週平均デフォルト値の更新用。"""
    ensure_db()
    data = request.get_json(force=True)
    try:
        default_avg = float(data.get("default_weekly_avg") or 0)
    except (ValueError, TypeError):
        return jsonify({"error": "週平均の値が不正です"}), 400
    if default_avg < 0:
        return jsonify({"error": "週平均は0以上で入力してください"}), 400
    with conn() as c:
        # bug19: 削除済み品目へのPATCHを防ぐ
        item = c.fetchone("SELECT id FROM items WHERE id=%s AND active=1", (iid,))
        if not item:
            return jsonify({"error": "品目が見つかりません"}), 404
        c.run("UPDATE items SET default_weekly_avg=%s WHERE id=%s", (default_avg, iid))
    return jsonify({"ok": True})


@app.route("/api/items/<iid>", methods=["DELETE"])
def delete_item(iid):
    ensure_db()
    with conn() as c:
        c.run("UPDATE items SET active=0 WHERE id=%s", (iid,))
    return jsonify({"ok": True})


# ── API: 受払登録 ────────────────────────

@app.route("/api/transactions", methods=["POST"])
def create_transaction():
    ensure_db()
    data = request.get_json(force=True)
    item_id = (data.get("item_id") or "").strip()
    tx_type = (data.get("type") or "").strip()
    quantity = int(data.get("quantity") or 0)
    staff_name = (data.get("staff_name") or "").strip()
    note = (data.get("note") or "").strip()

    if not item_id or tx_type not in ("in", "out") or quantity <= 0:
        return jsonify({"error": "入力内容が不正です"}), 400

    now = datetime.now().isoformat(timespec="seconds")
    tid = str(uuid.uuid4())

    with conn() as c:
        # 同時アクセス競合対策: FOR UPDATE で行ロック取得
        item = c.fetchone(
            "SELECT * FROM items WHERE id=%s AND active=1 FOR UPDATE",
            (item_id,),
        )
        if not item:
            return jsonify({"error": "品目が見つかりません"}), 404
        new_stock = int(item["current_stock"]) + (quantity if tx_type == "in" else -quantity)
        if new_stock < 0:
            return jsonify({"error": f"在庫が不足しています（現在庫: {item['current_stock']}{item['unit']}）"}), 400
        c.run(
            "INSERT INTO transactions (id,item_id,type,quantity,staff_name,note,recorded_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (tid, item_id, tx_type, quantity, staff_name, note, now),
        )
        c.run("UPDATE items SET current_stock=%s WHERE id=%s", (new_stock, item_id))
    return jsonify({"id": tid, "new_stock": new_stock})


@app.route("/api/transactions")
def list_transactions():
    ensure_db()
    item_id = request.args.get("item_id")
    # bug20: 不正な limit 値（文字列など）で ValueError → 400 を返す
    try:
        limit = min(int(request.args.get("limit") or 200), 500)
    except (ValueError, TypeError):
        return jsonify({"error": "limitの値が不正です"}), 400
    with conn() as c:
        # bug6: INNER JOIN → LEFT JOIN（削除済み品目の履歴も消えないようにする）
        if item_id:
            rows = c.fetchall(
                """SELECT t.*, COALESCE(i.name, '削除済み品目') AS item_name
                   FROM transactions t
                   LEFT JOIN items i ON t.item_id=i.id
                   WHERE t.item_id=%s
                   ORDER BY t.recorded_at DESC LIMIT %s""",
                (item_id, limit),
            )
        else:
            rows = c.fetchall(
                """SELECT t.*, COALESCE(i.name, '削除済み品目') AS item_name
                   FROM transactions t
                   LEFT JOIN items i ON t.item_id=i.id
                   ORDER BY t.recorded_at DESC LIMIT %s""",
                (limit,),
            )
    return jsonify({"transactions": [dict(r) for r in rows]})


# ── API: スタッフ ────────────────────────

@app.route("/api/staff")
def list_staff():
    ensure_db()
    with conn() as c:
        rows = c.fetchall("SELECT * FROM staff WHERE active=1 ORDER BY name")
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
    # bug11: 同上、事前チェックで rollback 漏れを回避
    with conn() as c:
        exists = c.fetchone("SELECT id FROM staff WHERE name=%s", (name,))
        if exists:
            return jsonify({"error": "同じ名前が既に存在します"}), 409
        c.run(
            "INSERT INTO staff (id,name,active,created_at) VALUES (%s,%s,1,%s)",
            (sid, name, now),
        )
    return jsonify({"id": sid})


@app.route("/api/staff/<sid>", methods=["DELETE"])
def delete_staff(sid):
    ensure_db()
    with conn() as c:
        c.run("UPDATE staff SET active=0 WHERE id=%s", (sid,))
    return jsonify({"ok": True})


if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=5070, debug=True)
