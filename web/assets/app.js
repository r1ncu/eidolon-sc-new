/* Localbot-SC 的前端：照着原版（Next.js + Tailwind + shadcn）的结构与样式复刻。
 *
 * 复刻的方式：直接用原版编译后的 Tailwind CSS（/assets/css/app.css）与同名 utility class，
 * 所以颜色、字号、圆角、间距和原版天然一致；这里的 JS 只负责画出同样的 DOM 与行为。
 *
 * 结构（对应原版）：
 *   导航外壳   NavShell   —— 左 72px 导航栏（宽屏）/ 底部 dock（窄屏）
 *   对话       /conversations              —— 列表栏
 *   对话       /conversation/<id>           —— 她的对话页
 *   礼物       /gifts                      —— 礼物页
 *   设置       /settings                    —— 一级列表 + 二级页面
 */

import { getJSON, postJSON, streamTurn } from "/assets/api.js";
import { herVars } from "/assets/color.js";
import { heartIcon, icon, iconFilled } from "/assets/icons.js";
import { T } from "/assets/i18n.js";


/* ------------------------------------------------------------------ 状态 */

const state = {
  userId: "",
  characters: [],
  work: null,
  roster: [],
  selectedId: null,
  conversationId: null,
  affinity: null,
  memories: [],
  messages: [],
  capabilities: {},
  profile: null,
  profileDraft: { address: "", gender: "default", birth_month: null, birth_day: null, timezone: "" },
  dirty: false,
  photos: [],
  draft: "",
  sending: false,
  scsp: false,
  scspBg: false,
  /* SCSP 的聊天背景要不要铺在对话页上；和头像一样只存在这台设备。 */
  reachOuts: null,
  stats: null,
  statsRange: "all",
  statsPending: false,
  statsFailed: false,
  /* 对话列表：这台设备上的隐藏/置顶、搜索框里的字、筛选面板开没开。 */
  prefs: { hidden: [], pinned: [] },
  query: "",
  filterOpen: false,
  statusOpen: false,
  announcementsOpen: false,
  announcements: [],
  announcementsReadAt: null,
  serviceStatus: null,
  listWidth: 320,
  wide: true,
  /* 输入栏「＋」展开的面板开没开（本 fork 的图片上传子菜单）。 */
  panelOpen: false,
  ownKeysEditing: null,
  ownKeysDraft: {},
  callDraft: {},
  /* 历史对话面板开没开；电话键点了以后的说明。 */
  pastOpen: false,
  callNote: "",
  /* 礼物页：读到的状态与目录、抽奖结果、送礼面板、提示、类别筛选、重置卡确认框。 */
  gifts: null,
  giftCatalog: null,
  giftFailure: "",
  giftDrawing: null,
  giftGiving: null,
  giftNotice: null,
  giftFilter: "all",
  giftCardConfirm: false,
  giftSending: false,
  /* 数据包导入读到的文件。 */
  importData: null,
};

const route = { path: "/", category: null, characterId: null };

/* ------------------------------------------------------------------ 工具 */

const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/**
 * 把所有自绘 select / combobox 的浮层一次性收起。
 *
 * 点页面空白、按 Escape、点一个新的触发器时都要做这同一件事——以前这三处各写一份
 * 同样的三行 `querySelectorAll.forEach`，改一处忘一处的风险很高（Escape 漏关
 * combobox-content 那种）。收成一份，调用点只说「关」。
 */
const closeAllSelects = () => {
  document.querySelectorAll('[data-slot="select-content"]').forEach((o) => o.classList.add("hidden"));
  document.querySelectorAll('[data-slot="combobox-content"]').forEach((o) => o.classList.add("hidden"));
  document.querySelectorAll('[role="combobox"]').forEach((o) => o.setAttribute("aria-expanded", "false"));
};

/**
 * 空状态：居中虚线框 + 标题（可选描述、图标、操作）。
 *
 * 以前这套 DOM 在公告、列表、对话、统计、记忆、礼物（×2）七处各写一份，class 字符串
 * 逐字重复。收成一个函数后，class 全部以**字面量**留在源码里（不用 `${变量}` 拼装）——
 * 这是 `tools/check_classes.py` 能逐个校验的前提：它按 `class="([^"]*)"` 正则提取，
 * 含 `${` 的 token 会被跳过。两个布尔开关（`heading` / `full`）走 if 分支，让两套
 * titleClass / 容器 class 都以字面量出现，不靠参数插值。
 */
const emptyState = ({ title, description = "", iconHTML = "", content = "", heading = false, full = false }) => {
  const titleLine = heading
    ? `<div data-slot="empty-title" class="font-heading text-sm font-medium tracking-tight">${title}</div>`
    : `<div data-slot="empty-title" class="text-sm font-medium">${title}</div>`;
  const header = `<div data-slot="empty-header" class="flex max-w-sm flex-col items-center gap-2">
      ${iconHTML ? `<div data-slot="empty-icon" data-variant="icon" class="mb-2 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground [&_svg:not([class*='size-'])]:size-4">${iconHTML}</div>` : ""}
      ${titleLine}
      ${description ? `<div data-slot="empty-description" class="text-sm text-muted-foreground">${description}</div>` : ""}
      ${content ? `<div data-slot="empty-content" class="flex min-w-0 max-w-sm flex-col items-center gap-2 text-sm text-muted-foreground">${content}</div>` : ""}
    </div>`;
  if (full) {
    return `<div data-slot="empty" class="flex h-full w-full min-w-0 flex-1 flex-col items-center justify-center gap-4 rounded-xl border-dashed p-6 text-center text-balance">
      ${header}
    </div>`;
  }
  return `<div data-slot="empty" class="flex w-full min-w-0 flex-1 flex-col items-center justify-center gap-4 rounded-xl border-dashed p-6 text-center text-balance">
    ${header}
  </div>`;
};

/** 两个时刻之间：今天写时刻，昨天写「昨天」，今年写月日，更早写年月日。 */
function listTime(at, now = new Date()) {
  const d = new Date(at);
  const two = (n) => String(n).padStart(2, "0");
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (days === 0) return `${d.getHours()}:${two(d.getMinutes())}`;
  if (days === 1) return T.list.yesterday;
  if (d.getFullYear() === now.getFullYear()) return T.list.monthDay(d.getMonth() + 1, d.getDate());
  return T.list.fullDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
}

const clock = (at) => {
  const d = new Date(at);
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
};

/** 消息区里跨天处的日期条：今天、昨天，今年写月日，更早写年月日。 */
function dayKey(at) {
  const d = new Date(at);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayLabel(at) {
  const d = new Date(at);
  const now = new Date();
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (days === 0) return T.chat.today;
  if (days === 1) return T.list.yesterday;
  if (d.getFullYear() === now.getFullYear()) return T.list.monthDay(d.getMonth() + 1, d.getDate());
  return T.list.fullDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
}

/* ------------------------------------------------------------------ 路由 */

function parseRoute() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/" ) return { path: "/conversations", category: null, characterId: null };
  const settings = /^\/settings\/([^/]+)$/.exec(path);
  if (settings) return { path: "/settings", category: settings[1], characterId: null };
  if (path === "/settings") return { path: "/settings", category: null, characterId: null };
  if (path === "/gifts") return { path: "/gifts", category: null, characterId: null };
  const conv = /^\/conversation\/([^/]+)$/.exec(path);
  if (conv) return { path: "/conversations", category: null, characterId: conv[1] };
  if (path === "/conversations") return { path: "/conversations", category: null, characterId: null };
  return { path: "/conversations", category: null, characterId: null };
}

function go(href) {
  if (href === window.location.pathname) return;
  window.history.pushState({}, "", href);
  render();
  // 礼物页每次进来重读一次：抽奖次数是按天发的，页面停在别处时可能已经到账。
  if (route.path === "/gifts" && state.userId) void loadGifts();
}

window.addEventListener("popstate", () => {
  render();
  if (route.path === "/gifts" && state.userId) void loadGifts();
});

/* ------------------------------------------------------------------ 导航栏 */

const NAV_ITEMS = [
  { key: "conversations", href: "/conversations", icon: "conversations" },
  { key: "gifts", href: "/gifts", icon: "gifts" },
  { key: "settings", href: "/settings", icon: "settings" },
];

/** 当前是哪一项（按网址；二级页面上也算那一项）。 */
function navCurrent() {
  if (route.path === "/gifts") return "gifts";
  if (route.path === "/settings") return "settings";
  return "conversations";
}

/** 原版 NavLinks：图标在上、字在下；当前项图标铺 bg-muted 圆角底、字 foreground 加 medium。 */
function navLinks(className) {
  const current = navCurrent();
  return NAV_ITEMS.map((item) => {
    const isCurrent = item.key === current;
    return `<a href="${item.href}" data-nav="${item.key}"${isCurrent ? ' aria-current="page"' : ""} class="group flex flex-col items-center justify-center text-meta outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${className}">
      <span class="relative flex h-8 w-12 items-center justify-center rounded-full transition-colors ${isCurrent ? "bg-muted text-foreground" : "text-muted-foreground group-hover:bg-muted/60"}">
        ${icon(item.icon, "size-5")}
      </span>
      <span class="${isCurrent ? "font-medium text-foreground" : "text-muted-foreground"}">${T.nav[item.key]}</span>
    </a>`;
  }).join("");
}

/** 原版 NavRail：最左一条 72px，右边一条细线，不能收起；GitHub 仓库链接在最底下。 */
function navRail() {
  return `<nav aria-label="${T.nav.label}" class="flex w-18 shrink-0 flex-col items-center gap-2 border-r bg-background py-3 max-md:hidden">
    ${navLinks("w-16 gap-1 rounded-lg py-1")}
    <div class="flex-1"></div>
    <a href="https://github.com/Sallyn0225/eidolon-sc" target="_blank" rel="noopener noreferrer" role="button" tabindex="0" data-slot="button" aria-label="GitHub" class="group/button inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:hover:bg-muted/50">${icon("github")}</a>
  </nav>`;
}

/** 原版 NavDock：底部一条，上沿一条细线、留出 safe-area；输入框拿到焦点时藏起来。 */
function navDock() {
  return `<nav aria-label="${T.nav.label}" class="grid shrink-0 auto-cols-fr grid-flow-col border-t bg-background pb-[env(safe-area-inset-bottom)] md:hidden" id="nav-dock">
    ${navLinks("h-14 gap-0.5")}
  </nav>`;
}

/* ------------------------------------------------------------------ 对话列表 */

/* 原版 lib/listPreferences.ts：隐藏和置顶只存在这台设备的 localStorage 里，按用户分开存。 */
const prefsKey = () => "eidolon.listPreferences." + (state.userId || "local");

function loadPrefs() {
  try {
    const stored = JSON.parse(localStorage.getItem(prefsKey()) || "{}");
    const known = new Set(state.characters.map((c) => c.id));
    const ids = (v) => (Array.isArray(v) ? v.filter((id) => typeof id === "string" && known.has(id)) : []);
    return { hidden: ids(stored.hidden), pinned: ids(stored.pinned) };
  } catch {
    return { hidden: [], pinned: [] };
  }
}

function keepPrefs() {
  try {
    localStorage.setItem(prefsKey(), JSON.stringify(state.prefs));
  } catch {
    /* 存不下（隐私模式等）：改动只在这次页面里生效。 */
  }
}

/* 原版 listPreferences.ts 的几个纯函数。 */
const without = (list, ids) => list.filter((id) => !ids.includes(id));
const union = (list, ids) => [...list, ...ids.filter((id) => !list.includes(id))];
const setShown = (prefs, ids, shown) =>
  shown ? { ...prefs, hidden: without(prefs.hidden, ids) } : { hidden: union(prefs.hidden, ids), pinned: without(prefs.pinned, ids) };
const showAll = (prefs) => ({ ...prefs, hidden: [] });
const pinOf = (prefs, id) => ({ hidden: without(prefs.hidden, [id]), pinned: union(prefs.pinned, [id]) });
const unpinOf = (prefs, id) => ({ ...prefs, pinned: without(prefs.pinned, [id]) });

/** 改一处整理：存下来、重画。 */
function tidy(fn) {
  state.prefs = fn(state.prefs);
  keepPrefs();
  render();
}

/* 原版 lib/localOwnKeys.ts：自带密钥只存在这台设备的这个浏览器里，按用户分开存。 */
function loadOwnKeys() {
  try {
    const stored = JSON.parse(localStorage.getItem("eidolon.ownKeys." + (state.userId || "local")) || "{}");
    return stored && typeof stored === "object" ? stored : {};
  } catch {
    return {};
  }
}

function keepOwnKeys(keys) {
  try {
    localStorage.setItem("eidolon.ownKeys." + (state.userId || "local"), JSON.stringify(keys));
  } catch {
    /* 存不下：这次只在页面里生效。 */
  }
}

/* 原版 lib/appearance.ts：外观（现在只有「SCSP 头像」一个开关）只存在这台设备上，
   在 localStorage 里按用户分开存（键为 eidolon.appearance.<用户 ID>）。读不到或数据坏了
   当作默认（关）；存不进去时不抛错，改动只留在页面内存里。 */
function appearanceKey() {
  return "eidolon.appearance." + (state.userId || "local");
}

function loadAppearance() {
  try {
    const stored = JSON.parse(localStorage.getItem(appearanceKey()) || "{}");
    return {
      scspAvatars: stored && stored.scspAvatars === true,
      scspBackgrounds: stored && stored.scspBackgrounds === true,
    };
  } catch {
    return { scspAvatars: false, scspBackgrounds: false };
  }
}

function keepAppearance() {
  try {
    localStorage.setItem(
      appearanceKey(),
      JSON.stringify({ scspAvatars: state.scsp, scspBackgrounds: state.scspBg })
    );
  } catch {
    /* 存不下：这次只在页面里生效。 */
  }
}

/** 这一轮随请求带上的自带密钥（原版 turnOwnKeys）。原版只带服务名和密钥、模型配置留在
 *  服务器侧；这里连地址和模型名一起带上，填了就按制作人自己的来，没填的用部署配好的。 */
function turnOwnKeys() {
  const keys = loadOwnKeys();
  const own = {};
  const llm = keys.llm || {};
  if (llm.key) {
    own.llm = { key: llm.key };
    if (llm.base_url) own.llm.base_url = llm.base_url;
    if (llm.model) own.llm.model = llm.model;
  }
  if (keys.vision?.key) own.vision = keys.vision.key;
  if (keys.image?.key) own.image = keys.image.key;
  return Object.keys(own).length ? own : null;
}

/** 原版自带密钥的内联表单：按服务画不同的字段。原版是 ServiceKey 里点「填写」展开的那一块，
 *  字段用 FieldGroup 一组一组排，下面是「保存 / 取消」两个键。
 *
 *  表单在保存之前是「草稿」：原版是受控组件（useState），改了什么显示什么。
 *  这里是重画式渲染，所以草稿放在 state.ownKeysDraft 里，保存时才写进 localStorage。 */
function ownKeysForm(service, keys) {
  const k = T.settings.ownKeys;
  const draft = state.ownKeysDraft[service] || (state.ownKeysDraft[service] = {});
  const input = (id, label, value, placeholder = "", type = "text") =>
    `<div data-slot="field" data-orientation="vertical" class="group/field flex w-full gap-2 flex-col">
      <label data-slot="field-label" for="${id}" class="${fieldLabelClass}">${label}</label>
      <input id="${id}" data-slot="input" type="${type}" value="${esc(value)}" ${placeholder ? `placeholder="${esc(placeholder)}"` : ""} autocomplete="off" class="${inputClass}">
    </div>`;
  const select = (id, label, options, value, field = "") =>
    `<div data-slot="field" data-orientation="vertical" class="group/field flex w-full gap-2 flex-col">
      <label data-slot="field-label" for="${id}-trigger" class="${fieldLabelClass}">${label}</label>
      ${selectTrigger(id, options, value, { field })}
    </div>`;
  const current = keys[service] || {};
  let fields = "";
  if (service === "llm") {
    fields =
      select("ok-protocol", k.protocol, Object.entries(k.protocols).map(([value, label]) => ({ value, label })), draft.protocol ?? current.protocol ?? "chat-completions", "protocol") +
      input("ok-base", k.baseUrl, draft.base_url ?? current.base_url ?? "", "https://api.deepseek.com/v1") +
      input("ok-model", k.model, draft.model ?? current.model ?? "", "deepseek-chat") +
      input("ok-key", k.apiKey, "", current.key ? k.keepApiKey : "", "password");
  } else if (service === "tts") {
    fields =
      select("ok-model", k.ttsModel, Object.entries(k.ttsModels).map(([value, label]) => ({ value, label })), draft.model ?? current.model ?? "s2.1-pro-free", "model") +
      input("ok-key", k.apiKey, "", current.key ? k.keepApiKey : "", "password");
  } else if (service === "stt") {
    fields =
      select("ok-provider", k.sttProvider, Object.entries(k.sttProviders).map(([value, label]) => ({ value, label })), draft.provider ?? current.provider ?? "soniox", "provider") +
      input("ok-key", k.apiKey, "", current.key ? k.keepApiKey : "", "password");
  } else if (service === "image") {
    fields =
      input("ok-base", k.baseUrl, draft.base_url ?? current.base_url ?? "", "https://api.deepseek.com/v1") +
      input("ok-model", k.model, draft.model ?? current.model ?? "", "deepseek-chat") +
      input("ok-key", k.apiKey, "", current.key ? k.keepApiKey : "", "password");
  } else {
    fields =
      input("ok-base", k.baseUrl, draft.base_url ?? current.base_url ?? "", "https://api.deepseek.com/v1") +
      input("ok-model", k.model, draft.model ?? current.model ?? "", "deepseek-flash") +
      input("ok-key", k.apiKey, "", current.key ? k.keepApiKey : "", "password");
  }
  return `<form data-own-form="${service}">
    <div data-slot="field-group" class="${fieldGroupClass}">${fields}</div>
    <div class="flex flex-wrap gap-2 pt-4">
      <button type="button" data-own-cancel="${service}" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 hover:bg-muted hover:text-foreground">${T.common.cancel}</button>
      <button type="button" data-own-save="${service}" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80">${k.save}</button>
    </div>
  </form>`;
}

/* 原版 lib/characterSearch.ts：名字（原名、中文译名、假名、罗马字，姓名两种顺序）和组合名、组合读音都算候选；
   输入和候选先经过同一套折叠（繁简字形、长短音、分隔符），折叠后的输入是某个候选的子串就留下。 */
