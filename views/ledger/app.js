/* 可视化账本 · 前端逻辑（无外部依赖，通过 AstrBotPluginView bridge 与后端通信） */

const bridge = window.AstrBotPluginView;

/* ---------------- i18n（中文默认文案，其余语言走 .astrbot-plugin/i18n） ---------------- */

const T = {
  title: "可视化账本",
  subtitle: "AI 记账管家 · 账本总览",
  export_csv: "导出 CSV",
  export_json: "导出 JSON",
  refresh: "刷新",
  all_users: "全部用户",
  all_types: "全部类型",
  type_expense: "支出",
  type_income: "收入",
  all_categories: "全部分类",
  tile_expense: "支出",
  tile_income: "收入",
  tile_net: "结余",
  tile_count: "笔数",
  delta_vs_prev: "较上一周期",
  cat_expense: "支出分类",
  cat_income: "收入分类",
  daily: "每日收支",
  legend_expense: "支出",
  legend_income: "收入",
  records: "账目明细",
  th_time: "时间",
  th_type: "类型",
  th_amount: "金额",
  th_category: "分类",
  th_note: "备注",
  th_user: "用户",
  th_source: "来源",
  empty_records: "这段时间还没有记录，去和机器人说句「午饭花了 30」吧",
  empty_expense: "暂无支出记录",
  empty_income: "暂无收入记录",
  empty_daily: "暂无任何记录",
  prev_page: "‹ 上一页",
  next_page: "下一页 ›",
  delete: "删除",
  confirm_delete: "确定删除这条记录吗？",
  deleted: "已删除",
  load_error: "加载失败",
  tip_count: "笔数",
  tip_share: "占比",
  other: "其他",
  total_records: "共 {n} 条",
  n_records: "{n} 笔",
  click_to_filter: "点击筛选明细",
};

const t = (key) => bridge.t(`views.ledger.${key}`, T[key] ?? key);

/* ---------------- 状态 ---------------- */

const state = {
  days: 30,
  userId: "",
  type: "",
  category: "",
  page: 1,
  pageSize: 15,
  stats: null,
  records: [],
  total: 0,
  users: [],
};

const $ = (id) => document.getElementById(id);

/* ---------------- 格式化 ---------------- */

function fmtNum(v) {
  const abs = Math.abs(v);
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(v / 1e4).toFixed(1)}万`;
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

const money = (v, cur) => `${cur}${fmtNum(v)}`;
const signedMoney = (v, cur) => `${v >= 0 ? "+" : "−"}${money(Math.abs(v), cur)}`;
const MINUS = "−";

function niceCeil(v) {
  if (!(v > 0)) return 1;
  const exp = 10 ** Math.floor(Math.log10(v));
  const f = v / exp;
  const step = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return step * exp;
}

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const ymd = d.getFullYear() === now.getFullYear() ? "" : `${d.getFullYear()}-`;
  return `${ymd}${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ---------------- DOM 工具 ---------------- */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

let toastTimer = null;
function toast(msg) {
  const node = $("toast");
  node.textContent = msg;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 2800);
}

/* ---------------- Tooltip（增强通道，不承载唯一数据） ---------------- */

function tipRow(color, name, valueText) {
  const row = el("div", "tip-row");
  const key = el("span", "tip-key");
  key.style.background = color;
  row.append(key, el("span", "tip-name", name));
  const val = el("span", "tip-value", valueText);
  row.append(val);
  return row;
}

const tooltip = $("tooltip");

function showTip(title, rows) {
  tooltip.replaceChildren(el("div", "tip-title", title), ...rows);
  tooltip.hidden = false;
}

