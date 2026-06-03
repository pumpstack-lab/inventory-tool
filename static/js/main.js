let items = [];
let staffList = [];
let selectedItemId = null;
let selectedType = "out";
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
    const alertClass = it.alert === "red" ? "alert-red" : it.alert === "yellow" ? "alert-yellow" : "";
    const badge = it.alert === "red"
      ? `<span class="alert-badge red">🔴 2週間以内</span>`
      : it.alert === "yellow"
      ? `<span class="alert-badge yellow">🟡 1ヶ月以内</span>`
      : "";
    const weeklyAvg = it.weekly_avg > 0 ? it.weekly_avg.toFixed(1) : "―";
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
            <div class="stat-value" style="font-size:15px">${it.weekly_avg > 0 ? Math.floor(it.current_stock / it.weekly_avg) + '週' : '―'}</div>
          </div>
        </div>
        <div class="item-actions">
          <button class="btn btn-primary btn-sm" onclick="openTx('${it.id}')">受払登録</button>
          <a class="btn btn-ghost btn-sm" href="/detail?id=${it.id}">履歴</a>
          <button class="btn btn-icon" onclick="confirmDeleteItem('${it.id}', '${escHtml(it.name)}')" title="削除">🗑</button>
        </div>
      </div>`;
  }).join("");
}

// ── 受払モーダル ──────────────────────

function openTx(itemId) {
  selectedItemId = itemId;
  const item = items.find(i => i.id === itemId);
  document.getElementById("tx-modal-title").textContent = `受払登録：${item?.name || ""}`;
  document.getElementById("tx-qty").value = 1;
  document.getElementById("tx-note").value = "";
  selectedType = "out";
  selectedStaff = "";
  renderTypeButtons();
  renderStaffChips("staff-chips-tx");
  openModal("modal-tx");
}

function selectType(type) {
  selectedType = type;
  renderTypeButtons();
}

function renderTypeButtons() {
  document.getElementById("type-in").className =
    "type-btn" + (selectedType === "in" ? " selected-in" : "");
  document.getElementById("type-out").className =
    "type-btn" + (selectedType === "out" ? " selected-out" : "");
}

function renderStaffChips(containerId) {
  const el = document.getElementById(containerId);
  if (!staffList.length) {
    el.innerHTML = `<span style="font-size:13px;color:var(--muted)">担当者を先に登録してください</span>`;
    return;
  }
  el.innerHTML = staffList.map(s =>
    `<div class="staff-chip ${selectedStaff === s.name ? 'selected' : ''}" onclick="selectStaff('${escHtml(s.name)}','${containerId}')">${escHtml(s.name)}</div>`
  ).join("");
}

function selectStaff(name, containerId) {
  selectedStaff = selectedStaff === name ? "" : name;
  renderStaffChips(containerId);
}

async function submitTx() {
  const qty = parseInt(document.getElementById("tx-qty").value || "0");
  const note = document.getElementById("tx-note").value.trim();
  if (!selectedItemId) return;
  if (qty <= 0) { alert("数量を入力してください"); return; }

  const res = await fetch("/api/transactions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: selectedItemId,
      type: selectedType,
      quantity: qty,
      staff_name: selectedStaff,
      note,
    }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || "登録に失敗しました"); return; }
  closeModal("modal-tx");
  flash(selectedType === "in" ? "入庫を登録しました" : "払出を登録しました");
  await loadItems();
}

// ── 品目追加 ──────────────────────────

document.getElementById("btn-open-add-item").addEventListener("click", () => {
  document.getElementById("new-item-name").value = "";
  document.getElementById("new-item-unit").value = "個";
  document.getElementById("new-item-stock").value = "0";
  openModal("modal-add-item");
});

async function submitAddItem() {
  const name = document.getElementById("new-item-name").value.trim();
  const unit = document.getElementById("new-item-unit").value.trim() || "個";
  const stock = parseInt(document.getElementById("new-item-stock").value || "0");
  if (!name) { alert("品名を入力してください"); return; }
  const res = await fetch("/api/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, unit, current_stock: stock }),
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
  el.innerHTML = staffList.map(s =>
    `<div class="staff-chip">
      ${escHtml(s.name)}
      <button class="staff-chip-del" onclick="deleteStaff('${s.id}')" title="削除">✕</button>
    </div>`
  ).join("");
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
    .replace(/"/g, "&quot;");
}

init();
