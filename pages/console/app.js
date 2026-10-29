// 群文件管理 - Plugin Pages 前端（v2.8.0：发送临时目录/清理策略 · 多群复制 · 未保存提示 · 精细视觉系统）
const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let groupsData = [];
let currentGroupId = "";
let currentFolderId = "";
let currentFolders = [];

// 规则模块 / 维护模块当前选中群与「顶部保存」回调
let rulesGroupId = "";
let rulesCollect = null;
let maintGroupId = "";
let maintCollect = null;

// 规则折叠状态（WeakMap：不写进规则对象，避免保存/导出时把 UI 状态带进 groups.json）
const ruleFolded = new WeakMap();
function isRuleFolded(r) { return ruleFolded.get(r) !== false; }
function setRuleFolded(r, v) { ruleFolded.set(r, !!v); }

// 规则未保存状态（由 renderRulesEditor 注入检测/重置函数）
let rulesDirtyCheck = null;
let rulesDirtyReset = null;
function isRulesDirty() { return rulesDirtyCheck ? rulesDirtyCheck() : false; }
function updateDirtyBadge() {
  const el = $("r-dirty");
  if (el) el.style.display = isRulesDirty() ? "" : "none";
}

// 扩展名快捷预设（v2.4.0：12 组 130+ 类型，新增漫画 cbz/cbr 等；v2.4.1 改为分组标签 + 单面板展示）
const EXT_PRESETS = [
  { key: "comic", label: "漫画", exts: ["cbz", "cbr", "cb7", "cbt"] },
  { key: "ebook", label: "电子书", exts: ["epub", "mobi", "azw3", "azw", "fb2", "lit", "djvu", "chm"] },
  { key: "image", label: "图片", exts: ["jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "avif", "svg", "ico", "jfif"] },
  { key: "doc", label: "文档", exts: ["pdf", "doc", "docx", "txt", "md", "rtf", "odt", "pages", "wps", "tex"] },
  { key: "sheet", label: "表格", exts: ["xls", "xlsx", "csv", "tsv", "ods", "numbers", "et"] },
  { key: "slide", label: "演示", exts: ["ppt", "pptx", "key", "odp", "dps"] },
  { key: "archive", label: "压缩包", exts: ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "zst"] },
  { key: "media", label: "音视频", exts: ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "rmvb", "mp3", "flac", "wav", "m4a", "aac", "ogg", "opus", "wma", "ape"] },
  { key: "code", label: "代码", exts: ["py", "js", "ts", "jsx", "tsx", "html", "css", "json", "xml", "yaml", "yml", "java", "c", "cpp", "h", "cs", "php", "go", "rs", "rb", "swift", "kt", "sh", "bat", "ps1", "sql", "ipynb", "vue", "lua"] },
  { key: "install", label: "安装包", exts: ["apk", "exe", "msi", "dmg", "pkg", "deb", "rpm", "appimage", "ipa"] },
  { key: "design", label: "设计/CAD", exts: ["psd", "ai", "eps", "sketch", "fig", "xd", "dwg", "dxf", "stl", "obj", "fbx", "blend"] },
  { key: "font", label: "字体", exts: ["ttf", "otf", "woff", "woff2", "ttc"] },
];

// 扩展名 → 类型组索引（规则编辑器、文件图标、文件页筛选共用同一份数据）
const EXT_CAT = new Map();
EXT_PRESETS.forEach((grp) => grp.exts.forEach((e) => EXT_CAT.set(e, grp)));
const EXT_OTHER = { key: "other", label: "其他", exts: [] };

// 取文件名扩展名（小写、无点；无扩展名返回 ""）
function extOf(name) {
  const m = String(name || "").trim().match(/\.([A-Za-z0-9][A-Za-z0-9+_-]{0,15})$/);
  return m ? m[1].toLowerCase() : "";
}

// 扩展名归一化：兼容 ".CBZ" / "*.cbr" / "cbz,cbr" / "cbz cbr"，去重并丢弃非法项
function cleanExts(raw) {
  let parts = [];
  if (typeof raw === "string") parts = raw.split(/[,，、;；\s]+/);
  else if (Array.isArray(raw)) {
    raw.forEach((x) => {
      if (typeof x === "string") parts.push(...x.split(/[,，、;；\s]+/));
      else if (x != null) parts.push(String(x));
    });
  }
  const out = [];
  parts.forEach((p) => {
    const e = String(p || "").trim().toLowerCase().replace(/^\*?\.?/, "");
    if (/^[a-z0-9][a-z0-9+_-]{0,15}$/.test(e) && !out.includes(e)) out.push(e);
  });
  return out;
}

function extMeta(ext) {
  return EXT_CAT.get(String(ext || "").toLowerCase()) || EXT_OTHER;
}

// 规则快捷模板：匹配内容留空 + 限定类型 = 按类型兜底（v2.5.0）
const RULE_TEMPLATES = {
  comic: { label: "漫画", folder: "漫画", exts: ["cbz", "cbr", "cb7", "cbt"] },
  ebook: { label: "电子书", folder: "电子书", exts: ["epub", "mobi", "azw3", "azw", "fb2"] },
  archive: { label: "压缩包", folder: "压缩包", exts: ["zip", "rar", "7z", "tar", "gz"] },
  image: { label: "图片", folder: "图片", exts: ["jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "avif"] },
  doc: { label: "文档", folder: "文档", exts: ["pdf", "doc", "docx", "txt", "md"] },
};

function sameExtSet(a, b) {
  if (a.length !== b.length) return false;
  const s = new Set(a);
  return b.every((x) => s.has(x));
}

// 规则查重键（复制到其他群时用于"追加合并，跳过重复"）
function ruleKey(r) {
  return [
    r.kind || "kw",
    String(r.pattern || ""),
    String(r.folder || ""),
    cleanExts(r.exts).slice().sort().join(","),
  ].join("\u0001");
}

// 兼容宿主桥可能的多层包装，取出 rules 列表
function pickRules(res) {
  let d = res;
  for (let i = 0; i < 4; i++) {
    if (d && Array.isArray(d.rules)) return d.rules;
    if (d && d.data) d = d.data;
    else break;
  }
  return [];
}

const HELP_TEXT = `群文件管理 v2.8.0  指令速查（短前缀 /gf 等效 /群文件；设置类全部走本控制台）

【查看】
  /gf 状态
      空间/开关/规则/时效/管理员一览
  /gf 列 [文件夹名]
      列根目录文件+文件夹；带名进文件夹；结果带序号
  /gf 查 <关键词> [序号]
      递归搜文件；带序号=把该文件发到群

【文件操作】（仅管理员）
  /gf 删 <序号|范围|全部>
      删最近一次 列/查 结果里的文件（先预览，回复 /gf 确认 执行）
  /gf 删 <文件夹名> <序号|全部>
      不用上下文直接定位删除
  /gf 移 <序号|关键词> <目标文件夹>
      移动单个文件（目标不存在会自动建）
  /gf 建 <文件夹名>
      新建文件夹
  /gf 整
      立即跑一遍流水线：分类 → 时效清理 → 容量检查，出报告

【群管理】（仅管理员）
  /gf 绑 [备注名]
      把当前群纳入管理
  /gf 解
      解绑当前群（两段确认）
  /gf 开 / /gf 关
      本群启停（停用后定时/自动动作暂停，手动命令仍可用）
  /gf 管 @某人 / /gf 管 删 @某人
      加/移除该群管理员（也可直接填 QQ 号）

【其它】
  /gf 诊断
      插件自查报告
  /gf 确认 / /gf 取消
      所有危险操作的统一两段确认（60 秒内有效）

【提示】
  · 旧命令名（列表/删除/移动/诊断/整理…）都能用，已做成兼容别名。
  · 文件夹名不用打全：/gf 列 hv 自动进 HV2；多匹配会列出候选。
  · 只发 /gf（不带子命令）会显示全部指令说明（AstrBot 内置）。
  · 群里发命令默认管当前群；私聊需带群号（如 /gf 状态 123456）。
  · 列表上下文 5 分钟内有效：列/查 之后直接用序号 删/移。
  · 找某类文件：/gf 查 cbz（扩展名也参与匹配，漫画/电子书等格式直接搜）。
  · 控制台里所有设置（规则/定时/时效/容量线/查重/发送临时文件）保存后立即生效。
  · 发送临时文件在「定时与维护 → 发送临时文件」：默认落共享 temp，协议端可读；保留秒数与残留小时可调。
`;

// ---------- 主题系统（v2.0.6：纯跟随系统，无手动切换） ----------
function applyTheme() {
  const sysDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", sysDark ? "dark" : "light");
}
(function initTheme() {
  applyTheme();
  if (window.matchMedia) {
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", applyTheme);
      else if (mq.addListener) mq.addListener(applyTheme);  // 旧浏览器
    } catch (e) { /* 忽略 */ }
  }
})();

// ---------- 通用工具 ----------
let toastTimer = null;
function toast(msg, type, duration) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast " + (type || "success");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), duration || 2600);
}

// 两段式确认：第一次点击进入"待确认"状态（按钮变红、改文案），3 秒内再点才真正执行
function armConfirm(btn, armedText) {
  if (btn.dataset.armed === "1") {
    btn.dataset.armed = "0";
    return true;
  }
  btn.dataset.armed = "1";
  const origText = btn.textContent;
  const origCls = btn.className;
  btn.classList.add("danger");
  btn.textContent = armedText;
  const reset = () => {
    btn.dataset.armed = "0";
    btn.className = origCls;
    btn.textContent = origText;
  };
  btn._restore = reset;
  setTimeout(() => { if (btn.dataset.armed === "1") reset(); }, 3000);
  return false;
}

function fmtSize(b) {
  if (b == null) return "";
  if (b >= 1073741824) return (b / 1073741824).toFixed(2) + "GB";
  if (b >= 1048576) return (b / 1048576).toFixed(1) + "MB";
  if (b >= 1024) return (b / 1024).toFixed(0) + "KB";
  return b + "B";
}
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------- DSL 构建器 ----------
function compileDsl(segs) {
  return segs.map((s) => {
    const sep = s.sep === "__custom__" ? (s.sepCustom || "") : (s.sep || "");
    let body = "";
    switch (s.type) {
      case "digit": body = "\\d+"; break;
      case "zh_cn": body = "[\\u4e00-\\u9fa5]+"; break;
      case "zh_tw": body = "[\\u4e00-\\u9fff]+"; break;
      case "ja": body = "[\\u3040-\\u30ff\\u4e00-\\u9fff]+"; break;
      case "letter": body = "[A-Za-z]+"; break;
      case "symbol": body = "[^\\w\\s]+"; break;
      case "any": body = "[^_\\-\\s]+"; break;
      case "text": body = (s.text || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); break;
      case "hanzi": body = "[\\u4e00-\\u9fa5]+"; break;
      default: body = "[^_\\-\\s]+";
    }
    return body + (sep ? sep.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") : "");
  }).join("");
}
function dslDesc(t) {
  return ({
    digit: "数字", zh_cn: "简体中文", zh_tw: "繁中", ja: "日文",
    letter: "英文字母", symbol: "符号", any: "任意非分隔", text: "固定文字", hanzi: "汉字",
  })[t] || "任意";
}
function renderDslEditor(box, segs, onChange) {
  box.innerHTML = "";
  const TYPES = [
    ["digit", "数字"], ["zh_cn", "简体中文"], ["zh_tw", "繁中"], ["ja", "日文"],
    ["letter", "英文字母"], ["symbol", "符号"], ["any", "任意非分隔"],
    ["hanzi", "汉字"], ["text", "固定文字"],
  ];
  const SEPS = [["_", "下划线 _"], ["-", "横线 -"], ["", "无（连写）"], [" ", "空格"], [".", "点 ."], ["__custom__", "自定义…"]];
  segs.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = "dsl-row";
    row.innerHTML = `
      <span class="dsl-idx">${i + 1}</span>
      <select class="dsl-type">
        ${TYPES.map(([v, t]) => `<option value="${v}"${s.type === v ? " selected" : ""}>${t}</option>`).join("")}
      </select>
      <input type="text" class="dsl-text" placeholder="固定文字内容" value="${esc(s.text || "")}" style="display:${s.type === "text" ? "" : "none"};flex:1 1 110px;min-width:90px;" />
      <select class="dsl-sep">
        ${SEPS.map(([v, t]) => `<option value="${v}"${s.sep === v ? " selected" : ""}>${t}</option>`).join("")}
      </select>
      <input type="text" class="dsl-sep-custom" placeholder="自定义分隔符" value="${esc(s.sepCustom || "")}" style="display:${s.sep === "__custom__" ? "" : "none"};flex:1 1 90px;min-width:70px;" />
      <button class="dsl-up btn btn-sm" type="button" title="上移">↑</button>
      <button class="dsl-down btn btn-sm" type="button" title="下移">↓</button>
      <button class="dsl-del btn btn-sm btn-danger" type="button" title="删除">×</button>`;
    const sync = () => {
      s.type = row.querySelector(".dsl-type").value;
      s.text = row.querySelector(".dsl-text").value;
      const sv = row.querySelector(".dsl-sep").value;
      s.sep = sv;
      s.sepCustom = sv === "__custom__" ? row.querySelector(".dsl-sep-custom").value : "";
      if (onChange) onChange();
    };
    row.querySelector(".dsl-type").addEventListener("change", () => {
      row.querySelector(".dsl-text").style.display = row.querySelector(".dsl-type").value === "text" ? "" : "none";
      sync();
    });
    row.querySelector(".dsl-text").addEventListener("input", sync);
    row.querySelector(".dsl-sep").addEventListener("change", () => {
      row.querySelector(".dsl-sep-custom").style.display = row.querySelector(".dsl-sep").value === "__custom__" ? "" : "none";
      sync();
    });
    row.querySelector(".dsl-sep-custom").addEventListener("input", sync);
    row.querySelector(".dsl-up").addEventListener("click", () => {
      if (i > 0) { const t = segs[i - 1]; segs[i - 1] = s; segs[i] = t; renderDslEditor(box, segs, onChange); }
    });
    row.querySelector(".dsl-down").addEventListener("click", () => {
      if (i < segs.length - 1) { const t = segs[i + 1]; segs[i + 1] = s; segs[i] = t; renderDslEditor(box, segs, onChange); }
    });
    row.querySelector(".dsl-del").addEventListener("click", () => {
      segs.splice(i, 1); renderDslEditor(box, segs, onChange);
    });
    box.appendChild(row);
  });
  const addRow = document.createElement("div");
  addRow.className = "toolbar";
  addRow.innerHTML = `<button class="dsl-add btn btn-sm" type="button">＋ 添加一段</button>`;
  addRow.querySelector(".dsl-add").addEventListener("click", () => {
    segs.push({ type: "any", sep: "_" });
    renderDslEditor(box, segs, onChange);
  });
  box.appendChild(addRow);
}

// 智能生成：把文件名切成"段"
function genTokenize(line) {
  const out = [];
  const re = /[A-Za-z]+|\d+|[\u4e00-\u9fa5]+|[^A-Za-z0-9\u4e00-\u9fa5\s]+/g;
  let m;
  while ((m = re.exec(line)) !== null) {
    const t = m[0];
    let type = "text";
    if (/^\d+$/.test(t)) type = "digit";
    else if (/^[A-Za-z]+$/.test(t)) type = "letter";
    else if (/^[\u4e00-\u9fa5]+$/.test(t)) type = "zh_cn";
    else type = "symbol";
    out.push({ type, text: t });
  }
  return out;
}
function genClassifyChar(c) {
  if (/[0-9]/.test(c)) return "digit";
  if (/[A-Za-z]/.test(c)) return "letter";
  if (/[\u4e00-\u9fa5]/.test(c)) return "zh_cn";
  return "symbol";
}
function genMode(type) {
  if (type === "digit") return "digit";
  if (type === "letter") return "letter";
  if (type === "zh_cn") return "hanzi";
  return "any";
}
// 多文件对齐：把每段按"字符类型序列"对齐，类型一致的位置合并为变量段，否则为固定文字
function genAlign(parsed) {
  const maxLen = Math.max(...parsed.map((x) => x.length));
  const dsl = [];
  for (let i = 0; i < maxLen; i++) {
    const col = parsed.map((p) => p[i]);
    const types = [...new Set(col.filter(Boolean).map((s) => s.type))];
    if (types.length === 1 && col.filter(Boolean).length === parsed.length) {
      dsl.push({ type: genMode(types[0]), sep: i < maxLen - 1 ? "_" : "" });
    } else {
      // 该位置不一致：取最长公共前缀作为固定文字，其余视为变量
      const vals = col.filter(Boolean).map((s) => s.text);
      let common = "";
      if (vals.length === parsed.length) {
        for (let k = 0; k < Math.min(...vals.map((v) => v.length)); k++) {
          if (vals.every((v) => genClassifyChar(v[k]) === genClassifyChar(vals[0][k]))) common += vals[0][k];
          else break;
        }
      }
      if (common) dsl.push({ type: "text", text: common, sep: i < maxLen - 1 ? "_" : "" });
      else dsl.push({ type: "any", sep: i < maxLen - 1 ? "_" : "" });
    }
  }
  return dsl;
}

// 从规则行 DOM 读回规则对象（保存时用）
function readRuleFromRow(row) {
  const kind = row.querySelector(".r-kind").value;
  const source = row.querySelector(".r-source").value.trim();
  const folderSV = row.querySelector(".r-folder").value;
  const folder = folderSV === "__manual__"
    ? row.querySelector(".r-folder-new").value.trim()
    : folderSV.trim();
  // v2.4.1：类型区改为"分组标签 + 单面板"，只有当前组的 checkbox 在 DOM 里，
  // 因此保存必须从行对象的状态读取，不能只查当前可见的 .r-ext:checked
  const exts = cleanExts(row._getExts ? row._getExts() : [...row.querySelectorAll(".r-ext:checked")].map((c) => c.value));
  const out = { source, folder, kind, exts };
  const pr = row.querySelector(".r-priority");
  out.priority = pr ? Number(pr.value || 0) : 0;
  const enBox = row.querySelector(".r-en");
  out.enabled = enBox ? enBox.checked : true;
  out.exclude = String(row.querySelector(".r-exclude") ? row.querySelector(".r-exclude").value : "")
    .split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean);
  if (kind === "kw") {
    out.pattern = row.querySelector(".r-kw").value.trim();
  } else if (kind === "regex") {
    out.pattern = row.querySelector(".r-regex").value.trim();
  } else {
    const dsl = row._dsl || [];
    out.dsl = dsl;
    out.pattern = compileDsl(dsl);
  }
  return out;
}

// ---------- Tab 切换 ----------
document.querySelectorAll(".tab-btn").forEach((t) => {
  t.addEventListener("click", () => {
    if (t.classList.contains("active")) return;
    document.querySelectorAll(".tab-btn").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    document.querySelectorAll(".tab-content").forEach((p) => p.classList.remove("active"));
    $("tab-" + t.dataset.tab).classList.add("active");
    if (taskTimer) { clearTimeout(taskTimer); taskTimer = null; }
    if (t.dataset.tab === "overview") loadOverview();
    if (t.dataset.tab === "groups") loadGroups();
    if (t.dataset.tab === "rules") loadRulesTab();
    if (t.dataset.tab === "maint") loadMaintTab();
    if (t.dataset.tab === "files") loadFileTab();
  if (t.dataset.tab === "tasks") loadTasksTab();
  if (t.dataset.tab === "audit") loadAuditTab();
  });
});

// ---------- 总览 ----------
async function loadOverview() {
  try {
    const d = await bridge.apiGet("status");
    const chip = document.querySelector(".ver-chip");
    if (chip && d.version) chip.textContent = "v" + d.version;
    setCardState("val-enabled", d.plugin_enabled, "已启用", "已停用");
    setCardState("val-bound", d.bot_bound, "已连接", "未连接");
    setCardNum("val-groups", d.groups);
    setCardNum("val-umi", Array.isArray(d.umi) ? d.umi.length : 0);
    $("ov-admins").value = (d.admin_users || []).join(",");
    loadTrend();
  } catch (e) {
    toast("加载状态失败：" + e.message, "error");
  }
}
// ---------- 容量趋势（v2.8.0） ----------
async function loadTrend() {
  const sel = document.getElementById("tr-group");
  const box = document.getElementById("tr-body");
  if (!sel || !box) return;
  if (!sel.options.length) {
    try {
      const g = await bridge.apiGet("groups");
      sel.innerHTML = (g.groups || []).map((x) =>
        '<option value="' + esc(String(x.group_id)) + '">' + esc(x.name || x.group_id) + "</option>"
      ).join("");
    } catch (e) {}
  }
  const gid = sel.value;
  if (!gid) { box.innerHTML = '<div class="empty-state">还没有配置群</div>'; return; }
  try {
    const d = await bridge.apiGet("trend", { gid, days: 14 });
    const rows = d.series || [];
    if (!rows.length) { box.innerHTML = '<div class="empty-state">还没有采样数据（发 /gf 状态 或 /gf 整 后会记录）</div>'; return; }
    const max = Math.max.apply(null, rows.map((r) => Number(r.u || 0)).concat([1]));
    box.innerHTML = rows.map((r) => {
      const pct = Math.round((Number(r.u || 0) / max) * 100);
      const gb = (Number(r.u || 0) / 1073741824).toFixed(2);
      return '<div style="display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12px;">'
        + '<span style="width:86px;" class="hint">' + esc(r.d) + "</span>"
        + '<div style="flex:1;height:10px;background:var(--border);border-radius:5px;overflow:hidden;">'
        + '<div style="height:100%;width:' + pct + '%;background:var(--accent);"></div></div>'
        + '<span style="width:130px;text-align:right;">' + gb + 'GB / ' + esc(String(r.n || 0)) + " 个</span></div>";
    }).join("");
  } catch (e) {
    box.innerHTML = '<div class="muted" style="color:var(--danger);">加载失败：' + esc(e.message) + "</div>";
  }
}
document.addEventListener("change", (ev) => {
  if (ev.target && ev.target.id === "tr-group") loadTrend();
});

