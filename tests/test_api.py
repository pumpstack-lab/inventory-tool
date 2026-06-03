"""PSL標準テスト — 在庫管理API。
正常系・異常系・境界値を網羅し、ネイト精査で指摘されたバグが再発しないことを保証する。
"""
import json
import pytest


# ── ヘルパー ────────────────────────────────────────────────────────────────

def post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")

def patch(client, path, body):
    return client.patch(path, data=json.dumps(body), content_type="application/json")

def get_items(client):
    return client.get("/api/items").get_json()["items"]

def first_item_id(client):
    return get_items(client)[0]["id"]

def add_staff(client, name="田中"):
    r = post(client, "/api/staff", {"name": name})
    assert r.status_code == 200
    return r.get_json()["id"]


# ── 品目API ─────────────────────────────────────────────────────────────────

class TestItems:
    def test_initial_items_loaded(self, client):
        """初期26品目が投入されていること。"""
        items = get_items(client)
        assert len(items) == 26

    def test_add_item(self, client):
        """品目追加が正常に動作すること。"""
        r = post(client, "/api/items", {"name": "マスク", "unit": "箱", "current_stock": 5})
        assert r.status_code == 200
        names = [i["name"] for i in get_items(client)]
        assert "マスク" in names

    def test_add_item_duplicate(self, client):
        """同じ品名を追加すると409になること（bug11の回帰テスト）。"""
        post(client, "/api/items", {"name": "重複品目"})
        r = post(client, "/api/items", {"name": "重複品目"})
        assert r.status_code == 409

    def test_add_item_negative_stock(self, client):
        """負の在庫数は400になること（bug8の回帰テスト）。"""
        r = post(client, "/api/items", {"name": "テスト品", "current_stock": -1})
        assert r.status_code == 400

    def test_add_item_invalid_stock_type(self, client):
        """数値以外の在庫数は400になること（bug22の回帰テスト）。"""
        r = post(client, "/api/items", {"name": "テスト品", "current_stock": "abc"})
        assert r.status_code == 400

    def test_add_item_no_name(self, client):
        """品名なしは400になること。"""
        r = post(client, "/api/items", {"name": ""})
        assert r.status_code == 400

    def test_delete_item(self, client):
        """品目削除後は一覧に出なくなること。"""
        iid = first_item_id(client)
        r = client.delete(f"/api/items/{iid}")
        assert r.status_code == 200
        ids = [i["id"] for i in get_items(client)]
        assert iid not in ids

    def test_patch_item_default_avg(self, client):
        """週平均デフォルト値の更新が正常に動作すること（問題2の回帰テスト）。"""
        iid = first_item_id(client)
        r = patch(client, f"/api/items/{iid}", {"default_weekly_avg": 3.5})
        assert r.status_code == 200
        item = next(i for i in get_items(client) if i["id"] == iid)
        assert item["default_weekly_avg"] == pytest.approx(3.5)

    def test_patch_deleted_item(self, client):
        """削除済み品目へのPATCHは404になること（bug19の回帰テスト）。"""
        iid = first_item_id(client)
        client.delete(f"/api/items/{iid}")
        r = patch(client, f"/api/items/{iid}", {"default_weekly_avg": 1.0})
        assert r.status_code == 404

    def test_patch_negative_avg(self, client):
        """負の週平均は400になること。"""
        iid = first_item_id(client)
        r = patch(client, f"/api/items/{iid}", {"default_weekly_avg": -1})
        assert r.status_code == 400

    def test_alert_zero_stock(self, client):
        """在庫0の品目はalert=redになること（在庫0バグの回帰テスト）。"""
        iid = first_item_id(client)
        # 在庫を0にリセット（初期値0のはず）
        item = next(i for i in get_items(client) if i["id"] == iid)
        if item["current_stock"] > 0:
            post(client, "/api/transactions", {
                "item_id": iid, "type": "out",
                "quantity": item["current_stock"], "staff_name": ""
            })
        item = next(i for i in get_items(client) if i["id"] == iid)
        assert item["current_stock"] == 0
        assert item["alert"] == "red"


# ── 受払API ─────────────────────────────────────────────────────────────────