function moveTip(e) {
  const pad = 14;
  const w = tooltip.offsetWidth;
  const h = tooltip.offsetHeight;
  let x = e.clientX + pad;
  let y = e.clientY - h - pad;
  if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
  if (y < 8) y = e.clientY + pad;
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${y}px`;
}

function hideTip() {
  tooltip.hidden = true;
}

function bindTooltip() {
  const build = (target) => {
    const cur = state.stats?.currency ?? "¥";
    if (target.classList.contains("band")) {
      const expense = Number(target.dataset.expense || 0);
      const income = Number(target.dataset.income || 0);
      showTip(target.dataset.date, [
        tipRow("var(--series-1)", t("legend_expense"), money(expense, cur)),
        tipRow("var(--series-2)", t("legend_income"), money(income, cur)),
      ]);
    } else if (target.classList.contains("bar-row")) {
      const total = Number(target.dataset.total || 0);
      const count = Number(target.dataset.count || 0);
      const share = Number(target.dataset.share || 0);
      showTip(target.dataset.name, [
        tipRow(target.dataset.color || "var(--ink-2)", t("th_amount"), money(total, cur)),
        tipRow("var(--muted)", t("tip_count"), `${count}`),
        tipRow("var(--muted)", t("tip_share"), `${share.toFixed(1)}%`),
      ]);
    }
  };
  document.addEventListener("pointermove", (e) => {
    const target = e.target.closest(".band, .bar-row");
    if (target) {
      build(target);
      moveTip(e);
    } else {
      hideTip();
    }
  });
  document.addEventListener("focusin", (e) => {
    const target = e.target.closest(".band, .bar-row");
    if (target) build(target);
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("pointerleave", hideTip);
}

/* ---------------- 渲染：指标瓦片 ---------------- */

function deltaNode(cur, prev, upIsGood, cur$) {
  const node = el("div", "delta");
  if (!prev && !cur) {
    node.classList.add("flat");
    node.textContent = `— ${t("delta_vs_prev")}`;
    return node;
  }
  const diff = cur - prev;
  if (Math.abs(diff) < 0.005) {
    node.classList.add("flat");
    node.textContent = `±0 ${t("delta_vs_prev")}`;
    return node;
  }
  const up = diff > 0;
  node.classList.add(up === upIsGood ? "up-good" : "up-bad");
  node.textContent = `${up ? "+" : MINUS}${money(Math.abs(diff), cur$)} ${t("delta_vs_prev")}`;
  return node;
}

function renderTiles() {
  const s = state.stats;
  const box = $("tiles");
  box.replaceChildren();
  if (!s) return;
  const cur = s.currency ?? "¥";
  const sum = s.summary ?? { expense: 0, income: 0, count: 0 };
  const prev = s.prev_summary ?? { expense: 0, income: 0, count: 0 };

  const mk = (labelText, valueNode) => {
    const tile = el("div", "tile");
    tile.append(el("div", "label", labelText));
    tile.append(valueNode);
    return tile;
  };

  const v1 = el("div", "value", money(sum.expense, cur));
  const t1 = mk(t("tile_expense"), v1);
  t1.append(deltaNode(sum.expense, prev.expense, false, cur));

  const v2 = el("div", "value", money(sum.income, cur));
  const t2 = mk(t("tile_income"), v2);
  t2.append(deltaNode(sum.income, prev.income, true, cur));

  const net = (sum.income ?? 0) - (sum.expense ?? 0);
  const v3 = el("div", "value", `${net >= 0 ? "+" : MINUS}${money(Math.abs(net), cur)}`);
  v3.style.color = net >= 0 ? "var(--good)" : "var(--bad)";
  const t3 = mk(t("tile_net"), v3);

  const t4 = mk(t("tile_count"), el("div", "value", `${sum.count ?? 0}`));

  box.append(t1, t2, t3, t4);
}

/* ---------------- 渲染：分类排行条 ---------------- */

function renderBars(containerId, rows, emptyText, cssVar, totalLabelId, cur) {
  const box = $(containerId);
  box.replaceChildren();
  const list = Array.isArray(rows) ? rows.filter((r) => r.total > 0) : [];
  if (!list.length) {
    const empty = el("div", "empty");
    empty.append(el("div", "empty-icon", "🍃"));
    empty.append(el("div", "", emptyText));
    box.append(empty);
    $(totalLabelId).textContent = "";
    return;
  }
  // 前端自己排序，不依赖后端顺序；最大值取排序后的第一名，保证条形不会溢出
  const sorted = [...list].sort((a, b) => b.total - a.total);
  let top = sorted.slice(0, 8);
  const rest = sorted.slice(8);
  if (rest.length) {
    top = top.concat([
      {
        category: t("other"),
        total: rest.reduce((a, r) => a + r.total, 0),
        count: rest.reduce((a, r) => a + (r.count || 0), 0),
        folded: true,
      },
    ]);
  }
  const grand = list.reduce((a, r) => a + r.total, 0);
  $(totalLabelId).textContent = `${money(grand, cur)} · ${list.length} 类`;
  const max = top[0].total || 1;
  top.forEach((r, idx) => {
    const row = el("div", "bar-row");
    row.dataset.name = r.category;
    row.dataset.total = r.total;
    row.dataset.count = r.count || 0;
    row.dataset.share = grand ? (r.total / grand) * 100 : 0;
    row.dataset.color = cssVar;
    row.tabIndex = 0;
    if (r.folded) {
      row.dataset.folded = "1"; // 聚合行不可作为筛选条件
    } else {
      row.title = t("click_to_filter");
    }
    if (state.category && state.category === r.category) row.classList.add("active");

    const name = el("div", "bar-name");
    name.append(el("span", "bar-rank", `${idx + 1}`));
    name.append(el("span", "name-text", r.category));

    const track = el("div", "bar-track");
    const fill = el("div", "bar-fill");
    fill.style.width = `${Math.max((r.total / max) * 100, 1.5)}%`;
    track.append(fill);

    const value = el("div", "bar-value");
    value.append(document.createTextNode(money(r.total, cur)));
    const share = el("span", "bar-share");
    share.textContent = `${grand ? ((r.total / grand) * 100).toFixed(0) : 0}%`;
    value.append(share);

    row.append(name, track, value);
    box.append(row);
  });
}

/* ---------------- 渲染：每日收支 ---------------- */

function renderDaily() {
  const box = $("daily");
  box.replaceChildren();
  const days = state.stats?.by_day ?? [];
  const cur = state.stats?.currency ?? "¥";
  const hasData = days.some((d) => d.expense > 0 || d.income > 0);
  if (!hasData) {
    const empty = el("div", "empty");
    empty.append(el("div", "empty-icon", "🍃"));
    empty.append(el("div", "", t("empty_daily")));
    box.append(empty);
    return;
  }

  const plot = el("div", "daily-plot");
  const maxVal = niceCeil(Math.max(...days.map((d) => Math.max(d.expense, d.income))));

  // 网格线 + 刻度（0 / 25% / 50% / 75% / 100%）
  for (let i = 4; i >= 1; i--) {
    const line = el("div", "grid-line");
    line.style.bottom = `${(i / 4) * 100}%`;
    const label = el("span", "grid-label", fmtNum((maxVal * i) / 4));
    label.style.bottom = `${(i / 4) * 100}%`;
    plot.append(line, label);
  }
  // 基线 0 刻度（基线本身由 border-bottom 绘制）
  const zero = el("span", "grid-label", "0");
  zero.style.bottom = "-1px";
  plot.append(zero);

  const bands = el("div", "daily-bands");
  const maxExpense = Math.max(...days.map((d) => d.expense));
  const labelEvery = Math.max(1, Math.ceil(days.length / 12));
  days.forEach((d, idx) => {
    const band = el("div", "band");
    band.tabIndex = 0;
    band.dataset.date = d.date;
    band.dataset.expense = d.expense;
    band.dataset.income = d.income;
    band.setAttribute(
      "role",
      "img",
    );
    band.setAttribute(
      "aria-label",
      `${d.date} ${t("legend_expense")} ${d.expense} ${t("legend_income")} ${d.income}`,
    );
    const pair = el("div", "pair");
    const ce = el("div", "col col-expense");
    ce.style.height = `${(d.expense / maxVal) * 100}%`;
    const ci = el("div", "col col-income");
    ci.style.height = `${(d.income / maxVal) * 100}%`;
    pair.append(ce, ci);
    band.append(pair);
    // 选择性直接标注：只标最大支出日（其余交给坐标轴和 tooltip）
    if (d.expense > 0 && d.expense === maxExpense && days.length <= 45) {
      const tag = el("span", "col-label", fmtNum(d.expense));
      ce.append(tag);
    }
    bands.append(band);
  });
  plot.append(bands);
  box.append(plot);

  // x 轴日期标签：等间隔取点；末尾标签若与前一标签相邻则替换前一标签，避免重叠
  const labeled = new Set();
  for (let i = 0; i < days.length; i++) {
    if (i % labelEvery === 0) labeled.add(i);
  }
  const lastIdx = days.length - 1;
  if (!labeled.has(lastIdx)) {
    let prev = lastIdx - 1;
    while (prev >= 0 && !labeled.has(prev)) prev--;
    if (lastIdx - prev <= Math.ceil(labelEvery * 0.6)) labeled.delete(prev);
    labeled.add(lastIdx);
  }
  const labels = el("div", "x-labels");
  days.forEach((d, idx) => {
    const lab = el("div", "x-label");
    if (labeled.has(idx)) lab.textContent = d.date.slice(5); // MM-DD
    labels.append(lab);
  });
  box.append(labels);
}

/* ---------------- 渲染：明细表 ---------------- */

function renderTable() {
  const body = $("records-body");
  body.replaceChildren();
  const rows = state.records ?? [];
  const cur = state.stats?.currency ?? "¥";
  $("records-empty").hidden = rows.length > 0;
  $("records-table").style.display = rows.length ? "" : "none";
  $("records-total-line").textContent = state.total
    ? t("total_records").replace("{n}", `${state.total}`)
    : "";

  for (const r of rows) {
    const tr = el("tr", r.type === "income" ? "income-row" : "expense-row");
    tr.append(el("td", "", fmtDate(r.created_at)));

    const typeCell = el("td");
    const typeWrap = el("span", "type-cell");
    typeWrap.append(
      el("span", `dot ${r.type === "income" ? "dot-income" : "dot-expense"}`),
      el("span", "", r.type === "income" ? t("type_income") : t("type_expense")),
    );
    typeCell.append(typeWrap);
    tr.append(typeCell);

    const amount = el("td", "num amount", `${r.type === "income" ? "+" : MINUS}${money(r.amount, cur)}`);
    tr.append(amount);

    const catCell = el("td");
    const chip = el("button", `cat-chip${state.category === r.category ? " active" : ""}`, r.category || "—");
    chip.type = "button";
    chip.dataset.category = r.category || "";
    chip.title = t("click_to_filter");
    catCell.append(chip);
    tr.append(catCell);

    tr.append(el("td", "note-cell", r.note || "—"));

    const userCell = el("td");
    const userBtn = el("button", "user-link", r.user_name || r.user_id);
    userBtn.type = "button";
    userBtn.dataset.userId = r.user_id;
    userCell.append(userBtn);
    tr.append(userCell);
    tr.append(el("td", "", r.platform || "—"));

    const action = el("td");
    const del = el("button", "btn btn-danger", t("delete"));
    del.type = "button";
    del.dataset.id = r.id;
    action.append(del);
    tr.append(action);
    body.append(tr);
  }
  renderPager();
}

function renderPager() {
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  $("page-info").textContent = `${state.page} / ${pages} · ${state.total}`;
  $("btn-prev").disabled = state.page <= 1;
  $("btn-next").disabled = state.page >= pages;
}

/* ---------------- 筛选器 ---------------- */

function renderUserCards() {
  const strip = $("user-strip");
  strip.replaceChildren();
  // 已选用户不再有记录时回退到「全部用户」
  if (state.userId && !(state.users ?? []).some((u) => u.user_id === state.userId)) {
    state.userId = "";
  }

  // 「全部用户」卡片
  const all = el("button", `user-card${state.userId ? "" : " active"}`);
  all.type = "button";
  all.dataset.userId = "";
  const allAvatar = el("span", "avatar avatar-all", "👥");
  const allInfo = el("span", "user-info");
  allInfo.append(el("span", "u-name", t("all_users")));
  const totalCnt = (state.users ?? []).reduce((a, u) => a + (u.count || 0), 0);
  allInfo.append(el("span", "u-meta", t("n_records").replace("{n}", `${totalCnt}`)));
  all.append(allAvatar, allInfo);
  strip.append(all);

  // 各用户卡片：头像首字 + 名称 + 平台/笔数/累计支出
  (state.users ?? []).forEach((u, i) => {
    const card = el(
      "button",
      `user-card${state.userId === u.user_id ? " active" : ""}`,
    );
    card.type = "button";
    card.dataset.userId = u.user_id;
    card.title = `${u.user_name}（${u.platform || "?"}）`;

    const avatar = el(
      "span",
      `avatar ${i < 8 ? `avatar-c${i}` : "avatar-cx"}`,
      [...(u.user_name || "?")][0]?.toUpperCase() ?? "?",
    );
    const info = el("span", "user-info");
    info.append(el("span", "u-name", u.user_name || u.user_id));
    const meta = el("span", "u-meta");
    meta.append(document.createTextNode(`${u.platform || "?"} · `));
    const exp = el("span", "u-expense");
    exp.textContent = money(u.expense_total ?? 0, state.stats?.currency ?? "¥");
    meta.append(exp, document.createTextNode(` · ${t("n_records").replace("{n}", `${u.count ?? 0}`)}`));
    info.append(meta);
    card.append(avatar, info);
    strip.append(card);
  });
}

function renderCategorySelect() {
  const sel = $("sel-category");
  const prev = state.category;
  const cats = new Set();
  for (const list of Object.values(state.stats?.by_category ?? {})) {
    for (const row of list ?? []) cats.add(row.category);
  }
  const options = [el("option", "", t("all_categories"))];
  options[0].value = "";
  for (const c of [...cats].sort((a, b) => a.localeCompare(b, "zh-CN"))) {
    const opt = el("option", "", c);
    opt.value = c;
    options.push(opt);
  }
  sel.replaceChildren(...options);
  const exists = options.some((o) => o.value === prev);
  sel.value = exists ? prev : "";
  state.category = sel.value;
}

/* ---------------- 数据加载 ---------------- */

async function load() {
  const content = $("content");
  content.classList.add("loading");
  try {
    const base = { days: state.days };
    if (state.userId) base.user_id = state.userId;
    const [users, stats, recs] = await Promise.all([
      bridge.apiGet("users", {}),
      bridge.apiGet("stats", base),
      bridge.apiGet("records", {
        ...base,
        type: state.type,
        category: state.category,
        page: state.page,
        page_size: state.pageSize,
      }),
    ]);
    state.users = users.users ?? [];
    state.stats = stats;
    state.records = recs.records ?? [];
    state.total = recs.total ?? 0;
    renderUserCards();
    renderCategorySelect();
    renderAll();
  } catch (e) {
    toast(`${t("load_error")}: ${e?.message ?? e}`);
  } finally {
    content.classList.remove("loading");
  }
}

function renderAll() {
  const cur = state.stats?.currency ?? "¥";
  renderTiles();
  renderBars(
    "bars-expense",
    state.stats?.by_category?.expense ?? [],
    t("empty_expense"),
    "var(--series-1)",
    "expense-cat-total",
    cur,
  );
  renderBars(
    "bars-income",
    state.stats?.by_category?.income ?? [],
    t("empty_income"),
    "var(--series-2)",
    "income-cat-total",
    cur,
  );
  renderDaily();
  renderTable();
}

/* ---------------- i18n 文案 ---------------- */

function renderTexts() {
  document.title = t("title");
  $("t-title").textContent = t("title");
  $("t-subtitle").textContent = t("subtitle");
  $("btn-export-csv").textContent = t("export_csv");
  $("btn-export-json").textContent = t("export_json");
  $("btn-refresh").textContent = t("refresh");
  $("t-cat-expense").textContent = t("cat_expense");
  $("t-cat-income").textContent = t("cat_income");
  $("t-daily").textContent = t("daily");
  $("t-leg-expense").textContent = t("legend_expense");
  $("t-leg-income").textContent = t("legend_income");
  $("t-records").textContent = t("records");
  $("th-time").textContent = t("th_time");
  $("th-type").textContent = t("th_type");
  $("th-amount").textContent = t("th_amount");
  $("th-category").textContent = t("th_category");
  $("th-note").textContent = t("th_note");
  $("th-user").textContent = t("th_user");
  $("th-source").textContent = t("th_source");
  $("t-empty-records").textContent = t("empty_records");
  $("btn-prev").textContent = t("prev_page");
  $("btn-next").textContent = t("next_page");
  const typeSel = $("sel-type");
  typeSel.options[0].textContent = t("all_types");
  typeSel.options[1].textContent = t("type_expense");
  typeSel.options[2].textContent = t("type_income");
  // 语言切换后重建带数据的组件（保持当前选择）
  if (state.users.length) renderUserCards();
  if (state.stats) renderCategorySelect();
}

/* ---------------- 事件 ---------------- */

function bindEvents() {
  $("seg-days").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-days]");
    if (!btn) return;
    for (const b of $("seg-days").children) b.classList.toggle("active", b === btn);
    state.days = Number(btn.dataset.days);
    state.page = 1;
    load();
  });

  // 用户卡片条：点击切换用户筛选
  $("user-strip").addEventListener("click", (e) => {
    const card = e.target.closest("button[data-user-id]");
    if (!card) return;
    if (state.userId === card.dataset.userId) return;
    state.userId = card.dataset.userId;
    state.page = 1;
    load();
  });

  $("sel-type").addEventListener("change", (e) => {
    state.type = e.target.value;
    state.page = 1;
    load();
  });

  $("sel-category").addEventListener("change", (e) => {
    state.category = e.target.value;
    state.page = 1;
    load();
  });

  // 分类排行条：点击/回车 切换该分类的明细筛选
  const onBarAction = (row) => {
    if (!row || row.dataset.folded) return;
    state.category = state.category === row.dataset.name ? "" : row.dataset.name;
    $("sel-category").value = state.category;
    state.page = 1;
    load();
  };
  for (const boxId of ["bars-expense", "bars-income"]) {
    $(boxId).addEventListener("click", (e) => onBarAction(e.target.closest(".bar-row")));
    $(boxId).addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        const row = e.target.closest(".bar-row");
        if (row) {
          e.preventDefault();
          onBarAction(row);
        }
      }
    });
  }

  $("btn-refresh").addEventListener("click", () => load());

  $("btn-prev").addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      load();
    }
  });

  $("btn-next").addEventListener("click", () => {
    state.page += 1;
    load();
  });

  $("records-body").addEventListener("click", async (e) => {
    // 删除
    const btn = e.target.closest("button[data-id]");
    if (btn) {
      if (!window.confirm(t("confirm_delete"))) return;
      try {
        const res = await bridge.apiPost(`records/${btn.dataset.id}/delete`, {});
        if (res?.deleted) {
          toast(t("deleted"));
          load();
        } else {
          toast(`${t("load_error")}: not found`);
        }
      } catch (err) {
        toast(`${t("load_error")}: ${err?.message ?? err}`);
      }
      return;
    }
    // 点击分类芯片：按该分类筛选
    const chip = e.target.closest("button[data-category]");
    if (chip) {
      const cat = chip.dataset.category;
      if (cat) {
        state.category = state.category === cat ? "" : cat;
        $("sel-category").value = state.category;
        state.page = 1;
        load();
      }
      return;
    }
    // 点击用户名：切换到该用户
    const userBtn = e.target.closest("button[data-user-id]");
    if (userBtn && userBtn.dataset.userId) {
      state.userId = userBtn.dataset.userId;
      state.page = 1;
      load();
    }
  });

  const doExport = async (format) => {
    try {
      await bridge.download(
        "export",
        { days: state.days, user_id: state.userId, format },
        `ledger_${state.days}d.${format}`,
      );
    } catch (err) {
      toast(`${t("load_error")}: ${err?.message ?? err}`);
    }
  };
  $("btn-export-csv").addEventListener("click", () => doExport("csv"));
  $("btn-export-json").addEventListener("click", () => doExport("json"));
}

/* ---------------- 启动 ---------------- */

async function main() {
  await bridge.ready();
  bindEvents();
  bindTooltip();
  renderTexts();
  bridge.onContext(renderTexts);
  await load();
}

main().catch((e) => {
  const content = $("content");
  content.replaceChildren(
    (() => {
      const n = el("div", "empty");
      n.append(el("div", "empty-icon", "⚠️"));
      n.append(el("div", "", `${T.load_error}: ${e?.message ?? e}`));
      return n;
    })(),
  );
});