// ---- v2.2.5：总览页全局配置备份（导出全部群 / 导入全局备份，学维护页交互） ----
(function () {
  function ovBackupJson() { return $("ov-export-text").value.trim(); }
  function copyText(txt, okMsg) {
    const done = () => toast(okMsg);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(done, () => fallbackCopy(txt, done));
    } else fallbackCopy(txt, done);
  }
  function fallbackCopy(txt, done) {
    const ta = document.createElement("textarea");
    ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta); done();
  }
  async function genExport() {
    const msg = $("ov-export-msg");
    try {
      msg.textContent = "生成中…";
      const r = await bridge.apiGet("export_all");
      // v2.3.6 全层级容错：r.groups / r.data.groups / r.data.data.groups 任一命中即可
      let data = (r && r.data) || {};
      if (!data.groups && r && r.groups && typeof r.groups === "object") data = r;
      else if (data.data && data.data.groups && typeof data.data.groups === "object") data = data.data;
      const groups = data.groups;
      if (!groups || typeof groups !== "object") {
        const raw = JSON.stringify(r).slice(0, 140);
        msg.textContent = "⚠ 接口没有返回数据（groups 缺失）：刚更新插件后请重启 AstrBot 再试。若重启后仍失败，请把这段反馈给开发者：" + raw;
        toast("导出接口异常：需重启 AstrBot", "error");
        return null;
      }
      const n = Object.keys(groups).length;
      if (!n) { msg.textContent = "还没有任何群配置可导出，先去「群配置」添加群"; toast("还没有群配置可导出", "error"); return null; }
      $("ov-export-text").value = JSON.stringify(data, null, 2);
      msg.textContent = "✅ 已生成 " + n + " 个群的全局备份，可复制或下载";
      toast("已生成 " + n + " 个群的全局备份");
      return n;
    } catch (e) {
      msg.textContent = "导出失败：" + e.message + "（若提示 404/不存在：刚更新插件后需重启 AstrBot 注册新接口）";
      toast("导出失败：" + e.message, "error");
      return null;
    }
  }
  $("ov-backup-toggle").addEventListener("click", () => {
    const body = $("ov-backup-body");
    const show = body.style.display === "none";
    body.style.display = show ? "" : "none";
    $("ov-backup-toggle").textContent = show ? "▾ 收起" : "▸ 展开";
  });
  $("ov-export-gen").addEventListener("click", genExport);
  $("ov-export-copy").addEventListener("click", async () => {
    if (!ovBackupJson() && !await genExport()) return;
    copyText(ovBackupJson(), "已复制全局备份 JSON");
  });
  $("ov-export-dl").addEventListener("click", async () => {
    if (!ovBackupJson() && !await genExport()) return;
    const blob = new Blob([ovBackupJson()], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const d = new Date();
    const pad = (x) => String(x).padStart(2, "0");
    a.download = "全局备份_群文件_" + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + "_" + pad(d.getHours()) + pad(d.getMinutes()) + ".json";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast("已下载全局备份文件");
  });
  $("ov-import-toggle").addEventListener("click", () => {
    const panel = $("ov-import-panel");
    const show = panel.style.display === "none";
    panel.style.display = show ? "" : "none";
    if (show) $("ov-import-msg").textContent = "";
  });
  $("ov-import-file-btn").addEventListener("click", () => { $("ov-import-file").click(); });
  $("ov-import-file").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    ev.target.value = "";  // 允许连续选择同一文件
    if (!file) return;
    $("ov-import-text").value = await file.text();
    $("ov-import-name").textContent = "已读入文件：" + file.name;
    $("ov-import-msg").textContent = "已读入，请检查内容后点「确认覆盖导入」";
  });
  $("ov-import-cancel").addEventListener("click", () => { $("ov-import-panel").style.display = "none"; });
  $("ov-import-go").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    if (!armConfirm(btn, "⚠ 再点一次：覆盖全部")) return;
    const msg = $("ov-import-msg");
    const raw = String($("ov-import-text").value || "").replace(/^\uFEFF/, "").trim();
    if (!raw) { msg.textContent = "导入失败：内容为空，请粘贴或选择全局备份 JSON"; return; }
    msg.textContent = "解析中…";
    let data = null;
    try { data = JSON.parse(raw); }
    catch (e) { msg.textContent = "导入失败：JSON 格式错误（" + e.message + "），请确认复制的是完整备份内容"; return; }
    // 兼容 {ok,data} 包装 / 顶层 {groups} / {data:{groups}}
    if (data && data.data && typeof data.data === "object" && data.data.groups) data = data.data;
    if (!data || typeof data !== "object" || !data.groups || typeof data.groups !== "object") {
      msg.textContent = "导入失败：不是全局备份格式（缺少 groups 字段）；单群配置请去「群配置」页该群卡片导入";
      return;
    }
    msg.textContent = "导入中…";
    try {
      const r = await bridge.apiPost("import_all", { data });
      const saved = (r && r.saved) || [];
      const skipped = (r && r.skipped) || [];
      msg.textContent = "✅ 导入成功：" + saved.length + " 个群（" + saved.join("、") + "）" + (skipped.length ? "；" + skipped.length + " 个群数据无效已跳过" : "");
      toast("全局导入完成：" + saved.length + " 个群");
      $("ov-import-text").value = "";
      $("ov-import-name").textContent = "";
      loadOverview();
      setTimeout(() => { $("ov-import-panel").style.display = "none"; }, 800);
    } catch (e) {
      msg.textContent = "导入失败：" + e.message;
    }
  });
})();

function setCardState(id, ok, okText, noText) {
  const el = $(id);
  el.textContent = ok ? okText : noText;
  el.className = "stat-val " + (ok ? "ok" : "err");
}
function setCardNum(id, n) {
  const el = $(id);
  el.textContent = n;
  el.className = "stat-val num";
}

// 全局管理员：从群成员里挑（默认取第一个已配置群）
$("ov-admin-pick").addEventListener("click", async () => {
  const panel = $("ov-admin-panel");
  if (panel.style.display !== "none") { panel.style.display = "none"; return; }
  panel.style.display = "block";
  if (!groupsData.length) {
    try {
      const d = await bridge.apiGet("groups");
      groupsData = d.groups || [];
    } catch (e) { /* 下面会提示 */ }
  }
  if (!groupsData.length) { panel.innerHTML = '<div class="muted">还没有配置群：先去「群配置」勾选群，再回来选管理员。</div>'; return; }
  openMemberPicker(panel, {
    gid: groupsData[0].group_id,
    groups: groupsData,
    getSelected: () => $("ov-admins").value.split(/[,，\s]+/).filter(Boolean),
    setSelected: (ids) => { $("ov-admins").value = ids.join(","); },
  });
});
$("ov-admin-save").addEventListener("click", async () => {
  const admins = $("ov-admins").value.split(/[,，\s]+/).filter(Boolean);
  try {
    await bridge.apiPost("admin_users", { admins });
    toast("已保存全局管理员");
  } catch (e) {
    toast("保存失败：" + e.message, "error");
  }
});

// ---------- 成员选择器（搜索 + 头像网格 + 点击选中，v2.0.5） ----------
const FALLBACK_AVATAR = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='50' fill='%23678'/><text x='50' y='66' font-size='44' text-anchor='middle'>👤</text></svg>";

function openMemberPicker(panel, opts) {
  // opts: { gid, groups(可切换的群列表，可选), getSelected(): string[], setSelected(ids) }
  panel.style.display = "block";
  panel.innerHTML = '<div class="muted">加载群成员…</div>';
  let gid = String(opts.gid || "");
  let members = [];
  const selected = new Set(opts.getSelected());
  const nameMap = {};

  panel.innerHTML = `
    <div class="picker-toolbar">
      ${opts.groups ? '<select class="pk-group"></select>' : ""}
      <input type="text" class="pk-search" placeholder="搜索昵称 / 群名片 / QQ 号" />
    </div>
    <div class="member-grid"></div>
    <div class="picker-actions">
      <span class="picker-count"></span>
      <button class="btn btn-primary btn-sm pk-done" type="button">✓ 完成选择</button>
      <span class="hint">点卡片选中 / 再点取消；名字看不清可先搜索</span>
    </div>`;

  const gsel = panel.querySelector(".pk-group");
  if (gsel) {
    (opts.groups || []).forEach((g) => {
      const o = document.createElement("option");
      o.value = g.group_id;
      o.textContent = (g.name ? g.name + " " : "") + "(" + g.group_id + ")";
      gsel.appendChild(o);
    });
    if (gid) gsel.value = gid;
    gsel.addEventListener("change", () => { gid = gsel.value; load(); });
  }
  panel.querySelector(".pk-search").addEventListener("input", renderGrid);
  panel.querySelector(".pk-done").addEventListener("click", () => {
    opts.setSelected([...selected]);
    panel.style.display = "none";
    toast(`已选定 ${selected.size} 人`);
  });

  function renderGrid() {
    const kw = (panel.querySelector(".pk-search").value || "").trim().toLowerCase();
    const grid = panel.querySelector(".member-grid");
    grid.innerHTML = "";
    if (!members.length) {
      grid.innerHTML = '<div class="muted">这个群没拉到成员，可直接手动输入 QQ 号。</div>';
    }
    const list = members.filter((m) =>
      !kw || (m.name || "").toLowerCase().includes(kw) || String(m.user_id).includes(kw));
    list.forEach((m) => {
      const it = document.createElement("div");
      it.className = "member-item" + (selected.has(m.user_id) ? " selected" : "");
      it.title = m.name + " (" + m.user_id + ")";
      it.innerHTML = '<img class="member-avatar" src="https://q1.qlogo.cn/g?b=qq&nk=' + esc(String(m.user_id)) + '&s=100" loading="lazy" alt="" />'
        + '<span class="member-name">' + esc(m.name) + "</span>"
        + '<span class="member-uid">' + esc(String(m.user_id)) + "</span>";
      const img = it.querySelector("img");
      img.addEventListener("error", () => { img.src = FALLBACK_AVATAR; }, { once: true });
      it.addEventListener("click", () => {
        if (selected.has(m.user_id)) selected.delete(m.user_id);
        else { selected.add(m.user_id); nameMap[m.user_id] = m.name; }
        it.classList.toggle("selected", selected.has(m.user_id));
        updateCount();
      });
      grid.appendChild(it);
    });
    updateCount();
  }
  function updateCount() {
    panel.querySelector(".picker-count").textContent = "已选 " + selected.size + " 人";
  }
  async function load() {
    if (!gid) { renderGrid(); return; }
    grid_loading();
    try {
      const data = await bridge.apiGet("members", { gid });
      members = (data && data.members) || [];
      members.forEach((m) => { nameMap[m.user_id] = m.name; });
      renderGrid();
    } catch (e) {
      members = [];
      const grid = panel.querySelector(".member-grid");
      grid.innerHTML = '<div class="muted" style="color:var(--danger);">加载失败：' + esc(e.message) + "</div>";
    }
  }
  function grid_loading() {
    panel.querySelector(".member-grid").innerHTML = '<div class="muted">加载群成员…</div>';
  }
  load();
}

// ---------- 群配置：chips 子页（管理范围 / 群基础设置，学 forward_pack 二级导航） ----------
function renderGroupsViews() {
  const editSel = $("g-edit-sel");
  const host = $("g-edit-host");
  const prevSel = editSel.value;
  editSel.innerHTML = groupsData.map((g) =>
    '<option value="' + esc(String(g.group_id)) + '">' + esc((g.name || g.group_id) + "（" + g.group_id + "）") + '</option>'
  ).join("");
  if (prevSel && groupsData.some((g) => String(g.group_id) === prevSel)) editSel.value = prevSel;
  if (!groupsData.length) {
    host.innerHTML = '<div class="section muted">还没有配置任何群：先切到「管理范围」勾选启用；也可用「高级编辑 / 手动添加」表单。</div>';
    editSel.innerHTML = '<option value="">暂无已配置的群</option>';
    return;
  }
  renderEditCard();
}
function switchGroupPane(pane) {
  document.querySelectorAll("#g-subnav .subchip").forEach((b) =>
    b.classList.toggle("active", b.dataset.pane === pane));
  $("g-pane-scope").style.display = pane === "scope" ? "" : "none";
  $("g-pane-edit").style.display = pane === "edit" ? "" : "none";
}
$("g-subnav").addEventListener("click", (ev) => {
  const b = ev.target.closest(".subchip");
  if (b) switchGroupPane(b.dataset.pane);
});
$("g-edit-sel").addEventListener("change", renderEditCard);
function renderEditCard() {
  const sel = $("g-edit-sel");
  const host = $("g-edit-host");
  const gid = sel.value;
  host.innerHTML = "";
  if (!gid) return;
  const g = groupsData.find((x) => String(x.group_id) === String(gid));
  if (g) host.appendChild(renderGroupCard(g));
}
function switchToEdit(gid) {
  switchGroupPane("edit");
  $("g-edit-sel").value = String(gid);
  renderEditCard();
  $("g-pane-edit").scrollIntoView({ block: "start", behavior: "smooth" });
}

// ---------- 群配置：群基础信息卡片 ----------
function renderGroupCard(g) {
  const admins = Array.isArray(g.admins) ? g.admins.join(",") : "";
  const card = document.createElement("div");
  card.className = "section group-card";
  card.dataset.gid = g.group_id;

  card.innerHTML = `
    <h2 class="group-head">
      <span class="g-head-ava">${esc((g.name || g.group_id || "群").toString().replace(/^(\s|\[|【)+/, "").charAt(0) || "群")}</span>
      <span class="g-gid">${esc(g.group_id)}</span>
      <input class="g-name" type="text" value="${esc(g.name || "")}" placeholder="备注名" />
      <span class="group-head-actions">
        <button class="g-export btn btn-sm" type="button" title="导出该群全部配置为 JSON 文件，会下载到浏览器下载目录"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>导出配置</button>
        <button class="g-import btn btn-sm" type="button" title="选择一个此前导出的 JSON 文件，覆盖导入该群配置"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V3m0 0 4 4m-4-4-4 4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>导入配置</button>
        <button class="g-del btn btn-sm btn-danger" type="button">删除群</button>
        <button class="g-collapse btn btn-sm" type="button" title="折叠/展开本群">▾ 折叠</button>
      </span>
    </h2>
    <input type="file" class="g-import-file" accept=".json,application/json" style="display:none;" />

    <div class="g-export-panel sub-panel" style="display:none;">
      <div class="muted" style="margin-bottom:6px;">导出该群全部配置（规则 / 定时 / 容量 / 查重），可备份或迁移到其他群：</div>
      <textarea class="ge-text" rows="8" readonly style="width:100%;font-family:monospace;font-size:12px;"></textarea>
      <div class="toolbar" style="margin-top:6px;">
        <button class="ge-gen btn" type="button">生成并显示</button>
        <button class="ge-copy btn" type="button">复制 JSON</button>
        <button class="ge-dl btn btn-primary" type="button">⬇ 下载文件</button>
        <button class="ge-close btn" type="button">关闭</button>
        <span class="ge-msg hint-text"></span>
      </div>
    </div>

    <div class="g-import-panel sub-panel" style="display:none;">
      <div class="muted" style="margin-bottom:6px;">导入配置（会覆盖该群现有全部设置）：粘贴此前导出的 JSON，或点「选择文件」：</div>
      <textarea class="gi-text" rows="8" placeholder='把导出的 JSON 完整粘贴到这里' style="width:100%;font-family:monospace;font-size:12px;"></textarea>
      <div class="toolbar" style="margin-top:6px;">
        <button class="gi-file btn" type="button">选择文件…</button>
        <button class="gi-do btn btn-danger" type="button">确认覆盖导入</button>
        <button class="gi-cancel btn" type="button">取消</button>
        <span class="gi-msg hint-text"></span>
      </div>
    </div>

    <div class="sub-panel g-basic">
      <div class="muted g-basic-title">⚙ 群基础信息</div>
      <label class="enable-check" title="关闭后该群不执行定时整理/任何自动推送；手动命令和本控制台仍可用">
        <input type="checkbox" class="g-enabled" ${g.enabled === false ? "" : "checked"} />
        <span class="ec-main">✅ 本群启用插件</span>
        <span class="ec-sub">关闭后仅停定时整理与推送，手动命令 / 控制台仍可用</span>
      </label>
      <div class="field">
        <label class="muted" style="margin-bottom:5px;">👤 该群文件管理员 QQ</label>
        <div class="toolbar" style="margin:0;">
          <input type="text" class="g-admins" value="${esc(admins)}" placeholder="1423178195,3841157342" />
          <button class="g-pick-admin btn" type="button">👥 选成员</button>
        </div>
        <div class="hint" style="margin-top:6px;">能使用 /群文件 管理命令的人，逗号分隔；留空 = 仅机器人主人。点「选成员」从群里挑，不用手输。</div>
      </div>
      <div class="g-admin-panel sub-panel" style="display:none;"></div>
      <div class="field">
        <label class="muted" style="margin-bottom:5px;">🔐 角色权限（每行：QQ 角色；view / move / delete / admin）</label>
        <textarea class="g-perms" rows="3" placeholder="123456 move&#10;789012 delete" style="width:100%;font-family:monospace;font-size:12px;">${esc(Object.entries(g.permissions || {}).map(([k, v]) => k + " " + v).join("\n"))}</textarea>
        <div class="hint" style="margin-top:4px;">view 只看；move 可移动/建文件夹；delete 可删除；admin 全部。上方「管理员 QQ」等价 admin。</div>
      </div>
      <div class="toolbar g-save-bar">
        <button class="g-save btn btn-primary" type="button">保存群基础信息</button>
        <span class="hint-text">分类规则在「分类规则」页；定时 / 容量 / 查重在「定时与维护」页；导出 / 导入包含该群全部设置</span>
      </div>
    </div>
  `;

  // 删除群（两段式确认）
  card.querySelector(".g-del").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    if (!armConfirm(btn, "⚠ 再点一次：删除本群配置")) return;
    try {
      await bridge.apiPost("group_delete", { group_id: g.group_id });
      toast("已删除");
      loadGroups();
    } catch (err) {
      if (btn._restore) btn._restore();
      toast("删除失败：" + err.message, "error");
    }
  });

  // 折叠/展开（状态按群号记忆）
  (function () {
    const colBtn = card.querySelector(".g-collapse");
    const bodyEls = [];
    let node = card.firstElementChild;
    while (node) { const nx = node.nextElementSibling; if (node.tagName !== "H2") bodyEls.push(node); node = nx; }
    function setCollapsed(collapsed, persist) {
      bodyEls.forEach((el) => { el.style.display = collapsed ? "none" : ""; });
      colBtn.textContent = collapsed ? "▸ 展开" : "▾ 折叠";
      if (persist) {
        try {
          const set = JSON.parse(localStorage.getItem("gfc_collapsed") || "{}");
          if (collapsed) set[String(g.group_id)] = 1; else delete set[String(g.group_id)];
          localStorage.setItem("gfc_collapsed", JSON.stringify(set));
        } catch (e) { /* 隐私模式等忽略 */ }
      }
    }
    colBtn.addEventListener("click", () => setCollapsed(colBtn.textContent.indexOf("折叠") !== -1, true));
    let initCollapsed = false;
    try { initCollapsed = !!JSON.parse(localStorage.getItem("gfc_collapsed") || "{}")[String(g.group_id)]; } catch (e) {}
    if (initCollapsed) setCollapsed(true, false);
  })();

  // 从群成员里选管理员
  const adminPanel = card.querySelector(".g-admin-panel");
  card.querySelector(".g-pick-admin").addEventListener("click", () => {
    if (adminPanel.style.display !== "none") { adminPanel.style.display = "none"; return; }
    openMemberPicker(adminPanel, {
      gid: g.group_id,
      getSelected: () => card.querySelector(".g-admins").value.split(/[,，\s]+/).filter(Boolean),
      setSelected: (ids) => {
        card.querySelector(".g-admins").value = ids.join(",");
        toast("已写回 " + ids.length + " 人，记得「保存群基础信息」");
      },
    });
  });

  // 保存群基础信息：全量接口，需把其他模块的设置原样回传，避免覆盖
  card.querySelector(".g-save").addEventListener("click", async () => {
    const perms = {};
    String(card.querySelector(".g-perms") ? card.querySelector(".g-perms").value : "")
      .split("\n").forEach((line) => {
        const m = line.trim().match(/^(\d{5,})\s+([a-zA-Z]+|查看|移动|删除|管理员|管)$/);
        if (m) perms[m[1]] = m[2].toLowerCase();
      });
    const body = {
      group_id: g.group_id,
      enabled: card.querySelector(".g-enabled").checked,
      name: card.querySelector(".g-name").value.trim(),
      admins: card.querySelector(".g-admins").value.split(/[,，\s]+/).filter(Boolean),
      permissions: perms,
      rules: Array.isArray(g.rules) ? g.rules : [],
      cleanup: g.cleanup || { enabled: false, crones: ["0 3 * * *"] },
      limits: g.limits || {},
      dupe_rule: g.dupe_rule || {},
    };
    try {
      await bridge.apiPost("groups", body);
      toast("已保存 群 " + g.group_id);
    } catch (e) {
      toast("保存失败：" + e.message, "error");
    }
  });

  // ---- 导出面板 ----
  const exportPanel = card.querySelector(".g-export-panel");
  const geText = exportPanel.querySelector(".ge-text");
  const geMsg = exportPanel.querySelector(".ge-msg");
  let gePayload = null;
  const loadExport = async () => {
    geMsg.textContent = "加载中…";
    try {
      const r = await bridge.apiGet("export", { gid: g.group_id });
      let payload = null;
      if (r && r.data && Array.isArray(r.data.rules)) payload = r.data;
      else if (r && Array.isArray(r.rules)) payload = r;
      if (!payload) throw new Error("未取到规则数据（桥接返回结构异常）");
      gePayload = payload;
      geText.value = JSON.stringify(payload, null, 2);
      geMsg.textContent = "已生成，共 " + (payload.rules || []).length + " 条规则";
    } catch (e) {
      geMsg.textContent = "导出失败：" + e.message;
    }
  };
  card.querySelector(".g-export").addEventListener("click", async () => {
    const show = exportPanel.style.display === "none";
    exportPanel.style.display = show ? "block" : "none";
    if (show) await loadExport();
  });
  exportPanel.querySelector(".ge-gen").addEventListener("click", loadExport);
  exportPanel.querySelector(".ge-close").addEventListener("click", () => { exportPanel.style.display = "none"; });
  exportPanel.querySelector(".ge-copy").addEventListener("click", async () => {
    if (!gePayload) { geMsg.textContent = "请先点「生成并显示」"; return; }
    try {
      await navigator.clipboard.writeText(JSON.stringify(gePayload, null, 2));
      geMsg.textContent = "✅ 已复制到剪贴板，可直接粘贴到导入面板";
    } catch (e) {
      geMsg.textContent = "复制失败（浏览器限制），请手动选中上方文本框内容复制：" + e.message;
    }
  });
  exportPanel.querySelector(".ge-dl").addEventListener("click", () => {
    if (!gePayload) { geMsg.textContent = "请先点「生成并显示」"; return; }
    try {
      const blob = new Blob([JSON.stringify(gePayload, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "群文件管理_" + g.group_id + "_配置.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(a.href), 3000);
      geMsg.textContent = "已触发下载；若下载的文件是空的，请改用「复制 JSON」";
    } catch (e) {
      geMsg.textContent = "下载失败：" + e.message + "，请改用「复制 JSON」";
    }
  });

  // ---- 导入面板 ----
  const importPanel = card.querySelector(".g-import-panel");
  const giText = importPanel.querySelector(".gi-text");
  const giMsg = importPanel.querySelector(".gi-msg");
  card.querySelector(".g-import").addEventListener("click", () => {
    importPanel.style.display = importPanel.style.display === "none" ? "block" : "none";
    giMsg.textContent = "";
  });
  const doImport = async (txt) => {
    const raw = String(txt || "").replace(/^\uFEFF/, "").trim();
    if (!raw) { giMsg.textContent = "导入失败：内容为空，请粘贴或选择导出的 JSON"; return; }
    giMsg.textContent = "解析中…";
    try {
      let parsed;
      try { parsed = JSON.parse(raw); }
      catch (e) { throw new Error("JSON 格式错误（" + e.message + "），请确认复制的是导出文本框里的完整内容"); }
      let data = null;
      if (parsed && parsed.data && Array.isArray(parsed.data.rules)) data = parsed.data;
      else if (parsed && Array.isArray(parsed.rules)) data = parsed;
      else if (parsed && parsed.groups && typeof parsed.groups === "object") {
        const c = parsed.groups[g.group_id] || Object.values(parsed.groups)[0];
        if (c && Array.isArray(c.rules)) data = Object.assign({ group_id: g.group_id }, c);
      }
      if (!data || !Array.isArray(data.rules)) {
        throw new Error("无法识别的配置结构（需要 rules 字段）；若是旧版文件，请用当前版本重新导出");
      }
      data.group_id = String(g.group_id);
      const giDoBtn = importPanel.querySelector(".gi-do");
      if (!armConfirm(giDoBtn, "⚠ 再点一次：覆盖导入本群配置")) { giMsg.textContent = ""; return; }
      giMsg.textContent = "导入中…";
      const r = await bridge.apiPost("import", { data, target_gid: String(g.group_id) });
      giMsg.textContent = "✅ 导入成功（" + ((r && r.saved) || g.group_id) + "，字段：" + ((r && r.fields && r.fields.join("/")) || "配置") + "）";
      importPanel.style.display = "none";
      toast("导入成功，已刷新");
      loadGroups();
    } catch (err) {
      giMsg.textContent = "导入失败：" + err.message;
    }
  };
  importPanel.querySelector(".gi-do").addEventListener("click", () => doImport(giText.value));
  importPanel.querySelector(".gi-file").addEventListener("click", () => { card.querySelector(".g-import-file").click(); });
  importPanel.querySelector(".gi-cancel").addEventListener("click", () => { importPanel.style.display = "none"; });
  card.querySelector(".g-import-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    e.target.value = "";  // 允许连续选择同一文件
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      giText.value = reader.result;  // 只填入粘贴框，不直接执行（防误覆盖，流程与维护页一致）
      giMsg.textContent = "已读入文件：" + f.name + "，请检查内容后点「确认覆盖导入」";
    };
    reader.onerror = () => { giMsg.textContent = "读取文件出错，请重试"; };
    reader.readAsText(f);
  });

  return card;
}