const GLYPHS = {
  櫻: "樱", 風: "风", 織: "织", 燈: "灯", 宮: "宫", 岡: "冈", 戀: "恋", 鐘: "钟", 鍾: "钟",
  瀬: "濑", 瀨: "濑", 結: "结", 華: "华", 霧: "雾", 倉: "仓", 淺: "浅", 円: "圆", 圓: "圆",
  絲: "糸", 雛: "雏", 緋: "绯", 沢: "泽", 澤: "泽", 優: "优", 愛: "爱", 穂: "穗", 園: "园",
  樹: "树", 凜: "凛", 葉: "叶", 鳩: "鸠", 鈴: "铃", 陽: "阳", 課: "课", 後: "后",
};
const LONG_MARKS = [
  [/[ōô]/g, "o"], [/[ūû]/g, "u"], [/[āâ]/g, "a"], [/[īî]/g, "i"], [/[ēê]/g, "e"],
  [/o[ouh]/g, "o"], [/uu/g, "u"], [/aa/g, "a"], [/ii/g, "i"], [/ee/g, "e"],
];
function fold(text) {
  let folded = String(text || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\s・'’\-·.]/g, "")
    .replace(/[ァ-ヶ]/g, (kana) => String.fromCharCode(kana.charCodeAt(0) - 0x60))
    .replace(/(.)々/gu, "$1$1");
  folded = Array.from(folded, (ch) => GLYPHS[ch] ?? ch).join("");
  for (const [mark, vowel] of LONG_MARKS) folded = folded.replace(mark, vowel);
  return folded;
}
function bothOrders(full) {
  if (!full) return [];
  const parts = full.trim().split(/\s+/);
  return [parts.join(" "), [...parts].reverse().join(" ")];
}
function matchesSearch(c, query) {
  const q = fold(query);
  if (!q) return true;
  const candidates = [c.name, c.name_zh, ...bothOrders(c.name_kana), ...bothOrders(c.name_romaji), c.unit, c.unit_reading].filter(Boolean);
  return candidates.some((one) => fold(one).includes(q));
}

/* 原版 lib/conversationList.ts 的 arrange()：置顶的在前，其余的之间、置顶的之间都按最近往来排；
   从没聊过的排最后并保持配置顺序；搜索时在所有人里搜，隐藏的照样出现。 */
function arrange(keep) {
  const hidden = new Set(state.prefs.hidden);
  const pinned = new Set(state.prefs.pinned);
  const searching = state.query.trim() !== "";
  const lastTalked = (c) => {
    const line = state.roster.find((r) => r.id === c.id);
    return line && line.at ? Date.parse(line.at) : null;
  };
  // 原版 listPreferences 的排序：置顶在最前，然后最近说过的（新的在前），
  // 从来没说过的一律排在后面，彼此之间保持配置里的顺序。
  const rank = (at) => (at == null ? 1 : 0);
  return state.characters
    .filter((c) => (searching ? matchesSearch(c, state.query) : !hidden.has(c.id) || keep.includes(c.id)))
    .map((c) => ({ c, pinned: pinned.has(c.id), hidden: hidden.has(c.id), at: lastTalked(c) }))
    .sort(
      (a, b) =>
        Number(b.pinned) - Number(a.pinned) ||
        rank(a.at) - rank(b.at) ||
        (a.at === b.at || a.at == null || b.at == null ? 0 : b.at - a.at)
    )
    .map((row) => row);
}

/** 按组合分组（原版 unitsOf）：组合按官方顺序，组内保持 characters 原来的顺序；没有组合的在最后。 */
function unitsOf() {
  const groups = new Map();
  const none = [];
  for (const c of state.characters) {
    if (c.unit == null || c.unit_order == null) {
      none.push(c);
      continue;
    }
    const group = groups.get(c.unit_order) ?? { unit: c.unit, members: [] };
    group.members.push(c);
    groups.set(c.unit_order, group);
  }
  const sorted = [...groups.entries()].sort(([a], [b]) => a - b).map(([, group]) => group);
  if (none.length) sorted.push({ unit: null, members: none });
  return sorted;
}

const BUTTON_BASE =
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4";

/* 原版 Button 的 outline 变体：cva 会用 tailwind-merge 把基础里的 border-transparent 换成
   border-border；这里手工做同一件事（CSS 里 .border-transparent 排在 .border-border 之后，
   两个类同时留着的话透明色会赢，边框就看不见了）。 */
const BUTTON_OUTLINE =
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border border-border bg-background bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50";

/** 原版 Checkbox：16px 方角方框，勾了铺 primary；部分勾选（indeterminate）画一道横线。 */
function checkboxEl(checked, indeterminate) {
  const attrs = [`role="checkbox"`, `aria-checked="${indeterminate ? "mixed" : checked}"`];
  if (checked) attrs.push('data-checked=""');
  if (indeterminate) attrs.push('data-indeterminate=""');
  return `<span ${attrs.join(" ")} data-slot="checkbox" class="group/checkbox data-indeterminate:border-primary data-indeterminate:bg-primary data-indeterminate:text-primary-foreground dark:data-indeterminate:bg-primary peer relative flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-input transition-colors outline-none group-has-disabled/field:opacity-50 group-has-[:focus-visible]/field-label:ring-0 group-has-[:focus-visible]/field-label:not-data-checked:border-input after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:bg-input/30 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 data-checked:border-primary data-checked:bg-primary data-checked:text-primary-foreground group-has-[:focus-visible]/field-label:data-checked:border-primary dark:data-checked:bg-primary">
    <span data-slot="checkbox-indicator" class="grid place-content-center text-current transition-none [&>svg]:size-3.5">
      ${icon("check", "group-data-indeterminate/checkbox:hidden")}
      ${icon("minus", "hidden group-data-indeterminate/checkbox:block")}
    </span>
  </span>`;
}

/** 原版 ListFilter 的 Groups：按组合分组的勾选清单，组标题前三态勾选框，组内每人一行勾选框、小头像、名字。 */
function filterGroups() {
  return `<div class="flex flex-col gap-2" id="filter-groups">
    ${unitsOf()
      .map(({ unit, members }) => {
        const ids = members.map((c) => c.id);
        const shownCount = ids.filter((id) => !state.prefs.hidden.includes(id)).length;
        const all = shownCount === ids.length;
        const some = shownCount > 0 && shownCount < ids.length;
        return `<section class="flex flex-col">
          <label class="flex items-center gap-3 py-2 font-medium">
            ${checkboxEl(all, some)}
            ${esc(unit ?? T.list.noUnit)}
          </label>
          <ul>
            ${members
              .map(
                (c) => `<li>
                  <label class="flex items-center gap-3 py-1.5 pl-7">
                    ${checkboxEl(!state.prefs.hidden.includes(c.id), false)}
                    <span aria-hidden class="flex">${avatarEl(c.name, avatarUrlOf(c.id), "size-6")}</span>
                    <span class="min-w-0 truncate">${esc(c.name)}</span>
                  </label>
                </li>`
              )
              .join("")}
          </ul>
        </section>`;
      })
      .join("")}
  </div>`;
}

/** 原版 ListFilter：宽屏是挂在按钮下面的 Popover，按组合分组列出所有人，勾选立即生效。 */
function listFilter(hiddenCount) {
  const label = hiddenCount > 0 ? T.list.filterHidden(hiddenCount) : T.list.filter;
  return `<div class="relative" id="filter-wrap">
    <button type="button" aria-label="${esc(label)}" aria-expanded="${state.filterOpen}" data-slot="popover-trigger" id="filter-button" class="${BUTTON_BASE} size-9 relative hover:bg-muted hover:text-foreground">
      ${icon("filter")}
      ${hiddenCount > 0 ? '<span aria-hidden class="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-muted-foreground"></span>' : ""}
    </button>
    <div id="filter-popover" class="${state.filterOpen ? "" : "hidden"} absolute top-full right-0 z-50 mt-1 flex max-h-[min(32rem,70dvh)] w-72 origin-top-right flex-col gap-2.5 rounded-lg bg-popover p-2.5 text-sm text-popover-foreground shadow-md ring-1 ring-foreground/10">
      <div class="flex flex-row items-center justify-between gap-0.5 text-sm" data-slot="popover-header">
        <span class="font-medium" data-slot="popover-title">${T.list.filterTitle}</span>
        <button type="button" id="filter-show-all" ${hiddenCount === 0 ? "disabled" : ""} class="${BUTTON_BASE} h-7 gap-1.5 px-2.5 text-[0.8rem] hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50">${T.list.showAll}</button>
      </div>
      <div class="-mx-2.5 min-h-0 flex-1 overflow-y-auto px-2.5">
        ${filterGroups()}
      </div>
    </div>
  </div>`;
}

/** 原版 ServiceStatusButton：铃铛左边的服务状态按钮和它打开的小窗口。
 *  数据来自 `/api/service-status`（原版 lib/serviceStatus.ts）：后台按最近几分钟的调用
 * 汇总出来的每项服务状态。拉不到就保持上一次的；一次都没拉到、或者后台一项都没显示时，
 * 按钮不出现。 */
function serviceStatusButton() {
  const status = state.serviceStatus;
  if (!status || !status.services || !status.services.length) return "";
  const rows = status.services.map((row) => [row.service, row.status]);
  const failing = rows.some(([, s]) => s === "down");
  const unsteady = rows.some(([, s]) => s === "unsteady");
  const worst = failing ? "down" : unsteady ? "unsteady" : null;
  return `<div class="relative" id="status-wrap">
    <button type="button" aria-label="${worst ? T.list.serviceStatusFailing : T.list.serviceStatus}" aria-expanded="${state.statusOpen}" data-slot="popover-trigger" id="status-button" class="${BUTTON_BASE} size-9 relative hover:bg-muted hover:text-foreground">
      ${icon("serviceStatus")}
      ${worst ? `<span aria-hidden class="absolute top-1.5 right-1.5 size-1.5 rounded-full ${worst === "down" ? "bg-destructive" : "bg-warning"}"></span>` : ""}
    </button>
    <div id="status-popover" class="${state.statusOpen ? "" : "hidden"} absolute top-full right-0 z-50 mt-1 flex w-60 flex-col gap-2.5 rounded-lg bg-popover p-2.5 text-sm text-popover-foreground shadow-md ring-1 ring-foreground/10">
      <div class="flex flex-col gap-0.5 text-sm" data-slot="popover-header">
        <span class="font-medium" data-slot="popover-title">${T.list.serviceStatus}</span>
      </div>
      <ul class="flex flex-col gap-1.5">
        ${rows
          .map(
            ([service, status]) => `<li class="flex items-center justify-between gap-3">
              <span>${T.list.serviceStatusNames[service] || service}</span>
              <span class="${status === "ok" || status === "idle" ? "text-muted-foreground" : "flex items-center gap-1.5 font-medium"}">${T.list.serviceStatuses[status] || status}</span>
            </li>`
          )
          .join("")}
      </ul>
      <p class="text-meta text-muted-foreground">${T.list.serviceStatusWindow(status.window_minutes || 60)}</p>
    </div>
  </div>`;
}

/** 原版 Announcements：页首的铃铛和它打开的公告弹窗。这台部署没有公告：空列表，弹窗里写「还没有公告」。 */
function announcementsButton() {
  const unread = state.announcements.length > 0 && state.announcementsReadAt === null;
  return `<div class="relative" id="announcements-wrap">
    <button type="button" aria-label="${unread ? T.list.announcementsUnread : T.list.announcements}" aria-expanded="${state.announcementsOpen}" data-slot="dialog-trigger" id="announcements-button" class="${BUTTON_BASE} size-9 relative hover:bg-muted hover:text-foreground">
      ${icon("announcements")}
      ${unread ? '<span aria-hidden class="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-foreground"></span>' : ""}
    </button>
    <div id="announcements-dialog" class="${state.announcementsOpen ? "" : "hidden"} fixed inset-0 z-50 flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/50" data-close="announcements"></div>
      <div class="relative flex max-h-[calc(100dvh-2rem)] w-full max-w-md flex-col gap-4 rounded-xl border bg-background p-6 shadow-lg">
        <div class="flex flex-col gap-0.5" data-slot="dialog-header">
          <h2 class="text-lg font-semibold" data-slot="dialog-title">${T.list.announcements}</h2>
        </div>
        ${emptyState({ title: T.list.noAnnouncements })}
        <div class="flex flex-row justify-end gap-2" data-slot="dialog-footer">
          <button type="button" data-close="announcements" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 hover:bg-muted hover:text-foreground">${T.common.close || "关闭"}</button>
          <button type="button" data-close="announcements" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80" disabled>${T.list.markAnnouncementsRead}</button>
        </div>
      </div>
    </div>
  </div>`;
}

/** 一行的右键 / 长按菜单（原版 ContextMenuContent）：置顶或取消置顶，在列表中隐藏或显示。 */
function rowMenu(c) {
  const isPinned = state.prefs.pinned.includes(c.id);
  const isHidden = state.prefs.hidden.includes(c.id);
  const item = (action, label, iconName) =>
    `<button type="button" data-menu="${action}" data-character="${esc(c.id)}" data-slot="context-menu-item" class="group/context-menu-item relative flex w-full cursor-default items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-sm outline-hidden select-none hover:bg-accent hover:text-accent-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4">${icon(iconName)}${label}</button>`;
  return `<div data-slot="context-menu-content" class="fixed z-50 hidden min-w-36 rounded-lg bg-popover p-1 text-popover-foreground shadow-md ring-1 ring-foreground/10" id="row-menu">
    ${item(isPinned ? "unpin" : "pin", isPinned ? T.list.unpin : T.list.pin, isPinned ? "unpin" : "pin")}
    ${item(isHidden ? "show" : "hide", isHidden ? T.list.show : T.list.hide, isHidden ? "show" : "hide")}
  </div>`;
}

/** 列表里的一行（原版 Row）：一整个按钮，选中时 aria-current、铺 bg-muted；置顶的时间旁边有个小图钉。 */
function listRow(entry) {
  const { c, pinned, hidden } = entry;
  const line = state.roster.find((r) => r.id === c.id);
  const preview = line && line.preview ? line.preview : "";
  // 原版 previewOf：有对话记录就返回 preview（哪怕还没说话，这时 at 是对话开始的时间），
  // 从没开过对话才是 null —— 那时既没有时间也没有预览。
  const hasConversation = Boolean(line && (preview || line.fresh));
  const isSelected = c.id === state.selectedId;
  const avatar = avatarEl(c.name, avatarUrlOf(c.id), "size-list-avatar");
  const time = hasConversation ? listTime(line.at) : "";
  return `<li data-row="${esc(c.id)}">
    <button type="button" data-character="${esc(c.id)}"${isSelected ? ' aria-current="page"' : ""} class="flex w-full items-center gap-3 px-4 py-2.5 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset ${isSelected ? "bg-muted" : "hover:bg-muted/60"}">
      ${avatar}
      <span class="flex min-w-0 flex-1 flex-col gap-0.5">
        <span class="flex items-baseline gap-2">
          <span class="min-w-0 flex-1 truncate text-row-name">${esc(c.name)}</span>
          ${hasConversation || pinned ? `<span class="flex shrink-0 items-center gap-1 text-meta text-muted-foreground tabular-nums">${hasConversation ? esc(time) : ""}${pinned ? `${icon("pin", "size-3")}<span class="sr-only">${T.list.pinned}</span>` : ""}</span>` : ""}
        </span>
        <span class="truncate text-preview text-muted-foreground tabular-nums">${esc(preview || (line && line.fresh ? T.list.nothingSaidYet : T.list.neverTalked))}</span>
      </span>
    </button>
    ${rowMenu(c)}
  </li>`;
}

function conversationList() {
  const keep = [state.selectedId].filter(Boolean);
  const shown = arrange(keep);
  const hiddenCount = state.prefs.hidden.length;
  const searching = state.query.trim() !== "";
  const showsHidden = !searching && hiddenCount > 0;
  let body;
  if (shown.length === 0 && showsHidden) {
    body = emptyState({
      title: T.list.allHidden,
      content: `<button type="button" id="open-filter-empty" class="${BUTTON_BASE} h-8 gap-1.5 border-border bg-background px-2.5 hover:bg-muted hover:text-foreground">${T.list.chooseShown}</button>`,
    });
  } else if (shown.length === 0) {
    body = `<p class="px-4 py-6 text-center text-preview text-muted-foreground">${T.list.noMatch}</p>`;
  } else {
    body = `<ul aria-label="${T.list.title}">${shown.map(listRow).join("")}</ul>
      ${showsHidden ? `<button type="button" id="open-filter-hidden" class="w-full px-4 py-3 text-left text-meta text-muted-foreground outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">${T.list.hiddenCount(hiddenCount)}</button>` : ""}`;
  }
  return `<div class="flex h-full flex-col bg-background text-foreground">
    <header class="flex shrink-0 items-center gap-2 pt-3 pr-2 pb-2 pl-4">
      <h1 class="flex-1 text-list-title">${T.list.title}</h1>
      ${serviceStatusButton()}
      ${announcementsButton()}
    </header>
    <div class="flex shrink-0 items-center gap-1 pr-2 pb-2 pl-4">
      <label class="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg bg-muted px-3 text-muted-foreground has-focus-visible:ring-3 has-focus-visible:ring-ring/50">
        ${icon("search", "size-4 shrink-0")}
        <input type="search" id="list-search" value="${esc(state.query)}" placeholder="${T.list.search}" aria-label="${T.list.search}" class="min-w-0 flex-1 bg-transparent text-body text-foreground outline-none placeholder:text-muted-foreground">
      </label>
      ${listFilter(hiddenCount)}
    </div>
    <div class="min-h-0 flex-1 overflow-y-auto">${body}</div>
  </div>`;
}

/* ------------------------------------------------------------------ 对话页 */

/** 头像：原版 Avatar 组件的结构（Root / Image / Fallback），没有图时显示名字的第一个字。
 *  原版 Avatar 的基础类里有 size-8，MarkedAvatar 再用 tw-merge 把它换成 size-bubble-avatar
 *  （列表 56px、气泡旁 36px、小处 24px）。这里没有 tw-merge，所以尺寸类只留一个。 */
function avatarEl(name, src, size) {
  const image = src
    ? `<img data-slot="avatar-image" src="${src}" alt="${esc(name)}" class="aspect-square size-full rounded-full object-cover">`
    : "";
  const fallback = src
    ? ""
    : `<span data-slot="avatar-fallback" class="flex size-full items-center justify-center rounded-full bg-muted text-sm text-muted-foreground">${esc(Array.from(name)[0] || "")}</span>`;
  return `<span data-slot="avatar" data-size="default" class="group/avatar relative flex shrink-0 rounded-full select-none ${size}">${image}${fallback}</span>`;
}

/** 电话键画不画，由服务端说了算（`capabilities.call`）。
 *
 * 这一版没有通话后端：语音合成和判断模型都没配，点开只会得到「这一版还没有开放」。
 * 一个按了只会道歉的键比没有键更糟——它看起来像坏了，而且每周都会让人再问一次。
 */
function callEnabled() {
  const call = state.capabilities && state.capabilities.call;
  return !!(call && call.enabled);
}

function avatarFor(character) {
  const src = state.scsp && character.scsp_avatar_url ? character.scsp_avatar_url : character.avatar_url;
  return avatarEl(character.name, src, "size-bubble-avatar");
}

/** 消息区（原版 Messages + MessageScroller）：打开时停在最底；消息从顶部开始排，气泡之间 4px，换人时多 8px。 */
function messageArea(character) {
  const items = state.messages;
  if (items.length === 0) {
    return emptyState({
      full: true,
      title: T.chat.emptyTitle(character.name),
      description: callEnabled() ? T.chat.emptyDescription : T.chat.emptyDescriptionTyping,
    });
  }
  const blocks = [];
  let group = null;
  let lastDay = null;
  for (const m of items) {
    const who = m.role === "user" ? "me" : "her";
    const day = dayKey(m.at);
    // 跨天：先放一条日期条，然后另起一组。
    if (day !== lastDay) {
      group = null;
      lastDay = day;
      blocks.push({ kind: "day", label: dayLabel(m.at) });
    }
    if (!group || group.who !== who) {
      group = { who, items: [] };
      blocks.push(group);
    }
    group.items.push(m);
  }
  const items2 = blocks
    .map((block) => {
      if (block.kind === "day") {
        return `<div data-slot="message-scroller-item" class="my-3 flex min-w-0 shrink-0 justify-center [contain-intrinsic-size:auto_10rem] [content-visibility:auto]">
          <span class="rounded-full bg-foreground/10 px-3 py-1 text-meta text-muted-foreground">${esc(block.label)}</span>
        </div>`;
      }
      const isHer = block.who === "her";
      return block.items
        .map((m, i) => {
          const first = i === 0;
          const last = i === block.items.length - 1;
          const time = `<time class="shrink-0 pb-0.5 text-meta text-muted-foreground tabular-nums">${clock(m.at)}</time>`;
          if (!isHer) {
            return `<div data-slot="message-scroller-item" class="min-w-0 shrink-0 [contain-intrinsic-size:auto_10rem] [content-visibility:auto]${first ? " mt-2" : ""}">
            <div data-slot="message" data-align="end" class="group/message relative flex w-full min-w-0 gap-2 text-sm data-[align=end]:flex-row-reverse">
              <div class="flex min-w-0 flex-1 items-end justify-end gap-1.5">
                ${last ? time : ""}
                <div data-slot="bubble" data-variant="producer" data-align="end" ${!first ? "data-joins-previous" : ""} ${!last ? "data-joins-next" : ""} class="group/bubble relative flex w-fit max-w-[72%] min-w-0 flex-col gap-1 self-end *:data-[slot=bubble-content]:rounded-bubble *:data-[slot=bubble-content]:bg-producer-ink *:data-[slot=bubble-content]:px-4 *:data-[slot=bubble-content]:text-on-producer-ink data-joins-previous:data-[align=end]:*:data-[slot=bubble-content]:rounded-tr-md data-joins-next:data-[align=end]:*:data-[slot=bubble-content]:rounded-br-md">
                  <div data-slot="bubble-content" class="w-fit max-w-full min-w-0 overflow-hidden rounded-xl border border-transparent px-3 py-2 text-sm leading-relaxed wrap-break-word group-data-[align=end]/bubble:self-end">
                    <div class="flex flex-col gap-2">
                      ${m.photos && m.photos.length ? m.photos.map((p) => `<img src="${p}" alt="" class="max-h-64 w-auto max-w-full rounded-lg object-contain">`).join("") : ""}
                      ${m.text ? `<p class="text-body whitespace-pre-wrap">${esc(m.text)}</p>` : ""}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>`;
          }
          const segments = (m.segments || []).map(
            (seg) => `<div class="flex flex-col gap-0.5">
              <p class="text-japanese-line font-japanese">${esc(seg[0])}</p>
              ${seg[1] ? `<p class="text-subtitle text-muted-foreground">${esc(seg[1])}</p>` : ""}
            </div>`
          );
          return `<div data-slot="message-scroller-item" class="min-w-0 shrink-0 [contain-intrinsic-size:auto_10rem] [content-visibility:auto]${first ? " mt-2" : ""}">
            <div data-slot="message" data-align="start" class="group/message relative flex w-full min-w-0 gap-2 text-sm data-[align=end]:flex-row-reverse">
              ${first ? avatarFor(character) : '<span aria-hidden class="w-bubble-avatar shrink-0"></span>'}
              <div data-slot="message-content" class="flex w-full min-w-0 flex-col gap-2.5 wrap-break-word group-data-[align=end]/message:*:data-slot:self-end gap-1">
                ${first ? `<div data-slot="message-header" class="flex max-w-full min-w-0 items-center px-3 text-xs font-medium text-muted-foreground group-has-data-[variant=ghost]/message:px-0 px-1">${esc(character.name)}</div>` : ""}
                <div class="flex min-w-0 items-end gap-1.5">
                  <div data-slot="bubble" data-variant="her" data-align="start" ${!first ? "data-joins-previous" : ""} ${!last ? "data-joins-next" : ""} class="group/bubble relative flex w-fit max-w-[min(30rem,72vw)] min-w-0 flex-col gap-1 *:data-[slot=bubble-content]:rounded-bubble *:data-[slot=bubble-content]:bg-bubble-her *:data-[slot=bubble-content]:px-4 *:data-[slot=bubble-content]:text-foreground data-joins-previous:data-[align=start]:*:data-[slot=bubble-content]:rounded-tl-md data-joins-next:data-[align=start]:*:data-[slot=bubble-content]:rounded-bl-md">
                    <div data-slot="bubble-content" class="w-fit max-w-full min-w-0 overflow-hidden rounded-xl border border-transparent px-3 py-2 text-sm leading-relaxed wrap-break-word group-data-[align=end]/bubble:self-end">
                      ${segments.length ? `<div class="flex flex-col gap-2">${segments.join("")}</div>` : `<span class="my-1 text-muted-foreground">…</span>`}
                    </div>
                  </div>
                  ${last ? time : ""}
                </div>
              </div>
            </div>
          </div>`;
        })
        .join("");
    })
    .join("");
  return `<div data-slot="message-scroller" class="group/message-scroller relative flex min-h-0 flex-1 flex-col overflow-hidden">
    <div data-slot="message-scroller-viewport" class="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain" id="message-viewport" role="region" aria-label="${T.chat.messages}" tabindex="0">
      <div data-slot="message-scroller-content" class="flex h-max min-h-full flex-col gap-1 px-3 pt-2 pb-4">${items2}</div>
    </div>
  </div>`;
}

/** 输入栏：左边「＋」展开一个面板（图片上传的子菜单），中间输入框，右边发送键。 */
function composer(character) {
  const attachments = state.photos
    .map(
      (p) => `<li class="relative">
        <img src="${p.url}" alt="" class="size-16 rounded-xl border border-border object-cover">
        <button type="button" aria-label="${T.chat.removePicture}" data-remove-photo="${p.id}" class="absolute -right-1.5 -top-1.5 inline-flex size-6 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 bg-secondary text-secondary-foreground shadow-sm [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3">${icon("remove", "size-3")}</button>
      </li>`
    )
    .join("");
  const canSend = state.draft.trim() !== "" || state.photos.length > 0;
  // 冷却中：她不想说话，输入栏换成一句说明（原版 coolingComposer）。
  const cooling = state.affinity && state.affinity.cooling ? state.affinity.cooldown_until : null;
  if (cooling) {
    return `<div class="flex shrink-0 flex-col gap-2 border-t bg-background px-3 py-3">
      <p class="flex items-center gap-2 text-sm text-muted-foreground">
        ${icon("cooldown", "size-4")}
        <span class="min-w-0 flex-1">${esc(T.chat.affinity.coolingComposer(character.name, nextDrawText(cooling)))}</span>
        <a href="/gifts" class="shrink-0 underline">${T.gifts.giveHer}</a>
      </p>
    </div>`;
  }
  return `<form id="composer" class="flex shrink-0 flex-col gap-2 border-t bg-background px-3 py-2">
    ${attachments ? `<ul class="flex flex-wrap items-end gap-2" aria-label="${T.chat.attached(state.photos.length)}">${attachments}</ul>` : ""}
    <div id="attach-panel" class="${state.panelOpen ? "grid" : "hidden"} grid-cols-4 gap-2">
      <button type="button" id="pick-picture" class="flex flex-col items-center gap-1.5 rounded-xl border border-border bg-muted/40 px-2 py-3 text-meta text-muted-foreground hover:bg-muted hover:text-foreground">
        ${icon("picture", "size-6")}
        <span>${T.chat.picture}</span>
      </button>
    </div>
    <input type="file" id="file-picker" accept="image/*" multiple hidden>
    <div class="flex items-end gap-2">
      <button type="button" id="attach-toggle" aria-label="${T.chat.attach}" aria-expanded="${state.panelOpen}" class="group/button inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground">${icon("plus", "size-5")}</button>
      <textarea id="draft" rows="1" placeholder="${T.chat.placeholder}" aria-label="${T.chat.placeholder}" class="flex min-h-9 w-full flex-1 resize-none rounded-xl border border-input bg-transparent px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50">${esc(state.draft)}</textarea>
      <button type="submit" id="send" aria-label="${T.chat.send}"${canSend ? "" : " disabled"} class="group/button inline-flex size-9 shrink-0 items-center justify-center rounded-full border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 bg-her-button text-her-button-fg hover:bg-her-button/80 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4">${icon("send", "size-5")}</button>
    </div>
  </form>`;
}

function conversationView(character) {
  const affinity = state.affinity;
  const affinityLabel = affinity ? T.chat.affinity.tiers[affinity.tier] || affinity.tier_label || "" : "";
  // 原版 AffinityBadge：电话键右边、更多菜单左边的 ghost 键，心形（她的颜色）加档名；点开是说明。
  const affinityMark = affinity
    ? `<div class="relative" id="affinity-wrap">
        <button type="button" aria-label="${esc(T.chat.affinity.label(affinityLabel))}" aria-expanded="false" id="affinity-button" class="group/button inline-flex h-9 min-w-0 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-transparent bg-clip-padding px-2.5 text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground max-md:h-11">
          <span aria-hidden class="relative flex shrink-0">${heartIcon(affinity.tier, "size-5")}</span>
          <span class="truncate tabular-nums">${esc(affinityLabel)}</span>
        </button>
        <div id="affinity-popover" class="absolute top-full right-0 z-50 mt-1 hidden w-72 flex-col gap-2.5 rounded-lg bg-popover p-2.5 text-sm text-popover-foreground shadow-md ring-1 ring-foreground/10">
          <div class="flex flex-col gap-0.5">
            <p class="font-medium">${esc(T.chat.affinity.title(affinityLabel))}</p>
            <p class="text-muted-foreground">${esc(affinity.note || T.chat.affinity.notes[affinity.tier] || "")}</p>
          </div>
          <ol class="flex gap-1" aria-label="${esc(T.chat.affinity.tiersLabel)}">
            ${Object.keys(T.chat.affinity.tiers)
              .map((tier) => {
                const current = tier === affinity.tier;
                return `<li${current ? ' aria-current="true"' : ""} class="flex-1 rounded-md py-1 text-center text-meta ${current ? "bg-foreground text-background" : "bg-muted text-muted-foreground"}">${T.chat.affinity.tiers[tier]}</li>`;
              })
              .join("")}
          </ol>
          <p class="text-meta text-muted-foreground">${T.chat.affinity.rule}</p>
        </div>
      </div>`
    : "";
  const phoneKey = callEnabled()
    ? `<button type="button" aria-label="${esc(T.chat.call(character.name))}" title="${T.chat.callTooltip}" class="group/button inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground" id="call-button">${icon("phone", "size-5 text-her-mark")}</button>`
    : "";
  // SCSP 聊天背景：铺在 frame 自己的 background 上，不加图层——加绝对定位的子层会盖住
  // 气泡，或者要给它后面每一块都补 z-index。顶部压一层她的印象色，名字才看得清。
  const wallpaper = state.scspBg && character.background_url ? character.background_url : "";
  const paper = wallpaper
    ? ";background-image:linear-gradient(to bottom,color-mix(in srgb,var(--her-wall) 82%,transparent) 0,color-mix(in srgb,var(--her-wall) 34%,transparent) 10%,transparent 30%),url('" +
      wallpaper +
      "');background-size:auto,cover;background-position:top,center top;background-repeat:no-repeat;"
    : "";
  return `<div id="chat-frame" style="${herVars(character.color)}${paper}" class="relative flex h-full flex-col overflow-hidden bg-her-wall text-foreground">
    <header class="flex h-14 shrink-0 items-center gap-1 px-2">
      <a href="/conversations" aria-label="${T.chat.back}" class="group/button inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground md:hidden">${icon("back", "size-6")}</a>
      <div class="flex min-w-0 flex-1 flex-col px-2">
        <h1 class="truncate text-header">${esc(character.name)}</h1>
      </div>
      ${phoneKey}
      ${affinityMark}
      <div class="relative">
        <button type="button" id="more-button" aria-label="${T.chat.menu}" aria-expanded="false" class="group/button inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground">${icon("more", "size-5")}</button>
        <div id="more-menu" class="absolute top-full right-0 z-50 mt-1 hidden min-w-32 flex-col overflow-x-hidden overflow-y-auto rounded-lg bg-popover p-1 text-popover-foreground shadow-md ring-1 ring-foreground/10">
          <button type="button" data-action="new-conversation" class="group/dropdown-menu-item relative flex w-full cursor-default items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-sm outline-hidden select-none hover:bg-accent hover:text-accent-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4">${icon("newConversation", "size-4")}<span>${T.chat.newConversation}</span></button>
          <button type="button" data-action="past-conversations" class="group/dropdown-menu-item relative flex w-full cursor-default items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-sm outline-hidden select-none hover:bg-accent hover:text-accent-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4">${icon("pastConversations", "size-4")}<span>${T.chat.past.open}</span></button>
        </div>
      </div>
    </header>
    ${messageArea(character)}
    ${composer(character)}
    ${pastPanel(character)}
  </div>`;
}

/** 原版 PastConversations：更多菜单里「历史对话」打开的面板，列出这位角色的所有对话，最新的在上面。 */
function pastPanel(character) {
  if (!state.pastOpen) return "";
  const rows = (state.pastList || []).map((row) => {
    const isCurrent = row.id === state.conversationId;
    const preview = row.preview || "";
    return `<div class="flex items-center gap-1 rounded-lg pr-2 ${isCurrent ? "bg-muted" : "hover:bg-muted/60"}">
      <button type="button" data-past-row="${esc(row.id)}"${isCurrent ? ' aria-current="true"' : ""} class="flex min-h-14 min-w-0 flex-1 flex-col gap-0.5 rounded-lg px-3 py-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
        <span class="flex items-center gap-2">
          <span class="min-w-0 flex-1 truncate text-sm font-medium tabular-nums">${esc(listTime(row.at))}</span>
          ${isCurrent ? `<span class="shrink-0 rounded-md bg-secondary px-1.5 py-0.5 text-meta text-secondary-foreground">${T.chat.past.current}</span>` : ""}
        </span>
        <span class="truncate text-preview text-muted-foreground tabular-nums">${esc(preview || T.list.neverTalked)}</span>
      </button>
      ${isCurrent ? '<span aria-hidden class="size-7 shrink-0"></span>' : `<button type="button" data-past-delete="${esc(row.id)}" aria-label="${esc(T.chat.past.removeLabel(listTime(row.at)))}" class="inline-flex size-7 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 text-muted-foreground hover:bg-muted hover:text-foreground">${icon("trash", "size-4")}</button>`}
    </div>`;
  });
  return `<div class="absolute inset-0 z-40 flex items-end justify-center p-4" id="past-layer">
    <div class="absolute inset-0 bg-black/40" data-close="past"></div>
    <div class="relative flex max-h-[70dvh] w-full max-w-md flex-col gap-2 rounded-xl border bg-background p-4 shadow-lg">
      <div class="flex items-center gap-2">
        <h2 class="min-w-0 flex-1 truncate text-header">${T.chat.past.title}</h2>
        <button type="button" data-close="past" aria-label="${T.common.close}" class="group/button inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground">${icon("close", "size-5")}</button>
      </div>
      <p class="text-subtitle text-muted-foreground">${T.chat.past.description(character.name)}</p>
      <div class="min-h-0 flex-1 overflow-y-auto">
        ${rows.join("") || `<p class="px-3 py-6 text-center text-preview text-muted-foreground">${T.chat.past.onlyCurrent}</p>`}
      </div>
    </div>
  </div>`;
}

/** 原版 lib/shareCard.ts 的分享图内容：称呼、认识天数、最常来的时段、这一跨度的几个数。 */
function shareCardHTML() {
  const stats = state.stats;
  const t = T.settings.stats;
  if (!stats) return `<p class="text-body text-muted-foreground">${t.share.failed}</p>`;
  const address = (state.profile && state.profile.address) || t.share.producer;
  const shown = statsSections(stats);
  const top = shown.portrait ? portraitText(stats.all_hours) : "";
  return `<div style="background:var(--background);color:var(--foreground)" class="flex flex-col gap-4 p-6">
    <p class="text-header">${esc(address)}</p>
    <dl class="grid grid-cols-3 gap-3">
      ${figure(t.daysKnown, stats.days_known, t.days)}
      ${shown.visits ? figure(t.currentVisits, stats.visits.current, t.days) : ""}
      ${shown.visits ? figure(t.longestVisits, stats.visits.longest, t.days) : ""}
    </dl>
    ${top ? `<p class="text-body text-muted-foreground">${esc(top)}</p>` : ""}
    <dl class="grid grid-cols-3 gap-3">
      ${figure(t.turns, stats.range.turns)}
      ${figure(t.calls, stats.range.calls.count, t.timesUnit)}
    </dl>
    <p class="text-meta text-muted-foreground">${esc(T.settings.about.site)}</p>
  </div>`;
}

/* ------------------------------------------------------------------ 设置 */

const SETTINGS_GROUPS = [
  ["profile", "world", "memories", "stats"],
  ["call", "reach-outs", "appearance", "own-keys"],
  ["sign-in", "data-package"],
  ["about"],
];

const SETTINGS_ICONS = {
  profile: "profile",
  world: "about",
  memories: "memories",
  stats: "stats",
  call: "phone",
  "reach-outs": "reachOuts",
  appearance: "appearance",
  "own-keys": "ownKeys",
  "sign-in": "signIn",
  "data-package": "dataPackage",
  about: "about",
};

/** 原版 settingsGroups(reachOuts)：部署者没开搭话时，一级列表里没有「搭话」这一类。 */
function settingsGroups() {
  return SETTINGS_GROUPS.map((group) =>
    group.filter(
      (category) =>
        (category !== "reach-outs" || state.reachOuts !== null) &&
        // 没有通话后端就整段不出现：麦克风、通话音质、电话质感这些设置项，
        // 对一个打不出去的电话没有意义。
        (category !== "call" || callEnabled())
    )
  );
}

function settingsRow(category, summary, isCurrent) {
  const href = `/settings/${category}`;
  const isCurrentPage = route.category === category;
  return `<a href="${href}" data-category="${category}"${isCurrentPage ? ' aria-current="page"' : ""} class="flex min-h-12 w-full items-center gap-3 px-4 py-2.5 text-body outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset ${isCurrent ? "md:bg-muted md:hover:bg-muted" : ""}">
    ${icon(SETTINGS_ICONS[category], "size-5 shrink-0 text-muted-foreground")}
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="truncate">${T.settings.categories[category]}</span>
      ${summary ? `<span class="truncate text-subtitle text-muted-foreground">${esc(summary)}</span>` : ""}
    </span>
    ${icon("forward", "size-4 shrink-0 text-muted-foreground md:hidden")}
  </a>`;
}

/** 原版 sameDraft：草稿和存下的不一样，就是「有修改未保存」。 */
function sameDraft() {
  const saved = state.profile || {};
  const draft = state.profileDraft;
  return (
    (saved.address || "") === (draft.address || "") &&
    (saved.gender || "default") === (draft.gender || "default") &&
    (saved.timezone || "") === (draft.timezone || "") &&
    (saved.birth_month ?? null) === (draft.birth_month ?? null) &&
    (saved.birth_day ?? null) === (draft.birth_day ?? null)
  );
}

function settingsSummary(category) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  switch (category) {
    case "profile": {
      if (!sameDraft()) return T.settings.summaries.profileUnsaved;
      const saved = state.profile || {};
      const timezone = saved.timezone || T.settings.summaries.timezoneAuto(tz);
      return `${saved.address || T.settings.summaries.addressDefault} · ${timezone}`;
    }
    case "world": {
      const w = state.work;
      if (!w || !w.named) return T.settings.summaries.worldNone;
      return T.settings.summaries.worldShare(w.words || 0);
    }
    case "memories":
      return state.memories.length === 0 ? T.settings.summaries.noMemories : T.settings.summaries.memories(state.memories.length);
    case "stats":
      return state.stats ? T.settings.summaries.daysKnown(state.stats.days_known) : null;
    case "call": {
      const c = T.settings.call;
      const quality = c.qualities[state.callDraft.quality || "standard"] || c.qualities.standard;
      return `${quality} · ${T.settings.summaries.phoneTextureOff}`;
    }
    case "reach-outs":
      return state.reachOuts ? T.settings.summaries.reachOutsOn : T.settings.summaries.reachOutsOff;
    case "appearance": {
      const avatars = state.scsp ? T.settings.summaries.scspAvatarsOn : T.settings.summaries.scspAvatarsOff;
      const wall = state.scspBg ? T.settings.summaries.scspBackgroundsOn : T.settings.summaries.scspBackgroundsOff;
      return `${avatars} · ${wall}`;
    }
    case "own-keys":
      return T.settings.summaries.ownKeysNone;
    case "sign-in":
      return null;
    default:
      return null;
  }
}

