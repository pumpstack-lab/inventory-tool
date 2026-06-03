let items = [];
let staffList = [];
let selectedItemId = null;
let txType = "out";        // bug1: selectedTypeから改名（関数名と競合回避）
let selectedStaff = "";

// ── 初期化 ────────────────────────────

async function init() {
  await Promise.all([loadItems(), loadStaff()]);
}

async function loadItems() {
  const res = await fetch("/api/items");
  const data = await res.json();
  items = data.items || [];
  renderItems();
}

async function loadStaff() {
  const res = await fetch("/api/staff");
  const data = await res.json();
  staffList = data.staff || [];
}

// ── レンダリング ──────────────────────

function renderItems() {
  const grid = document.getElementById("item-grid");
  const empty = document.getElementById("empty-msg");
  const query = document.getElementById("search-input").value.trim().toLowerCase();

  const filtered = items.filter(it =>
    !query || it.name.toLowerCase().includes(query)
  );

  if (!filtered.length) {
    grid.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  grid.innerHTML = filtered.map(it => {
    // 在庫0は履歴がなくてもバッジ表示
    const isZero = it.current_stock === 0;
    const alertLevel = isZero ? "zero" : it.alert;
    const alertClass = alertLevel === "zero" ? "alert-red"
      : alertLevel === "red" ? "alert-red"
      : alertLevel === "yellow" ? "alert-yellow" : "";
    const badge = alertLevel === "zero"
      ? `<span class="alert-badge red">🔴 在庫なし</span>`
      : alertLevel === "red"
      ? `<span class="alert-badge red">🔴 2週間以内</span>`
      : alertLevel === "yellow"
      ? `<span class="alert-badge yellow">🟡 1ヶ月以内</span>`
      : "";
    const weeklyAvg = it.weekly_avg > 0 ? it.weekly_avg.toFixed(1) : "―";
    const weeksLeft = it.weekly_avg > 0
      ? Math.floor(it.current_stock / it.weekly_avg) + "週"
      : "―";

    // bug4: onclick属性でのsafeな渡し方（data属性経由）
    return `
      <div class="item-card ${alertClass}" data-id="${it.id}">
        <div class="item-name">${escHtml(it.name)} ${badge}</div>
        <div class="item-stats">
          <div class="stat-box">
            <div class="stat-label">現在庫</div>
            <div class="stat-value">${it.current_stock}<span class="stat-unit">${escHtml(it.unit)}</span></div>
          </div>
          <div class="stat-box">
            <div class="stat-label">週平均払出</div>
            <div class="stat-value" style="font-size:15px">${weeklyAvg}<span class="stat-unit">${it.weekly_avg > 0 ? it.unit + '/週' : ''}</span></div>
          </div>
          <div class="stat-box">
            <div class="stat-label">残余週数</div>
            <div class="stat-value" style="font-size:15px">${weeksLeft}</div>
          </div>
        </div>
        <div class="item-actions">
          <button class="btn btn-primary btn-sm js-open-tx" data-id="${it.id}">受払登録</button>
          <a class="btn btn-ghost btn-sm" href="/detail?id=${it.id}">履歴</a>
          <button class="btn btn-icon js-delete-item" data-id="${it.id}" data-name="${escHtml(it.name)}" title="削除">🗑</button>
        </div>
      </div>`;
  }).join("");

  // イベント委譲（onclick属性を使わない）
  grid.querySelectorAll(".js-open-tx").forEach(btn => {
    btn.addEventListener("click", () => openTx(btn.dataset.id));
  });
  grid.querySelectorAll(".js-delete-item").forEach(btn => {
    btn.addEventListener("click", () => confirmDeleteItem(btn.dataset.id, btn.dataset.name));
  });
}

// ── 受払モーダル ──────────────────────

function openTx(itemId) {
  selectedItemId = itemId;
  const item = items.find(i => i.id === itemId);
  document.getElementById("tx-modal-title").textContent = `受払登録：${item?.name || ""}`;
  document.getElementById("tx-qty").value = 1;
  document.getElementById("tx-note").value = "";
  // 日付を当日にデフォルト設定
  document.getElementById("tx-date").value = fmtDateToday();
  txType = "out";
  selectedStaff = "";
  renderTypeButtons();
  renderStaffChips("staff-chips-tx");
  openModal("modal-tx");
}

function fmtDateToday() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// bug1: 関数名を setTxType に変更（変数名 txType と競合しない）
function setTxType(type) {
  txType = type;
  renderTypeButtons();
}

function renderTypeButtons() {
  document.getElementById("type-in").className =
    "type-btn" + (txType === "in" ? " selected-in" : "");
  document.getElementById("type-out").className =
    "type-btn" + (txType === "out" ? " selected-out" : "");
}

function renderStaffChips(containerId) {
  const el = document.getElementById(containerId);
  if (!staffList.length) {
    el.innerHTML = `<span style="font-size:13px;color:var(--muted)">ヘッダーの「担当者」から先に登録してください</span>`;
    return;
  }
  // bug4: data属性経由でイベント設定（onclick属性のアポストロフィ問題を回避）
  el.innerHTML = staffList.map(s =>
    `<div class="staff-chip ${selectedStaff === s.name ? 'selected' : ''}" data-name="${escHtml(s.name)}" data-container="${containerId}">${escHtml(s.name)}</div>`
  ).join("");
  el.querySelectorAll(".staff-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      selectedStaff = selectedStaff === chip.dataset.name ? "" : chip.dataset.name;
      renderStaffChips(containerId);
    });
  });
}