async function loadGroups() {
  try {
    const data = await bridge.apiGet("groups");
    groupsData = data.groups || [];
    const available = data.available || [];
    const pick = $("g-pick");
    const prev = pick.value;
    pick.innerHTML = '<option value="">— 从 bot 所在群选择 —</option>';
    available.forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g.group_id;
      opt.textContent = (g.group_name || g.group_id) + "（" + g.group_id + "）";
      pick.appendChild(opt);
    });
    if (prev && [...pick.options].some((o) => o.value === prev)) pick.value = prev;
    renderScopeList(available);
    renderGroupsViews();
  } catch (e) {
    toast("加载群配置失败：" + e.message, "error");
  }
}

// ---------- 管理范围（勾选启用 / 取消停用） ----------
function renderScopeList(available) {
  const listEl = $("g-scope-list");
  const searchEl = $("g-search");
  const countEl = document.querySelector(".g-scope-count");
  if (!listEl) return;
  const cfgMap = {};
  groupsData.forEach((g) => { cfgMap[String(g.group_id)] = g; });

  // 行 = bot 所在群 + 已配置但 bot 不在的群（孤儿）
  const availIds = new Set(available.map((g) => String(g.group_id)));
  const rows = available.map((g) => ({
    id: String(g.group_id),
    name: g.group_name || String(g.group_id),
    members: g.member_count,
    orphan: false,
  }));
  groupsData.forEach((g) => {
    const gid = String(g.group_id);
    if (!availIds.has(gid)) {
      rows.push({ id: gid, name: g.name || gid, members: null, orphan: true });
    }
  });
  // 已启用的排前面
  rows.sort((a, b) => Number(!!cfgMap[b.id] && cfgMap[b.id].enabled !== false) - Number(!!cfgMap[a.id] && cfgMap[a.id].enabled !== false));

  async function toggle(gid, checked) {
    try {
      await bridge.apiPost("groups", { group_id: gid, enabled: checked });
      toast(checked ? `✅ 群 ${gid} 已启用本插件` : `⛔ 群 ${gid} 已停用（配置保留，勾回来即恢复）`);
    } catch (e) {
      toast("保存失败：" + e.message, "error");
    }
    // loadGroups 会用当前搜索词重渲染（搜索框内容不被清掉）
    await loadGroups();
  }

  function draw() {
    const kw = (searchEl.value || "").trim().toLowerCase();
    const filtered = rows.filter((r) => !kw || r.name.toLowerCase().includes(kw) || r.id.includes(kw));
    const enabledCount = rows.filter((r) => cfgMap[r.id] && cfgMap[r.id].enabled !== false).length;
    listEl.innerHTML = "";
    if (!rows.length) {
      listEl.innerHTML = '<div class="hint" style="padding:8px 0;">拉取不到 bot 的群列表（OneBot 未就绪或已退出所有群）。可用下方表单手动添加，或在群里发 /gf 绑。</div>';
    }
    filtered.forEach((r) => {
      const cfg = cfgMap[r.id];
      const on = !!cfg && cfg.enabled !== false;
      const wrap = document.createElement("label");
      wrap.className = "g-scope-row" + (on ? " on" : "");
      const info = [];
      if (r.members != null) info.push(r.members + " 人");
      if (cfg) info.push((cfg.rules || []).length + " 条规则");
      if (r.orphan) info.push("机器人不在此群");
      wrap.innerHTML = `
        <input type="checkbox" ${on ? "checked" : ""} />
        <span class="g-ava">${esc(String(r.name).replace(/^(\s|\[|【)+/, "").charAt(0) || "群")}</span>
        <span class="g-name">${esc(r.name)}</span>
        <span class="hint">（${esc(r.id)}）${info.length ? " · " + esc(info.join(" · ")) : ""}</span>
        <button type="button" class="btn btn-sm g-jump">配置</button>`;
      const cb = wrap.querySelector("input");
      cb.addEventListener("change", () => toggle(r.id, cb.checked));
      wrap.querySelector(".g-jump").addEventListener("click", async (e) => {
        e.preventDefault();
        $("g-gid").value = r.id;
        $("g-name").value = (cfg && cfg.name) || (r.orphan ? r.name : "");
        const pick = $("g-pick");
        if ([...pick.options].some((o) => o.value === r.id)) pick.value = r.id;
        loadGroups().then(() => switchToEdit(gid));
      });
      listEl.appendChild(wrap);
    });
    if (countEl) countEl.textContent = rows.length
      ? `已启用 ${enabledCount} / 共 ${rows.length} 个群${filtered.length < rows.length ? `（筛选显示 ${filtered.length}）` : ""}`
      : "";
  }
  searchEl.oninput = draw;  // 赋值式替换，避免重复绑定
  draw();
}

$("g-pick").addEventListener("change", async () => {
  const gid = $("g-pick").value;
  if (!gid) return;
  $("g-gid").value = gid;
  $("g-name").value = "";
  const hit = groupsData.find((g) => String(g.group_id) === gid);
  if (hit && hit.name) $("g-name").value = hit.name;
  if (!$("g-name").value) {
    try {
      const data = await bridge.apiGet("groups");
      const g = (data.available || []).find((x) => String(x.group_id) === gid);
      if (g && g.group_name) $("g-name").value = g.group_name;
    } catch (e) { /* 忽略 */ }
  }
  toast("已选择群 " + gid + "，点「保存群」完成添加");
});
$("g-save").addEventListener("click", async () => {
  const gid = $("g-gid").value.trim();
  if (!gid) return toast("请填写群号");
  try {
    await bridge.apiPost("groups", { group_id: gid, name: $("g-name").value.trim() });
    toast("已添加/更新 群 " + gid);
    $("g-gid").value = "";
    $("g-name").value = "";
    loadGroups();
  } catch (e) {
    toast("保存失败：" + e.message, "error");
  }
});
$("g-reset").addEventListener("click", () => { $("g-gid").value = ""; $("g-name").value = ""; });

// ---------- 分类规则模块 ----------
async function loadRulesTab() {
  if (isRulesDirty() && !confirm("当前群的规则有未保存修改，重新加载会丢失。继续吗？")) {
    updateDirtyBadge();
    return;
  }
  const sel = $("r-group");
  const prev = sel.value;
  sel.innerHTML = "";
  try {
    const data = await bridge.apiGet("groups");
    groupsData = data.groups || [];
    if (!groupsData.length) {
      $("rules-body").innerHTML = '<div class="section empty-state">还没有配置任何群，先去「群配置」添加群。</div>';
      rulesCollect = null;
      return;
    }
    groupsData.forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g.group_id;
      opt.textContent = (g.name ? g.name + " (" : "") + g.group_id + (g.name ? ")" : "");
      sel.appendChild(opt);
    });
    const target = prev && [...sel.options].some((o) => o.value === prev) ? prev : sel.value;
    sel.value = target;
    renderRulesEditor(target);
  } catch (e) {
    toast("加载群列表失败：" + e.message, "error");
  }
}
$("r-group").addEventListener("change", (e) => {
  if (isRulesDirty() && !confirm("当前群的规则有未保存修改，切换群会丢失。仍要切换吗？")) {
    e.target.value = rulesGroupId;
    return;
  }
  rulesDirtyCheck = null;
  rulesDirtyReset = null;
  updateDirtyBadge();
  renderRulesEditor(e.target.value);
});
$("r-save").addEventListener("click", () => { if (rulesCollect) rulesCollect(); });