function settingsLevelOne() {
  const current = route.category || "profile";
  const groups = settingsGroups()
    .map((group, i) => {
      const rows = group
        .map((category) => `<li>${settingsRow(category, settingsSummary(category), category === current)}</li>`)
        .join("");
      return `${i > 0 ? '<div data-orientation="horizontal" role="separator" aria-orientation="horizontal" data-slot="separator" class="shrink-0 bg-border data-horizontal:h-px data-horizontal:w-full data-vertical:w-px data-vertical:self-stretch my-1"></div>' : ""}<ul>${rows}</ul>`;
    })
    .join("");
  return `<nav aria-label="${T.settings.title}" class="flex w-80 shrink-0 flex-col border-r max-md:w-full max-md:border-r-0 ${route.category ? "max-md:hidden" : ""}">
    <header class="flex min-h-14 shrink-0 items-center pt-3 pr-2 pb-2 pl-4">
      <h1 class="text-list-title">${T.settings.title}</h1>
    </header>
    <div class="min-h-0 flex-1 overflow-y-auto pb-4">${groups}</div>
  </nav>`;
}

function settingsPane(category, end) {
  const body = settingsPanelBody(category);
  return `<div class="flex flex-col bg-background text-foreground h-full">
    <header class="shrink-0 border-b">
      <div class="flex min-h-14 items-center mx-auto w-full max-w-160 px-4">
        <div class="flex w-full items-center gap-2">
          <a href="/settings" aria-label="${T.common.back}" class="-ml-2 inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none text-foreground hover:bg-muted hover:text-foreground md:hidden">${icon("back", "size-5")}</a>
          <h1 class="min-w-0 flex-1 truncate text-header">${T.settings.categories[category]}</h1>
          ${end || ""}
        </div>
      </div>
    </header>
    <main class="relative flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div class="flex flex-1 flex-col mx-auto w-full max-w-160 px-4 py-6">${body}</div>
    </main>
  </div>`;
}

/* ---- 原版 ui/field、ui/input、ui/select、ui/switch 的样子 ------------------------------------ */

const PROSE_WIDTH = "max-w-[42em]";

const fieldGroupClass =
  "group/field-group flex w-full flex-col gap-5";

const fieldClass = "group/field flex w-full gap-2 flex-col";

const fieldLabelClass =
  "group/field-label peer/field-label flex w-fit items-center gap-2 text-sm leading-none font-medium select-none peer-disabled:cursor-not-allowed peer-disabled:opacity-50";

const fieldDescriptionClass =
  "text-left text-sm leading-normal font-normal text-muted-foreground group-has-data-horizontal/field:text-balance last:mt-0 nth-last-2:-mt-1";