async function submitTx() {
  const qty = parseInt(document.getElementById("tx-qty").value || "0");
  const note = document.getElementById("tx-note").value.trim();
  if (!selectedItemId) return;
  if (qty <= 0) { alert("数量を入力してください"); return; }
  const home_name = document.getElementById("tx-home").value;
  if (!home_name) {
    if (!confirm("ホームが選択されていません。このまま登録しますか？")) return;
  }
  // 担当者未選択を警告（必須ではないが確認）
  if (!selectedStaff) {
    if (!confirm("担当者が選択されていません。このまま登録しますか？")) return;
  }

  const transaction_date = document.getElementById("tx-date").value.trim();
  const res = await fetch("/api/transactions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: selectedItemId,
      type: txType,
      quantity: qty,
      staff_name: selectedStaff,
      home_name,
      transaction_date,
      note,
    }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || "登録に失敗しました"); return; }
  closeModal("modal-tx");
  flash(txType === "in" ? "入庫を登録しました" : "払出を登録しました");
  await loadItems();
}

// ── 品目追加 ──────────────────────────

document.getElementById("btn-open-add-item").addEventListener("click", () => {
  document.getElementById("new-item-name").value = "";
  document.getElementById("new-item-unit").value = "個";
  document.getElementById("new-item-stock").value = "0";
  document.getElementById("new-item-avg").value = "0";
  openModal("modal-add-item");
});

async function submitAddItem() {
  const name = document.getElementById("new-item-name").value.trim();
  const unit = document.getElementById("new-item-unit").value.trim() || "個";
  // bug25: スペースのみ入力でNaNになるのを防ぐ
  const stock = parseInt(document.getElementById("new-item-stock").value.trim() || "0") || 0;
  const defaultAvg = parseFloat(document.getElementById("new-item-avg").value.trim() || "0") || 0;
  if (!name) { alert("品名を入力してください"); return; }
  const res = await fetch("/api/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, unit, current_stock: stock, default_weekly_avg: defaultAvg }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || "追加に失敗しました"); return; }
  closeModal("modal-add-item");
  flash("品目を追加しました");
  await loadItems();
}

async function confirmDeleteItem(id, name) {
  if (!confirm(`「${name}」を削除しますか？履歴は残ります。`)) return;
  await fetch(`/api/items/${id}`, { method: "DELETE" });
  flash("品目を削除しました");
  await loadItems();
}

// ── 担当者管理 ───────────────────────

document.getElementById("btn-open-staff").addEventListener("click", () => {
  renderStaffManage();
  openModal("modal-staff");
});

function renderStaffManage() {
  const el = document.getElementById("staff-list-manage");
  if (!staffList.length) {
    el.innerHTML = `<p style="font-size:13px;color:var(--muted)">担当者が登録されていません</p>`;
    return;
  }
  // bug4: data属性経由でイベント設定
  el.innerHTML = staffList.map(s =>
    `<div class="staff-chip">
      ${escHtml(s.name)}
      <button class="staff-chip-del js-del-staff" data-id="${s.id}" title="削除">✕</button>
    </div>`
  ).join("");
  el.querySelectorAll(".js-del-staff").forEach(btn => {
    btn.addEventListener("click", () => deleteStaff(btn.dataset.id));
  });
}

async function submitAddStaff() {
  const name = document.getElementById("new-staff-name").value.trim();
  if (!name) { alert("担当者名を入力してください"); return; }
  const res = await fetch("/api/staff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || "追加に失敗しました"); return; }
  document.getElementById("new-staff-name").value = "";
  await loadStaff();
  renderStaffManage();
  flash("担当者を追加しました");
}

async function deleteStaff(id) {
  if (!confirm("この担当者を削除しますか？")) return;
  await fetch(`/api/staff/${id}`, { method: "DELETE" });
  await loadStaff();
  renderStaffManage();
  // bug9: 削除した担当者が選択中だったらリセット
  const stillExists = staffList.some(s => s.name === selectedStaff);
  if (!stillExists) selectedStaff = "";
}

// ── 検索 ──────────────────────────────

document.getElementById("search-input").addEventListener("input", renderItems);

// ── ユーティリティ ───────────────────

function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}
document.querySelectorAll(".modal-backdrop").forEach(el => {
  el.addEventListener("click", e => {
    if (e.target === el) el.classList.add("hidden");
  });
});

function flash(msg) {
  const el = document.getElementById("flash");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");   // bug4: アポストロフィもエスケープ
}

init();