function renderRulesEditor(gid) {
  rulesGroupId = gid;
  rulesDirtyCheck = null;
  rulesDirtyReset = null;
  updateDirtyBadge();
  const g = groupsData.find((x) => String(x.group_id) === String(gid));
  const body = $("rules-body");
  if (!g) { body.innerHTML = '<div class="section empty-state">请先选择群。</div>'; rulesCollect = null; return; }
  const rules = Array.isArray(g.rules) && g.rules.length
    ? g.rules
    : [{ source: "", pattern: "", folder: "" }];

  body.innerHTML = "";

  // —— 胶囊切换：规则配置 / 扫描测试执行 ——
  const tabs = document.createElement("div");
  tabs.className = "subnav r-tabs";
  tabs.innerHTML = `
    <button class="subchip active" data-rpane="conf" type="button">规则配置</button>
    <button class="subchip" data-rpane="ops" type="button">扫描 / 测试 / 执行</button>`;
  const paneConf = document.createElement("div");
  const paneOps = document.createElement("div");
  paneOps.style.display = "none";
  tabs.addEventListener("click", (ev) => {
    const b = ev.target.closest(".subchip");
    if (!b) return;
    tabs.querySelectorAll(".subchip").forEach((x) => x.classList.toggle("active", x === b));
    const isOps = b.dataset.rpane === "ops";
    paneConf.style.display = isOps ? "none" : "";
    paneOps.style.display = isOps ? "" : "none";
  });
  body.appendChild(tabs);
  body.appendChild(paneConf);
  body.appendChild(paneOps);

  // —— 分区 1：规则编辑（→ 规则配置视图） ——
  const sec1 = document.createElement("div");
  sec1.className = "section";
  sec1.innerHTML = `
    <div class="section-header"><h2>分类规则</h2>
      <span class="hint-text">文件名命中条件 → 自动移入目标文件夹</span></div>
    <div class="hint" style="margin-bottom:10px;">规则平级、不看顺序：所有规则都会参与匹配，同一文件被多条命中时自动选「最具体」的一条（匹配内容越长越优先）。正则/关键词留空 = 兜底规则；若同时限定了文件类型，则只兜底这些类型，且比完全不限的兜底更优先（例：匹配内容留空 + 类型选 cbz/cbr = 群里所有漫画进同一文件夹）。目标文件夹要先在 QQ 群里建好。</div>
    <div class="g-gen-wrap sub-panel">
      <div class="toolbar" style="margin-bottom:0;">
        <button class="g-gen-toggle btn" type="button">✨ 从文件名智能生成规则（粘贴几个同类文件名，自动分析）</button>
      </div>
      <div class="g-gen-panel" style="display:none;margin-top:8px;">
        <div class="muted" style="margin-bottom:4px;">① 粘贴 2~5 个「应该归到同一文件夹」的文件名（一行一个）：</div>
        <textarea class="g-gen-input" rows="4" placeholder="第1章_引言.pdf&#10;第2章_安装.pdf&#10;第3章_配置.pdf"></textarea>
        <div class="muted" style="margin:8px 0 4px;">② 自动分析出的分段（类型相同的位置=变量，可手动改类型/分隔符）：</div>
        <div class="g-gen-dsl"></div>
        <div class="g-gen-preview hint" style="margin-top:6px;"></div>
        <div class="g-gen-test" style="margin:4px 0;"></div>
        <div class="g-gen-ext hint" style="margin:4px 0;"></div>
        <div class="muted" style="margin:8px 0 4px;">③ 这些文件应归到哪个文件夹：</div>
        <div class="toolbar" style="margin-bottom:0;">
          <input type="text" class="g-gen-source" placeholder="规则备注（可不填，如：课件章节）" />
          <select class="g-gen-folder"><option value="">选择目标文件夹…</option></select>
          <input type="text" class="g-gen-folder-new" placeholder="新文件夹名" style="display:none;" />
          <button class="g-gen-add btn btn-primary" type="button">加入规则列表</button>
        </div>
      </div>
    </div>
    <div class="g-rules" style="margin-top:10px;"></div>
    <div class="toolbar" style="margin-bottom:0;">
      <button class="g-rule-add btn" type="button">+ 添加规则</button>
      <select class="g-rule-template" title="一键生成常用规则（匹配留空 + 限定类型）">
        <option value="">⚡ 快捷模板…</option>
        <option value="comic">漫画（cbz/cbr/cb7/cbt → 漫画）</option>
        <option value="ebook">电子书（epub/mobi/azw3… → 电子书）</option>
        <option value="archive">压缩包（zip/rar/7z… → 压缩包）</option>
        <option value="image">图片（jpg/png/webp… → 图片）</option>
        <option value="doc">文档（pdf/doc/docx… → 文档）</option>
      </select>
      <button class="g-rule-fold btn" type="button">▾ 全部展开</button>
      <button class="g-rule-copy btn" type="button">📋 复制到其他群</button>
      <span class="hint">默认收起；点每条规则的「展开」编辑匹配内容、文件类型与排除词</span>
    </div>
    <div class="g-copy-panel sub-panel" style="display:none;margin-top:10px;"></div>`;
  paneConf.appendChild(sec1);

  // —— 分区 2：扫描与立即执行（→ 扫描/测试/执行视图） ——
  const sec2 = document.createElement("div");
  sec2.className = "section";
  sec2.innerHTML = `
    <div class="section-header"><h2>扫描、测试与执行</h2>
      <span class="hint-text">先测试规则看效果，预览确认后再真跑</span></div>
    <div class="ops-grid">
      <button class="g-scan btn" type="button">🔍 扫描群文件，自动生成规则</button>
      <button class="g-rule-test btn" type="button">🧪 试一试：文件名测规则</button>
      <button class="g-preview btn" type="button">👁 预览整理（不移动）</button>
      <button class="g-run-cleanup btn" type="button">▶ 立即整理一次</button>
    </div>
    <div class="g-verify-panel sub-panel" style="display:none;margin-top:12px;"></div>
    <div class="g-scan-panel sub-panel" style="display:none;margin-top:12px;"></div>`;
  paneOps.appendChild(sec2);

  const rulesBox = sec1.querySelector(".g-rules");
  const foldAllBtn = sec1.querySelector(".g-rule-fold");
  const scanPanel = sec2.querySelector(".g-scan-panel");
  let ruleFolderNames = []; // 缓存该群现有文件夹，重渲染后仍能回填下拉

  function applyFolderNames() {
    if (!ruleFolderNames.length) return;
    rulesBox.querySelectorAll(".rules-row").forEach((row) => {
      if (row._fillFolders) row._fillFolders(ruleFolderNames);
    });
  }

  // 未保存检测：以当前 DOM 快照和进入该群时的基线对比
  let rulesBaseline = null;
  function collectRulesDom() {
    const out = [];
    rulesBox.querySelectorAll(".rules-row").forEach((row) => {
      out.push(cleanRuleObj(readRuleFromRow(row)));
    });
    return out;
  }
  rulesDirtyCheck = () => {
    if (rulesBaseline == null) return false;
    try {
      return JSON.stringify(collectRulesDom()) !== rulesBaseline;
    } catch (e) {
      return true;
    }
  };
  rulesDirtyReset = () => {
    rulesBaseline = JSON.stringify(collectRulesDom());
    updateDirtyBadge();
  };
  rulesBox.addEventListener("input", updateDirtyBadge);
  rulesBox.addEventListener("change", updateDirtyBadge);

  // 自动判断规则类型
  function detectKind(r) {
    if (r.kind) return r.kind;
    if (Array.isArray(r.dsl) && r.dsl.length) return "dsl";
    return "kw";
  }

  // 一条规则行
  function renderRuleRow(r, i) {
    const kind = detectKind(r);
    const folded = isRuleFolded(r); // 已存在的规则默认收起，避免页面被详情撑长
    const row = document.createElement("div");
    row.className = "rules-row" + (folded ? " folded" : "");
    row._dsl = Array.isArray(r.dsl) ? r.dsl : [];
    row.innerHTML = `
      <div class="row" style="align-items:center;gap:8px;flex-wrap:wrap;">
        <label class="inline-check" style="flex:0 0 auto;" title="取消勾选=本条停用（不删除，随时可恢复）">
          <input type="checkbox" class="r-en" ${r.enabled !== false ? "checked" : ""} />启用
        </label>
        <span class="r-idx hint">${i + 1}.</span>
        <input type="text" class="r-source" value="${esc(r.source || "")}" placeholder="规则备注（可不填）" style="flex:1 1 120px;min-width:90px;" />
        <input type="number" class="r-priority" value="${r.priority != null ? r.priority : 0}" min="-100" max="100" title="优先级：越高越先命中（-100~100，默认 0）" style="flex:0 0 74px;" />
        <select class="r-kind" style="flex:0 0 100px;">
          <option value="kw">🔑 关键词</option>
          <option value="dsl">🧩 构建器</option>
          <option value="regex">⚙ 正则</option>
        </select>
        <select class="r-folder" style="flex:1 1 150px;min-width:110px;">
          <option value="__manual__">✍ 手动输入文件夹名…</option>
        </select>
        <input type="text" class="r-folder-new" placeholder="目标文件夹名（不存在会自动创建）" style="display:none;flex:1 1 120px;min-width:90px;" />
        <button class="r-fold btn btn-sm" type="button" title="展开/收起本条的匹配内容、文件类型与排除词">${folded ? "▸ 展开" : "▾ 收起"}</button>
        <button class="r-save btn btn-sm" type="button" title="只保存这一条规则（其他规则/设置不动）">💾 保存本条</button>
        <button class="r-del btn btn-sm btn-danger" type="button">删除</button>
      </div>
      <div class="r-sum hint" style="${folded ? "" : "display:none;"}"></div>
      <div class="r-body" style="${folded ? "display:none;" : ""}">
        <div class="r-kw-wrap" style="margin-top:10px;">
          <input type="text" class="r-kw" value="${kind === "kw" ? esc(r.pattern || "") : ""}" placeholder="文件名包含的关键词（留空=兜底规则）" />
        </div>
        <div class="r-regex-wrap" style="margin-top:6px;display:none;">
          <input type="text" class="r-regex" value="${kind === "regex" ? esc(r.pattern || "") : ""}" placeholder="正则，如 ^第\d+章（留空=兜底规则）" />
        </div>
        <div class="r-dsl-wrap" style="margin-top:6px;display:none;"></div>
        <div class="r-ext-wrap" style="margin-top:10px;">
          <div class="r-ext-toggle hint" style="cursor:pointer;user-select:none;padding:2px 0;">▸ 限定文件类型：不限（点击修改）</div>
          <div class="r-ext-box" style="display:none;margin-top:8px;"></div>
        </div>
        <div class="r-exclude-wrap" style="margin-top:10px;">
          <input type="text" class="r-exclude" value="${esc(Array.isArray(r.exclude) ? r.exclude.join("，") : "")}" placeholder="排除词（可选，逗号分隔）：文件名含这些词时本条规则不生效，如：预告，目录" />
        </div>
      </div>`;
    const kindSel = row.querySelector(".r-kind");
    const enBox = row.querySelector(".r-en");
    function enShow() { row.style.opacity = enBox.checked ? "" : "0.45"; }
    enBox.addEventListener("change", enShow);
    enShow();
    function kindShow() {
      const k = kindSel.value;
      row.querySelector(".r-kw-wrap").style.display = k === "kw" ? "" : "none";
      row.querySelector(".r-regex-wrap").style.display = k === "regex" ? "" : "none";
      row.querySelector(".r-dsl-wrap").style.display = k === "dsl" ? "" : "none";
    }
    kindSel.value = kind;
    kindShow();
    kindSel.addEventListener("change", () => {
      if (kindSel.value === "dsl" && !row._dsl.length) {
        row._dsl = [{ type: "any", sep: "_" }, { type: "any", sep: "" }];
        renderDslEditor(row.querySelector(".r-dsl-wrap"), row._dsl);
      }
      kindShow();
    });
    if (kind === "dsl") renderDslEditor(row.querySelector(".r-dsl-wrap"), row._dsl);

    // 限定文件类型（v2.4.1：分组标签 + 单面板。只渲染当前组，避免 130 个选项平铺一大片）
    const extBox = row.querySelector(".r-ext-box");
    const extToggle = row.querySelector(".r-ext-toggle");
    const selExts = cleanExts(r.exts);
    const selected = new Set(selExts);
    let extOpen = false;

    // 可编辑的类型组：预置 12 组 + 自定义组（只有保存过/添加过自定义后缀才出现）
    const groups = EXT_PRESETS.map((g) => ({ key: g.key, label: g.label, exts: g.exts.slice() }));
    const initialCustom = selExts.filter((e) => !EXT_CAT.has(e));
    if (initialCustom.length) groups.push({ key: "custom", label: "自定义", exts: initialCustom.slice() });

    extBox.innerHTML = `
      <div class="r-ext-top">
        <input type="text" class="r-ext-filter" placeholder="🔍 搜类型：cbz / 漫画 / epub…" />
        <button type="button" class="btn btn-sm r-ext-clear">清空已选</button>
        <span class="hint r-ext-count"></span>
      </div>
      <div class="r-ext-picked" style="display:none;">
        <span class="hint">已选：</span><span class="r-ext-picked-list"></span>
      </div>
      <div class="r-ext-tabs"></div>
      <div class="r-ext-panel">
        <div class="r-ext-panel-head">
          <span class="r-ext-panel-title"></span>
          <span class="hint r-ext-panel-count"></span>
          <span class="r-ext-panel-spacer"></span>
          <button type="button" class="btn btn-sm r-ext-all">全选本组</button>
          <button type="button" class="btn btn-sm r-ext-none">清空本组</button>
        </div>
        <div class="r-ext-chips"></div>
      </div>
      <div class="r-ext-custom">
        <input type="text" class="r-ext-custom-input" placeholder="自定义扩展名，逗号分隔，如 cbz,cbr,avif" />
        <button type="button" class="btn btn-sm r-ext-add">＋ 添加类型</button>
      </div>
      <div class="hint r-ext-tip">先点上面的分组标签切换类型；已选类型会集中显示在「已选」一行，点 × 可移除。列表里没有的格式可直接在下方输入添加。</div>`;

    const qs = (s) => extBox.querySelector(s);
    const tabsBox = qs(".r-ext-tabs");
    const panel = qs(".r-ext-panel");
    const panelTitle = qs(".r-ext-panel-title");
    const panelCount = qs(".r-ext-panel-count");
    const panelChips = qs(".r-ext-panel .r-ext-chips");
    const pickedBox = qs(".r-ext-picked");
    const pickedList = qs(".r-ext-picked-list");
    const filterInput = qs(".r-ext-filter");

    let activeKey = "";
    let query = "";

    function visibleGroups() { return groups.filter((g) => g.exts.length); }
    function currentGroup() { return groups.find((g) => g.key === activeKey) || visibleGroups()[0] || null; }

    function extSummary() {
      const checked = [...selected];
      const countEl = qs(".r-ext-count");
      if (countEl) countEl.textContent = checked.length ? `已选 ${checked.length} 项` : "未选（= 不限类型）";
      extToggle.textContent = (extOpen ? "▾" : "▸") + " 限定文件类型："
        + (checked.length
          ? `已选 ${checked.length} 项（${checked.slice(0, 8).join("、")}${checked.length > 8 ? "…" : ""}）——点击${extOpen ? "收起" : "修改"}`
          : "不限（点击修改）");
    }

    extToggle.addEventListener("click", () => {
      extOpen = !extOpen;
      extBox.style.display = extOpen ? "" : "none";
      extSummary();
    });

    function addChip(parent, ext, custom) {
      const lab = document.createElement("label");
      lab.className = "ext-chip" + (custom ? " ext-chip-custom" : "");
      lab.dataset.ext = ext;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "r-ext";
      cb.value = ext;
      cb.checked = selected.has(ext);
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(ext); else selected.delete(ext);
        extSummary();
        syncTabCounts();
        updatePanelCount();
        renderPicked();
      });
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(" " + ext));
      parent.appendChild(lab);
    }

    function renderTabs() {
      tabsBox.innerHTML = "";
      visibleGroups().forEach((g) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "r-ext-tab";
        b.dataset.group = g.key;
        b.innerHTML = "<span>" + esc(g.label) + '</span><b class="r-ext-tab-cnt"></b>';
        b.addEventListener("click", () => {
          filterInput.value = "";
          query = "";
          activeKey = g.key;
          renderTabs();
          renderPanel();
        });
        tabsBox.appendChild(b);
      });
      syncTabCounts();
    }

    function syncTabCounts() {
      tabsBox.querySelectorAll(".r-ext-tab").forEach((b) => {
        const g = groups.find((x) => x.key === b.dataset.group);
        if (!g) return;
        const n = g.exts.filter((e) => selected.has(e)).length;
        b.classList.toggle("active", g.key === activeKey);
        b.classList.toggle("has", n > 0);
        const cnt = b.querySelector(".r-ext-tab-cnt");
        if (cnt) cnt.textContent = n ? String(n) : "";
      });
    }

    function updatePanelCount() {
      const g = currentGroup();
      if (!g) { panelCount.textContent = ""; return; }
      const n = g.exts.filter((e) => selected.has(e)).length;
      panelCount.textContent = `本组已选 ${n}/${g.exts.length}`;
    }

    function renderPanel() {
      const g = currentGroup();
      if (!g) { panel.style.display = "none"; return; }
      panel.style.display = "";
      const q = query.trim().toLowerCase();
      const labelHit = g.label.toLowerCase().includes(q);
      const exts = q && !labelHit ? g.exts.filter((e) => e.includes(q)) : g.exts;
      panelTitle.textContent = q ? `搜索「${query.trim()}」 · ${g.label}` : g.label;
      panelChips.innerHTML = "";
      if (!exts.length) {
        panelChips.innerHTML = '<span class="hint">没有匹配的类型；可在下方直接添加自定义后缀</span>';
      } else {
        exts.forEach((e) => addChip(panelChips, e, g.key === "custom"));
      }
      updatePanelCount();
    }

    function renderPicked() {
      const list = [...selected];
      pickedBox.style.display = list.length ? "" : "none";
      pickedList.innerHTML = "";
      list.slice(0, 18).forEach((e) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "r-ext-picked-chip";
        b.title = "点击移除 " + e;
        b.textContent = e + " ×";
        b.addEventListener("click", () => {
          selected.delete(e);
          refreshAll();
        });
        pickedList.appendChild(b);
      });
      if (list.length > 18) {
        const more = document.createElement("span");
        more.className = "hint";
        more.textContent = `+${list.length - 18} 项`;
        pickedList.appendChild(more);
      }
    }

    function applyFilter() {
      query = filterInput.value;
      if (!query.trim()) {
        tabsBox.style.display = "";
        renderTabs();
        renderPanel();
        return;
      }
      tabsBox.style.display = "none";
      const q = query.trim().toLowerCase();
      const matches = visibleGroups().filter((g) =>
        g.label.toLowerCase().includes(q) || g.exts.some((e) => e.includes(q))
      );
      if (!matches.length) {
        panel.style.display = "";
        panelTitle.textContent = `搜索「${query.trim()}」`;
        panelCount.textContent = "";
        panelChips.innerHTML = '<span class="hint">没有匹配的类型；可在下方直接添加自定义后缀</span>';
        return;
      }
      if (!matches.some((g) => g.key === activeKey)) activeKey = matches[0].key;
      renderPanel();
      syncTabCounts();
    }

    function refreshAll() {
      extSummary();
      applyFilter();
      renderPicked();
    }

    function setGroupSelection(target, on) {
      target.exts.forEach((e) => { if (on) selected.add(e); else selected.delete(e); });
      refreshAll();
    }

    // 默认展示"第一个已有选中项"的分组；没有选中就展示漫画组
    const firstSelected = visibleGroups().find((g) => g.exts.some((e) => selected.has(e)));
    activeKey = firstSelected ? firstSelected.key : ((visibleGroups()[0] || {}).key || "");

    qs(".r-ext-filter").addEventListener("input", applyFilter);
    qs(".r-ext-clear").addEventListener("click", () => {
      selected.clear();
      refreshAll();
    });
    qs(".r-ext-all").addEventListener("click", () => {
      const g = currentGroup();
      if (g) setGroupSelection(g, true);
    });
    qs(".r-ext-none").addEventListener("click", () => {
      const g = currentGroup();
      if (g) setGroupSelection(g, false);
    });
    qs(".r-ext-add").addEventListener("click", () => {
      const input = qs(".r-ext-custom-input");
      const list = cleanExts(input.value);
      if (!list.length) { toast("请输入扩展名，如 cbz（可用逗号一次加多个）"); return; }
      list.forEach((e) => {
        selected.add(e);
        if (!EXT_CAT.get(e)) {
          let cg = groups.find((g) => g.key === "custom");
          if (!cg) {
            cg = { key: "custom", label: "自定义", exts: [] };
            groups.push(cg);
          }
          if (!cg.exts.includes(e)) cg.exts.push(e);
        }
      });
      input.value = "";
      filterInput.value = "";
      query = "";
      const firstCustom = list.find((e) => !EXT_CAT.get(e));
      if (firstCustom) activeKey = "custom";
      else activeKey = (EXT_CAT.get(list[0]) || {}).key || activeKey;
      refreshAll();
      toast("已添加类型：" + list.join("、"));
    });
    qs(".r-ext-custom-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); qs(".r-ext-add").click(); }
    });

    // 保存时从状态读取，不再依赖当前可见面板里的 checkbox
    row._getExts = () => [...selected];
    refreshAll();

    // 目标文件夹：下拉选现有（自动拉取），或切「手动输入」填名称（不存在会自动创建）
    const folderSel = row.querySelector(".r-folder");
    const folderNew = row.querySelector(".r-folder-new");
    function folderShow() {
      folderNew.style.display = folderSel.value === "__manual__" ? "" : "none";
    }
    folderSel.addEventListener("change", folderShow);
    row._fillFolders = (folderNames) => {
      const cur = String(r.folder || "").trim();
      folderSel.innerHTML = '<option value="__manual__">✍ 手动输入文件夹名…</option>'
        + folderNames.map((n) => '<option value="' + esc(n) + '">📂 ' + esc(n) + "</option>").join("");
      if (cur && folderNames.includes(cur)) {
        folderSel.value = cur;
        folderNew.value = "";
      } else if (cur) {
        folderSel.value = "__manual__";
        folderNew.value = cur;  // 保留已存的名称，绝不在保存时丢失
      } else {
        folderSel.value = "__manual__";
        folderNew.value = "";
      }
      folderShow();
    };
    row._fillFolders([]);
    row.querySelector(".r-save").addEventListener("click", async () => {
      const btn = row.querySelector(".r-save");
      btn.disabled = true;
      btn.textContent = "保存中…";
      try {
        const allRows = [...rulesBox.querySelectorAll(".rules-row")];
        const cleaned = allRows.map((rr) => cleanRuleObj(readRuleFromRow(rr)));
        const r = await bridge.apiPost("rules", { group_id: g.group_id, rules: cleaned });
        btn.textContent = "✓ 已保存";
        if (rulesDirtyReset) rulesDirtyReset();
        toast("已单独保存本条规则（其他规则/设置未动）");
        if (r && r.warnings && r.warnings.length)
          toast("⚠ " + r.warnings.join("；"), "error", 6000);
        setTimeout(() => { btn.textContent = "💾 保存本条"; btn.disabled = false; }, 1400);
      } catch (e) {
        btn.textContent = "💾 保存本条";
        btn.disabled = false;
        toast("保存失败：" + e.message, "error");
      }
    });
    row.querySelector(".r-del").addEventListener("click", () => {
      row.remove(); // 先移出 DOM，renderRules 同步其余行时下标才与 rules 对齐
      rules.splice(i, 1);
      renderRules();
    });

    // 规则折叠：默认只留头部 + 一行摘要；点「展开」才显示匹配内容/文件类型/排除词
    const bodyEl = row.querySelector(".r-body");
    const sumEl = row.querySelector(".r-sum");
    const foldBtn = row.querySelector(".r-fold");
    function buildSum() {
      const k = kindSel.value;
      let p = "";
      if (k === "kw") p = row.querySelector(".r-kw").value.trim();
      else if (k === "regex") p = row.querySelector(".r-regex").value.trim();
      else p = compileDsl(row._dsl || []);
      let txt;
      if (!p) txt = "兜底（不限关键词）";
      else if (k === "regex") txt = "正则 " + p;
      else if (k === "dsl") txt = "构建器「" + p + "」";
      else txt = "关键词「" + p + "」";
      const exts = row._getExts ? row._getExts() : [];
      txt += " · 类型：" + (exts.length ? exts.join("、") : "不限");
      const ex = row.querySelector(".r-exclude").value.trim();
      if (ex) txt += " · 排除：" + ex;
      sumEl.textContent = txt;
    }
    function setFolded(v) {
      setRuleFolded(r, v);
      row.classList.toggle("folded", !!v);
      bodyEl.style.display = v ? "none" : "";
      sumEl.style.display = v ? "" : "none";
      foldBtn.textContent = v ? "▸ 展开" : "▾ 收起";
      if (v) buildSum();
    }
    foldBtn.addEventListener("click", () => setFolded(!isRuleFolded(r)));
    row._setFolded = setFolded;
    setFolded(folded);
    return row;
  }

  function cleanRuleObj(r) {
    const out = {
      source: String(r.source || "").trim(),
      folder: String(r.folder || "").trim(),
      kind: r.kind || "kw",
      pattern: r.pattern || "",
      exts: cleanExts(r.exts),
      enabled: r.enabled !== false,
      exclude: Array.isArray(r.exclude) ? r.exclude.filter(Boolean) : [],
      priority: Number(r.priority || 0),
    };
    if (out.kind === "dsl" && Array.isArray(r.dsl) && r.dsl.length) {
      out.dsl = r.dsl.map((s) => {
        const o = { type: s.type, sep: s.sep === undefined ? "" : s.sep };
        if (s.type === "text") o.text = s.text || "";
        if (o.sep === "__custom__") o.sepCustom = s.sepCustom || "";
        return o;
      });
      out.pattern = compileDsl(out.dsl);
    }
    return out;
  }

  // 重新渲染前把各行的当前输入写回 rules，避免"加/删一条规则"导致其他行未保存的修改丢失
  function syncRowsToRules() {
    const rows = [...rulesBox.querySelectorAll(".rules-row")];
    rows.forEach((row, idx) => {
      const target = rules[idx];
      if (!target) return;
      const fresh = cleanRuleObj(readRuleFromRow(row));
      Object.keys(target).forEach((k) => { if (!(k in fresh)) delete target[k]; });
      Object.assign(target, fresh);
    });
  }

  function renderRules() {
    syncRowsToRules();
    rulesBox.innerHTML = "";
    rules.forEach((r, i) => rulesBox.appendChild(renderRuleRow(r, i)));
    applyFolderNames();
    if (foldAllBtn) {
      const anyOpen = rules.some((x) => !isRuleFolded(x));
      foldAllBtn.textContent = anyOpen ? "▸ 全部收起" : "▾ 全部展开";
    }
    updateDirtyBadge();
  }
  renderRules();
  rulesBaseline = JSON.stringify(collectRulesDom());
  updateDirtyBadge();

  // ---- ✨ 从文件名自动生成规则 ----
  const genWrap = sec1.querySelector(".g-gen-wrap");
  const genPanel = sec1.querySelector(".g-gen-panel");
  const genInput = sec1.querySelector(".g-gen-input");
  const genDslBox = sec1.querySelector(".g-gen-dsl");
  const genPreview = sec1.querySelector(".g-gen-preview");
  const genTest = sec1.querySelector(".g-gen-test");
  const genExt = sec1.querySelector(".g-gen-ext");
  const genFolder = sec1.querySelector(".g-gen-folder");
  const genFolderNew = sec1.querySelector(".g-gen-folder-new");
  let genDsl = [], genSamples = [], genExts = [];
  genWrap.querySelector(".g-gen-toggle").addEventListener("click", () => {
    genPanel.style.display = genPanel.style.display === "none" ? "block" : "none";
  });
  genFolder.addEventListener("change", () => {
    genFolderNew.style.display = genFolder.value === "__new__" ? "" : "none";
  });
  function genRefresh() {
    const pat = compileDsl(genDsl);
    genPreview.textContent = pat ? ("生成的匹配表达式：" + pat) : "";
    genTest.innerHTML = "";
    if (!pat) return;
    let rx = null;
    try { rx = new RegExp(pat); } catch (e) { genTest.textContent = "表达式有误：" + e.message; return; }
    genSamples.forEach((s) => {
      const ok = rx.test(s.name);
      const d = document.createElement("div");
      d.style.color = ok ? "var(--success)" : "var(--danger)";
      d.textContent = (ok ? "✓ 命中  " : "✗ 不命中  ") + s.name;
      genTest.appendChild(d);
    });
  }
  // 分析按钮（动态插在示例输入框下方）
  const analyzeBtn = document.createElement("button");
  analyzeBtn.className = "btn btn-primary";
  analyzeBtn.type = "button";
  analyzeBtn.textContent = "智能分析分段";
  analyzeBtn.style.marginTop = "6px";
  analyzeBtn.addEventListener("click", () => {
    const lines = genInput.value.split("\n").map((x) => x.trim()).filter(Boolean);
    if (!lines.length) { toast("先粘贴示例文件名（一行一个）"); return; }
    genSamples = []; genExts = [];
    const per = lines.map((ln) => {
      const dot = ln.lastIndexOf(".");
      const body = dot > 0 ? ln.slice(0, dot) : ln;
      const ext = dot > 0 ? ln.slice(dot + 1).toLowerCase() : "";
      if (ext) genExts.push(ext);
      genSamples.push({ name: ln });
      return genTokenize(body);
    });
    genExts = [...new Set(genExts)];
    const segCounts = per.map((x) => x.length);
    genDsl = genAlign(per);
    genExt.textContent = genExts.length
      ? "识别到文件类型：" + genExts.join("、") + "（加入规则后可在该行勾选调整）" : "";
    if (new Set(segCounts).size > 1)
      genExt.textContent += "　⚠ 几个文件名分段数不一致，已按最多段对齐，请逐段核对";
    genDslBox.innerHTML = "";
    renderDslEditor(genDslBox, genDsl, genRefresh);
    genRefresh();
  });
  genInput.insertAdjacentElement("afterend", analyzeBtn);

  genWrap.querySelector(".g-gen-add").addEventListener("click", () => {
    if (!genDsl.length) { toast("请先点「智能分析分段」"); return; }
    const fv = genFolder.value === "__new__" ? genFolderNew.value.trim() : genFolder.value;
    if (!fv) { toast("请选择已有文件夹，或选“新建”并输入名字"); return; }
    const rule = {
      source: genWrap.querySelector(".g-gen-source").value.trim(),
      folder: fv,
      kind: "dsl",
      dsl: genDsl.map((s) => Object.assign({}, s)),
      pattern: compileDsl(genDsl),
      exts: genExts.slice(),
    };
    rules.push(rule);
    setRuleFolded(rule, false); // 新加入的规则展开，方便马上核对
    renderRules();
    bridge.apiGet("files", { gid: g.group_id }).then((d) => {
      ruleFolderNames = (d.folders || []).map((x) => x.name);
      applyFolderNames();
    }).catch(() => {});
    toast("已加入规则列表，记得点顶部「保存全部规则」生效");
    genPanel.style.display = "none";
  });
  bridge.apiGet("files", { gid: g.group_id }).then((d) => {
    const names = (d.folders || []).map((x) => x.name);
    genFolder.innerHTML = '<option value="">选择目标文件夹…</option>'
      + names.map((n) => '<option value="' + esc(n) + '">📂 ' + esc(n) + "</option>").join("")
      + '<option value="__new__">＋ 新建文件夹…</option>';
  }).catch(() => {});

  // 异步拉取该群实际文件夹，填充每行"目标文件夹"下拉
  (async () => {
    try {
      const data = await bridge.apiGet("files", { gid: g.group_id });
      ruleFolderNames = (data.folders || []).map((f) => f.name);
      applyFolderNames();
    } catch (e) { /* 拉不到就用手动输入 */ }
  })();

  // 添加空规则
  sec1.querySelector(".g-rule-add").addEventListener("click", () => {
    const nr = { source: "", pattern: "", folder: "" };
    rules.push(nr);
    setRuleFolded(nr, false);
    renderRules();
  });

  // 快捷模板：一键生成"匹配留空 + 限定类型 + 目标文件夹"的规则
  const templateSel = sec1.querySelector(".g-rule-template");
  templateSel.addEventListener("change", () => {
    const key = templateSel.value;
    templateSel.value = "";
    const tpl = RULE_TEMPLATES[key];
    if (!tpl) return;
    const exists = rules.some((r) =>
      String(r.folder || "") === tpl.folder && sameExtSet(cleanExts(r.exts), tpl.exts)
    );
    if (exists) {
      toast(`已有「${tpl.folder}」同类型规则，未重复添加`);
      return;
    }
    const nr = {
      source: tpl.label,
      pattern: "",
      folder: tpl.folder,
      kind: "kw",
      exts: tpl.exts.slice(),
      enabled: true,
    };
    rules.push(nr);
    setRuleFolded(nr, false);
    renderRules();
    const last = rulesBox.lastElementChild;
    if (last && last.scrollIntoView) last.scrollIntoView({ block: "nearest", behavior: "smooth" });
    toast(`已添加「${tpl.label}」模板规则（${tpl.exts.join("/")}），点顶部「保存全部规则」生效`);
  });

  // 全部展开 / 全部收起（直接切 DOM，不重渲染，避免丢未保存输入）
  foldAllBtn.addEventListener("click", () => {
    const anyOpen = rules.some((x) => !isRuleFolded(x));
    const target = anyOpen; // 有展开的 -> 全部收起；全收起 -> 全部展开
    rules.forEach((x) => setRuleFolded(x, target));
    rulesBox.querySelectorAll(".rules-row").forEach((row) => {
      if (row._setFolded) row._setFolded(target);
      else row.classList.toggle("folded", target);
    });
    const anyAfter = rules.some((x) => !isRuleFolded(x));
    foldAllBtn.textContent = anyAfter ? "▸ 全部收起" : "▾ 全部展开";
  });

  // 复制当前群规则到其他群（v2.6.0：多群运营一键同步）
  const copyBtn = sec1.querySelector(".g-rule-copy");
  const copyPanel = sec1.querySelector(".g-copy-panel");
  function renderCopyPanel() {
    const others = groupsData.filter((x) => String(x.group_id) !== String(g.group_id));
    if (!others.length) {
      copyPanel.innerHTML = '<div class="panel-title">复制规则到其他群</div><div class="hint">还没有其他群，先去「群配置」添加。</div>';
      return;
    }
    copyPanel.innerHTML = `
      <div class="panel-title">复制当前群规则到其他群</div>
      <div class="hint" style="margin-bottom:6px;">当前群共 <b>${rulesBox.querySelectorAll(".rules-row").length}</b> 条规则；选择目标群后点「开始复制」。规则里的目标文件夹不存在时，保存会自动创建。</div>
      <div class="copy-groups">${others.map((x) => `<label class="copy-group"><input type="checkbox" class="copy-target" value="${esc(String(x.group_id))}" /> ${esc(x.name || x.group_id)}（${esc(String(x.group_id))}）</label>`).join("")}</div>
      <div class="copy-mode">
        <label class="inline-check"><input type="radio" name="gfc-copy-mode" value="merge" checked /> 追加合并（跳过重复）</label>
        <label class="inline-check"><input type="radio" name="gfc-copy-mode" value="overwrite" /> 覆盖目标群规则</label>
      </div>
      <div class="toolbar" style="margin-bottom:0;">
        <button type="button" class="btn btn-sm copy-all">全选目标群</button>
        <button type="button" class="btn btn-sm copy-none">清空选择</button>
        <button type="button" class="btn btn-primary copy-go">开始复制</button>
        <button type="button" class="btn btn-sm copy-close">收起</button>
        <span class="hint copy-result"></span>
      </div>`;
    copyPanel.querySelector(".copy-all").addEventListener("click", () => {
      copyPanel.querySelectorAll(".copy-target").forEach((c) => { c.checked = true; });
    });
    copyPanel.querySelector(".copy-none").addEventListener("click", () => {
      copyPanel.querySelectorAll(".copy-target").forEach((c) => { c.checked = false; });
    });
    copyPanel.querySelector(".copy-close").addEventListener("click", () => {
      copyPanel.style.display = "none";
    });
    copyPanel.querySelector(".copy-go").addEventListener("click", async () => {
      const targets = [...copyPanel.querySelectorAll(".copy-target:checked")].map((c) => c.value);
      if (!targets.length) { toast("请先勾选目标群"); return; }
      const mode = (copyPanel.querySelector('input[name="gfc-copy-mode"]:checked') || {}).value || "merge";
      const sourceRules = collectRulesDom();
      if (mode === "overwrite" && !confirm(`将用当前群的 ${sourceRules.length} 条规则覆盖 ${targets.length} 个目标群，确定继续吗？`)) return;
      const goBtn = copyPanel.querySelector(".copy-go");
      const result = copyPanel.querySelector(".copy-result");
      goBtn.disabled = true;
      result.textContent = "复制中…";
      let ok = 0, added = 0, fail = [];
      for (let i = 0; i < targets.length; i++) {
        const gid = targets[i];
        result.textContent = `复制中… ${i + 1}/${targets.length}`;
        try {
          let outRules;
          if (mode === "merge") {
            const existing = pickRules(await bridge.apiGet("rules", { group_id: gid }));
            const seen = new Set(existing.map(ruleKey));
            outRules = existing.slice();
            sourceRules.forEach((r) => {
              const k = ruleKey(r);
              if (!seen.has(k)) {
                seen.add(k);
                outRules.push(JSON.parse(JSON.stringify(r)));
                added++;
              }
            });
            if (outRules.length === existing.length) { ok++; continue; }
          } else {
            outRules = sourceRules.map((r) => JSON.parse(JSON.stringify(r)));
          }
          const resp = await bridge.apiPost("rules", { group_id: gid, rules: outRules });
          if (resp && resp.warnings && resp.warnings.length) fail.push(`${gid}: ${resp.warnings.join("；")}`);
          ok++;
        } catch (e) {
          fail.push(`${gid}: ${e.message}`);
        }
      }
      result.textContent = `完成：成功 ${ok} 个群`
        + (added ? `，累计新增 ${added} 条` : "")
        + (mode === "overwrite" ? "（覆盖模式）" : "")
        + (fail.length ? `；失败 ${fail.length} 个：${fail.slice(0, 2).join("；")}` : "");
      goBtn.disabled = false;
      toast(ok ? `规则已复制到 ${ok} 个群` : "复制失败，请查看结果", ok ? undefined : "error", 6000);
    });
  }
  copyBtn.addEventListener("click", () => {
    const show = copyPanel.style.display === "none";
    if (show) renderCopyPanel();
    copyPanel.style.display = show ? "" : "none";
  });

  // ---- 扫描群文件 → 自动生成规则 ----
  let scanData = null;
  sec2.querySelector(".g-scan").addEventListener("click", async () => {
    if (scanPanel.style.display !== "none") { scanPanel.style.display = "none"; return; }
    scanPanel.style.display = "block";
    scanPanel.innerHTML = '<div class="muted">扫描中…</div>';
    try {
      scanData = await bridge.apiGet("scan", { gid: g.group_id });
    } catch (e) {
      scanPanel.innerHTML = '<div class="muted" style="color:var(--danger);">扫描失败：' + esc(e.message) + '</div>';
      return;
    }
    const folders = scanData.folders || [];
    const groups = scanData.groups || [];
    if (!groups.length) {
      scanPanel.innerHTML = '<div class="muted">群里暂时没有文件可扫描。</div>';
      return;
    }
    function suggestRule(name) {
      const base = name.replace(/\.[^.]+$/, "");
      if (/^\d+_\d+/.test(base)) return { kind: "regex", pattern: "^\\d+_\\d+" };
      if (/^\[[^\]]+\]/.test(base)) return { kind: "regex", pattern: "^\\[[^\\]]+\\]" };
      const d = base.match(/([\u4e00-\u9fa5]{1,10})_([\u4e00-\u9fa5]{1,10})$/);
      if (d) return { kind: "dsl", dsl: [{ type: "hanzi", sep: "_" }, { type: "hanzi", sep: "" }], pattern: "" };
      const segs = base.split("_").map((s) => s.trim()).filter(Boolean);
      const last = segs.filter((s) => !/^\d+$/.test(s)).pop();
      if (last && last.length <= 12) return { kind: "kw", pattern: last };
      return { kind: "kw", pattern: base.slice(0, 8) };
    }
    scanPanel.innerHTML = "";
    const fileRows = [];
    const folderDetails = [];
    const scanTools = document.createElement("div");
    scanTools.className = "toolbar scan-tools";
    scanTools.innerHTML = '<button class="btn btn-sm scan-expand" type="button">全部展开</button>'
      + '<button class="btn btn-sm scan-collapse" type="button">全部收起</button>'
      + '<span class="hint">按文件夹折叠；默认只展开根目录，可手动展开子文件夹</span>';
    scanPanel.appendChild(scanTools);

    groups.forEach((g2) => {
      const depth = Number(g2.depth || 0) || 0;
      const files = g2.files || [];
      if (!files.length) return;
      const det = document.createElement("details");
      det.className = "scan-folder";
      det.style.marginLeft = (depth * 16) + "px";
      det.open = depth === 0 && files.length <= 12; // 默认只展开不大的根目录；子文件夹/大目录先收起
      const summary = document.createElement("summary");
      summary.className = "scan-folder-head";
      summary.innerHTML = '<span class="scan-folder-name">' + (depth === 0 ? "📂" : "└") + " "
        + esc(g2.source) + '</span><span class="hint">（' + files.length + " 个文件）</span>";
      const folderBody = document.createElement("div");
      folderBody.className = "scan-folder-body";
      det.appendChild(summary);
      det.appendChild(folderBody);
      scanPanel.appendChild(det);
      folderDetails.push(det);
      files.forEach((f) => {
        const card2 = document.createElement("div");
        card2.className = "scan-file-card";
        card2.style.marginBottom = "8px";
        const head = document.createElement("div");
        head.className = "sf-head";
        const nameL = document.createElement("span");
        nameL.style.cssText = "flex:1 1 200px;font-size:12px;min-width:0;";
        nameL.innerHTML = "<b title='" + esc(f.name) + "'>" + esc(f.name) + "</b> <span class='hint'>" + fmtSize(f.size) + "</span>";
        const fSel = document.createElement("select");
        fSel.innerHTML = '<option value="">不移动</option>' +
          folders.filter((d) => d !== g2.source).map((d) => '<option value="' + esc(d) + '">→ ' + esc(d) + "</option>").join("") +
          '<option value="__new__">＋ 新建…</option>';
        fSel.style.cssText = "flex:0 0 110px;";
        const fNew = document.createElement("input");
        fNew.type = "text";
        fNew.placeholder = "新文件夹名";
        fNew.style.cssText = "flex:1 1 90px;display:none;";
        fSel.addEventListener("change", () => { fNew.style.display = fSel.value === "__new__" ? "" : "none"; });
        const body2 = document.createElement("div");
        body2.className = "sf-body";
        const kindSel = document.createElement("select");
        kindSel.style.cssText = "flex:0 0 92px;";
        kindSel.innerHTML = '<option value="kw">🔑 关键词</option><option value="dsl">🧩 构建器</option><option value="regex">⚙ 正则</option>';
        const patInp = document.createElement("input");
        patInp.type = "text";
        patInp.placeholder = "匹配内容（可改）";
        patInp.style.cssText = "flex:1 1 160px;min-width:100px;";
        const dslWrap = document.createElement("div");
        dslWrap.style.cssText = "flex:1 1 100%;display:none;";
        let rowDsl = null;
        function kindShow() {
          const k = kindSel.value;
          patInp.style.display = k === "dsl" ? "none" : "";
          dslWrap.style.display = k === "dsl" ? "" : "none";
        }
        kindSel.addEventListener("change", () => {
          if (kindSel.value === "dsl" && !rowDsl) {
            rowDsl = [{ type: "any", sep: "_" }, { type: "any", sep: "" }];
            renderDslEditor(dslWrap, rowDsl);
          }
          kindShow();
        });
        const sug = suggestRule(f.name);
        kindSel.value = sug.kind || "kw";
        if (sug.kind === "dsl") {
          rowDsl = sug.dsl;
          renderDslEditor(dslWrap, rowDsl);
          patInp.style.display = "none";
        } else {
          patInp.value = sug.pattern || "";
        }
        kindShow();
        const addBtn = document.createElement("button");
        addBtn.textContent = "＋ 加入规则";
        addBtn.className = "btn";
        addBtn.style.cssText = "flex:0 0 auto;";
        function addFileRule() {
          const target = fSel.value === "__new__" ? fNew.value.trim() : fSel.value;
          const kind = kindSel.value;
          let pattern = "";
          if (kind === "dsl") {
            const d = rowDsl || [];
            if (!d.length) { toast("请先设置构建器"); return; }
            pattern = compileDsl(d);
          } else {
            pattern = patInp.value.trim();
          }
          if (!pattern) { toast("匹配内容不能为空"); return; }
          if (!target) { toast("请选择目标文件夹"); return; }
          const m = String(f.name).match(/\.([^.]+)$/);
          const ext = m ? m[1].toLowerCase() : "";
          const newRule = {
            source: String(f.name).replace(/\.[^.]+$/, "").slice(0, 12),
            kind,
            pattern,
            folder: target,
            exts: ext ? [ext] : [],
            dsl: kind === "dsl" ? rowDsl : undefined,
          };
          rules.push(newRule);
          setRuleFolded(newRule, true);
          renderRules();
          addBtn.textContent = "✓ 已加入";
          addBtn.disabled = true;
          toast("已加入规则：" + pattern + " → " + target);
        }
        addBtn.addEventListener("click", addFileRule);
        head.appendChild(nameL);
        head.appendChild(fSel);
        head.appendChild(fNew);
        head.appendChild(addBtn);
        body2.appendChild(kindSel);
        body2.appendChild(patInp);
        body2.appendChild(dslWrap);
        card2.appendChild(head);
        card2.appendChild(body2);
        folderBody.appendChild(card2);
        fileRows.push({ f, kindSel, patInp, dslWrap, fSel, fNew, getDsl: () => rowDsl, addBtn });
      });
    });
    scanTools.querySelector(".scan-expand").addEventListener("click", () => {
      folderDetails.forEach((d) => { d.open = true; });
    });
    scanTools.querySelector(".scan-collapse").addEventListener("click", () => {
      folderDetails.forEach((d) => { d.open = false; });
    });
    const genBtn = document.createElement("button");
    genBtn.className = "btn btn-primary";
    genBtn.textContent = "✓ 全部生成规则（每个文件一行；同类型的新文件以后也会命中同一条）";
    genBtn.style.cssText = "margin-top:8px;";
    genBtn.addEventListener("click", () => {
      // v2.6.0：文件多时二次确认，避免一次生成几十条规则
      const pending = fileRows.filter((x) => !x.addBtn.disabled).length;
      if (pending >= 5 && !confirm(`将为 ${pending} 个文件各生成一条规则（未选目标文件夹/匹配内容的会跳过）。\n生成后还需点「保存全部规则」才会生效。确定继续吗？`)) return;
      let added = 0;
      fileRows.forEach(({ f, kindSel, patInp, fSel, fNew, getDsl, addBtn }) => {
        if (addBtn.disabled) return;
        let target = fSel.value;
        if (target === "__new__") target = fNew.value.trim();
        let kind = kindSel.value;
        let pattern = "";
        const dslArr = kind === "dsl" ? (getDsl && getDsl()) : null;
        if (kind === "dsl") {
          if (!dslArr || !dslArr.length) return;
          pattern = compileDsl(dslArr);
        } else {
          pattern = patInp.value.trim();
        }
        if (!target || !pattern) return;
        const m = String(f.name).match(/\.([^.]+)$/);
        const ext = m ? m[1].toLowerCase() : "";
        const newRule = {
          source: String(f.name).replace(/\.[^.]+$/, "").slice(0, 12),
          kind,
          pattern,
          folder: target,
          exts: ext ? [ext] : [],
          dsl: kind === "dsl" ? dslArr : undefined,
        };
        rules.push(newRule);
        setRuleFolded(newRule, true);
        addBtn.textContent = "✓ 已加入";
        addBtn.disabled = true;
        added++;
      });
      renderRules();
      toast(added ? "已生成 " + added + " 条规则，可再调整，点顶部「保存全部规则」" : "没生成：请先给文件选目标文件夹/填匹配内容");
    });
    scanPanel.appendChild(genBtn);
  });

  // 立即整理一次
  sec2.querySelector(".g-run-cleanup").addEventListener("click", async () => {
    const btn = sec2.querySelector(".g-run-cleanup");
    btn.disabled = true;
    try {
      const r = await bridge.apiPost("task", { kind: "cleanup", group_id: g.group_id, actor: "panel" });
      toast("已提交整理任务 " + (r.task_id || "") + "，进度见「任务」页");
    } catch (e) {
      toast("整理失败：" + e.message, "error");
    }
    btn.disabled = false;
  });

  // ---- 🧪 试一试：文件名测规则 / 👁 预览整理 ----
  const verifyPanel = sec2.querySelector(".g-verify-panel");
  function renderTestResults(results) {
    const lines = [];
    results.forEach((r) => {
      if (r.hit) {
        lines.push(`<div class="vt-row">✅ <code>${esc(r.name)}</code> → 「${esc(r.hit.folder)}」（${esc(r.hit.source || "兜底")}）</div>`);
      } else {
        const why = (r.skipped || []).slice(0, 2).map((s) => esc(s.reason)).join("；");
        lines.push(`<div class="vt-row">➖ <code>${esc(r.name)}</code> 不动${why ? `（${why}）` : "（无规则命中）"}</div>`);
      }
    });
    verifyPanel.innerHTML = `
      <div class="muted" style="margin-bottom:4px;">试一试结果（改完规则记得先「保存全部规则」再测）：</div>
      ${lines.join("") || '<div class="hint">没有输入文件名</div>'}`;
  }
  function renderPreview(r) {
    const planned = r.planned || [];
    const rows = planned.slice(0, 30).map((m) =>
      `<div class="vt-row">📦 <code>${esc(m.name)}</code>：${esc(m.from)} → <b>${esc(m.to)}</b>（${esc(m.rule)}）</div>`).join("");
    verifyPanel.innerHTML = `
      <div class="muted" style="margin-bottom:4px;">预览整理（只看不动）：</div>
      <div class="hint" style="margin-bottom:4px;">共 ${r.total || 0} 个文件：将移动 <b>${planned.length}</b> 个，已在目标文件夹 ${r.in_place} 个，无规则命中 ${r.no_match} 个${(r.missing_folders || []).length ? `；缺文件夹：${esc(r.missing_folders.join("、"))}（整理时会自动创建）` : ""}</div>
      ${rows || '<div class="hint">没有需要移动的文件</div>'}
      ${planned.length > 30 ? `<div class="hint">… 其余 ${planned.length - 30} 个略</div>` : ""}`;
  }
  sec2.querySelector(".g-rule-test").addEventListener("click", () => {
    verifyPanel.style.display = "";
    verifyPanel.innerHTML = `
      <div class="muted" style="margin-bottom:4px;">输入几个文件名（一行一个），看每条会被现在的规则分到哪：</div>
      <textarea class="g-vt-input" rows="4" placeholder="第1章_引言.pdf&#10;温泉景图_01.jpg&#10;readme.txt"></textarea>
      <div class="toolbar" style="margin-top:6px;">
        <button class="g-vt-run btn btn-primary" type="button">测试</button>
        <span class="hint-text">改完规则先「保存全部规则」再测</span>
      </div>`;
    verifyPanel.querySelector(".g-vt-run").addEventListener("click", async () => {
      const names = verifyPanel.querySelector(".g-vt-input").value.split("\n").map((s) => s.trim()).filter(Boolean);
      if (!names.length) { toast("先输入几个文件名", "error"); return; }
      try {
        const r = await bridge.apiPost("rule_test", { group_id: g.group_id, filenames: names });
        renderTestResults(r.results || []);
      } catch (e) {
        toast("测试失败：" + e.message, "error");
      }
    });
  });
  sec2.querySelector(".g-preview").addEventListener("click", async () => {
    verifyPanel.style.display = "";
    verifyPanel.innerHTML = '<div class="hint">正在扫描群文件…</div>';
    try {
      const r = await bridge.apiPost("cleanup_preview", { group_id: g.group_id });
      renderPreview(r);
    } catch (e) {
      verifyPanel.innerHTML = `<div class="hint" style="color:var(--danger);">预览失败：${esc(e.message)}</div>`;
    }
  });

  // 顶部「保存全部规则」
  rulesCollect = async function () {
    const newRules = [];
    rulesBox.querySelectorAll(".rules-row").forEach((row) => {
      const r = readRuleFromRow(row);
      if (r) newRules.push(r);
    });
    const cleaned = newRules.map(cleanRuleObj);
    // 保存前本地校验：目标文件夹为空的规则不会生效，直接提醒
    const emptyFolder = cleaned.filter((r) => !r.folder);
    if (emptyFolder.length) {
      const names = emptyFolder.map((r) => r.source || "（无备注规则）").slice(0, 3).join("、");
      if (!confirm(`有 ${emptyFolder.length} 条规则没填目标文件夹（${names}…），这些规则不会移动任何文件。\n仍要保存吗？`)) return;
    }
    try {
      const r = await bridge.apiPost("rules", { group_id: g.group_id, rules: cleaned });
      if (rulesDirtyReset) rulesDirtyReset();
      toast("已保存全部规则（" + cleaned.length + " 条）");
      if (r && r.warnings && r.warnings.length)
        toast("⚠ " + r.warnings.join("；"), "error", 6000);
    } catch (e) {
      toast("保存失败：" + e.message, "error");
    }
  };
}