const inputClass =
  "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm";

/** 原版 SelectTrigger：h-8、圆角 lg、右侧一个下拉箭头。
 *
 *  原版是 Base UI 的 Select：触发器是一个 role="combobox" 的 button，展开的浮层是一块
 *  自绘的面板（白底圆角、宽度同触发器、选中项铺灰底、右侧一个勾）。原生 <select> 的
 *  浮层是操作系统画的，对不上，所以这里自绘：触发器仍是 button，浮层是一块绝对定位的
 *  div，选中时把值写回隐藏的 input（表单读它），并派发 change 事件。
 */
function selectTrigger(id, options, value, { disabled = false, ariaLabel = "", field = "" } = {}) {
  const chosen = options.find((o) => o.value === value);
  const label = chosen ? chosen.label : "";
  const items = options
    .map(
      (o) => `<div data-slot="select-item" role="option" aria-selected="${o.value === value}"${o.value === value ? " data-selected" : ""} data-value="${esc(o.value)}" class="relative flex w-full cursor-default items-center gap-1.5 rounded-md py-1 pr-8 pl-1.5 text-sm outline-hidden select-none data-selected:bg-muted hover:bg-accent hover:text-accent-foreground *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2">
        <span data-slot="select-item-text" class="flex flex-1 shrink-0 gap-2 whitespace-nowrap">${esc(o.label)}</span>
        ${o.value === value ? `<span data-slot="select-item-indicator" class="pointer-events-none absolute right-2 flex size-4 items-center justify-center">${icon("check", "size-4")}</span>` : ""}
      </div>`
    )
    .join("");
  return `<div class="relative w-full" data-select="${esc(id)}">
    <button type="button" role="combobox" id="${id}-trigger" data-slot="select-trigger" data-size="default" aria-haspopup="listbox" aria-expanded="false"${ariaLabel ? ` aria-label="${esc(ariaLabel)}"` : ""}${disabled ? " disabled" : ""} class="flex h-8 w-full items-center justify-between gap-1.5 rounded-lg border border-input bg-transparent py-2 pr-2 pl-2.5 text-sm whitespace-nowrap transition-colors outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 data-placeholder:text-muted-foreground *:data-[slot=select-value]:line-clamp-1 *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-1.5">
      <span data-slot="select-value" class="flex flex-1 items-center gap-1.5 truncate">${esc(label)}</span>
      ${icon("collapse", "pointer-events-none size-4 shrink-0 text-muted-foreground")}
    </button>
    <input type="hidden" id="${id}" value="${esc(value)}" data-select-value${field ? ` data-select-field="${esc(field)}"` : ""}>
    <div data-slot="select-content" role="listbox" class="absolute top-full left-0 z-50 mt-1 hidden max-h-72 w-full min-w-36 overflow-x-hidden overflow-y-auto rounded-lg bg-popover text-popover-foreground shadow-md ring-1 ring-foreground/10">
      ${items}
    </div>
  </div>`;
}

/** 自绘下拉的值：藏在触发器旁边的 hidden input 里（表单读它）。 */
function selectValue(id) {
  return document.getElementById(id)?.value || "";
}

/** 全部时区（原版 lib/timezone.ts 的 listTimezones：Intl.supportedValuesOf("timeZone")，
 *  浏览器不认识就退回 Asia/Shanghai）。 */
function listTimezones() {
  try {
    const zones = Intl.supportedValuesOf("timeZone");
    return zones && zones.length ? zones : ["Asia/Shanghai"];
  } catch {
    return ["Asia/Shanghai"];
  }
}

/** 原版 Combobox（时区用）：触发器长得和 SelectTrigger 一样，浮层顶上带一个搜索框。
 *  选项很多、要能搜的选择框用它；普通选择框用 selectTrigger。 */
function comboboxTrigger(id, options, value, { placeholder = "", searchPlaceholder = "" } = {}) {
  const chosen = options.find((o) => o.value === value);
  const label = chosen ? chosen.label : "";
  return `<div class="relative w-full" data-combobox="${esc(id)}">
    <button type="button" role="combobox" id="${id}-trigger" data-slot="combobox-select-trigger" aria-haspopup="listbox" aria-expanded="false" class="flex h-8 w-full items-center justify-between gap-1.5 rounded-lg border border-input bg-transparent py-2 pr-2 pl-2.5 text-sm whitespace-nowrap transition-colors outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0">
      <span class="line-clamp-1 flex-1 truncate text-left">${esc(label || placeholder)}</span>
      ${icon("collapse", "pointer-events-none size-4 text-muted-foreground")}
    </button>
    <input type="hidden" id="${id}" value="${esc(value)}" data-combobox-value>
    <div data-slot="combobox-content" class="combobox-pop absolute top-full left-0 z-50 mt-1.5 hidden max-w-full overflow-hidden rounded-lg bg-popover text-popover-foreground shadow-md ring-1 ring-foreground/10">
      <div data-slot="input-group" class="m-1 mb-0">
        <input type="text" data-combobox-search placeholder="${esc(searchPlaceholder)}" aria-label="${esc(searchPlaceholder)}" autocomplete="off" class="h-8 w-full min-w-0 rounded-lg border border-input/30 bg-input/30 px-2.5 text-sm shadow-none outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50">
      </div>
      <div data-slot="combobox-list" role="listbox" class="no-scrollbar max-h-72 overflow-y-auto overscroll-contain p-1">
        ${options
          .map(
            (o) => `<div data-slot="combobox-item" role="option" aria-selected="${o.value === value}"${o.value === value ? " data-selected" : ""} data-value="${esc(o.value)}" data-label="${esc(o.label)}" class="relative flex w-full cursor-default items-center gap-2 rounded-md py-1 pr-8 pl-1.5 text-sm outline-hidden select-none data-selected:bg-muted hover:bg-accent hover:text-accent-foreground *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2">
          <span class="flex flex-1 shrink-0 gap-2 whitespace-nowrap">${esc(o.label)}</span>
          ${o.value === value ? `<span data-slot="combobox-item-indicator" class="pointer-events-none absolute right-2 flex size-4 items-center justify-center">${icon("check", "size-4")}</span>` : ""}
        </div>`
          )
          .join("")}
      </div>
      <p data-combobox-empty class="hidden px-3 py-6 text-center text-sm text-muted-foreground"></p>
    </div>
  </div>`;
}

/** 原版 Field：一块标签、控件、说明。 */
function field(label, control, hint) {
  return `<div data-slot="field" data-orientation="vertical" class="${fieldClass}">
    <label data-slot="field-label" class="${fieldLabelClass}">${label}</label>
    ${control}
    ${hint ? `<p data-slot="field-description" class="${fieldDescriptionClass}">${hint}</p>` : ""}
  </div>`;
}

/** 原版 SettingsSection：一节，下沿一条细分隔线，不套卡片。 */
function settingsSection(content, { title = "", description = "" } = {}) {
  const heading = title || description
    ? `<div class="flex flex-col gap-1">${title ? `<h2 class="text-header">${title}</h2>` : ""}${description ? `<p class="${PROSE_WIDTH} text-body text-muted-foreground">${description}</p>` : ""}</div>`
    : "";
  return `<section class="flex flex-col gap-4 border-b py-6">${heading}${content}</section>`;
}

/** 原版 Switch：h-[18.4px]、w-[32px] 的胶囊，滑块 16px。
 *  原版的 base-ui 用属性「有就是开」：`data-checked` / `data-unchecked` 是属性的有无，不是值。 */
function switchEl(id, checked) {
  const attrs = checked ? 'data-checked' : 'data-unchecked';
  // 原版 base-ui 的 Switch.Thumb 自己也带 data-checked / data-unchecked（CSS 的
  // group-data-…:data-checked: 匹配的是元素自身），所以滑块上也得有这两个属性之一。
  const thumbAttrs = checked ? 'data-checked' : 'data-unchecked';
  return `<button type="button" role="switch" id="${id}" data-slot="switch" data-size="default" aria-checked="${checked}" ${attrs} class="peer group/switch relative inline-flex shrink-0 items-center rounded-full border border-transparent transition-all outline-none after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 data-[size=default]:h-[18.4px] data-[size=default]:w-[32px] data-checked:bg-primary data-unchecked:bg-input dark:data-unchecked:bg-input/80">
    <span data-slot="switch-thumb" ${thumbAttrs} class="pointer-events-none block rounded-full bg-background ring-0 transition-transform group-data-[size=default]/switch:size-4 group-data-[size=default]/switch:data-checked:translate-x-[calc(100%-2px)] group-data-[size=default]/switch:data-unchecked:translate-x-0 dark:data-checked:bg-primary-foreground dark:data-unchecked:bg-foreground"></span>
  </button>`;
}

/* ------------------------------------------------------------------ 统计页 */

/** 原版 lib/stats.ts 的算法搬过来：画像、排行、热度、显示哪几块。 */

const STATS_RANGES = ["30d", "1y", "all"];
const RANKING_SIZE = 5;
const TIER_PREVIEW = 5;
const DAY_PARTS = ["night", "morning", "afternoon", "evening"];

function heatLevel(count, most) {
  if (count <= 0 || most <= 0) return 0;
  return Math.min(4, Math.max(1, Math.ceil((count / most) * 4)));
}

function busiestParts(hours) {
  const counts = DAY_PARTS.map((_, i) =>
    hours.slice(i * 6, i * 6 + 6).reduce((sum, n) => sum + n, 0)
  );
  const most = Math.max(...counts);
  return most === 0 ? [] : DAY_PARTS.filter((_, i) => counts[i] === most);
}

function portraitText(hours) {
  const t = T.settings.stats.portrait;
  const parts = busiestParts(hours).map((part) => t.parts[part]);
  if (parts.length === 0) return null;
  if (parts.length > 2) return t.anyTime;
  return t.comesAt(parts.join(t.and));
}

function rankCharacters(characters, by) {
  const ranked = characters.filter((c) => by(c) > 0).sort((a, b) => by(b) - by(a)).slice(0, RANKING_SIZE);
  const most = ranked[0] ? by(ranked[0]) : 0;
  return ranked.map((c) => ({ ...c, share: most ? by(c) / most : 0 }));
}

function affinityGroups(affinity) {
  return (affinity || []).filter((group) => group.characters.length > 0).slice().reverse();
}

function tierPreview(count) {
  return count <= TIER_PREVIEW + 1 ? count : TIER_PREVIEW;
}

function statsSections(stats) {
  const hasCome = stats.all_hours.some((n) => n > 0);
  const range = stats.range;
  const isEmpty = !hasCome && range.characters.length === 0;
  return {
    isEmpty,
    visits: hasCome && stats.visits.longest > 0,
    portrait: hasCome,
    heatmap: stats.heatmap.counts.some((n) => n > 0),
    affinity: !isEmpty && affinityGroups(stats.affinity).length > 0,
    totals: !isEmpty,
    giftsGiven: !isEmpty && range.gifts !== null,
    mostTalked: range.characters.some((c) => c.turns > 0),
    mostCalled: range.characters.some((c) => c.call_seconds > 0),
    hours: range.by_hour.some((n) => n > 0),
    gifts: (range.gifts?.given ?? 0) > 0,
  };
}

/** 一个数：上面 muted 的标签，下面 1.5rem 的数，后面跟单位。 */
function figure(label, value, unit) {
  return `<div class="flex min-w-0 flex-col gap-1">
    <dt class="text-subtitle text-muted-foreground">${esc(label)}</dt>
    <dd class="flex min-w-0 items-baseline gap-1">
      <span class="text-2xl font-semibold tabular-nums">${value}</span>
      ${unit ? `<span class="text-subtitle text-muted-foreground">${esc(unit)}</span>` : ""}
    </dd>
  </div>`;
}

/** 通话时长：「2 小时 5 分」，不满 1 小时只写「12 分」。 */
function callTimeText(seconds) {
  const t = T.settings.stats;
  const minutes = seconds > 0 ? Math.max(1, Math.round(seconds / 60)) : 0;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours > 0 ? t.calledHours(hours, rest) : t.calledMinutes(rest);
}

/** 一个排行：标题 + 每行头像、名字、读数和一根 `--her-bar` 的条。 */
function ranking(title, ranked, reading) {
  const rows = ranked
    .map((one) => {
      const character = state.characters.find((c) => c.id === one.character_id);
      if (!character) return "";
      // 原版：这一行带她的颜色，条子才是她颜色的（--her-bar）；头像是 sm 档（24px）。
      return `<li style="${herVars(character.color)}" class="flex items-center gap-3">
        ${avatarEl(character.name, avatarUrlOf(character.id), "size-6")}
        <div class="flex min-w-0 flex-1 flex-col gap-1.5">
          <div class="flex items-baseline justify-between gap-2">
            <span class="truncate text-sm font-medium">${esc(character.name)}</span>
            <span class="shrink-0 text-subtitle text-muted-foreground tabular-nums">${esc(reading(one))}</span>
          </div>
          <div class="h-2 rounded-full bg-muted">
            <div class="h-full rounded-full bg-her-bar" style="width:${(one.share * 100).toFixed(2)}%"></div>
          </div>
        </div>
      </li>`;
    })
    .join("");
  return `<section class="flex flex-col gap-3">
    <h2 class="text-header">${esc(title)}</h2>
    <ol class="flex flex-col gap-3">${rows}</ol>
  </section>`;
}

/** 好感度分组：每档一行，左边档名，右边这一档里的头像，外面一圈她的 `--her-mark`。 */
function affinityGroupRows(groups) {
  const rows = groups
    .map((group) => {
      const shown = tierPreview(group.characters.length);
      const folded = group.characters.length - shown;
      const members = group.characters
        .map((id, i) => {
          const character = state.characters.find((c) => c.id === id);
          if (!character) return "";
          const isFolded = i >= shown;
          // 原版：每个头像外面一圈她的颜色（--her-mark），所以这一格要带上她的颜色变量。
          return `<li style="${herVars(character.color)}" class="${isFolded ? "hidden group-hover/tier:block" : ""} rounded-full ring-2 ring-her-mark ring-offset-2 ring-offset-background">
            ${avatarEl(character.name, avatarUrlOf(character.id), "size-bubble-avatar")}
          </li>`;
        })
        .join("");
      const more = folded
        ? `<li class="group-hover/tier:hidden">
            <button type="button" aria-label="${esc(T.settings.stats.tierMoreLabel(folded))}" class="inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none text-muted-foreground hover:bg-muted hover:text-foreground">${esc(T.settings.stats.tierMore(folded))}</button>
          </li>`
        : "";
      return `<dt class="flex h-9 items-center text-subtitle text-muted-foreground">${esc(T.affinity.tiers[group.tier] || group.tier)}</dt>
        <dd>
          <div class="group/tier -m-1 overflow-hidden">
            <ul class="flex flex-wrap gap-2.5 p-1">${members}${more}</ul>
          </div>
        </dd>`;
    })
    .join("");
  return `<section class="flex flex-col gap-3">
    <h2 class="text-header">${esc(T.settings.stats.affinity)}</h2>
    <dl class="grid grid-cols-[auto_1fr] items-start gap-x-4 gap-y-3">${rows}</dl>
  </section>`;
}

/** 原版 ui/tabs 的样子：一个灰底的小条，选中的那个是白底。 */
function statsTabs() {
  const list = STATS_RANGES.map((r) => {
    const active = state.statsRange === r;
    return `<button type="button" role="tab" data-range="${r}" aria-selected="${active}" data-active="${active ? "" : undefined}" class="relative inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent px-1.5 py-0.5 text-sm font-medium whitespace-nowrap text-foreground/60 transition-all hover:text-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1 focus-visible:outline-ring group-data-[variant=default]/tabs-list:data-active:shadow-sm data-active:bg-background data-active:text-foreground">${esc(T.settings.stats.ranges[r])}</button>`;
  }).join("");
  return `<div role="tablist" aria-label="${esc(T.settings.stats.rangeLabel)}" data-slot="tabs-list" data-variant="default" class="group/tabs-list inline-flex w-fit items-center justify-center rounded-lg bg-muted p-[3px] text-muted-foreground group-data-horizontal/tabs:h-8">
    ${list}
  </div>`;
}

function statsBody() {
  const stats = state.stats;
  if (!stats) return `<p class="text-body text-muted-foreground">${esc(T.settings.loading)}</p>`;
  const shown = statsSections(stats);
  if (shown.isEmpty) {
    return emptyState({
      heading: true,
      title: esc(T.settings.stats.empty),
      content: `<a href="/conversations" class="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-input bg-background px-2.5 text-sm font-medium whitespace-nowrap transition-all outline-none select-none hover:bg-muted hover:text-foreground">${esc(T.settings.stats.toConversations)}</a>`,
    });
  }
  const t = T.settings.stats;
  const calls = stats.range.calls;
  const top = `<div class="flex flex-col gap-4">
    ${shown.portrait ? `<p class="text-header">${esc(portraitText(stats.all_hours) || "")}</p>` : ""}
    <dl class="grid grid-cols-3 gap-3">
      ${figure(t.daysKnown, stats.days_known, t.days)}
      ${shown.visits ? figure(t.currentVisits, stats.visits.current, t.days) : ""}
      ${shown.visits ? figure(t.longestVisits, stats.visits.longest, t.days) : ""}
    </dl>
  </div>`;

  const bottom = `<div class="flex flex-col gap-4">
    ${statsTabs()}
    <div class="flex flex-col gap-8">
      ${
        shown.totals
          ? `<dl class="${shown.giftsGiven ? "grid grid-cols-2 gap-3 sm:grid-cols-4" : "grid grid-cols-2 gap-3 sm:grid-cols-3"}">
            ${figure(t.turns, stats.range.turns)}
            ${figure(t.calls, calls.count, t.timesUnit)}
            <div class="flex min-w-0 flex-col gap-1">
              <dt class="text-subtitle text-muted-foreground">${esc(t.callTime)}</dt>
              <dd class="flex min-w-0 items-baseline gap-1">${callTimeFigure(calls.seconds)}</dd>
            </div>
            ${shown.giftsGiven && stats.range.gifts ? figure(t.giftsGiven, stats.range.gifts.given) : ""}
          </dl>`
          : ""
      }
      ${shown.mostTalked ? ranking(t.mostTalked, rankCharacters(stats.range.characters, (c) => c.turns), (one) => t.talked(one.turns, one.days_known)) : ""}
      ${shown.mostCalled ? ranking(t.mostCalled, rankCharacters(stats.range.characters, (c) => c.call_seconds), (one) => callTimeText(one.call_seconds)) : ""}
      ${
        shown.hours
          ? `<section class="flex flex-col gap-2">
            <h2 class="text-header">${esc(t.hours)}</h2>
            <div id="hour-chart" class="flex min-w-0 flex-col gap-2"></div>
          </section>`
          : ""
      }
    </div>
  </div>`;

  return `<div class="flex flex-col gap-8">
    ${top}
    ${
      shown.heatmap
        ? `<section class="flex flex-col gap-2">
          <h2 class="text-header">${esc(t.heatmap)}</h2>
          <div id="heatmap" class="flex min-w-0 flex-col gap-2"></div>
        </section>`
        : ""
    }
    ${shown.affinity ? affinityGroupRows(affinityGroups(stats.affinity)) : ""}
    ${bottom}
  </div>`;
}

function callTimeFigure(seconds) {
  const t = T.settings.stats;
  const minutes = seconds > 0 ? Math.max(1, Math.round(seconds / 60)) : 0;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `${
    hours > 0
      ? `<span class="text-2xl font-semibold tabular-nums">${hours}</span><span class="text-subtitle text-muted-foreground">${esc(t.hoursUnit)}</span>`
      : ""
  }<span class="text-2xl font-semibold tabular-nums">${rest}</span><span class="text-subtitle text-muted-foreground">${esc(t.minutesUnit)}</span>`;
}

/* ---- 图表：热力图与分时段，都是手画的 SVG，宽度按容器算。 */

const HEAT_MAX_CELL = 14;
const HEAT_GAP = 2;
const HEAT_MONTH_ROW = 18;
const HEAT_MONTH_LABEL_WIDTH = 30;
const HEAT_SHADES = [
  "fill-foreground/8",
  "fill-foreground/25",
  "fill-foreground/45",
  "fill-foreground/70",
  "fill-foreground",
];

const HOUR_HEIGHT = 120;
const HOUR_AXIS = 20;
const HOUR_TOP = 4;
const HOUR_MAX_BAR = 20;
const HOUR_GAP = 2;
const HOUR_RADIUS = 3;
const HOUR_LABELLED = [0, 6, 12, 18];