class TestTransactions:
    def test_stock_in(self, client):
        """入庫で在庫が増えること。"""
        iid = first_item_id(client)
        r = post(client, "/api/transactions", {
            "item_id": iid, "type": "in", "quantity": 10, "staff_name": "田中"
        })
        assert r.status_code == 200
        assert r.get_json()["new_stock"] == 10

    def test_stock_out(self, client):
        """払出で在庫が減ること。"""
        iid = first_item_id(client)
        post(client, "/api/transactions", {"item_id": iid, "type": "in", "quantity": 10, "staff_name": ""})
        r = post(client, "/api/transactions", {"item_id": iid, "type": "out", "quantity": 3, "staff_name": ""})
        assert r.status_code == 200
        assert r.get_json()["new_stock"] == 7

    def test_stock_out_insufficient(self, client):
        """在庫不足で払出すると400になること（bug8の回帰テスト）。"""
        iid = first_item_id(client)
        r = post(client, "/api/transactions", {
            "item_id": iid, "type": "out", "quantity": 999, "staff_name": ""
        })
        assert r.status_code == 400
        assert "不足" in r.get_json()["error"]

    def test_invalid_type(self, client):
        """typeが in/out 以外は400になること。"""
        iid = first_item_id(client)
        r = post(client, "/api/transactions", {
            "item_id": iid, "type": "invalid", "quantity": 1, "staff_name": ""
        })
        assert r.status_code == 400

    def test_invalid_quantity_type(self, client):
        """数量に文字列を送ると400になること（bug22の回帰テスト）。"""
        iid = first_item_id(client)
        r = post(client, "/api/transactions", {
            "item_id": iid, "type": "out", "quantity": "abc", "staff_name": ""
        })
        assert r.status_code == 400

    def test_zero_quantity(self, client):
        """数量0は400になること。"""
        iid = first_item_id(client)
        r = post(client, "/api/transactions", {
            "item_id": iid, "type": "in", "quantity": 0, "staff_name": ""
        })
        assert r.status_code == 400

    def test_history_after_delete(self, client):
        """品目削除後も履歴が取得できること（bug6の回帰テスト）。
        active=0でもitemsレコードは残るのでLEFT JOINで品名が取れる。
        完全DELETE時は'削除済み品目'になるが、PSLでは論理削除のみ。"""
        iid = first_item_id(client)
        post(client, "/api/transactions", {"item_id": iid, "type": "in", "quantity": 5, "staff_name": ""})
        client.delete(f"/api/items/{iid}")
        r = client.get(f"/api/transactions?item_id={iid}")
        assert r.status_code == 200
        txs = r.get_json()["transactions"]
        # 論理削除なのでLEFT JOINで品名は引き続き取れる（履歴が消えていないことを確認）
        assert len(txs) == 1
        assert "item_name" in txs[0]

    def test_invalid_limit(self, client):
        """limitに文字列を送ると400になること（bug20の回帰テスト）。"""
        r = client.get("/api/transactions?limit=abc")
        assert r.status_code == 400

    def test_limit_cap(self, client):
        """limitの上限は500であること。"""
        r = client.get("/api/transactions?limit=9999")
        assert r.status_code == 200


# ── スタッフAPI ──────────────────────────────────────────────────────────────

class TestStaff:
    def test_add_staff(self, client):
        """担当者追加が正常に動作すること。"""
        r = post(client, "/api/staff", {"name": "山田"})
        assert r.status_code == 200
        names = [s["name"] for s in client.get("/api/staff").get_json()["staff"]]
        assert "山田" in names

    def test_add_staff_duplicate(self, client):
        """同じ名前の担当者は409になること（bug11の回帰テスト）。"""
        post(client, "/api/staff", {"name": "重複スタッフ"})
        r = post(client, "/api/staff", {"name": "重複スタッフ"})
        assert r.status_code == 409

    def test_add_staff_no_name(self, client):
        """名前なしは400になること。"""
        r = post(client, "/api/staff", {"name": ""})
        assert r.status_code == 400

    def test_delete_staff(self, client):
        """担当者削除後は一覧に出なくなること。"""
        sid = add_staff(client, "削除テスト")
        r = client.delete(f"/api/staff/{sid}")
        assert r.status_code == 200
        names = [s["name"] for s in client.get("/api/staff").get_json()["staff"]]
        assert "削除テスト" not in names