// ---------- 定时与维护模块 ----------
function maintBadge(g) {
  const name = (g.name ? g.name + " " : "") + "(" + g.group_id + ")";
  return name;
}

function renderMaintChips(groups, activeGid) {
  const box = $("m-chips");
  box.innerHTML = "";
  groups.forEach((g) => {
    const chip = document.createElement("div");
    chip.className = "m-chip" + (String(g.group_id) === String(activeGid) ? " active" : "") + (g.enabled === false ? " off" : "");
    chip.title = maintBadge(g) + (g.enabled === false ? "（该群已停用）" : "");
    chip.innerHTML = '<span class="dot"></span><span class="chip-name">' + esc(g.name || g.group_id) + "</span><span>(" + esc(String(g.group_id)) + ")</span>";
    chip.addEventListener("click", () => renderMaintEditor(String(g.group_id)));
    box.appendChild(chip);
  });
}

async function loadMaintTab() {
  try {
    const data = await bridge.apiGet("groups");
    groupsData = data.groups || [];
    if (!groupsData.length) {
      $("maint-body").innerHTML = '<div class="section empty-state">还没有配置任何群，先去「群配置」添加群。</div>';
      maintCollect = null;
      return;
    }
    // 默认选第一个启用中的群
    const target = String((groupsData.find((g) => g.enabled !== false) || groupsData[0]).group_id);
    renderMaintEditor(target);
  } catch (e) {
    toast("加载群列表失败：" + e.message, "error");
  }
}
$("m-save").addEventListener("click", () => { if (maintCollect) maintCollect(); });