function heatmapWeeks(heatmap) {
  const most = Math.max(0, ...heatmap.counts);
  const start = Date.parse(`${heatmap.from}T00:00:00Z`);
  const weeks = [];
  heatmap.counts.forEach((count, i) => {
    if (i % 7 === 0) weeks.push([]);
    const date = new Date(start + i * 86400000).toISOString().slice(0, 10);
    weeks[weeks.length - 1].push({
      date,
      month: Number(date.slice(5, 7)),
      day: Number(date.slice(8, 10)),
      count,
      level: heatLevel(count, most),
    });
  });
  return weeks;
}

/** 上面一行月份：每列的第一个日子落在哪个月就标哪个月，靠得太近的跳过。 */
function heatmapMonths(weeks, apart) {
  const months = new Map();
  let last = -Infinity;
  weeks.forEach((week, column) => {
    const first = week.find((cell) => cell.day === 1);
    if (!first || column - last < apart) return;
    months.set(column, T.settings.stats.month(first.month));
    last = column;
  });
  return months;
}

function drawHeatmap() {
  const host = document.getElementById("heatmap");
  if (!host || !state.stats) return;
  const heatmap = state.stats.heatmap;
  const weeks = heatmapWeeks(heatmap);
  const cameOn = heatmap.counts.filter((n) => n > 0).length;
  const width = Math.max(240, host.clientWidth || 600);
  const cell = Math.max(2, Math.min(HEAT_MAX_CELL, (width - HEAT_GAP * (weeks.length - 1)) / weeks.length));
  const step = cell + HEAT_GAP;
  const months = heatmapMonths(weeks, Math.ceil(HEAT_MONTH_LABEL_WIDTH / step));
  const labels = [...months]
    .map(([column, label]) => `<text x="${column * step}" y="12" class="fill-muted-foreground">${esc(label)}</text>`)
    .join("");
  const rects = weeks
    .map((week, column) =>
      week
        .map(
          (day, row) =>
            `<rect data-date="${day.date}" x="${column * step}" y="${HEAT_MONTH_ROW + row * step}" width="${cell}" height="${cell}" rx="${Math.min(2, cell / 4)}" class="${HEAT_SHADES[day.level]}" stroke-width="1"></rect>`
        )
        .join("")
    )
    .join("");
  host.innerHTML = `<p class="text-sm text-muted-foreground tabular-nums" data-readout>${esc(
    T.settings.stats.cameOn(cameOn)
  )}</p>
    <svg width="100%" height="${HEAT_MONTH_ROW + step * 7 - HEAT_GAP}" role="img" aria-label="${esc(
    T.settings.stats.heatmapLabel
  )}" class="block text-xs">${labels}${rects}</svg>`;
  const readout = host.querySelector("[data-readout]");
  host.querySelectorAll("[data-date]").forEach((rect) => {
    rect.addEventListener("mouseenter", () => {
      const date = rect.getAttribute("data-date");
      const cellData = weeks.flat().find((c) => c.date === date);
      if (cellData && readout) {
        readout.textContent = T.settings.stats.dayCount(cellData.month, cellData.day, cellData.count);
      }
    });
  });
  const svg = host.querySelector("svg");
  if (svg) {
    svg.addEventListener("mouseleave", () => {
      if (readout) readout.textContent = T.settings.stats.cameOn(cameOn);
    });
  }
}

function columnPath(x, width, top, base, radius) {
  const r = Math.min(radius, width / 2, base - top);
  return `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + width - r} Q${x + width},${top} ${x + width},${top + r} V${base} Z`;
}

function drawHourChart() {
  const host = document.getElementById("hour-chart");
  if (!host || !state.stats) return;
  const hours = state.stats.range.by_hour;
  const most = Math.max(1, ...hours);
  const base = HOUR_HEIGHT - HOUR_AXIS;
  const y = (n) => base - (n / most) * (base - HOUR_TOP);
  const width = Math.max(240, host.clientWidth || 600);
  const slot = width / hours.length;
  const bar = Math.max(1, Math.min(HOUR_MAX_BAR, slot - HOUR_GAP));
  const total = hours.reduce((sum, n) => sum + n, 0);
  const cells = hours
    .map((count, hour) => {
      const x = slot * hour + (slot - bar) / 2;
      return `<g data-hour="${hour}">
        <rect x="${slot * hour}" y="0" width="${slot}" height="${base}" class="fill-transparent"></rect>
        ${count > 0 ? `<path d="${columnPath(x, bar, y(count), base, HOUR_RADIUS)}" class="fill-foreground transition-opacity"></path>` : ""}
        ${
          HOUR_LABELLED.includes(hour)
            ? `<text x="${slot * hour + slot / 2}" y="${HOUR_HEIGHT - 4}" text-anchor="middle" class="fill-muted-foreground">${esc(
                T.settings.stats.hour(hour)
              )}</text>`
            : ""
        }
      </g>`;
    })
    .join("");
  host.innerHTML = `<p class="text-sm text-muted-foreground tabular-nums" data-readout>${esc(
    T.settings.stats.hoursTotal(total)
  )}</p>
    <svg width="100%" height="${HOUR_HEIGHT}" role="img" aria-label="${esc(
      T.settings.stats.hoursLabel
    )}" class="block overflow-visible text-xs tabular-nums">
      <line x1="0" x2="${width}" y1="${base}" y2="${base}" class="stroke-border" stroke-width="1"></line>
      ${cells}
    </svg>`;
  const readout = host.querySelector("[data-readout]");
  host.querySelectorAll("[data-hour]").forEach((group) => {
    group.addEventListener("mouseenter", () => {
      const hour = Number(group.getAttribute("data-hour"));
      if (readout) readout.textContent = T.settings.stats.hourCount(hour, hours[hour]);
      const slotRect = group.querySelector("rect");
      if (slotRect) slotRect.setAttribute("class", "fill-muted");
      group.querySelectorAll("path").forEach((p) => p.setAttribute("class", "fill-foreground transition-opacity opacity-50"));
    });
    group.addEventListener("mouseleave", () => {
      if (readout) readout.textContent = T.settings.stats.hoursTotal(total);
      const slotRect = group.querySelector("rect");
      if (slotRect) slotRect.setAttribute("class", "fill-transparent");
      group.querySelectorAll("path").forEach((p) => p.setAttribute("class", "fill-foreground transition-opacity"));
    });
  });
}

function settingsPanelBody(category) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";  switch (category) {
    case "profile": {
      const p = T.settings.profile;
      const draft = state.profileDraft;
      const monthOptions = [
        { value: "", label: p.birthdayUnset },
        ...Array.from({ length: 12 }, (_, i) => i + 1).map((m) => ({ value: String(m), label: p.monthOption(m) })),
      ];
      const dayOptions = [
        { value: "", label: p.birthdayUnset },
        ...(draft.birth_month
          ? Array.from({ length: daysInMonth(Number(draft.birth_month)) }, (_, i) => i + 1).map((d) => ({
              value: String(d),
              label: p.dayOption(d),
            }))
          : []),
      ];
      return settingsSection(
        `<form id="profile-form" class="contents">
          <div data-slot="field-group" class="${fieldGroupClass}">
            ${field(
              p.formOfAddress,
              `<input id="p-address" data-slot="input" class="${inputClass}" placeholder="${p.formOfAddressPlaceholder}" autocomplete="off" value="${esc(draft.address || "")}">`,
              p.formOfAddressHint
            )}
            ${field(
              p.gender,
              selectTrigger(
                "p-gender",
                [
                  { value: "default", label: p.genderDefault },
                  { value: "male", label: p.male },
                  { value: "female", label: p.female },
                ],
                draft.gender || "default"
              ),
              p.genderHint
            )}
            ${field(
              p.timezone,
              // 原版是 Combobox：选项是全部时区（Intl.supportedValuesOf），
              // 浮层顶上能搜；别的设备上存的时区这个浏览器不认识，也照样列出来。
              comboboxTrigger(
                "p-tz",
                [
                  { value: "", label: p.timezoneAuto(tz || p.timezoneUnknown) },
                  ...(draft.timezone && draft.timezone !== tz ? [{ value: draft.timezone, label: draft.timezone }] : []),
                  ...listTimezones().map((zone) => ({ value: zone, label: zone })),
                ],
                draft.timezone || "",
                { searchPlaceholder: p.timezoneSearch }
              ),
              p.timezoneHint
            )}
            ${field(
              p.birthday,
              `<div class="grid grid-cols-2 gap-3">
                ${selectTrigger("p-bm", monthOptions, draft.birth_month === null ? "" : String(draft.birth_month), { ariaLabel: p.birthMonth })}
                ${selectTrigger("p-bd", dayOptions, draft.birth_day === null ? "" : String(draft.birth_day), {
                  ariaLabel: p.birthDay,
                  disabled: draft.birth_month === null,
                })}
              </div>`,
              p.birthdayHint
            )}
            <div class="flex flex-wrap items-center gap-3">
              <button type="submit" id="save-profile" ${state.dirty ? "" : "disabled"} class="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding px-2.5 text-sm font-medium whitespace-nowrap transition-all outline-none select-none bg-primary text-primary-foreground hover:bg-primary/80 disabled:pointer-events-none disabled:opacity-50">${p.save}</button>
              <p role="status" id="profile-status" class="text-subtitle text-muted-foreground">${state.dirty ? p.unsaved : p.saved}</p>
            </div>
          </div>
        </form>`
      );
    }
    case "world": {
      const t = T.settings.world;
      const w = state.work;
      const c = state.characters.find((x) => x.id === state.selectedId) || state.characters[0] || null;
      const pack = (c && c.pack) || null;
      const title =
        w && w.named ? t.workOne(w.title_zh || w.title || "", w.agency_zh || w.agency || "") : t.workNone;
      const rows = [];
      if (pack) {
        rows.push(pack.home ? t.home(pack.home) : t.noHome);
        rows.push(pack.fallback && pack.fallback.length ? t.fallback(pack.fallback.join("、")) : t.noFallback);
        rows.push(pack.missing && pack.missing.length ? t.missing(pack.missing.join("、")) : t.noMissing);
      }
      return settingsSection(`<div class="${fieldGroupClass}">
        <div class="flex flex-col gap-1">
          <p class="${fieldLabelClass}">${esc(t.work)}</p>
          <p class="text-subtitle text-muted-foreground">${esc(title)}</p>
          <p class="${PROSE_WIDTH} text-subtitle text-muted-foreground">${esc(t.workHint)}</p>
        </div>
        <div class="flex flex-col gap-1">
          <p class="${fieldLabelClass}">${esc(t.her(c ? c.name : "—"))}</p>
          ${rows.map((row) => `<p class="text-subtitle text-muted-foreground">${esc(row)}</p>`).join("")}
          <p class="${PROSE_WIDTH} text-subtitle text-muted-foreground">${esc(t.hint)}</p>
        </div>
      </div>`);
    }
    case "memories": {
      const list = state.memories.length
        ? `<ul class="flex flex-col divide-y">${state.memories
            .map((m) => {
              const name = characterName(m.character_id) || T.memories.goneCharacter;
              const d = new Date(m.at);
              const mark = m.superseded ? T.memories.superseded : "";
              const where = m.surface ? T.memories.surface(m.surface) : "";
              return `<li class="flex items-start gap-3 py-4">
                <div class="pt-0.5">${avatarEl(name, avatarUrlOf(m.character_id), "size-6")}</div>
                <div class="flex min-w-0 flex-1 flex-col gap-1">
                  <p class="text-body break-words${mark ? " text-muted-foreground" : ""}">${esc(m.text || "")}</p>
                  <div class="flex flex-wrap items-center gap-2">
                    ${mark ? `<span data-slot="badge" variant="secondary" class="inline-flex items-center justify-center rounded-md border border-transparent px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 gap-1 bg-secondary text-secondary-foreground">${mark}</span>` : ""}
                    ${where ? `<span data-slot="badge" variant="outline" class="inline-flex items-center justify-center rounded-md border border-border px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 gap-1 text-muted-foreground">${esc(where)}</span>` : ""}
                    <span class="text-subtitle text-muted-foreground">${T.memories.toldTo(name)} · ${T.memories.date(d.getFullYear(), d.getMonth() + 1, d.getDate())}</span>
                  </div>
                </div>
                <button type="button" data-delete-memory="${esc(m.id)}" aria-label="${esc(T.memories.removeLabel(m.text || ""))}" class="inline-flex size-7 shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 text-muted-foreground hover:bg-muted hover:text-foreground">${icon("trash", "size-4")}</button>
              </li>`;
            })
            .join("")}</ul>`
        : emptyState({ title: T.memories.empty });
      return `<div class="flex flex-1 flex-col gap-4">
        <p class="${PROSE_WIDTH} text-body text-muted-foreground">${T.memories.intro}</p>
        ${list}
      </div>`;
    }
    case "stats":
      return statsBody();
    case "call": {
      const c = T.settings.call;
      // 通话的设置还没有后端可存（见交接文档 §7），先存在本页的草稿里，
      // 至少选中以后显示得对。
      const call = state.callDraft;
      return settingsSection(
        `<div data-slot="field-group" class="${fieldGroupClass}">
          ${field(
            c.mic,
            selectTrigger("c-mic", [{ value: "system-default", label: c.micDefault }], call.mic ?? "system-default", { field: "mic" }),
            c.micHint
          )}
          ${field(
            c.quality,
            selectTrigger(
              "c-quality",
              [
                { value: "low", label: c.qualities.low },
                { value: "standard", label: c.qualities.standard },
                { value: "high", label: c.qualities.high },
              ],
              call.quality ?? "standard",
              { field: "quality" }
            ),
            c.qualityHint
          )}
          <div data-slot="field" data-orientation="horizontal" class="group/field flex w-full flex-row items-center gap-2">
            <div data-slot="field-content" class="group/field-content flex flex-1 flex-col gap-0.5 leading-snug">
              <label data-slot="field-label" class="${fieldLabelClass}">${c.phoneTexture}</label>
              <p data-slot="field-description" class="${fieldDescriptionClass}">${c.phoneTextureHint}</p>
            </div>
            ${switchEl("c-texture", false)}
          </div>
        </div>`
      );
    }
    case "reach-outs":
      return settingsSection(
        `<div data-slot="field-group" class="${fieldGroupClass}">
          <div data-slot="field" data-orientation="horizontal" class="group/field flex w-full flex-row items-center gap-2">
            <div data-slot="field-content" class="group/field-content flex flex-1 flex-col gap-0.5 leading-snug">
              <label data-slot="field-label" class="${fieldLabelClass}">${T.settings.categories["reach-outs"]}</label>
            </div>
            ${switchEl("reach-outs", false)}
          </div>
        </div>`
      );
    case "appearance":
      return settingsSection(
        `<div data-slot="field-group" class="${fieldGroupClass}">
          <div data-slot="field" data-orientation="horizontal" class="group/field flex w-full flex-row items-center gap-2">
            <div data-slot="field-content" class="group/field-content flex flex-1 flex-col gap-0.5 leading-snug">
              <label data-slot="field-label" class="${fieldLabelClass}">${T.settings.appearance.scspAvatars}</label>
            </div>
            ${switchEl("toggle-scsp", state.scsp)}
          </div>
          <div data-slot="field" data-orientation="horizontal" class="group/field flex w-full flex-row items-center gap-2">
            <div data-slot="field-content" class="group/field-content flex flex-1 flex-col gap-0.5 leading-snug">
              <label data-slot="field-label" class="${fieldLabelClass}">${T.settings.appearance.scspBackgrounds}</label>
            </div>
            ${switchEl("toggle-scsp-bg", state.scspBg)}
          </div>
        </div>`
      );
    case "own-keys": {
      const k = T.settings.ownKeys;
      const keys = loadOwnKeys();
      // 原版 ServiceKey：标题一行、摘要一行、右侧两个 ghost 键；点「填写」在原位展开表单
      // （不弹窗），再点一次收起。
      const service = (title, hint, statusText, form) => {
        const notSet = !statusText;
        const editing = state.ownKeysEditing === form;
        return `<div class="flex flex-col gap-3" data-own-service="${form}">
          <div class="flex flex-col gap-1">
            <h3 class="text-row-name">${title}</h3>
            ${hint ? `<p class="text-body text-muted-foreground">${hint}</p>` : ""}
          </div>
          ${editing
            ? ""
            : `<div class="flex flex-wrap items-center gap-2">
                <div class="flex min-w-0 flex-1 flex-col gap-0.5">
                  <p role="status" class="text-body break-words${notSet ? " text-muted-foreground" : ""}">${statusText || k.notSet}</p>
                </div>
                <div class="flex gap-1">
                  <button type="button" data-own-keys="${form}" class="${BUTTON_BASE} h-7 gap-1.5 px-2.5 text-[0.8rem] hover:bg-muted hover:text-foreground">${notSet ? k.fill : k.edit}</button>
                  ${notSet ? "" : `<button type="button" data-own-remove="${form}" aria-label="${k.removeLabel(title)}" class="${BUTTON_BASE} h-7 gap-1.5 px-2.5 text-[0.8rem] hover:bg-muted hover:text-foreground">${k.remove}</button>`}
                </div>
              </div>`}
          ${editing ? `<div data-own-form="${form}">${ownKeysForm(form, keys)}</div>` : ""}
        </div>`;
      };
      const llm = keys.llm;
      const tts = keys.tts;
      const stt = keys.stt;
      const image = keys.image;
      const vision = keys.vision;
      return `<div id="own-keys-page">
        ${settingsSection(
          `<p class="text-body">${k.exemptingNone}</p>
           <p role="status" class="text-body">${k.exemptNone}${k.perCallLimits}</p>`,
          { description: `${k.summary}<button type="button" class="ml-1 inline-flex size-4 items-center justify-center align-text-bottom text-muted-foreground outline-none" aria-label="${k.detailsLabel}">${icon("helpCircle", "size-4")}</button>` }
        )}
        <div class="flex flex-col gap-4">
        ${service(k.llmTitle, k.llmHint, llm ? `${llm.model} · ${llm.base_url}` : "", "llm")}
        ${service(k.ttsTitle, "", tts ? `${k.ttsModels[tts.model] || tts.model} · ${k.last4(tts.key.slice(-4))}` : "", "tts")}
        ${service(k.sttTitle, "", stt ? `${k.sttProviders[stt.provider]} · ${k.last4(stt.key.slice(-4))}` : "", "stt")}
        ${service(k.visionTitle, k.visionHint, vision ? `${vision.model} · ${k.last4(vision.key.slice(-4))}` : "", "vision")}
        ${service(k.imageTitle, k.imageHint, image ? `${image.model} · ${k.last4(image.key.slice(-4))}` : "", "image")}
        </div>
      </div>`;
    }
    case "sign-in": {
      const s = T.settings.signIn;
      const ghost = `${BUTTON_BASE} h-8 gap-1.5 px-2.5 hover:bg-muted hover:text-foreground`;
      const outline = `${BUTTON_OUTLINE} h-8 gap-1.5 px-2.5 self-start`;
      return `<div id="sign-in-page">
        ${settingsSection("", { title: s.methodsTitle, description: s.methodsHint })}
        ${settingsSection(
          `<div class="flex flex-wrap gap-2">
            <button type="button" class="${ghost}">${s.signOut}</button>
            <button type="button" class="${ghost}">${s.signOutEverywhere}</button>
          </div>`,
          { title: s.signOutTitle }
        )}
        ${settingsSection(
          `<button type="button" id="delete-data" class="${outline}">${s.deleteStart}</button>
           <div id="delete-dialog" class="fixed inset-0 z-50 hidden items-center justify-center p-4">
             <div class="absolute inset-0 bg-black/50" data-close="delete"></div>
             <div class="relative flex w-full max-w-md flex-col gap-4 rounded-xl border bg-background p-6 shadow-lg">
               <div class="flex flex-col gap-2">
                 <h2 class="text-lg font-semibold">${s.deleteConfirmTitle}</h2>
                 <p class="text-sm text-muted-foreground">${s.deleteConfirmHint}</p>
               </div>
               <div class="flex flex-row justify-end gap-2">
                 <button type="button" data-close="delete" class="${ghost}">${T.common.cancel}</button>
                 <button type="button" data-close="delete" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 bg-destructive text-white hover:bg-destructive/90">${s.deleteConfirm}</button>
               </div>
             </div>
           </div>`,
          { title: s.deleteTitle, description: s.deleteHint }
        )}
      </div>`;
    }
    case "data-package": {
      const d = T.settings.dataPackage;
      const cats = [
        ["conversations", d.categories.conversations],
        ["photos", d.categories.photos],
        ["memories", d.categories.memories],
        ["profile", d.categories.profile],
      ];
      const rows = cats
        .map(
          ([value, label], i) => `<li>
            <label class="flex items-center gap-3 py-1.5${i % 2 ? " pl-7" : ""}">
              ${checkboxEl(true, false)}
              <span class="text-body">${label}</span>
            </label>
          </li>`
        )
        .join("");
      return `${settingsSection(
        `<ul class="flex flex-col">${rows}</ul>
         <div class="flex flex-col items-start">
           <a href="/api/export" download="scbot-data.json" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80">${d.export}</a>
         </div>`,
        { title: d.exportTitle, description: d.exportHint }
      )}${settingsSection(
        `<div class="flex flex-col items-start gap-3" id="import-box">
           <button type="button" id="import-pick" class="${BUTTON_OUTLINE} h-8 gap-1.5 px-2.5">${d.chooseFile}</button>
           <input type="file" id="import-file" accept=".json,application/json" class="hidden">
           <div id="import-choice" class="hidden w-full flex-col gap-3">
             <ul class="flex flex-col">${rows}</ul>
             <button type="button" id="import-run" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80">${d.import}</button>
           </div>
           <p id="import-status" role="status" class="text-body text-muted-foreground"></p>
         </div>`,
        { title: d.importTitle, description: d.importHint }
      )}`;
    }
    case "about": {
      const a = T.settings.about;
      const rows = [
        `<li><a href="/about" class="flex min-h-12 w-full items-center gap-3 px-4 py-2.5 text-body outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
          <span class="flex min-w-0 flex-1 flex-col"><span class="truncate">${a.site}</span></span>
          ${icon("forward", "size-4 shrink-0 text-muted-foreground")}
        </a></li>`,
        `<li><a href="https://github.com/Sallyn0225/eidolon-sc" target="_blank" rel="noopener noreferrer" class="flex min-h-12 w-full items-center gap-3 px-4 py-2.5 text-body outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
          <span class="flex min-w-0 flex-1 flex-col"><span class="truncate">${a.repository}</span></span>
          ${icon("externalLink", "size-4 shrink-0 text-muted-foreground")}
        </a></li>`,
        `<li><a href="/privacy" class="flex min-h-12 w-full items-center gap-3 px-4 py-2.5 text-body outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
          <span class="flex min-w-0 flex-1 flex-col"><span class="truncate">${a.privacy}</span></span>
          ${icon("forward", "size-4 shrink-0 text-muted-foreground")}
        </a></li>`,
        `<li><a href="/terms" class="flex min-h-12 w-full items-center gap-3 px-4 py-2.5 text-body outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset">
          <span class="flex min-w-0 flex-1 flex-col"><span class="truncate">${a.terms}</span></span>
          ${icon("forward", "size-4 shrink-0 text-muted-foreground")}
        </a></li>`,
      ].join("");
      return `<div class="flex flex-col">
        <ul class="-mx-4">${rows}</ul>
        <p class="py-4 text-meta text-muted-foreground">${a.disclaimer}</p>
      </div>`;
    }
    default:
      return `<p class="text-body text-muted-foreground">${T.common.notOpen}</p>`;
  }
}

/** 一个月的天数：闰年照原版算。 */
function daysInMonth(month) {
  return [31, (isLeap() ? 29 : 28), 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1] || 31;
}

function isLeap() {
  const y = new Date().getFullYear();
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

function characterName(id) {
  const c = state.characters.find((x) => x.id === id);
  return c ? c.name : "";
}

function avatarUrlOf(id) {
  const c = state.characters.find((x) => x.id === id);
  if (!c) return "";
  return state.scsp && c.scsp_avatar_url ? c.scsp_avatar_url : c.avatar_url;
}

function settingsPage() {
  const category = route.category || "profile";
  // 原版：记忆页页首右边是「清空全部记忆」（有记忆时才出现），统计页是「分享」。
  const end =
    category === "memories" && state.memories.length > 0
      ? `<button type="button" id="clear-memories" class="group/button inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-transparent bg-clip-padding px-2.5 text-[0.8rem] font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground">${esc(T.memories.clear)}</button>`
      : category === "stats"
        ? `<button type="button" id="share-stats" class="group/button inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-transparent bg-clip-padding px-2.5 text-[0.8rem] font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 hover:bg-muted hover:text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0">${icon("share", "size-3.5")}${esc(T.settings.stats.share.button)}</button>
           <div id="share-dialog" class="fixed inset-0 z-50 hidden items-center justify-center p-4">
             <div class="absolute inset-0 bg-black/50" data-close="share"></div>
             <div class="relative flex max-h-[calc(100dvh-2rem)] w-full max-w-md flex-col gap-4 overflow-y-auto rounded-xl border bg-background p-6 shadow-lg">
               <div class="flex flex-col gap-2">
                 <h2 class="text-lg font-semibold">${esc(T.settings.stats.share.button)}</h2>
                 <p class="text-sm text-muted-foreground">${esc(T.settings.stats.share.hint)}</p>
               </div>
               <div id="share-card" class="overflow-hidden rounded-xl border"></div>
               <div class="flex flex-row justify-end gap-2">
                 <button type="button" data-close="share" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 hover:bg-muted hover:text-foreground">${T.common.close}</button>
                 <button type="button" id="share-download" class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80">${esc(T.settings.stats.share.download)}</button>
               </div>
             </div>
           </div>`
        : "";
  return `<div class="flex h-full bg-background text-foreground">
    ${settingsLevelOne()}
    <div class="min-w-0 flex-1 ${route.category ? "" : "max-md:hidden"}">${settingsPane(category, end)}</div>
  </div>`;
}

/* ------------------------------------------------------------------ 礼物页 */

/** 下次到账的时刻：今天 / 明天写「今天 0:00」，再远写月日。 */
function nextDrawText(at) {
  const d = new Date(at);
  const now = new Date();
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(d) - startOf(now)) / 86400000);
  if (days <= 0) return `${T.chat.affinity.today} ${clock(at)}`;
  if (days === 1) return `${T.chat.affinity.tomorrow} ${clock(at)}`;
  return `${T.list.monthDay(d.getMonth() + 1, d.getDate())} ${clock(at)}`;
}

function giftById(id) {
  return (state.giftCatalog || []).find((g) => g.id === id) || null;
}

/** 抽奖台：还剩几次、7 个点、下次什么时候发，右边「抽一次 / 全部抽完」。 */
function drawDesk() {
  const g = state.gifts;
  const t = T.gifts;
  const next = g.next_draw_at ? nextDrawText(g.next_draw_at) : "";
  const note =
    g.draws >= g.max_draws ? t.draw.full(g.max_draws) : g.draws === 0 ? t.draw.none(next) : t.draw.usual(next, g.max_draws);
  const dots = Array.from({ length: g.max_draws }, (_, i) => {
    const on = i < g.draws;
    return `<span style="width:8px;height:8px;border-radius:9999px;background:${on ? "currentColor" : "color-mix(in oklch, currentColor 15%, transparent)"}"></span>`;
  }).join("");
  return `<section aria-label="${t.draw.title}" class="flex flex-wrap items-center gap-4 rounded-2xl bg-muted p-5">
    <div class="flex min-w-0 flex-1 flex-col gap-2">
      <p class="flex items-baseline gap-2">
        <span class="text-header">${t.draw.title}</span>
        <span class="text-sm text-muted-foreground tabular-nums">${t.draw.left(g.draws)}</span>
      </p>
      <span role="img" aria-label="${t.draw.label(g.draws, g.max_draws)}" class="flex gap-1">${dots}</span>
      <p class="text-subtitle text-muted-foreground">${note}</p>
    </div>
    <div class="flex gap-2">
      ${g.draws >= 2 ? `<button type="button" data-gift-draw="all" class="${BUTTON_OUTLINE} h-8 px-2.5">${t.draw.all}</button>` : ""}
      <button type="button" data-gift-draw="one"${g.draws === 0 ? " disabled" : ""} class="${BUTTON_BASE} h-8 gap-1.5 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80 disabled:pointer-events-none disabled:opacity-50">${icon("draw", "size-4")}${t.draw.once}</button>
    </div>
  </section>`;
}

/** 重置卡条：有几张、现在用会拉回谁。 */
function resetStrip() {
  const g = state.gifts;
  const t = T.gifts.reset;
  const resettable = g.resettable || [];
  const note = resettable.length ? t.some(resettable.length) : t.none;
  return `<section class="flex flex-wrap items-center gap-4 rounded-2xl border p-4">
    <div class="flex min-w-0 flex-1 flex-col gap-1">
      <p class="flex items-baseline gap-2"><span class="text-header">${t.title}</span><span class="text-sm text-muted-foreground tabular-nums">${T.gifts.count(g.reset_cards)}</span></p>
      <p class="text-subtitle text-muted-foreground">${note}</p>
    </div>
    <button type="button" data-gift-card="confirm"${resettable.length ? "" : " disabled"} class="${BUTTON_BASE} h-8 px-2.5 ${resettable.length ? "bg-secondary text-secondary-foreground hover:bg-secondary/80" : "border border-border opacity-50"}">${t.use}</button>
  </section>`;
}

/** 我的礼物：类别筛选 + 一格一件的网格。 */
function myGifts() {
  const g = state.gifts;
  const t = T.gifts;
  const items = g.backpack.map((b) => ({ ...b, gift: giftById(b.gift_id) })).filter((b) => b.gift);
  const filters = ["all", "food", "drink", "flower", "trinket"];
  const tabs = filters
    .map((key) => {
      const on = state.giftFilter === key;
      return `<button type="button" data-gift-filter="${key}"${on ? ' aria-current="true"' : ""} class="rounded-lg px-2.5 py-1 text-sm ${on ? "bg-foreground text-background" : "bg-muted text-muted-foreground hover:bg-muted/70"}">${T.gifts.filters[key]}</button>`;
    })
    .join("");
  const shown = state.giftFilter === "all" ? items : items.filter((i) => i.gift.category === state.giftFilter);
  const grid = shown.length
    ? `<ul class="grid grid-cols-3 gap-3">${shown
        .map(
          (item) => `<li>
        <button type="button" data-gift-hold="${esc(item.gift.id)}" aria-label="${esc(T.gifts.held(item.gift.name, item.count, T.gifts.rarity[item.gift.rarity]))}" class="flex w-full flex-col items-center gap-1 rounded-xl border bg-background p-2 text-center hover:bg-muted">
          <img src="/assets/gifts/${esc(item.gift.id)}.webp" alt="" style="width:72px;height:72px;object-fit:contain">
          <span class="w-full truncate text-sm">${esc(item.gift.name)}</span>
          <span class="text-meta text-muted-foreground tabular-nums">${T.gifts.count(item.count)} · ${T.gifts.rarity[item.gift.rarity]}</span>
        </button>
      </li>`
        )
        .join("")}</ul>`
    : `<p class="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">${state.giftFilter === "all" ? "" : T.gifts.noneOfKind}</p>`;
  return `<section class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="min-w-0 flex-1 text-header">${t.mine}</h2>
      <div role="group" aria-label="${esc(t.filters.all)}" class="flex flex-wrap gap-1">${tabs}</div>
    </div>
    ${items.length ? grid : giftEmpty(g.draws > 0 ? t.emptyWithDraws : t.emptyWithoutDraws(g.next_draw_at ? nextDrawText(g.next_draw_at) : ""))}
  </section>`;
}

function giftEmpty(description) {
  return emptyState({
    iconHTML: icon("gift"),
    title: T.gifts.emptyTitle,
    description: esc(description),
  });
}

/** 抽奖结果：一层遮罩 + 一张卡，列出抽到的每一样。 */
function drawDialog() {
  const drawing = state.giftDrawing;
  if (!drawing) return "";
  const t = T.gifts.drawResult;
  const result = drawing.result;
  if (!result) {
    return `<div class="absolute inset-0 z-40 flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/40"></div>
      <div class="relative flex items-center gap-2 rounded-xl border bg-background px-4 py-3 shadow-lg">
        ${icon("spinner", "size-4")}<span class="text-sm">${T.gifts.loading}</span>
      </div>
    </div>`;
  }
  const items = result.drawn
    .map((d) => {
      if (d.kind === "reset_card") {
        return `<li class="flex items-center gap-3 rounded-xl border p-2">
          <span class="flex size-16 items-center justify-center rounded-lg bg-muted">${icon("draw", "size-6")}</span>
          <span class="flex min-w-0 flex-1 flex-col"><span class="text-sm">${t.resetName}</span><span class="text-meta text-muted-foreground">${t.resetNote}</span></span>
        </li>`;
      }
      const gift = giftById(d.gift_id);
      return `<li class="flex items-center gap-3 rounded-xl border p-2">
        <img src="/assets/gifts/${esc(d.gift_id)}.webp" alt="" style="width:64px;height:64px;object-fit:contain">
        <span class="flex min-w-0 flex-1 flex-col"><span class="text-sm">${esc(gift ? gift.name : d.gift_id)}</span><span class="text-meta text-muted-foreground">${gift ? T.gifts.rarity[gift.rarity] : ""}</span></span>
        ${gift ? `<button type="button" data-gift-give="${esc(d.gift_id)}" class="${BUTTON_OUTLINE} h-8 px-2.5">${t.give}</button>` : ""}
      </li>`;
    })
    .join("");
  return `<div class="absolute inset-0 z-40 flex items-center justify-center p-4" id="draw-layer">
    <div class="absolute inset-0 bg-black/40" data-gift-close="draw"></div>
    <div class="relative flex max-h-[80dvh] w-full max-w-sm flex-col gap-3 rounded-xl border bg-background p-4 shadow-lg">
      <h2 class="text-header">${result.drawn.length > 1 ? t.all(result.drawn.length) : t.one}</h2>
      <ul class="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">${items}</ul>
      <div class="flex flex-wrap gap-2">
        <button type="button" data-gift-close="draw" class="${BUTTON_OUTLINE} h-8 px-2.5">${t.keep}</button>
        ${result.draws > 0 ? `<button type="button" data-gift-draw="one" class="${BUTTON_BASE} h-8 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80">${t.again(result.draws)}</button>` : ""}
      </div>
    </div>
  </div>`;
}

/** 送礼面板：选一位，送出。 */
function givePanel() {
  const giving = state.giftGiving;
  if (!giving) return "";
  const t = T.gifts.give;
  const to = state.giftGiving.to;
  const rows = (state.characters || [])
    .map((c) => {
      const status = (state.gifts.characters || []).find((s) => s.id === c.id);
      const on = c.id === to;
      const tier = status ? T.chat.affinity.tiers[status.tier] || "" : "";
      return `<button type="button" data-gift-to="${esc(c.id)}"${on ? ' aria-current="true"' : ""} class="flex items-center gap-2 rounded-lg border px-2 py-1.5 text-left ${on ? "border-foreground bg-muted" : "hover:bg-muted/60"}">
        ${avatarEl(c.name, avatarUrlOf(c.id), "size-6")}
        <span class="min-w-0 flex-1 truncate text-sm">${esc(c.name)}</span>
        <span class="text-meta text-muted-foreground">${status && status.cooling ? t.cooling : tier}</span>
      </button>`;
    })
    .join("");
  const chosen = (state.characters || []).find((c) => c.id === to);
  return `<div class="absolute inset-0 z-40 flex items-end justify-center p-4" id="give-layer">
    <div class="absolute inset-0 bg-black/40" data-gift-close="give"></div>
    <div class="relative flex max-h-[80dvh] w-full max-w-md flex-col gap-3 rounded-xl border bg-background p-4 shadow-lg">
      <div class="flex items-center gap-2">
        <h2 class="min-w-0 flex-1 truncate text-header">${T.gifts.giveHer}</h2>
        <button type="button" data-gift-close="give" aria-label="${T.common.close}" class="inline-flex size-8 items-center justify-center rounded-lg hover:bg-muted">${icon("close", "size-4")}</button>
      </div>
      <div class="flex items-center gap-3 rounded-xl border p-2">
        <img src="/assets/gifts/${esc(giving.gift)}.webp" alt="" style="width:56px;height:56px;object-fit:contain">
        <span class="flex min-w-0 flex-1 flex-col">
          <span class="text-sm">${esc((giftById(giving.gift) || {}).name || giving.gift)}</span>
          <span class="text-meta text-muted-foreground tabular-nums">${t.left(giving.left)}</span>
        </span>
      </div>
      <p class="text-sm text-muted-foreground">${t.to}</p>
      <div class="grid max-h-64 grid-cols-2 gap-1 overflow-y-auto">${rows || `<p class="text-sm text-muted-foreground">${t.choose}</p>`}</div>
      <div class="flex justify-end gap-2">
        <button type="button" data-gift-close="give" class="${BUTTON_OUTLINE} h-8 px-2.5">${T.common.cancel}</button>
        <button type="button" data-gift-send="${esc(giving.gift)}"${chosen && giving.left > 0 ? "" : " disabled"} class="${BUTTON_BASE} h-8 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80 disabled:pointer-events-none disabled:opacity-50">${chosen ? t.send(chosen.name) : t.choose}</button>
      </div>
    </div>
  </div>`;
}

/** 用重置卡之前的确认框：列出会被拉回的那几位。 */
function resetCardDialog() {
  if (!state.giftCardConfirm) return "";
  const t = T.gifts.reset;
  const rows = (state.gifts.resettable || [])
    .map((c) => {
      const character = (state.characters || []).find((x) => x.id === c.id);
      const name = character ? character.name : c.id;
      return `<li class="flex items-center gap-2">${avatarEl(name, avatarUrlOf(c.id), "size-6")}<span class="min-w-0 flex-1 truncate text-sm">${esc(name)}</span><span class="text-meta text-muted-foreground">${T.chat.affinity.tiers[c.tier] || ""}</span></li>`;
    })
    .join("");
  return `<div class="absolute inset-0 z-40 flex items-center justify-center p-4" id="card-layer">
    <div class="absolute inset-0 bg-black/40" data-gift-close="card"></div>
    <div class="relative flex w-full max-w-sm flex-col gap-3 rounded-xl border bg-background p-4 shadow-lg">
      <h2 class="text-header">${t.confirmTitle}</h2>
      <p class="text-sm text-muted-foreground">${t.confirmDescription}</p>
      <ul class="flex flex-col gap-1.5">${rows}</ul>
      <div class="flex justify-end gap-2">
        <button type="button" data-gift-close="card" class="${BUTTON_OUTLINE} h-8 px-2.5">${T.common.cancel}</button>
        <button type="button" data-gift-card="use" class="${BUTTON_BASE} h-8 px-2.5 bg-primary text-primary-foreground hover:bg-primary/80">${t.confirm}</button>
      </div>
    </div>
  </div>`;
}

/** 送出后的提示条，停在页面底部。 */
function giftToast() {
  const notice = state.giftNotice;
  if (!notice) return "";
  return `<div role="status" class="pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center px-4">
    <div class="pointer-events-auto flex items-center gap-3 rounded-xl bg-foreground px-3 py-2 text-sm text-background shadow-lg">
      <span class="min-w-0 truncate">${esc(notice)}</span>
    </div>
  </div>`;
}

/** 原版 GiftsPage：上面抽奖台、重置卡条、「我的礼物」，下面是三个浮层。 */
function giftsPage() {
  const g = state.gifts;
  const failure = state.giftFailure;
  const closed = state.capabilities && state.capabilities.gifts && state.capabilities.gifts.enabled === false;
  const head = `<header class="shrink-0 border-b">
      <div class="flex min-h-14 items-center px-4"><h1 class="text-list-title">${T.gifts.title}</h1></div>
    </header>`;
  let body = giftEmpty(T.gifts.loading);
  if (closed) {
    body = emptyState({
      iconHTML: icon("gift"),
      title: T.gifts.closedTitle,
      description: T.gifts.closedDescription,
    });
  } else if (g) {
    body = `${drawDesk()}
        ${g.reset_cards > 0 ? resetStrip() : ""}
        ${myGifts()}`;
  }
  return `<div class="flex h-full flex-col bg-background text-foreground">
    ${head}
    <main class="relative flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div class="flex flex-1 flex-col">
        <div class="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
          ${failure ? `<p class="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">${esc(failure)}</p>` : ""}
          ${body}
        </div>
        ${giftToast()}
      </div>
    </main>
    ${drawDialog()}
    ${givePanel()}
    ${resetCardDialog()}
  </div>`;
}

/* ------------------------------------------------------------------ 装配 */

/** 原版 conversations/layout.tsx 的双栏外壳：宽屏左栏是列表（320px 起、可拖 240–480），
 *  右栏是这一路由的内容；窄屏只露一级页面的那一栏。 */