// v2.1.2：维护页导出 / 导入（只动 定时整理 / 容量 / 时效清理 / 查重 四块，不碰规则和管理员）
$("m-export").addEventListener("click", async () => {
  const gid = maintGroupId || (groupsData[0] && groupsData[0].group_id);
  if (!gid) { toast("请先选择群", "error"); return; }
  try {
    const r = await bridge.apiGet("export", { gid });
    const data = (r && r.data) || r || {};
    const payload = {
      app: "groupfile-console-maint",
      group_id: data.group_id || gid,
      name: data.name || "",
      cleanup: data.cleanup || {},
      limits: data.limits || {},
      ttl: data.ttl || {},
      dupe_rule: data.dupe_rule || {},
      export_version: "2.8.0",
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const g = groupsData.find((x) => String(x.group_id) === String(gid));
    a.download = "维护配置_" + ((g && g.name) || gid) + "_" + gid + ".json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast("已导出维护配置（含 定时 / 容量 / 时效 / 查重）");
  } catch (e) {
    toast("导出失败：" + e.message, "error");
  }
});

$("m-import").addEventListener("click", () => {
  const gid = maintGroupId || (groupsData[0] && groupsData[0].group_id);
  if (!gid) { toast("请先选择群", "error"); return; }
  const panel = $("m-import-panel");
  panel.style.display = panel.style.display === "none" ? "" : "none";
});
$("m-import-pick").addEventListener("click", () => { $("m-import-file").click(); });
$("m-import-file").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";  // 允许连续导入同一文件
  if (!file) return;
  $("m-import-text").value = await file.text();
  $("m-import-name").textContent = "已读入文件：" + file.name;
  $("m-import-msg").textContent = "已读入，请检查内容后点「确认覆盖导入」";
});
$("m-import-cancel").addEventListener("click", () => { $("m-import-panel").style.display = "none"; });
$("m-import-go").addEventListener("click", async (ev) => {
  const gid = maintGroupId || (groupsData[0] && groupsData[0].group_id);
  if (!gid) { toast("请先选择群", "error"); return; }
  const btn = ev.currentTarget;
  if (!armConfirm(btn, "⚠ 再点一次：覆盖导入")) return;
  const msg = $("m-import-msg");
  const raw = String($("m-import-text").value || "").replace(/^\uFEFF/, "").trim();
  if (!raw) { msg.textContent = "导入失败：内容为空，请粘贴或选择导出的 JSON"; return; }
  msg.textContent = "解析中…";
  let data = null;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    msg.textContent = "导入失败：JSON 格式错误（" + e.message + "），请确认复制的是完整导出内容";
    return;
  }
  // 兼容多层结构：{ok,data} 包装 / 顶层四块 / {groups:{群号:{…}}} / 群配置整体导出
  if (data && data.data && typeof data.data === "object") data = data.data;
  else if (data && data.groups && typeof data.groups === "object") {
    const hit = data.groups[String(gid)] || data.groups[Object.keys(data.groups)[0]];
    if (hit) data = hit;
  }
  msg.textContent = "保存中…";
  const src = data || {};
  const blocks = {};
  ["cleanup", "limits", "ttl", "dupe_rule"].forEach((k) => {
    if (src[k] && typeof src[k] === "object") blocks[k] = src[k];
  });
  const names = { cleanup: "定时整理", limits: "容量提醒", ttl: "时效清理", dupe_rule: "查重" };
  const got = Object.keys(blocks).map((k) => names[k]).join("、");
  if (!got) { toast("文件里没有可导入的维护配置（需含 cleanup / limits / ttl / dupe_rule）", "error"); return; }
  const g = groupsData.find((x) => String(x.group_id) === String(gid));
  const body = {
    group_id: gid,
    enabled: g ? g.enabled !== false : true,
    name: (g && g.name) || "",
    admins: (g && Array.isArray(g.admins)) ? g.admins : [],
    rules: (g && Array.isArray(g.rules)) ? g.rules : [],
    cleanup: blocks.cleanup || (g && g.cleanup) || {},
    limits: blocks.limits || (g && g.limits) || {},
    dupe_rule: blocks.dupe_rule || (g && g.dupe_rule) || {},
    ttl: blocks.ttl || (g && g.ttl) || {},
  };
  try {
    await bridge.apiPost("groups", body);
    msg.textContent = "✅ 导入成功（已覆盖：" + got + "）";
    toast("已导入：" + got + "，配置已刷新");
    $("m-import-text").value = "";
    $("m-import-name").textContent = "";
    setTimeout(() => { $("m-import-panel").style.display = "none"; }, 600);
    loadMaintTab();
  } catch (e) {
    msg.textContent = "导入失败：" + e.message;
  }
});