function conversationsShell(children) {
  const character = state.characters.find((c) => c.id === state.selectedId);
  const list = conversationList();
  if (state.wide) {
    return `<div class="flex h-full flex-col bg-background text-foreground">
      <main class="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <div data-slot="resizable-panel-group" data-orientation="horizontal" class="flex h-full w-full min-h-0 flex-1">
          <div data-slot="resizable-panel" data-panel-id="list" class="shrink-0" style="width:${state.listWidth}px">
            <nav aria-label="${T.list.title}" class="h-full">${list}</nav>
          </div>
          <div data-slot="resizable-handle" role="separator" aria-orientation="vertical" aria-label="${T.list.resize}" tabindex="0" id="list-resize" class="relative flex w-px shrink-0 items-center justify-center bg-border ring-offset-background after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-hidden"></div>
          <div data-slot="resizable-panel" data-panel-id="content" class="min-w-0 flex-1">
            <div class="relative h-full">${children}</div>
          </div>
        </div>
      </main>
    </div>`;
  }
  return `<div class="flex h-full flex-col bg-background text-foreground">
    <main class="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <div class="flex min-h-0 flex-1 overflow-hidden">
        <nav aria-label="${T.list.title}" class="h-full w-(--list-width,20rem) shrink-0 max-md:w-full${character ? " max-md:hidden" : ""}">
          ${list}
        </nav>
        <div class="w-px shrink-0 bg-border max-md:hidden"></div>
        <div class="relative h-full min-w-0 flex-1${character ? "" : " max-md:hidden"}">${children}</div>
      </div>
    </main>
  </div>`;
}

/** 没选角色时右栏的一句提示（原版 noneSelected）。 */
function noneSelected() {
  return `<div class="flex h-full items-center justify-center p-6">
    <h2 class="text-center text-header text-muted-foreground">${T.list.noneSelected}</h2>
  </div>`;
}

function contentArea() {
  if (route.path === "/settings") return settingsPage();
  if (route.path === "/gifts") return giftsPage();
  const character = state.characters.find((c) => c.id === state.selectedId);
  const children = character ? conversationView(character) : noneSelected();
  if (character || state.wide) return conversationsShell(children);
  // 窄屏、没选角色：列表占满整页（原版 hydrated 分支：内容栏里放列表）。
  return `<div class="flex h-full flex-col bg-background text-foreground">
    <main class="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <div class="flex min-h-0 flex-1 overflow-hidden">
        <div class="relative h-full min-w-0 flex-1">${conversationList()}</div>
      </div>
    </main>
  </div>`;
}

function render() {
  Object.assign(route, parseRoute());
  state.selectedId = route.characterId;
  if (state.characters.length) state.prefs = loadPrefs();
  const app = document.getElementById("app");
  app.innerHTML = `<div class="flex h-dvh flex-col md:flex-row">
    ${navRail()}
    <div class="flex min-h-0 min-w-0 flex-1 flex-col">
      <div class="relative min-h-0 flex-1">
        <div class="h-full">${contentArea()}</div>
      </div>
    </div>
    ${navDock()}
  </div>`;
  wire();
  // 消息区打开时停在最底（原版 MessageScroller 的 defaultScrollPosition="end"）。
  const viewport = document.getElementById("message-viewport");
  if (viewport) viewport.scrollTop = viewport.scrollHeight;
  if (route.category === "stats") {
    if (!state.stats && !state.statsFailed) void loadStats();
    else if (state.stats) {
      drawHeatmap();
      drawHourChart();
    }
  }
}

/**
 * 点浮层外面就收起（原版 Popover/Dialog 的通用行为）。
 *
 * 以前每次调用都 `document.addEventListener("click", …)`，而它在 `wire()` 里被调、
 * `wire()` 每次 `render()` 都跑——于是每输入一个字、每勾一次选，document 上就多挂
 * 一个 listener。多挂的 listener 靠 `close()` 内部的 `if (state.filterOpen)` 保护
 * 没炸，但长会话下 listener 数量一直涨。现在改成：只挂一次 document click，由它
 * 扫一份注册表；`wire()` 只更新注册表里的 close 回调，不再重复挂 listener。
 */
const _outsideClosers = [];
let _outsideListenerOn = false;
function outsideClose(wrapId, close) {
  // 同一个 wrap 只留最新的一份 close（render 重跑时旧的该被替掉，不能叠着留）。
  const existing = _outsideClosers.find((c) => c.id === wrapId);
  if (existing) existing.close = close;
  else _outsideClosers.push({ id: wrapId, close });
  if (_outsideListenerOn) return;
  _outsideListenerOn = true;
  document.addEventListener("click", (event) => {
    for (const { id, close } of _outsideClosers) {
      const wrap = document.getElementById(id);
      if (!wrap || wrap.classList.contains("hidden")) continue;
      if (wrap.contains(event.target)) continue;
      close();
    }
  });
}

function wire() {
  document.querySelectorAll("a[href^='/']").forEach((a) => {
    a.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      go(a.getAttribute("href"));
    });
  });
  // 对话列表的行：原版是整行一个 button，点了换一位角色。
  document.querySelectorAll("button[data-character]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.getAttribute("data-character");
      if (!id || id === state.selectedId) return;
      go(`/conversation/${id}`);
      state.messages = [];
      state.affinity = null;
      void loadState().then(() => render());
    });
  });
  // 统计页的跨度。
  document.querySelectorAll("[data-range]").forEach((button) => {
    button.addEventListener("click", () => {
      const range = button.getAttribute("data-range");
      if (!range || range === state.statsRange) return;
      state.statsRange = range;
      state.stats = null;
      state.statsFailed = false;
      void loadStats();
    });
  });

  /* ---- 对话列表：搜索、筛选、置顶/隐藏的右键菜单 ---- */

  const search = document.getElementById("list-search");
  if (search) {
    search.addEventListener("input", () => {
      state.query = search.value;
      render();
      const box = document.getElementById("list-search");
      if (box) {
        box.focus();
        box.setSelectionRange(box.value.length, box.value.length);
      }
    });
  }
  const filterButton = document.getElementById("filter-button");
  if (filterButton) {
    filterButton.addEventListener("click", (event) => {
      event.stopPropagation();
      state.filterOpen = !state.filterOpen;
      render();
    });
  }
  outsideClose("filter-wrap", () => {
    if (state.filterOpen) {
      state.filterOpen = false;
      render();
    }
  });
  const showAll = document.getElementById("filter-show-all");
  if (showAll) {
    showAll.addEventListener("click", () => tidy(showAll));
  }
  // 筛选面板里的勾选框：点 label 就算勾上/取消。
  document.querySelectorAll("#filter-groups label").forEach((label) => {
    label.addEventListener("click", (event) => {
      event.preventDefault();
      const box = label.querySelector("[role='checkbox']");
      const checked = box && box.getAttribute("aria-checked") === "true";
      const row = label.closest("li");
      if (row) {
        const name = row.querySelector("span.truncate");
        const character = state.characters.find((c) => c.name === (name && name.textContent));
        if (character) tidy((p) => setShown(p, [character.id], !checked));
        return;
      }
      // 组标题：整组一起勾/取消。
      const section = label.closest("section");
      const ids = [...(section ? section.querySelectorAll("li") : [])]
        .map((li) => {
          const name = li.querySelector("span.truncate");
          const found = state.characters.find((c) => c.name === (name && name.textContent));
          return found ? found.id : null;
        })
        .filter(Boolean);
      if (ids.length) tidy((p) => setShown(p, ids, !checked));
    });
  });
  ["open-filter-empty", "open-filter-hidden"].forEach((id) => {
    const button = document.getElementById(id);
    if (button) {
      button.addEventListener("click", () => {
        state.filterOpen = true;
        render();
      });
    }
  });
  // 一行的右键 / 长按菜单（原版 ContextMenu）。
  const menu = document.getElementById("row-menu");
  document.querySelectorAll("[data-row]").forEach((row) => {
    const openMenu = (x, y) => {
      if (!menu) return;
      menu.style.left = `${Math.min(x, window.innerWidth - 160)}px`;
      menu.style.top = `${Math.min(y, window.innerHeight - 90)}px`;
      menu.classList.remove("hidden");
    };
    row.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      openMenu(event.clientX, event.clientY);
    });
    let timer = null;
    row.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse") return;
      timer = window.setTimeout(() => openMenu(event.clientX, event.clientY), 500);
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach((type) =>
      row.addEventListener(type, () => timer && window.clearTimeout(timer))
    );
  });
  if (menu) {
    menu.querySelectorAll("[data-menu]").forEach((item) => {
      item.addEventListener("click", () => {
        const id = item.getAttribute("data-character");
        const action = item.getAttribute("data-menu");
        menu.classList.add("hidden");
        if (action === "pin") tidy((p) => pinOf(p, id));
        if (action === "unpin") tidy((p) => unpinOf(p, id));
        if (action === "hide") tidy((p) => setShown(p, [id], false));
        if (action === "show") tidy((p) => setShown(p, [id], true));
      });
    });
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) menu.classList.add("hidden");
    });
  }
  // 服务状态和公告的小窗口。
  const statusButton = document.getElementById("status-button");
  if (statusButton) {
    statusButton.addEventListener("click", (event) => {
      event.stopPropagation();
      state.statusOpen = !state.statusOpen;
      render();
      // 原版：打开小窗口时再拉一次状态。
      if (state.statusOpen) void loadServiceStatus().then(render);
    });
  }
  outsideClose("status-wrap", () => {
    if (state.statusOpen) {
      state.statusOpen = false;
      render();
    }
  });
  const announcementsButton = document.getElementById("announcements-button");
  if (announcementsButton) {
    announcementsButton.addEventListener("click", (event) => {
      event.stopPropagation();
      state.announcementsOpen = !state.announcementsOpen;
      render();
    });
  }
  document.querySelectorAll("[data-close='announcements']").forEach((button) => {
    button.addEventListener("click", () => {
      state.announcementsOpen = false;
      state.announcementsReadAt = new Date().toISOString();
      render();
    });
  });

  /* ---- 对话页：更多菜单、好感度标识、附件面板、电话键 ---- */

  const toggle = document.getElementById("attach-toggle");
  const panel = document.getElementById("attach-panel");
  const picker = document.getElementById("file-picker");
  if (toggle && panel) {
    toggle.addEventListener("mousedown", (event) => event.preventDefault());
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      state.panelOpen = !state.panelOpen;
      render();
    });
    const draftBox = document.getElementById("draft");
    if (draftBox) {
      draftBox.addEventListener("focus", () => {
        if (state.panelOpen) {
          state.panelOpen = false;
          render();
        }
      });
      // 手机上打字（键盘弹起）时藏起底部 dock（原版 useTyping）。
      const dock = document.getElementById("nav-dock");
      if (dock) dock.classList.add("hidden");
    }
  }
  const pick = document.getElementById("pick-picture");
  if (pick && picker) pick.addEventListener("click", () => picker.click());
  if (picker) {
    picker.addEventListener("change", async () => {
      for (const file of Array.from(picker.files || []).slice(0, 4)) {
        if (!file.type.startsWith("image/")) continue;
        const data = await shrinkPicture(file);
        const out = await postJSON("/api/photos", { data });
        state.photos.push(out);
      }
      picker.value = "";
      state.panelOpen = false;
      render();
    });
  }
  document.querySelectorAll("[data-remove-photo]").forEach((button) => {
    button.addEventListener("click", () => {
      state.photos = state.photos.filter((p) => p.id !== button.getAttribute("data-remove-photo"));
      render();
    });
  });
  const draft = document.getElementById("draft");
  if (draft) {
    draft.addEventListener("blur", () => {
      const dock = document.getElementById("nav-dock");
      if (dock) dock.classList.remove("hidden");
    });
    draft.addEventListener("input", () => {
      state.draft = draft.value;
      draft.style.height = "auto";
      draft.style.height = `${draft.scrollHeight}px`;
      // 草稿变了，发送键的禁用态跟着变（原版 canSend）。
      const send = document.getElementById("send");
      if (send) {
        const canSend = state.draft.trim() !== "" || state.photos.length > 0;
        if (canSend) send.removeAttribute("disabled");
        else send.setAttribute("disabled", "");
      }
    });
    draft.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        document.getElementById("composer")?.requestSubmit();
      }
    });
  }
  const form = document.getElementById("composer");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void send();
    });
  }
  const more = document.getElementById("more-button");
  const menu2 = document.getElementById("more-menu");
  if (more && menu2) {
    more.addEventListener("click", (event) => {
      event.stopPropagation();
      const hidden = menu2.classList.toggle("hidden");
      menu2.classList.toggle("flex", !hidden);
      more.setAttribute("aria-expanded", String(!hidden));
    });
    document.addEventListener("click", (event) => {
      if (!menu2.contains(event.target) && event.target !== more && !more.contains(event.target)) {
        menu2.classList.add("hidden");
        menu2.classList.remove("flex");
        more.setAttribute("aria-expanded", "false");
      }
    });
    menu2.querySelectorAll("[data-action]").forEach((item) => {
      item.addEventListener("click", async () => {
        menu2.classList.add("hidden");
        menu2.classList.remove("flex");
        const action = item.getAttribute("data-action");
        if (action === "new-conversation") {
          const out = await postJSON(`/api/conversations?c=${state.selectedId}`, {});
          state.conversationId = out.conversation_id;
          await loadConversation();
          render();
        }
        if (action === "past-conversations") {
          state.pastOpen = true;
          state.pastList = null;
          render();
          try {
            const out = await getJSON(`/api/conversations?c=${encodeURIComponent(state.selectedId)}`);
            state.pastList = (out.conversations || []).map((row) => ({
              id: row.id,
              at: row.created_at,
              preview: row.preview || "",
            }));
          } catch {
            state.pastList = [];
          }
          render();
        }
      });
    });
  }
  // 历史对话面板（原版 PastConversations）：列出这位角色的所有对话，点一段就看那段。
  const pastButton = document.getElementById("past-button");
  if (pastButton) {
    pastButton.addEventListener("click", () => {
      state.pastOpen = false;
      render();
    });
  }
  document.querySelectorAll("[data-close='past']").forEach((button) => {
    button.addEventListener("click", () => {
      state.pastOpen = false;
      render();
    });
  });
  document.querySelectorAll("[data-past-row]").forEach((row) => {
    row.addEventListener("click", () => {
      state.conversationId = row.getAttribute("data-past-row");
      state.pastOpen = false;
      void loadConversation().then(() => render());
    });
  });
  document.querySelectorAll("[data-past-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      await postJSON(`/api/conversations/delete`, { id: button.getAttribute("data-past-delete") });
      await loadRoster();
      if (state.conversationId === button.getAttribute("data-past-delete")) {
        state.conversationId = null;
        await loadState();
      }
      render();
    });
  });
  const affinityButton = document.getElementById("affinity-button");
  if (affinityButton) {
    affinityButton.addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = document.getElementById("affinity-popover");
      const hidden = popover.classList.toggle("hidden");
      popover.classList.toggle("flex", !hidden);
      affinityButton.setAttribute("aria-expanded", String(!hidden));
    });
  }
  const callButton = document.getElementById("call-button");
  if (callButton) {
    callButton.addEventListener("click", () => {
      state.callNote = T.common.notOpen;
      render();
    });
  }
  // 礼物页：抽一次 / 全部抽完、点一件送礼、选人、送出、用重置卡、切类别、关浮层。
  document.querySelectorAll("[data-gift-draw]").forEach((button) => {
    button.addEventListener("click", () => void drawGifts(button.getAttribute("data-gift-draw") === "all"));
  });
  document.querySelectorAll("[data-gift-hold], [data-gift-give]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.getAttribute("data-gift-hold") || button.getAttribute("data-gift-give");
      if (id) openGive(id);
    });
  });
  document.querySelectorAll("[data-gift-to]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!state.giftGiving) return;
      state.giftGiving = { ...state.giftGiving, to: button.getAttribute("data-gift-to") };
      render();
    });
  });
  document.querySelectorAll("[data-gift-send]").forEach((button) => {
    button.addEventListener("click", () => void sendGift());
  });
  document.querySelectorAll("[data-gift-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.giftFilter = button.getAttribute("data-gift-filter") || "all";
      render();
    });
  });
  document.querySelectorAll("[data-gift-card]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.getAttribute("data-gift-card") === "use") void useResetCard();
      else {
        state.giftCardConfirm = true;
        render();
      }
    });
  });
  document.querySelectorAll("[data-gift-close]").forEach((element) => {
    element.addEventListener("click", () => {
      const what = element.getAttribute("data-gift-close");
      if (what === "draw") state.giftDrawing = null;
      if (what === "give") state.giftGiving = null;
      if (what === "card") state.giftCardConfirm = false;
      render();
    });
  });
  // 列表宽窄：拖右边的线在 240–480 之间调（原版 useSavedSize + ResizableHandle）。
  const handle = document.getElementById("list-resize");
  if (handle) {
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = state.listWidth;
      const move = (e) => {
        const width = Math.min(480, Math.max(240, startWidth + e.clientX - startX));
        if (width !== state.listWidth) {
          state.listWidth = width;
          const panel = document.querySelector("[data-panel-id='list']");
          if (panel) panel.style.width = `${width}px`;
        }
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        try {
          localStorage.setItem("eidolon.panelSizes", JSON.stringify({ list: state.listWidth }));
        } catch {
          /* 记不下：这次会话里照样好用。 */
        }
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  const save = document.getElementById("save-profile");
  if (save) {
    const submit = async () => {
      const address = document.getElementById("p-address")?.value || "";
      const gender = selectValue("p-gender") || "default";
      const timezone = selectValue("p-tz");
      const month = selectValue("p-bm");
      const day = selectValue("p-bd");
      const out = await postJSON("/api/profile", {
        u: state.userId,
        address,
        gender,
        timezone,
        birth_month: month ? Number(month) : null,
        birth_day: day ? Number(day) : null,
      });
      if (out && out.error) {
        const status = document.getElementById("profile-status");
        if (status) status.textContent = out.error;
        return;
      }
      state.profile = out;
      state.profileDraft = {
        address: out.address || "",
        gender: out.gender || "default",
        birth_month: out.birth_month ?? null,
        birth_day: out.birth_day ?? null,
        timezone: out.timezone || "",
      };
      state.dirty = false;
      render();
    };
    save.addEventListener("click", (event) => {
      event.preventDefault();
      void submit();
    });
    const profileForm = document.getElementById("profile-form");
    if (profileForm) {
      profileForm.addEventListener("submit", (event) => {
        event.preventDefault();
        void submit();
      });
    }
  }
  ["p-address", "p-gender", "p-tz", "p-bm", "p-bd"].forEach((id) => {
    // 自绘的下拉，值在 hidden input 里（id 就是字段 id）；文本框的值直接在自己身上。
    const input = document.getElementById(id);
    if (!input) return;
    const onChange = () => {
      state.profileDraft.address = document.getElementById("p-address")?.value || "";
      state.profileDraft.gender = selectValue("p-gender") || "default";
      state.profileDraft.timezone = selectValue("p-tz");
      state.profileDraft.birth_month = Number(selectValue("p-bm") || 0) || null;
      state.profileDraft.birth_day = Number(selectValue("p-bd") || 0) || null;
      state.dirty = true;
      const status = document.getElementById("profile-status");
      if (status) status.textContent = T.settings.profile.unsaved;
      const saveButton = document.getElementById("save-profile");
      if (saveButton) saveButton.removeAttribute("disabled");
    };
    input.addEventListener("input", onChange);
    input.addEventListener("change", onChange);
  });
  // 自绘的下拉：点触发器开浮层，点一项选中，点了外面或按 Esc 收起。
  document.querySelectorAll("[data-select]").forEach((wrap) => {
    const trigger = wrap.querySelector('[role="combobox"]');
    const content = wrap.querySelector('[data-slot="select-content"]');
    if (!trigger || !content) return;
    const close = () => {
      content.classList.add("hidden");
      trigger.setAttribute("aria-expanded", "false");
    };
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = content.classList.contains("hidden");
      // 一次只开一个。
      document.querySelectorAll('[data-slot="select-content"]').forEach((other) => other.classList.add("hidden"));
      document.querySelectorAll('[role="combobox"]').forEach((other) => other.setAttribute("aria-expanded", "false"));
      if (!open) {
        close();
        return;
      }
      content.classList.remove("hidden");
      trigger.setAttribute("aria-expanded", "true");
    });
    content.addEventListener("click", (event) => {
      const item = event.target.closest('[data-slot="select-item"]');
      if (!item) return;
      event.stopPropagation();
      const value = item.getAttribute("data-value") || "";
      const hidden = wrap.querySelector("[data-select-value]");
      if (hidden) {
        hidden.value = value;
        hidden.dispatchEvent(new Event("change", { bubbles: true }));
      }
      close();
      // 选了以后重画，触发器上的文字跟着变（连同"未保存"的提示）。
      render();
    });
    // 值变了：写进对应的草稿（自带密钥的表单、通话页的设置）。
    // 档案页的字段有自己的一套监听（改的是 state.profileDraft），不归这里管。
    const hidden = wrap.querySelector("[data-select-value]");
    if (hidden) {
      hidden.addEventListener("change", () => {
        const field = hidden.getAttribute("data-select-field");
        if (!field) return;
        const form = wrap.closest("[data-own-form]");
        if (form) {
          const service = form.getAttribute("data-own-form");
          state.ownKeysDraft[service] = state.ownKeysDraft[service] || {};
          state.ownKeysDraft[service][field] = hidden.value;
          return;
        }
        // 通话页的设置（还没有后端可存，先放在本页草稿里）。
        if (hidden.id.startsWith("c-")) {
          state.callDraft[field] = hidden.value;
        }
      });
    }
  });
  // 点浮层外面才收起（点浮层里面——比如时区那个搜索框——不能收）。
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof Node) {
      if (target.closest('[data-slot="select-content"]') || target.closest('[data-slot="combobox-content"]')) {
        return;
      }
    }
    closeAllSelects();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeAllSelects();
  });
  // 原版 Combobox（时区）：点触发器开浮层，浮层顶上能搜（按文字过滤选项），
  // 点一项选中。搜索框里打字时按方向键、Enter 也照原版那样走（这里只做鼠标的部分）。
  document.querySelectorAll("[data-combobox]").forEach((wrap) => {
    const trigger = wrap.querySelector('[role="combobox"]');
    const content = wrap.querySelector('[data-slot="combobox-content"]');
    const search = wrap.querySelector("[data-combobox-search]");
    const list = wrap.querySelector('[data-slot="combobox-list"]');
    const empty = wrap.querySelector("[data-combobox-empty]");
    if (!trigger || !content) return;
    const close = () => {
      content.classList.add("hidden");
      trigger.setAttribute("aria-expanded", "false");
    };
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = content.classList.contains("hidden");
      closeAllSelects();
      if (!open) {
        close();
        return;
      }
      content.classList.remove("hidden");
      trigger.setAttribute("aria-expanded", "true");
      if (search) {
        search.value = "";
        search.focus();
        filter();
      }
    });
    // 按文字过滤：原版 Combobox 的搜索是前缀匹配（base-ui 默认 contains）。
    const filter = () => {
      if (!search || !list) return;
      const q = search.value.trim().toLowerCase();
      let shown = 0;
      list.querySelectorAll('[data-slot="combobox-item"]').forEach((item) => {
        const label = (item.getAttribute("data-label") || "").toLowerCase();
        const value = (item.getAttribute("data-value") || "").toLowerCase();
        const hit = !q || label.includes(q) || value.includes(q);
        item.classList.toggle("hidden", !hit);
        if (hit) shown += 1;
      });
      if (empty) {
        empty.classList.toggle("hidden", shown > 0);
        empty.textContent = shown > 0 ? "" : T.settings.profile.timezoneSearch + "…";
      }
    };
    if (search) {
      search.addEventListener("input", filter);
      // 点搜索框不往外传，免得 document 上那个「点外面就收起」的监听把它关了。
      search.addEventListener("click", (event) => event.stopPropagation());
    }
    content.addEventListener("click", (event) => {
      const item = event.target.closest('[data-slot="combobox-item"]');
      if (!item) return;
      event.stopPropagation();
      const value = item.getAttribute("data-value") || "";
      const hidden = wrap.querySelector("[data-combobox-value]");
      if (hidden) {
        hidden.value = value;
        hidden.dispatchEvent(new Event("change", { bubbles: true }));
      }
      close();
      render();
    });
  });
  // 原版的 Switch：点一下就翻，翻的是属性（data-checked / data-unchecked 是属性的有无），
  // 滑块的位移是 CSS 的 transition-transform，所以原地改属性、不重画，动画才连得上。
  document.querySelectorAll("[role='switch']").forEach((button) => {
    button.addEventListener("click", () => {
      const checked = button.getAttribute("aria-checked") === "true";
      const thumb = button.querySelector('[data-slot="switch-thumb"]');
      const flip = (element) => {
        if (!element) return;
        if (checked) {
          element.removeAttribute("data-checked");
          element.setAttribute("data-unchecked", "");
        } else {
          element.setAttribute("data-checked", "");
          element.removeAttribute("data-unchecked");
        }
      };
      button.setAttribute("aria-checked", String(!checked));
      // 原版是属性「有就是开」，所以开的时候去掉 unchecked、加上 checked。
      flip(button);
      flip(thumb);
      if (button.id === "toggle-scsp") {
        state.scsp = !checked;
        keepAppearance();
      }
      if (button.id === "toggle-scsp-bg") {
        state.scspBg = !checked;
        keepAppearance();
      }
    });
  });
  document.querySelectorAll("[data-delete-memory]").forEach((button) => {
    button.addEventListener("click", async () => {
      await postJSON("/api/memory/delete", { u: state.userId, id: button.getAttribute("data-delete-memory") });
      await loadMemories();
      render();
    });
  });
  const clearMemories = document.getElementById("clear-memories");
  if (clearMemories) {
    clearMemories.addEventListener("click", async () => {
      await postJSON("/api/memory/clear", { u: state.userId });
      await loadMemories();
      render();
    });
  }

  /* ---- 自带密钥：填写 / 修改 / 删除 ---- */

  // 点「填写」在原位展开表单（state.ownKeysEditing 记着是哪一项服务），再点「取消」收起。
  document.querySelectorAll("[data-own-keys]").forEach((button) => {
    button.addEventListener("click", () => {
      const service = button.getAttribute("data-own-keys");
      state.ownKeysEditing = service;
      state.ownKeysDraft[service] = state.ownKeysDraft[service] || {};
      render();
    });
  });
  document.querySelectorAll("[data-own-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      const service = button.getAttribute("data-own-remove");
      const keys = loadOwnKeys();
      delete keys[service];
      keepOwnKeys(keys);
      render();
    });
  });
  document.querySelectorAll("[data-own-cancel]").forEach((button) => {
    button.addEventListener("click", () => {
      const service = button.getAttribute("data-own-cancel");
      state.ownKeysEditing = null;
      delete state.ownKeysDraft[service];
      render();
    });
  });
  // 表单里的文本框：改动写进草稿，重画后还在（保存时才落盘）。
  document.querySelectorAll("[data-own-form] input[data-slot='input']").forEach((input) => {
    const form = input.closest("[data-own-form]");
    const service = form ? form.getAttribute("data-own-form") : null;
    if (!service) return;
    const field = input.id === "ok-base" ? "base_url" : input.id === "ok-model" ? "model" : "key";
    const remember = () => {
      state.ownKeysDraft[service] = state.ownKeysDraft[service] || {};
      state.ownKeysDraft[service][field] = input.value;
    };
    input.addEventListener("input", remember);
    input.addEventListener("change", remember);
    // 打开时把框里已有的值记进草稿。
    remember();
  });
  document.querySelectorAll("[data-own-save]").forEach((button) => {
    button.addEventListener("click", () => {
      const service = button.getAttribute("data-own-save");
      const value = (id) => document.getElementById(id)?.value || "";
      const draft = state.ownKeysDraft[service] || {};
      const keys = loadOwnKeys();
      if (service === "llm") {
        keys.llm = { protocol: draft.protocol ?? "chat-completions", base_url: draft.base_url ?? value("ok-base"), model: draft.model ?? value("ok-model"), key: value("ok-key") || (keys.llm ? keys.llm.key : "") };
      } else if (service === "tts") {
        keys.tts = { model: draft.model ?? "s2.1-pro-free", key: value("ok-key") || (keys.tts ? keys.tts.key : "") };
      } else if (service === "stt") {
        keys.stt = { provider: draft.provider ?? "soniox", key: value("ok-key") || (keys.stt ? keys.stt.key : "") };
      } else if (service === "image") {
        keys.image = { format: "openai", base_url: draft.base_url ?? value("ok-base"), model: draft.model ?? value("ok-model"), key: value("ok-key") || (keys.image ? keys.image.key : "") };
      } else {
        keys.vision = { base_url: draft.base_url ?? value("ok-base"), model: draft.model ?? value("ok-model"), key: value("ok-key") || (keys.vision ? keys.vision.key : "") };
      }
      keepOwnKeys(keys);
      state.ownKeysEditing = null;
      delete state.ownKeysDraft[service];
      render();
    });
  });

  /* ---- 数据包：导入 ---- */

  const importPick = document.getElementById("import-pick");
  if (importPick) {
    importPick.addEventListener("click", () => document.getElementById("import-file")?.click());
  }
  const importFile = document.getElementById("import-file");
  if (importFile) {
    importFile.addEventListener("change", async () => {
      const file = importFile.files && importFile.files[0];
      const status = document.getElementById("import-status");
      const choice = document.getElementById("import-choice");
      if (!file) return;
      if (status) status.textContent = T.settings.dataPackage.reading;
      try {
        state.importData = JSON.parse(await file.text());
        if (choice) {
          choice.classList.remove("hidden");
          choice.classList.add("flex");
        }
        if (status) status.textContent = "";
      } catch {
        if (status) status.textContent = T.settings.dataPackage.nothingToChoose;
      }
      importFile.value = "";
    });
  }
  const importRun = document.getElementById("import-run");
  if (importRun) {
    importRun.addEventListener("click", async () => {
      const status = document.getElementById("import-status");
      if (!state.importData) return;
      if (status) status.textContent = T.settings.dataPackage.importing;
      importRun.setAttribute("disabled", "");
      try {
        const out = await postJSON("/api/import", state.importData);
        if (status) status.textContent = out && out.error ? out.error : T.settings.dataPackage.imported("对话、记忆和档案");
        await loadRoster();
        await loadMemories();
      } catch {
        if (status) status.textContent = T.settings.dataPackage.nothingToChoose;
      }
      importRun.removeAttribute("disabled");
    });
  }

  /* ---- 登录与数据：删除数据的确认框 ---- */

  const deleteData = document.getElementById("delete-data");
  if (deleteData) {
    deleteData.addEventListener("click", () => {
      const dialog = document.getElementById("delete-dialog");
      if (dialog) {
        dialog.classList.remove("hidden");
        dialog.classList.add("flex");
      }
    });
  }
  document.querySelectorAll("[data-close='delete']").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById("delete-dialog");
      if (dialog) {
        dialog.classList.add("hidden");
        dialog.classList.remove("flex");
      }
    });
  });

  /* ---- 统计页的分享图 ---- */

  const shareStats = document.getElementById("share-stats");
  if (shareStats) {
    shareStats.addEventListener("click", () => {
      const dialog = document.getElementById("share-dialog");
      const card = document.getElementById("share-card");
      if (dialog && card) {
        card.innerHTML = shareCardHTML();
        dialog.classList.remove("hidden");
        dialog.classList.add("flex");
      }
    });
  }
  document.querySelectorAll("[data-close='share']").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById("share-dialog");
      if (dialog) {
        dialog.classList.add("hidden");
        dialog.classList.remove("flex");
      }
    });
  });
  const shareDownload = document.getElementById("share-download");
  if (shareDownload) {
    shareDownload.addEventListener("click", () => {
      const card = document.getElementById("share-card");
      if (!card) return;
      const box = card.getBoundingClientRect();
      const clone = card.firstElementChild ? card.firstElementChild.cloneNode(true) : null;
      if (!clone) return;
      const svg =
        `<svg xmlns="http://www.w3.org/2000/svg" width="${box.width}" height="${box.height}">` +
        `<foreignObject width="100%" height="100%">` +
        `<div xmlns="http://www.w3.org/1999/xhtml" style="width:${box.width}px;height:${box.height}px">${clone.outerHTML}</div>` +
        `</foreignObject></svg>`;
      const image = new Image();
      image.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = box.width * 2;
        canvas.height = box.height * 2;
        const context = canvas.getContext("2d");
        if (context) {
          context.scale(2, 2);
          context.drawImage(image, 0, 0);
          const link = document.createElement("a");
          link.download = "localbot-sc-stats.png";
          link.href = canvas.toDataURL("image/png");
          link.click();
        }
      };
      image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    });
  }
}

/** 选中的图片压成最长边 1280 的 WebP，和原版一样。 */
async function shrinkPicture(file) {
  const bitmap = await createImageBitmap(file);
  const longest = Math.max(bitmap.width, bitmap.height);
  const scale = longest > 1280 ? 1280 / longest : 1;
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return canvas.toDataURL("image/webp", 0.85);
}

/* ------------------------------------------------------------------ 回合 */

async function send() {
  if (state.sending) return;
  const text = state.draft.trim();
  const photos = state.photos.map((p) => p.id);
  if (!text && !photos.length) return;
  state.sending = true;
  state.draft = "";
  state.photos = [];
  state.messages.push({
    id: "pending",
    role: "user",
    text,
    segments: [],
    photos: [],
    at: new Date().toISOString(),
  });
  render();
  const characterId = state.selectedId;
  try {
    await streamTurn({
      characterId,
      conversationId: state.conversationId,
      text,
      photos,
      ownKeys: turnOwnKeys(),
      onEvent: async (event) => {
        if (event.type === "done" || event.type === "failed" || event.type === "rejected") {
          await loadConversation();
          state.sending = false;
          render();
        }
      },
    });
  } catch (error) {
    state.sending = false;
    await loadConversation();
    render();
  }
}

/* ------------------------------------------------------------------ 读取 */

async function loadRoster() {
  const out = await getJSON("/api/roster");
  state.roster = out.roster || [];
}

/** 服务状态：拉不到就保持上一次的（原版也是这样，服务状态不是要紧的事）。 */
async function loadServiceStatus() {
  try {
    state.serviceStatus = await getJSON("/api/service-status");
  } catch {
    /* 保持上一次的。 */
  }
}

async function loadMemories() {
  if (!state.userId) return;
  const out = await getJSON(`/api/memory/all?u=${encodeURIComponent(state.userId)}`);
  state.memories = out.memories || [];
}

/** 拉出这件的送礼面板；还有几件按背包里的算。 */
function openGive(giftId) {
  const held = (state.gifts && state.gifts.backpack ? state.gifts.backpack : []).find(
    (b) => b.gift_id === giftId
  );
  const first = state.selectedId || (state.characters[0] && state.characters[0].id) || null;
  state.giftDrawing = null;
  state.giftGiving = { gift: giftId, left: held ? held.count : 0, to: first };
  render();
}

/** 抽一次或全部抽完：先亮着「正在打开」，再揭晓。 */
async function drawGifts(all) {
  if (!state.userId) return;
  state.giftFailure = "";
  state.giftDrawing = { all, result: null };
  render();
  try {
    const out = await postJSON("/api/gifts/draws", { u: state.userId, all });
    if (out.error) {
      state.giftFailure = out.error;
      state.giftDrawing = null;
      render();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 600));
    state.giftDrawing = { all, result: out };
    render();
    void loadGifts();
  } catch {
    state.giftFailure = T.gifts.failed;
    state.giftDrawing = null;
    render();
  }
}

/** 送出一件：面板收起，底部出一条提示，背包与好感度重读。 */
async function sendGift() {
  const giving = state.giftGiving;
  if (!giving || !giving.to || !state.userId) return;
  state.giftSending = true;
  try {
    const out = await postJSON("/api/gifts/give", {
      u: state.userId,
      c: giving.to,
      gift: giving.gift,
    });
    if (out.error) {
      state.giftFailure = out.error;
    } else {
      const name = characterName(giving.to);
      const affinity = out.affinity || {};
      const until = affinity.cooldown_until ? nextDrawText(affinity.cooldown_until) : "";
      state.giftNotice = until && affinity.cooling ? T.gifts.given.stillCooling(name, until) : T.gifts.given.given(name);
      if (giving.to === state.selectedId) state.affinity = affinity;
      window.setTimeout(() => {
        state.giftNotice = null;
        render();
      }, 6000);
    }
  } catch {
    state.giftFailure = T.gifts.failed;
  }
  state.giftSending = false;
  state.giftGiving = null;
  render();
  void loadGifts();
}

/** 用一张重置卡。 */
async function useResetCard() {
  if (!state.userId) return;
  try {
    const out = await postJSON("/api/gifts/reset-cards", { u: state.userId });
    if (out.error) state.giftFailure = out.error;
  } catch {
    state.giftFailure = T.gifts.failed;
  }
  state.giftCardConfirm = false;
  render();
  void loadGifts();
}

/** 礼物页：状态与目录各读一次，读完重画；读不到就在页首说一句，页面还留着上次的。 */
function loadGifts() {
  if (!state.userId) return;
  Promise.all([
    getJSON(`/api/gifts/state?u=${encodeURIComponent(state.userId)}`),
    getJSON("/api/gifts/catalog"),
  ]).then(
    ([stateOut, catalogOut]) => {
      state.gifts = stateOut;
      state.giftCatalog = catalogOut.gifts || [];
      state.giftFailure = "";
      render();
    },
    () => {
      state.giftFailure = T.gifts.failed;
      render();
    }
  );
}

/** 统计：按当前跨度读一次。换了跨度就重读整份。 */
async function loadStats() {
  if (state.statsPending) return;
  state.statsPending = true;
  try {
    const out = await getJSON(`/api/stats?range=${encodeURIComponent(state.statsRange)}`);
    state.stats = out;
    state.statsFailed = false;
  } catch {
    state.stats = null;
    state.statsFailed = true;
  }
  state.statsPending = false;
  render();
}

async function loadState() {
  const characterId = state.selectedId || (state.characters[0] && state.characters[0].id);
  if (!characterId) return;
  const out = await getJSON(`/api/state?c=${encodeURIComponent(characterId)}`);
  state.characters = out.characters || [];
  state.work = out.work || state.work;
  state.userId = out.user_id || state.userId;
  state.conversationId = out.conversation_id;
  state.affinity = out.affinity || null;
  state.messages = out.messages || [];
  state.capabilities = out.capabilities || {};
  await loadMemories();
}

async function loadConversation() {
  if (!state.selectedId) return;
  const out = await getJSON(
    `/api/state?c=${encodeURIComponent(state.selectedId)}${state.conversationId ? `&conv=${encodeURIComponent(state.conversationId)}` : ""}`
  );
  state.messages = out.messages || [];
  state.affinity = out.affinity || null;
}

async function boot() {
  // 原版 useWide：按 CSS 断点分宽窄（768px），跨过时重画。
  state.wide = !window.matchMedia("(max-width: 767px)").matches;
  window.addEventListener("resize", () => {
    const wide = !window.matchMedia("(max-width: 767px)").matches;
    if (wide !== state.wide) {
      state.wide = wide;
      render();
    }
  });
  // 原版 panelSizes：列表上次拖到的宽度（240–480，没拖过是 320）。
  try {
    const sizes = JSON.parse(localStorage.getItem("eidolon.panelSizes") || "{}");
    if (typeof sizes.list === "number" && sizes.list > 0) {
      state.listWidth = Math.min(480, Math.max(240, sizes.list));
    }
  } catch {
    /* 读不到：用默认 320。 */
  }
  try {
    const roster = await getJSON("/api/roster");
    state.roster = roster.roster || [];
    const first = state.roster[0];
    if (first) {
      const out = await getJSON(`/api/state?c=${encodeURIComponent(first.id)}`);
      state.characters = out.characters || [];
      state.work = out.work || null;
      state.userId = out.user_id || "";
      state.prefs = loadPrefs();
      // 外观（SCSP 头像开关）只存在这台设备上，原版 lib/appearance.ts 那样按用户分开存。
      const look = loadAppearance();
      state.scsp = look.scspAvatars;
      state.scspBg = look.scspBackgrounds;
    }
  } catch {
    state.characters = [];
  }
  render();
  if (route.characterId) {
    await loadState();
    render();
  }
  // 原版 ServiceStatusButton：载入时拉一次，之后每 5 分钟一次，切回这个标签页时一次，
  // 打开小窗口时再拉一次。拉不到就保持上一次的，不报错。
  void loadServiceStatus();
  window.setInterval(() => void loadServiceStatus(), 5 * 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void loadServiceStatus();
  });
  await loadMemories();
  if (route.path === "/gifts" && state.userId) await loadGifts();
  if (state.userId) {
    try {
      const profile = await getJSON(`/api/profile?u=${encodeURIComponent(state.userId)}`);
      const data = profile && profile.profile ? profile.profile : profile;
      state.profile = data;
      state.profileDraft = {
        address: data.address || "",
        gender: data.gender || "default",
        birth_month: data.birth_month ?? null,
        birth_day: data.birth_day ?? null,
        timezone: data.timezone || "",
      };
      state.dirty = false;
    } catch {
      state.profile = null;
    }
  }
  render();
}

void boot();