function renderMaintEditor(gid) {
  maintGroupId = gid;
  const g = groupsData.find((x) => String(x.group_id) === String(gid));
  const body = $("maint-body");
  if (!g) { body.innerHTML = '<div class="section empty-state">请先选择群。</div>'; maintCollect = null; return; }
  renderMaintChips(groupsData, gid);   // 群导航 chips（当前群高亮，点击切换）
  const badge = '<span class="g-badge">' + esc(g.name ? g.name + " " : "") + "(" + esc(String(g.group_id)) + ")</span>";  // 每个分区标题右侧的群徽章
  const cleanup = g.cleanup || {};
  const limits = g.limits || {};
  const dupeRule = g.dupe_rule || {};

  body.innerHTML = "";

  // —— 子页导航：四分区拆分（学 g-subnav 模式） ——
  const mTabs = document.createElement("div");
  mTabs.className = "subnav";
  mTabs.innerHTML = `
    <button class="subchip active" data-mpane="cron" type="button">定时整理</button>
    <button class="subchip" data-mpane="limit" type="button">容量提醒</button>
    <button class="subchip" data-mpane="ttl" type="button">老文件处理</button>
    <button class="subchip" data-mpane="dupe" type="button">查重清理</button>
    <button class="subchip" data-mpane="sendtmp" type="button">发送临时文件</button>`;
  const panes = { cron: document.createElement("div"), limit: document.createElement("div"), ttl: document.createElement("div"), dupe: document.createElement("div"), sendtmp: document.createElement("div") };
  panes.limit.style.display = "none";
  panes.ttl.style.display = "none";
  panes.dupe.style.display = "none";
  panes.sendtmp.style.display = "none";
  mTabs.addEventListener("click", (ev) => {
    const b = ev.target.closest(".subchip");
    if (!b) return;
    mTabs.querySelectorAll(".subchip").forEach((x) => x.classList.toggle("active", x === b));
    Object.keys(panes).forEach((k) => { panes[k].style.display = k === b.dataset.mpane ? "" : "none"; });
  });
  body.appendChild(mTabs);
  Object.values(panes).forEach((pn) => body.appendChild(pn));

  // —— 分区 1：定时自动整理 ——
  const sec1 = document.createElement("div");
  sec1.className = "section";
  sec1.innerHTML = `
    <div class="section-header"><span class="sec-ico"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg></span><h2>定时自动整理${badge}</h2>
      <span class="hint-text">到点按「分类规则」自动移动文件</span></div>
    <label class="inline-check">
      <input type="checkbox" class="g-clean-en" ${cleanup.enabled ? "checked" : ""} />
      启用定时自动整理
    </label>
    <div class="hint" style="margin:2px 0 8px;">开 = 按下方时间点自动跑分类移动；关 = 只在手动点「立即整理」时执行。</div>
    <div class="g-crones"></div>
    <div class="toolbar">
      <button class="g-cron-add btn" type="button">＋ 添加时间点</button>
      <span class="hint-text">可设多个时间点（如每天 6 点 + 18 点），任一命中就整理一次</span>
    </div>
    <div class="form-grid" style="margin-top:8px;">
      <div class="field">
        <label title="重启/卡顿错过执行时，在最近 N 分钟内补跑一次；0=不补">补跑窗口（分钟，0=不补）</label>
        <input type="number" class="g-catchup" min="0" max="1440" value="${cleanup.catchup_minutes != null ? cleanup.catchup_minutes : 60}" />
      </div>
    </div>
    <div class="hint" style="margin-top:8px;">定时整理只做分类移动；不删除任何文件。老文件是否回收/删除由「老文件处理」单独控制。</div>`;
  panes.cron.appendChild(sec1);

  // —— 分区 2：容量提醒 ——
  const sec2 = document.createElement("div");
  sec2.className = "section";
  sec2.innerHTML = `
    <div class="section-header"><span class="sec-ico"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z"/><circle cx="12" cy="12" r="2.6"/></svg></span><h2>容量提醒${badge}</h2>
      <span class="hint-text">超过提醒线时给出告警；不会自动删文件</span></div>
    <div class="form-grid">
      <div class="field">
        <label>文件数上限（个，0=不限）</label>
        <input type="number" class="g-limit-count" min="0" value="${limits.count_limit != null ? limits.count_limit : 0}" />
      </div>
      <div class="field">
        <label>空间上限（GB，0=不限）</label>
        <input type="number" class="g-limit-space" min="0" value="${limits.space_limit_gb != null ? limits.space_limit_gb : 0}" />
      </div>
      <div class="field">
        <label title="用到上限的这个比例就提醒">告警百分比（%）</label>
        <input type="number" class="g-limit-pct" min="1" max="100" value="${limits.warn_pct != null ? limits.warn_pct : 90}" />
      </div>
    </div>
    <div class="toolbar" style="margin-top:8px;">
      <label class="inline-check">
        <input type="checkbox" class="g-limit-notify" ${limits.notify ? "checked" : ""} />
        超线时在群内提醒
      </label>
      <button class="g-limit-check btn btn-primary" type="button">立即检查一次</button>
    </div>
    <div class="g-limit-result hint" style="margin-top:6px;"></div>`;
  panes.limit.appendChild(sec2);

  // —— 分区 2b：老文件自动处理（时效清理） ——
  const ttlCfg = g.ttl || {};
  const secT = document.createElement("div");
  secT.className = "section";
  secT.innerHTML = `
    <div class="section-header"><h2>老文件自动处理（时效清理）${badge}</h2>
      <span class="hint-text">文件放久了自动报告 / 回收 / 删除</span></div>
    <div class="hint" style="margin-bottom:6px;">
      「老」怎么算：取文件的上传时间和最后修改时间里较新的一个，超过下方天数就算超龄。
      启用后跟随<b>定时整理</b>一起跑（也可随时 <code>/gf 整</code> 手动触发），结果合并进同一条整理报告。
    </div>
    <label class="inline-check">
      <input type="checkbox" class="g-ttl-en" ${ttlCfg.enabled ? "checked" : ""} />
      启用老文件自动处理
    </label>
    <div class="form-grid" style="margin-top:8px;">
      <div class="field">
        <label title="0 = 不设全局天数，只看下面按文件夹的规则">超过多少天算「老」（0=只看文件夹规则）</label>
        <input type="number" class="g-ttl-days" min="0" value="${ttlCfg.default_days != null ? ttlCfg.default_days : 180}" />
      </div>
      <div class="field">
        <label>超龄的老文件怎么处理</label>
        <select class="g-ttl-action">
          <option value="report" ${(ttlCfg.action || "report") === "report" ? "selected" : ""}>只报告（安全，先观察）</option>
          <option value="recycle" ${ttlCfg.action === "recycle" ? "selected" : ""}>移入回收文件夹（可再翻出来）</option>
          <option value="delete" ${ttlCfg.action === "delete" ? "selected" : ""}>彻底删除（危险，慎选）</option>
        </select>
      </div>
      <div class="field">
        <label title="选「移入回收」时用，没有会自动建">回收站文件夹名</label>
        <input type="text" class="g-ttl-recycle" value="${esc(ttlCfg.recycle_folder || "_回收")}" />
      </div>
    </div>
    <div class="toolbar" style="margin-top:10px;">
      <b style="font-size:13px;">按文件夹单独设置</b>
      <span class="hint-text">点文件夹输入框可从现有文件夹里选；天数 0 = 这个文件夹永不处理；动作留空 = 跟全局一致</span>
    </div>
    <div class="g-ttl-rows"></div>
    <button class="g-ttl-add btn btn-sm" type="button">+ 添加文件夹规则</button>
    <button class="g-ttl-preview btn btn-sm" type="button">👁 试运行：预览超龄文件</button>
    <div class="g-ttl-preview-box hint" style="margin-top:6px;"></div>
    <span class="hint-text g-ttl-scan-hint" style="margin-left:8px;">（文件夹列表加载中…）</span>
    <datalist id="ttl-folder-list"></datalist>`;
  panes.ttl.appendChild(secT);

  // 拉取该群现有顶层文件夹，供覆盖行的文件夹输入框选择（自动扫描）
  (async () => {
    try {
      const d = await bridge.apiGet("files", { gid: String(gid) });
      const names = (d.all_folders || d.folders || []).map((f) => f.name).filter(Boolean);
      const dl = secT.querySelector("#ttl-folder-list");
      dl.innerHTML = "";
      names.forEach((n) => {
        const opt = document.createElement("option");
        opt.value = n;
        dl.appendChild(opt);
      });
      const hintEl = secT.querySelector(".g-ttl-scan-hint");
      if (hintEl) hintEl.textContent = names.length
        ? `（已扫描到 ${names.length} 个文件夹，点输入框即可选择）`
        : "（该群根目录下暂无文件夹）";
    } catch (e) {
      const hintEl = secT.querySelector(".g-ttl-scan-hint");
      if (hintEl) hintEl.textContent = "（文件夹扫描失败，可手动输入名称）";
    }
  })();

  const ttlRowsBox = secT.querySelector(".g-ttl-rows");
  const ttlFolders = Array.isArray(ttlCfg.folders) ? ttlCfg.folders.map((r) => ({
    folder: String(r.folder || ""), days: r.days != null ? Number(r.days) : 30, action: String(r.action || ""),
  })) : [];
  const TTL_ACT_LABELS = [["", "继承群级"], ["report", "只报告"], ["recycle", "移入回收"], ["delete", "直接删除"]];
  function renderTtlRow(row, idx) {
    const wrap = document.createElement("div");
    wrap.className = "g-ttl-row";
    wrap.style.cssText = "display:flex;flex-wrap:wrap;gap:10px 12px;align-items:center;margin-bottom:10px;";
    wrap.innerHTML = `
      <input type="text" class="g-ttl-folder" list="ttl-folder-list" placeholder="文件夹（点开选现有）" value="${esc(row.folder)}" style="flex:1 1 170px;min-width:150px;" />
      <input type="number" class="g-ttl-row-days" min="0" placeholder="天数" value="${row.days}" style="flex:0 0 90px;" />
      <select class="g-ttl-row-action" style="flex:0 0 126px;">
        ${TTL_ACT_LABELS.map(([v, t]) => `<option value="${v}" ${row.action === v ? "selected" : ""}>${t}</option>`).join("")}
      </select>
      <button class="g-ttl-row-del btn btn-sm btn-danger">×</button>`;
    wrap.querySelector(".g-ttl-row-del").addEventListener("click", () => {
      ttlFolders.splice(idx, 1);
      renderTtlRows();
    });
    ttlRowsBox.appendChild(wrap);
  }
  function renderTtlRows() {
    ttlRowsBox.innerHTML = "";
    ttlFolders.forEach((r, i) => renderTtlRow(r, i));
  }
  function collectTtlRows() {
    ttlRowsBox.querySelectorAll(".g-ttl-row").forEach((w, i) => {
      if (!ttlFolders[i]) return;
      ttlFolders[i].folder = w.querySelector(".g-ttl-folder").value.trim();
      ttlFolders[i].days = Number(w.querySelector(".g-ttl-row-days").value || 0);
      ttlFolders[i].action = w.querySelector(".g-ttl-row-action").value;
    });
    return ttlFolders.filter((r) => r.folder);
  }
  renderTtlRows();
  secT.querySelector(".g-ttl-add").addEventListener("click", () => {
    collectTtlRows();
    ttlFolders.push({ folder: "", days: 30, action: "" });
    renderTtlRows();
  });
  const ttlPrevBtn = secT.querySelector(".g-ttl-preview");
  if (ttlPrevBtn) ttlPrevBtn.addEventListener("click", async () => {
    const box = secT.querySelector(".g-ttl-preview-box");
    if (!box) return;
    box.textContent = "扫描中…";
    try {
      const r = await bridge.apiPost("ttl_preview", { group_id: g.group_id });
      if (!r.enabled) { box.textContent = "时效清理未启用"; return; }
      const rows = r.planned || [];
      box.textContent = "超龄 " + (r.over || 0) + " 个 / 无时间 " + (r.unknown_time || 0) + " 个"
        + (r.truncated ? "（扫描已截断）" : "") + (rows.length ? "：" : "")
        + rows.slice(0, 5).map((x) => x.action + "「" + x.name + "」").join("；");
    } catch (e) {
      box.textContent = "预览失败：" + e.message;
    }
  });

  // —— 分区 3：查重 ——
  const sec3 = document.createElement("div");
  sec3.className = "section";
  sec3.innerHTML = `
    <div class="section-header"><h2>查重${badge}</h2>
      <span class="hint-text">先定义“什么算同名”，再扫描；只查不删，删除前可逐组勾选</span></div>
    <div class="form-grid">
      <div class="field">
        <label>判定模式</label>
        <select class="g-dupe-mode">
          <option value="smart" ${(!dupeRule.mode || dupeRule.mode === "smart") ? "selected" : ""}>智能归一（去括号/末尾时间戳，smart）</option>
          <option value="exact" ${dupeRule.mode === "exact" ? "selected" : ""}>去扩展名后完全同名（exact）</option>
          <option value="split" ${dupeRule.mode === "split" ? "selected" : ""}>按分隔符取段（split）</option>
          <option value="regex" ${dupeRule.mode === "regex" ? "selected" : ""}>按正则归一（regex）</option>
        </select>
      </div>
    </div>
    <div class="g-dupe-split form-grid" style="margin-top:8px;">
      <div class="field">
        <label>分隔符（如 _ - 空格）</label>
        <input type="text" class="g-dupe-sep" value="${esc(dupeRule.sep || "_")}" />
      </div>
      <div class="field">
        <label>取第几段作为名字（0=整段文件名）</label>
        <input type="number" class="g-dupe-part" min="0" value="${dupeRule.part != null ? dupeRule.part : 0}" />
      </div>
    </div>
    <div class="g-dupe-regex-in field" style="display:none;margin-top:8px;">
      <label>归一正则（把匹配到的部分删掉后再比较，留空=直接比全名）</label>
      <input type="text" value="${esc(dupeRule.regex || "")}" placeholder="如 [（(].*?[）)] 去掉括号备注" />
    </div>
    <div class="toolbar" style="margin-top:10px;">
      <button class="g-dupes btn btn-primary" type="button">🔍 扫描查重（只查不删）</button>
    </div>
    <div class="g-dupes-panel sub-panel" style="display:none;"></div>`;
  panes.dupe.appendChild(sec3);

  // —— 分区 4：发送临时文件（全局，不随群切换） ——
  const secS = document.createElement("div");
  secS.className = "section";
  secS.innerHTML = `
    <div class="section-header"><h2>发送临时文件（全局）</h2>
      <span class="hint-text">/gf 查 … 序号 或文件页发文件时，会先把群文件下载到本机临时目录；协议端必须能读到它</span></div>
    <div class="hint" style="margin-bottom:8px;">
      只清理「发送用的临时副本」，与群文件「老文件处理」完全无关；发送中的文件不会被删，失败残留按下方小时数自动清。
    </div>
    <div class="form-grid">
      <div class="field" style="grid-column:1/-1;">
        <label>当前生效目录（只读）</label>
        <input type="text" class="st-dir" readonly value="加载中…" style="font-family:monospace;font-size:12px;" />
      </div>
      <div class="field">
        <label>保留秒数（发送后多久删，30–86400）</label>
        <input type="number" class="st-keep" min="30" max="86400" value="600" />
      </div>
      <div class="field">
        <label>残留清理（小时，1–168）</label>
        <input type="number" class="st-orphan" min="1" max="168" value="1" />
      </div>
    </div>
    <label class="inline-check" style="margin-top:8px;">
      <input type="checkbox" class="st-en" checked /> 自动清理发送临时文件
    </label>
    <div class="hint st-state" style="margin-top:4px;"></div>
    <div class="toolbar" style="margin-top:8px;">
      <button class="st-save btn btn-primary" type="button">保存发送设置</button>
      <button class="st-sweep btn" type="button">立即清理残留</button>
      <span class="hint-text st-msg"></span>
    </div>`;
  panes.sendtmp.appendChild(secS);
  const stDir = secS.querySelector(".st-dir");
  const stKeep = secS.querySelector(".st-keep");
  const stOrphan = secS.querySelector(".st-orphan");
  const stEn = secS.querySelector(".st-en");
  const stState = secS.querySelector(".st-state");
  const stMsg = secS.querySelector(".st-msg");
  function stFill(d) {
    if (!d) return;
    stDir.value = d.dir || "";
    stKeep.value = d.keep_seconds != null ? d.keep_seconds : 600;
    stOrphan.value = d.orphan_hours != null ? d.orphan_hours : 1;
    stEn.checked = d.enabled !== false;
    const parts = [];
    parts.push(d.shared ? "✅ 在共享 temp 内（协议端可读）" : "⚠ 不在共享 temp 内：协议端可能找不到文件（EACCES/ENOENT）");
    parts.push("来源：" + (d.source || "—"));
    if (d.inflight) parts.push("发送中：" + d.inflight + " 个");
    if (d.last_sweep && d.last_sweep.at) parts.push("上次清扫：" + d.last_sweep.at + "，删除 " + (d.last_sweep.removed || 0) + " 个");
    stState.textContent = parts.join(" · ");
    stState.style.color = d.shared ? "" : "var(--danger)";
  }
  (async () => {
    try { stFill(await bridge.apiGet("send_tmp")); }
    catch (e) { stState.textContent = "读取发送设置失败：" + e.message; stState.style.color = "var(--danger)"; }
  })();
  secS.querySelector(".st-save").addEventListener("click", async () => {
    stMsg.textContent = "保存中…";
    try {
      const r = await bridge.apiPost("send_tmp", {
        enabled: stEn.checked,
        keep_seconds: Number(stKeep.value || 600),
        orphan_hours: Number(stOrphan.value || 1),
      });
      stFill(r.settings || r);
      stMsg.textContent = "✅ 已保存，立即生效";
      toast("发送临时文件设置已保存");
    } catch (e) { stMsg.textContent = "保存失败：" + e.message; toast("保存失败：" + e.message, "error"); }
  });
  secS.querySelector(".st-sweep").addEventListener("click", async () => {
    stMsg.textContent = "清理中…";
    try {
      const r = await bridge.apiPost("send_tmp_sweep", {});
      stFill(r.state);
      stMsg.textContent = "✅ 本次清理 " + (r.removed || 0) + " 个残留" + (r.errors ? "，失败 " + r.errors : "");
      toast("已清理 " + (r.removed || 0) + " 个残留");
    } catch (e) { stMsg.textContent = "清理失败：" + e.message; }
  });

  const cronBox = sec1.querySelector(".g-crones");

  // ---- 多时间点 cron 组件 ----
  const crones = Array.isArray(cleanup.crones) && cleanup.crones.length
    ? cleanup.crones.map((c) => String(c))
    : (cleanup.cron ? [String(cleanup.cron)] : ["0 3 * * *"]);
  function fillOptions(sel, lo, hi, fmt) {
    sel.innerHTML = "";
    for (let v = lo; v <= hi; v++) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = fmt ? fmt(v) : v;
      sel.appendChild(o);
    }
  }
  const WEEK = [["1", "周一"], ["2", "周二"], ["3", "周三"], ["4", "周四"], ["5", "周五"], ["6", "周六"], ["0", "周日"]];
  function renderCronRow(cron, idx, box, arr) {
    cron = cron || "0 3 * * *";
    const m = String(cron).split(/\s+/);
    let mode = "daily", hour = 3, min = 0, wd = "1", interval = 2;
    if (m.length === 5) {
      min = Number(m[0]) || 0;
      hour = Number(m[1]) || 0;
      if (m[2] === "*" && m[3] === "*" && m[4] === "*") mode = "daily";
      else if (m[2] === "*" && m[3] === "*" && /^[0-7]$/.test(m[4])) { mode = "weekly"; wd = m[4]; }
      else if (m[0] === "0" && /^\*\/\d+$/.test(m[1]) && m[2] === "*" && m[3] === "*" && m[4] === "*") { mode = "interval"; interval = Number(m[1].replace("*\/", "")) || 2; }
      else mode = "custom";
    } else mode = "custom";
    const wrap = document.createElement("div");
    wrap.className = "g-cron-row";
    wrap.style.cssText = "display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-bottom:4px;";
    wrap.innerHTML = `
      <select class="g-cron-mode" style="flex:0 0 122px;">
        <option value="daily">每天</option>
        <option value="weekly">每周</option>
        <option value="interval">每隔几小时</option>
        <option value="custom">自定义(高级)</option>
      </select>
      <span class="g-cron-daily cron-grp" style="display:${mode === "daily" ? "" : "none"};">
        <select class="g-cron-hour"></select><span class="unit">时</span>
        <select class="g-cron-min"></select><span class="unit">分</span>
      </span>
      <span class="g-cron-weekly cron-grp" style="display:${mode === "weekly" ? "" : "none"};">
        <select class="g-cron-weekday"></select>
        <select class="g-cron-hour"></select><span class="unit">时</span>
        <select class="g-cron-min"></select><span class="unit">分</span>
      </span>
      <span class="g-cron-interval cron-grp" style="display:${mode === "interval" ? "" : "none"};">
        <select class="g-cron-interval-n"></select><span class="unit">小时整理一次</span>
      </span>
      <input type="text" class="g-cron-custom-input" placeholder="cron，如 0 3 * * *" value="${esc(cron)}" style="display:${mode === "custom" ? "" : "none"};flex:1 1 140px;min-width:110px;" />
      <button class="g-cron-del btn btn-sm btn-danger">×</button>`;
    wrap.querySelectorAll(".g-cron-hour").forEach((h) => fillOptions(h, 0, 23, (v) => v + " 点"));
    wrap.querySelectorAll(".g-cron-min").forEach((mi) => fillOptions(mi, 0, 55, (v) => v + " 分"));
    fillOptions(wrap.querySelector(".g-cron-interval-n"), 1, 12, (v) => v + " 小时");
    wrap.querySelector(".g-cron-weekday").innerHTML = WEEK.map(([v, tt]) => `<option value="${v}">${tt}</option>`).join("");
    wrap.querySelector(".g-cron-mode").value = mode;
    wrap.querySelectorAll(".g-cron-hour").forEach((h) => (h.value = hour));
    wrap.querySelectorAll(".g-cron-min").forEach((mi) => (mi.value = min));
    wrap.querySelector(".g-cron-weekday").value = wd;
    wrap.querySelector(".g-cron-interval-n").value = interval;
    function modeShow() {
      const md = wrap.querySelector(".g-cron-mode").value;
      wrap.querySelector(".g-cron-daily").style.display = md === "daily" ? "" : "none";
      wrap.querySelector(".g-cron-weekly").style.display = md === "weekly" ? "" : "none";
      wrap.querySelector(".g-cron-interval").style.display = md === "interval" ? "" : "none";
      wrap.querySelector(".g-cron-custom-input").style.display = md === "custom" ? "" : "none";
    }
    wrap.querySelector(".g-cron-mode").addEventListener("change", modeShow);
    wrap.querySelector(".g-cron-del").addEventListener("click", () => {
      arr.splice(idx, 1);
      renderCrones(box, arr);
    });
    return wrap;
  }
  function renderCrones(box, arr) {
    box.innerHTML = "";
    arr.forEach((c, i) => box.appendChild(renderCronRow(c, i, box, arr)));
  }
  function collectCrones(box, emptyDefault) {
    const out = [];
    box.querySelectorAll(".g-cron-row").forEach((w) => {
      const md = w.querySelector(".g-cron-mode").value;
      if (md === "daily") out.push(w.querySelector(".g-cron-daily .g-cron-min").value + " " + w.querySelector(".g-cron-daily .g-cron-hour").value + " * * *");
      else if (md === "weekly") out.push(w.querySelector(".g-cron-weekly .g-cron-min").value + " " + w.querySelector(".g-cron-weekly .g-cron-hour").value + " * * " + w.querySelector(".g-cron-weekday").value);
      else if (md === "interval") out.push("0 */" + w.querySelector(".g-cron-interval-n").value + " * * *");
      else out.push(w.querySelector(".g-cron-custom-input").value.trim() || "0 3 * * *");
    });
    if (out.length) return out;
    return emptyDefault === false ? [] : ["0 3 * * *"];
  }
  renderCrones(cronBox, crones);
  sec1.querySelector(".g-cron-add").addEventListener("click", () => { crones.push("0 6 * * *"); renderCrones(cronBox, crones); });

  // ---- 容量：立即检查 ----
  sec2.querySelector(".g-limit-check").addEventListener("click", async () => {
    const res = sec2.querySelector(".g-limit-result");
    res.textContent = "检查中…";
    try {
      const d = await bridge.apiGet("check_limit", { gid: g.group_id });
      let txt = `当前 ${d.file_count} 个文件，占用 ${d.used_gb}GB / ${d.total_gb}GB`;
      if (d.warnings && d.warnings.length) txt += "；⚠ " + d.warnings.join("；");
      else txt += "，未超限";
      res.textContent = txt;
    } catch (e) {
      res.textContent = "检查失败：" + e.message;
    }
  });

  // ---- 查重规则：模式切换显示对应参数 ----
  const dupeModeSel = sec3.querySelector(".g-dupe-mode");
  function dupeModeShow() {
    const md = dupeModeSel.value;
    sec3.querySelector(".g-dupe-split").style.display = md === "split" ? "" : "none";
    sec3.querySelector(".g-dupe-regex-in").style.display = md === "regex" ? "" : "none";
  }
  dupeModeSel.addEventListener("change", dupeModeShow);
  dupeModeShow();

  // ---- 查重两步面板 ----
  const dupesPanel = sec3.querySelector(".g-dupes-panel");
  function fmtTime(ts) {
    if (!ts) return "未知时间";
    const d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "月" + d.getDate() + "日 " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }
  let dupPicked = [];
  function renderDupResults(groupsDup) {
    dupesPanel.innerHTML = "";
    dupPicked = [];
    if (!groupsDup.length) {
      dupesPanel.innerHTML = '<div class="muted">✅ 按当前查重规则没有发现重复文件。</div>';
      return;
    }
    const head = document.createElement("div");
    head.className = "muted";
    head.style.cssText = "margin-bottom:6px;";
    head.textContent = "发现 " + groupsDup.length + " 组重复。已默认勾选每组较旧的文件（保留最新），可自行勾选调整：";
    dupesPanel.appendChild(head);
    groupsDup.forEach((grp) => {
      const h = document.createElement("div");
      h.style.cssText = "margin:8px 0 2px;font-weight:600;";
      h.textContent = "📄 " + grp[0].name + "（" + fmtSize(grp[0].size) + "）× " + grp.length;
      dupesPanel.appendChild(h);
      grp.forEach((f, idx) => {
        const isKeep = idx === grp.length - 1;
        const row = document.createElement("label");
        row.style.cssText = "display:flex;align-items:center;gap:6px;margin-bottom:2px;font-size:12px;cursor:pointer;";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !isKeep;
        const txt = document.createElement("span");
        txt.style.cssText = "flex:1;" + (isKeep ? "color:var(--success);font-weight:600;" : "");
        txt.textContent = (isKeep ? "🟢 保留·最新 " : "🗑 拟删 ")
          + (f.folder ? "📂" + f.folder : "根目录") + " · " + fmtTime(f.upload_time)
          + " · " + fmtSize(f.size) + " · " + (f.uploader_name || "");
        row.appendChild(cb);
        row.appendChild(txt);
        dupesPanel.appendChild(row);
        dupPicked.push({ file: f, cb, row });
      });
    });
    const ops = document.createElement("div");
    ops.style.cssText = "margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;";
    const selAll = document.createElement("button");
    selAll.textContent = "全选拟删";
    selAll.className = "btn";
    const selNone = document.createElement("button");
    selNone.textContent = "全部保留";
    selNone.className = "btn";
    const delBtn = document.createElement("button");
    delBtn.className = "btn btn-danger";
    const rescan = document.createElement("button");
    rescan.textContent = "🔁 重新扫描";
    rescan.className = "btn";
    const result = document.createElement("div");
    result.style.cssText = "width:100%;font-size:12px;margin-top:4px;";
    function updateN() { delBtn.textContent = "🗑 删除勾选（" + dupPicked.filter((p) => p.cb.checked).length + "）"; }
    selAll.onclick = () => { dupPicked.forEach((p) => { p.cb.checked = true; p.cb.disabled = false; }); updateN(); };
    selNone.onclick = () => { dupPicked.forEach((p) => (p.cb.checked = false)); updateN(); };
    rescan.onclick = () => scanDupes();
    delBtn.onclick = async () => {
      const toDel = dupPicked.filter((p) => p.cb.checked && !p.cb.disabled).map((p) => p.file);
      if (!toDel.length) { toast("没有勾选要删除的文件"); return; }
      if (!armConfirm(delBtn, "⚠ 再点一次：删除 " + toDel.length + " 个")) return;
      delBtn.disabled = true;
      result.style.color = "";
      result.textContent = "删除中…";
      let ok = 0, fail = 0;
      const errs = [];
      try {
        const items = toDel.map((f) => ({
          file_id: f.file_id,
          folder_id: f.folder_id || null,
          busid: f.busid || 0,
          name: f.name,
        }));
        const r = await bridge.apiPost("delete", { group_id: g.group_id, items });
        ok = r.deleted || 0;
        fail = r.failed || 0;
        (r.details || []).forEach((d) => { if (!d.ok) errs.push((d.name || d.file_id) + "：" + d.err); });
      } catch (e) {
        fail += toDel.length;
        errs.push("删除请求失败：" + e.message);
      }
      dupPicked.forEach((p) => {
        if (p.cb.checked) {
          p.row.style.opacity = "0.45";
          p.row.style.textDecoration = "line-through";
          p.cb.checked = false;
          p.cb.disabled = true;
        }
      });
      result.textContent = "删除完成：成功 " + ok + "，失败 " + fail
        + (errs.length ? "；失败原因：" + errs.slice(0, 3).join(" ｜ ") : "");
      result.style.color = fail ? "var(--danger)" : "var(--success)";
      delBtn.disabled = false;
      if (delBtn._restore) delBtn._restore();
      updateN();
      toast("删除完成：成功 " + ok + "，失败 " + fail);
    };
    ops.appendChild(selAll);
    ops.appendChild(selNone);
    ops.appendChild(delBtn);
    ops.appendChild(rescan);
    ops.appendChild(result);
    dupesPanel.appendChild(ops);
    updateN();
  }
  async function scanDupes() {
    dupesPanel.style.display = "block";
    dupesPanel.innerHTML = '<div class="muted">扫描查重中…（只查不删，不会动文件）</div>';
    try {
      const data = await bridge.apiGet("dupes", { gid: g.group_id });
      renderDupResults(data.groups || []);
    } catch (e) {
      dupesPanel.innerHTML = '<div class="muted" style="color:var(--danger);">查重失败：' + esc(e.message) + "</div>";
    }
  }
  sec3.querySelector(".g-dupes").addEventListener("click", () => {
    if (dupesPanel.style.display !== "none") { dupesPanel.style.display = "none"; return; }
    scanDupes();
  });

  // 顶部「保存维护设置」：全量接口，basics/rules 原样回传
  maintCollect = async function () {
    const body = {
      group_id: g.group_id,
      enabled: g.enabled !== false,
      name: g.name || "",
      admins: Array.isArray(g.admins) ? g.admins : [],
      rules: Array.isArray(g.rules) ? g.rules : [],
      cleanup: {
        enabled: sec1.querySelector(".g-clean-en").checked,
        crones: collectCrones(cronBox),
        catchup_minutes: Number((sec1.querySelector(".g-catchup") || {}).value || 0),
      },
      limits: {
        count_limit: Number(sec2.querySelector(".g-limit-count").value || 0),
        space_limit_gb: Number(sec2.querySelector(".g-limit-space").value || 0),
        warn_pct: Number(sec2.querySelector(".g-limit-pct").value || 100),
        notify: sec2.querySelector(".g-limit-notify").checked,
      },
      dupe_rule: {
        mode: sec3.querySelector(".g-dupe-mode").value,
        sep: sec3.querySelector(".g-dupe-sep").value || "_",
        part: Number(sec3.querySelector(".g-dupe-part").value || 0),
        regex: sec3.querySelector(".g-dupe-regex-in input").value || "",
      },
      ttl: {
        enabled: secT.querySelector(".g-ttl-en").checked,
        default_days: Number(secT.querySelector(".g-ttl-days").value || 0),
        action: secT.querySelector(".g-ttl-action").value,
        recycle_folder: secT.querySelector(".g-ttl-recycle").value.trim() || "_回收",
        folders: collectTtlRows(),
      },
    };
    try {
      await bridge.apiPost("groups", body);
      toast("已保存定时与维护设置");
    } catch (e) {
      toast("保存失败：" + e.message, "error");
    }
  };
}

// ---------- 文件管理 ----------
async function loadFileTab() {
  const sel = $("f-group");
  const prev = sel.value;
  sel.innerHTML = "";
  try {
    const data = await bridge.apiGet("groups");
    const groups = data.groups || [];
    if (!groups.length) {
      $("f-files").innerHTML = '<div class="empty-state">请先在「群配置」添加群。</div>';
      return;
    }
    groups.forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g.group_id;
      opt.textContent = (g.name ? g.name + " (" : "") + g.group_id + (g.name ? ")" : "");
      sel.appendChild(opt);
    });
    sel.value = prev && [...sel.options].some((o) => o.value === prev) ? prev : sel.value;
    await refreshFiles();
  } catch (e) {
    toast("加载群列表失败：" + e.message, "error");
  }
}

// 文件页类型筛选（v2.6.0）：可多选、默认收起、按群记忆（localStorage）
const TYPE_FILTER_KEY = "gfc_type_filter_by_group_v1";
const TYPE_FILTER_LEGACY = "gfc_type_filter_v1"; // v2.5.0 的全局 key，首次访问时迁移
const TYPE_GROUPS = EXT_PRESETS.map((g) => ({ key: g.key, label: g.label }))
  .concat([{ key: "other", label: "其他" }]);
let typeFilter = new Set();

function validTypeKeys(arr) {
  return arr.filter((k) => TYPE_GROUPS.some((g) => g.key === k));
}

function loadTypeFilter(gid) {
  typeFilter = new Set();
  if (!gid) return;
  try {
    const all = JSON.parse(localStorage.getItem(TYPE_FILTER_KEY) || "{}");
    if (all && typeof all === "object" && !Array.isArray(all) && Array.isArray(all[gid])) {
      typeFilter = new Set(validTypeKeys(all[gid]));
      return;
    }
    // 兼容 v2.5.0 的全局筛选：迁移给第一个打开的群，然后清掉旧 key
    const legacy = JSON.parse(localStorage.getItem(TYPE_FILTER_LEGACY) || "[]");
    if (Array.isArray(legacy) && legacy.length) {
      typeFilter = new Set(validTypeKeys(legacy));
      saveTypeFilter(gid);
      localStorage.removeItem(TYPE_FILTER_LEGACY);
    }
  } catch (e) {
    typeFilter = new Set();
  }
}

function saveTypeFilter(gid) {
  if (!gid) return;
  try {
    let all = {};
    try { all = JSON.parse(localStorage.getItem(TYPE_FILTER_KEY) || "{}") || {}; } catch (e) { all = {}; }
    if (!all || typeof all !== "object" || Array.isArray(all)) all = {};
    if (typeFilter.size) all[gid] = [...typeFilter];
    else delete all[gid];
    localStorage.setItem(TYPE_FILTER_KEY, JSON.stringify(all));
  } catch (e) {}
}

function typeSummary() {
  const btn = $("f-type-btn");
  if (!btn) return;
  const bar = $("f-type-bar");
  const open = bar && bar.style.display !== "none";
  if (!typeFilter.size) {
    btn.textContent = "类型：全部" + (open ? " ▴" : " ▾");
    btn.classList.remove("has-filter");
    return;
  }
  const labels = [...typeFilter].map((k) => {
    const g = TYPE_GROUPS.find((x) => x.key === k);
    return g ? g.label : k;
  });
  btn.textContent = "类型：" + (labels.length <= 2
    ? labels.join("、")
    : labels.slice(0, 2).join("、") + ` 等${labels.length}类`) + (open ? " ▴" : " ▾");
  btn.classList.add("has-filter");
}

function initTypeFilter(gid) {
  loadTypeFilter(gid);
  const bar = $("f-type-bar");
  if (!bar) return;
  bar.innerHTML = "";
  const chip = (key, label) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "type-chip";
    b.dataset.type = key;
    b.textContent = label;
    b.addEventListener("click", () => {
      if (!key) typeFilter.clear();
      else if (typeFilter.has(key)) typeFilter.delete(key);
      else typeFilter.add(key);
      saveTypeFilter(gid);
      syncTypeBar();
      renderFileList();
    });
    bar.appendChild(b);
  };
  chip("", "全部");
  TYPE_GROUPS.forEach((g) => chip(g.key, g.label));
  syncTypeBar();
}

function syncTypeBar() {
  const bar = $("f-type-bar");
  if (!bar) return;
  bar.querySelectorAll(".type-chip").forEach((b) => {
    const k = b.dataset.type;
    b.classList.toggle("active", k ? typeFilter.has(k) : !typeFilter.size);
  });
  typeSummary();
}

let currentFiles = [];
let filePage = 1;

function renderFileList() {
  const box = $("f-files");
  const list = currentFiles.filter((f) => !typeFilter.size || typeFilter.has(extMeta(extOf(f.name)).key));
  const st = $("f-status");
  if (st) {
    st.textContent = currentFiles.length
      ? `共 ${currentFiles.length} 个文件` + (typeFilter.size ? `，筛选出 ${list.length} 个` : "")
      : "";
  }
  if (!currentFiles.length) {
    box.innerHTML = '<div class="empty-state">该位置暂无文件。</div>';
    return;
  }
  if (!list.length) {
    box.innerHTML = '<div class="empty-state">该位置没有符合当前类型筛选的文件，换个类型或点「全部」。</div>';
    return;
  }
  box.innerHTML = "";
  list.forEach((f) => {
    const ext = extOf(f.name);
    const meta = extMeta(ext);
    const item = document.createElement("div");
    item.className = "file-item";
    item.dataset.type = meta.key;
    item.innerHTML = `
      <input type="checkbox" class="f-check" value="${esc(f.file_id)}" />
      <span class="f-ico cat-${esc(meta.key)}" title="${esc(meta.label)}${ext ? " · ." + esc(ext) : ""}">${esc(ext ? ext.slice(0, 4) : "bin")}</span>
      <div class="fname" title="${esc(f.name)}">${esc(f.name)}</div>
      <div class="fmeta">${fmtSize(f.size)}${f.uploader_name ? " · " + esc(f.uploader_name) : ""}</div>`;
    box.appendChild(item);
  });
}

async function refreshFiles() {
  const gid = $("f-group").value;
  if (!gid) return;
  currentGroupId = gid;
  initTypeFilter(gid);
  try {
    const data = await bridge.apiGet("files", { gid, folder_id: currentFolderId || "", page: filePage, page_size: 50 });
    currentFolders = data.all_folders || data.folders || [];
    if (data.pages) {
      filePage = Math.max(1, Math.min(filePage, data.pages));
      $("f-page").textContent = "第 " + filePage + "/" + data.pages + " 页 · 共 " + (data.total || 0) + " 个";
    }

    const folderSel = $("f-folder");
    folderSel.innerHTML = '<option value="">根目录</option>';
    currentFolders.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.folder_id;
      opt.textContent = d.name + "（" + d.file_count + " 个文件）";
      folderSel.appendChild(opt);
    });
    folderSel.value = currentFolderId || "";
    if (currentFolderId && ![...folderSel.options].some((o) => o.value === currentFolderId)) {
      currentFolderId = "";
      folderSel.value = "";
    }

    const moveSel = $("f-move-target");
    moveSel.innerHTML = '<option value="">移动到…</option>';
    currentFolders.filter((d) => d.folder_id !== currentFolderId).forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.name;
      opt.dataset.id = d.folder_id;
      opt.textContent = d.name;
      moveSel.appendChild(opt);
    });

    currentFiles = data.files || [];
    renderFileList();
  } catch (e) {
    $("f-files").innerHTML = '<span class="badge warn">加载失败：' + esc(e.message) + "</span>";
  }
}

$("f-type-btn").addEventListener("click", () => {
  const bar = $("f-type-bar");
  if (!bar) return;
  bar.style.display = bar.style.display === "none" ? "" : "none";
  typeSummary();
});

$("f-group").addEventListener("change", () => { currentFolderId = ""; filePage = 1; refreshFiles(); });
$("f-folder").addEventListener("change", (e) => { currentFolderId = e.target.value; filePage = 1; refreshFiles(); });
$("f-refresh").addEventListener("click", refreshFiles);
$("f-prev").addEventListener("click", () => { if (filePage > 1) { filePage -= 1; refreshFiles(); } });
$("f-next").addEventListener("click", () => { filePage += 1; refreshFiles(); });
$("f-selectall").addEventListener("change", (e) => {
  document.querySelectorAll(".f-check").forEach((c) => (c.checked = e.target.checked));
});

$("f-del").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll(".f-check:checked")].map((c) => c.value);
  if (!ids.length) return toast("请先勾选文件");
  const fdelBtn = $("f-del");
  if (!armConfirm(fdelBtn, "⚠ 再点一次：删除 " + ids.length + " 个")) return;
  try {
    const r = await bridge.apiPost("delete", {
      group_id: currentGroupId,
      file_ids: ids,
      folder_id: currentFolderId || null,
    });
    if (fdelBtn._restore) fdelBtn._restore();
    toast("删除完成：成功 " + r.deleted + "，失败 " + r.failed);
    refreshFiles();
  } catch (e) {
    if (fdelBtn._restore) fdelBtn._restore();
    toast("删除失败：" + e.message, "error");
  }
});

$("f-move").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll(".f-check:checked")].map((c) => c.value);
  const sel = $("f-move-target");
  const target = sel.value;
  const targetId = sel.selectedOptions[0]?.dataset?.id || "";
  if (!ids.length) return toast("请先勾选文件");
  if (!target) return toast("请选择目标文件夹");
  try {
    const r = await bridge.apiPost("move", {
      group_id: currentGroupId,
      file_ids: ids,
      target_folder_name: target,
      target_folder_id: targetId,
      current_folder_id: currentFolderId,
    });
    toast("移动完成：成功 " + r.moved + "，失败 " + r.failed);
    refreshFiles();
  } catch (e) {
    toast("移动失败：" + e.message, "error");
  }
});

$("f-cleanup").addEventListener("click", async () => {
  if (!currentGroupId) return toast("请先选择群");
  try {
    const r = await bridge.apiPost("task", { kind: "cleanup", group_id: currentGroupId, actor: "panel" });
    toast("已提交整理任务 " + (r.task_id || "") + "，进度见「任务」页");
  } catch (e) {
    toast("触发失败：" + e.message, "error");
  }
});

$("f-newfolder-btn").addEventListener("click", async () => {
  const name = $("f-newfolder").value.trim();
  if (!name) return toast("请输入文件夹名");
  if (!currentGroupId) return toast("请先选择群");
  try {
    const r = await bridge.apiPost("create_folder", { group_id: currentGroupId, folder_name: name });
    if (r.created) toast("已创建文件夹：" + name);
    else toast("创建异常，可能已存在同名文件夹");
    $("f-newfolder").value = "";
    refreshFiles();
  } catch (e) {
    toast("创建失败：" + e.message, "error");
  }
});

// ---------- 关键词查文件 + 发送到群 ----------
async function searchFiles() {
  const gid = $("f-group").value;
  const kw = $("f-search").value.trim();
  if (!gid) return toast("请先选择群");
  if (!kw) return toast("请输入关键词");
  const box = $("f-search-res");
  const st = $("f-search-status");
  st.textContent = "搜索中…";
  try {
    const r = await bridge.apiGet("search", { gid, kw });
    st.textContent = "";
    box.style.display = "block";
    if (!r.results || !r.results.length) {
      box.innerHTML = '<div class="muted">未找到文件名含「' + esc(kw) + '」的文件（已扫根目录和所有子文件夹）。</div>';
      return;
    }
    let html = '<div class="muted" style="margin-bottom:6px;">🔍 找到 ' + r.results.length + ' 个含「' + esc(kw) + '」的文件：</div>';
    r.results.forEach((f, i) => {
      const ext = extOf(f.name);
      const meta = extMeta(ext);
      html += `<div class="file-item">
        <span class="f-ico cat-${esc(meta.key)}" title="${esc(meta.label)}${ext ? " · ." + esc(ext) : ""}">${esc(ext ? ext.slice(0, 4) : "bin")}</span>
        <div class="fname">${i + 1}. ${esc(f.name)}</div>
        <div class="fmeta">${fmtSize(f.size)} @ ${esc(f.folder || "根目录")}</div>
        <button class="s-send btn" data-i="${i}" style="margin-left:8px;">发送到群</button>
      </div>`;
    });
    box.innerHTML = html;
    box._results = r.results;
  } catch (e) {
    st.textContent = "";
    box.style.display = "block";
    box.innerHTML = '<span class="badge warn">搜索失败：' + esc(e.message) + "</span>";
  }
}
$("f-search-btn").addEventListener("click", searchFiles);
$("f-search").addEventListener("keydown", (e) => { if (e.key === "Enter") searchFiles(); });
$("f-search-res").addEventListener("click", async (e) => {
  const btn = e.target.closest(".s-send");
  if (!btn) return;
  const box = $("f-search-res");
  const results = box._results || [];
  const f = results[Number(btn.dataset.i)];
  if (!f) return;
  btn.disabled = true;
  btn.textContent = "发送中…";
  try {
    const r = await bridge.apiPost("task", {
      kind: "send_file",
      group_id: $("f-group").value,
      file_id: f.file_id,
      folder_id: f.folder_id || "",
      busid: f.busid || 0,
      name: f.name,
      actor: "panel",
    });
    btn.textContent = "已提交 ✓";
    toast("已提交发送任务 " + (r.task_id || "") + "，可在「任务」页看进度");
  } catch (err) {
    btn.textContent = "发送失败";
    btn.disabled = false;
    toast("发送失败：" + err.message, "error");
  }
});

// ---------- Help ----------
$("help-text").textContent = HELP_TEXT;

// 关闭/刷新页面前提醒规则未保存
window.addEventListener("beforeunload", (e) => {
  if (!isRulesDirty()) return;
  e.preventDefault();
  e.returnValue = "";
});

// ---------- 任务（v2.8.0） ----------
let taskTimer = null;
async function loadTasksTab() {
  clearTimeout(taskTimer);
  const box = $("t-list");
  box.innerHTML = '<div class="muted">加载任务中…</div>';
  try {
    const d = await bridge.apiGet("tasks", { limit: 50 });
    const rows = d.tasks || [];
    $("t-stats").textContent = "运行中 " + ((d.stats && d.stats.running) || 0) + " / 历史 " + ((d.stats && d.stats.history) || 0);
    if (!rows.length) { box.innerHTML = '<div class="empty-state">暂无任务。面板「文件管理 → 查重/扫描」或发文件会创建任务。</div>'; return; }
    box.innerHTML = rows.map((t) => {
      const p = Number(t.progress || 0);
      const status = t.status === "running" ? "运行中" : t.status === "done" ? "完成" : t.status === "failed" ? "失败" : t.status === "cancelled" ? "已取消" : t.status;
      const color = t.status === "failed" ? "var(--danger)" : t.status === "done" ? "var(--success)" : "var(--accent)";
      return '<div class="section" style="padding:10px 12px;margin-bottom:8px;">'
        + '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">'
        + '<b>' + esc(t.kind) + '</b><span class="hint">群 ' + esc(t.group) + ' · ' + esc(status) + ' · ' + p + '%</span>'
        + '<span class="hint" style="margin-left:auto;">' + esc(t.message || "") + '</span>'
        + (t.status === "running" ? '<button class="btn btn-sm t-cancel" data-id="' + esc(t.id) + '">取消</button>' : '')
        + '</div>'
        + '<div style="height:6px;background:var(--border);border-radius:3px;margin-top:8px;overflow:hidden;">'
        + '<div style="height:100%;width:' + Math.max(0, Math.min(100, p)) + '%;background:' + color + ';"></div></div>'
        + (t.error ? '<div class="hint" style="color:var(--danger);margin-top:4px;">' + esc(t.error) + '</div>' : '')
        + '</div>';
    }).join("");
    box.querySelectorAll(".t-cancel").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try { await bridge.apiPost("task_cancel", { id: b.dataset.id }); toast("已请求取消"); loadTasksTab(); }
      catch (e) { toast("取消失败：" + e.message, "error"); b.disabled = false; }
    }));
    if (rows.some((t) => t.status === "running" || t.status === "cancelling")) {
      taskTimer = setTimeout(() => {
        const pane = document.getElementById("tab-tasks");
        if (pane && pane.classList.contains("active")) loadTasksTab();
      }, 3000);
    }
  } catch (e) {
    box.innerHTML = '<div class="muted" style="color:var(--danger);">加载失败：' + esc(e.message) + "</div>";
  }
}

// ---------- 审计 / 回收站（v2.8.0） ----------
async function loadAuditTab() {
  try {
    const g = await bridge.apiGet("groups");
    const sel = $("a-group");
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部群</option>' + (g.groups || []).map((x) =>
      '<option value="' + esc(String(x.group_id)) + '">' + esc(x.name || x.group_id) + "（" + esc(String(x.group_id)) + "）</option>"
    ).join("");
    sel.value = cur;
  } catch (e) {}
  loadAuditRows();
  loadRecycleRows();
  loadOps();
}

async function loadAuditRows() {
  const box = $("a-list");
  box.innerHTML = '<div class="muted">加载审计中…</div>';
  try {
    const d = await bridge.apiGet("audit", { limit: 200, gid: $("a-group").value, action: $("a-action").value.trim() });
    $("a-stats").textContent = "共 " + ((d.rows || []).length) + " 条 · 文件 " + Math.round(((d.stats && d.stats.size) || 0) / 1024) + "KB";
    const rows = d.rows || [];
    if (!rows.length) { box.innerHTML = '<div class="empty-state">暂无审计记录</div>'; return; }
    box.innerHTML = '<div style="max-height:420px;overflow:auto;font-size:12px;">' + rows.map((r) =>
      '<div style="padding:5px 4px;border-bottom:1px solid var(--border);">'
      + '<span class="hint">' + esc(r.t) + '</span> '
      + '<b>' + esc(r.actor) + '</b> '
      + '<span style="color:' + (r.result === "ok" ? "var(--success)" : "var(--danger)") + ';">' + esc(r.action) + '</span> '
      + esc(r.target || "") + ' <span class="hint">' + esc(r.detail || "") + "</span></div>"
    ).join("") + "</div>";
  } catch (e) {
    box.innerHTML = '<div class="muted" style="color:var(--danger);">加载失败：' + esc(e.message) + "</div>";
  }
}

async function loadRecycleRows() {
  const box = $("r-list");
  box.innerHTML = '<div class="muted">加载回收站中…</div>';
  try {
    const d = await bridge.apiGet("recycle", { limit: 200 });
    const rows = d.items || [];
    $("r-stats").textContent = "共 " + rows.length + " 条";
    if (!rows.length) { box.innerHTML = '<div class="empty-state">回收站为空</div>'; return; }
    box.innerHTML = '<div style="max-height:320px;overflow:auto;">' + rows.map((it) =>
      '<div style="display:flex;gap:8px;align-items:center;padding:6px 4px;border-bottom:1px solid var(--border);">'
      + '<span style="flex:1;">' + esc(it.name) + ' <span class="hint">原位置 ' + esc(it.from_label || "根目录") + " · 群 " + esc(it.group) + "</span></span>"
      + '<button class="btn btn-sm r-restore" data-id="' + esc(it.id) + '">还原</button></div>'
    ).join("") + "</div>";
    box.querySelectorAll(".r-restore").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try { await bridge.apiPost("recycle_restore", { id: b.dataset.id }); toast("已还原"); loadRecycleRows(); }
      catch (e) { toast("还原失败：" + e.message, "error"); b.disabled = false; }
    }));
  } catch (e) {
    box.innerHTML = '<div class="muted" style="color:var(--danger);">加载失败：' + esc(e.message) + "</div>";
  }
}

async function loadOps() {
  try {
    const d = await bridge.apiGet("ops");
    const o = d.ops || {};
    $("o-audit-days").value = o.audit_keep_days != null ? o.audit_keep_days : 30;
    $("o-recycle-days").value = o.recycle_keep_days != null ? o.recycle_keep_days : 30;
    $("o-audit-en").checked = o.audit_enabled !== false;
  } catch (e) {}
}

document.addEventListener("click", (ev) => {
  const t = ev.target;
  if (t && t.id === "t-refresh") loadTasksTab();
  if (t && t.id === "a-refresh") loadAuditRows();
  if (t && t.id === "r-refresh") loadRecycleRows();
  if (t && t.id === "r-prune") { bridge.apiPost("recycle_delete", {}).then(() => { toast("已清理过期索引"); loadRecycleRows(); }).catch((e) => toast("清理失败：" + e.message, "error")); }
  if (t && t.id === "o-save") {
    bridge.apiPost("ops", {
      audit_enabled: $("o-audit-en").checked,
      audit_keep_days: Number($("o-audit-days").value || 30),
      recycle_keep_days: Number($("o-recycle-days").value || 30),
    }).then(() => { toast("运维设置已保存"); $("o-msg").textContent = "已保存"; }).catch((e) => toast("保存失败：" + e.message, "error"));
  }
});
["a-group", "a-action"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("change", loadAuditRows);
});

// ---------- init ----------
(async function init() {
  try {
    await bridge.ready();
    loadOverview();
  } catch (e) {
    console.error("bridge init failed", e);
  }
})();
