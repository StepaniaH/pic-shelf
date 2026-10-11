// 管理页：用户 / 用户组 / 库（注册、授权、扫描）。
import { api } from "./api.js";
import { escapeHtml, toast, uiPrompt, uiConfirm, state } from "./app.js";

const CAP_LABEL = {
  view: "查看", rate: "评分", comment: "评论", note: "笔记",
  tag_edit: "标签/专辑编辑", manage_library: "库管理", admin: "管理权限",
};
const ALL_CAPS = Object.keys(CAP_LABEL);

export async function renderAdmin(view) {
  if (!state.user.is_admin) {
    view.innerHTML = `<p class="empty">需要管理员权限。</p>`;
    return;
  }
  view.innerHTML = `
    <div class="page-head"><h1>管理</h1></div>
    <div class="admin-tabs">
      <button class="btn" data-tab="users">用户</button>
      <button class="btn" data-tab="groups">用户组</button>
      <button class="btn" data-tab="import">导入</button>
      <button class="btn" data-tab="libraries">库</button>
    </div>
    <div id="admin-body"></div>`;
  const body = view.querySelector("#admin-body");
  const setTab = async (t) => {
    view.querySelectorAll(".admin-tabs .btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
    if (t === "users") await renderUsers(body);
    else if (t === "groups") await renderGroups(body, () => setTab("groups"));
    else if (t === "import") await renderImport(body);
    else await renderLibraries(body);
  };
  view.querySelectorAll(".admin-tabs .btn").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  await setTab("users");
}

// ---------------------------------------------------------------- 用户

async function renderUsers(body) {
  const [users, groups] = await Promise.all([api("/users"), api("/groups")]);
  body.innerHTML = `
    <h2>用户</h2>
    <div class="form-row">
      <input id="nu-name" placeholder="用户名" style="width:150px">
      <input id="nu-pass" type="password" placeholder="密码（≥6 位）" style="width:170px">
      <label class="muted"><input type="checkbox" id="nu-admin"> 管理员</label>
      <button class="btn primary" id="nu-create">创建用户</button>
    </div>
    <div class="table-wrap">
    <table class="data"><thead><tr><th>ID</th><th>用户名</th><th>状态</th><th>用户组</th><th>操作</th></tr></thead>
    <tbody>${users.map((u) => `
      <tr data-uid="${u.id}">
        <td>${u.id}</td><td>${escapeHtml(u.username)}</td>
        <td>${u.is_admin ? "管理员" : "普通用户"}${u.disabled ? " · 已禁用" : ""}</td>
        <td>${u.groups.map((g) => escapeHtml(g.name)).join("、") || "—"}</td>
        <td>
          <button class="btn small" data-act="admin">${u.is_admin ? "取消管理员" : "设为管理员"}</button>
          <button class="btn small" data-act="disable">${u.disabled ? "启用" : "禁用"}</button>
          <button class="btn small" data-act="passwd">重置密码</button>
          <button class="btn small" data-act="groups">用户组</button>
        </td>
      </tr>`).join("")}
    </tbody></table>
    </div>`;

  body.querySelector("#nu-create").addEventListener("click", async () => {
    const username = body.querySelector("#nu-name").value.trim();
    const password = body.querySelector("#nu-pass").value;
    const is_admin = body.querySelector("#nu-admin").checked;
    try {
      await api("/users", { method: "POST", body: { username, password, is_admin } });
      toast(`已创建用户：${username}`, "ok");
      await renderUsers(body);
    } catch (e) { toast(e.message, "error"); }
  });

  body.querySelectorAll("tr[data-uid]").forEach((tr) => {
    const u = users.find((x) => String(x.id) === tr.dataset.uid);
    tr.querySelector('[data-act="admin"]').addEventListener("click", async () => {
      try {
        await api(`/users/${u.id}`, { method: "PATCH", body: { is_admin: !u.is_admin } });
        await renderUsers(body);
      } catch (e) { toast(e.message, "error"); }
    });
    tr.querySelector('[data-act="disable"]').addEventListener("click", async () => {
      try {
        await api(`/users/${u.id}`, { method: "PATCH", body: { disabled: !u.disabled } });
        await renderUsers(body);
      } catch (e) { toast(e.message, "error"); }
    });
    tr.querySelector('[data-act="passwd"]').addEventListener("click", async () => {
      const pw = await uiPrompt(`为 ${u.username} 设置新密码（≥6 位）`, { type: "password" });
      if (!pw) return;
      try {
        await api(`/users/${u.id}`, { method: "PATCH", body: { password: pw } });
        toast("密码已重置", "ok");
      } catch (e) { toast(e.message, "error"); }
    });
    tr.querySelector('[data-act="groups"]').addEventListener("click", () => {
      const memberIds = new Set(u.groups.map((g) => g.id));
      const html = `
        <div class="checkbox-row">${groups.map((g) => `
          <label><input type="checkbox" value="${g.id}" ${memberIds.has(g.id) ? "checked" : ""}> ${escapeHtml(g.name)}</label>`).join("")}</div>
        <div class="modal-actions"><button class="btn primary" id="ug-save">保存</button><button class="btn" data-close>取消</button></div>`;
      import("./app.js").then(({ showModal }) => {
        showModal({
          title: `用户组：${u.username}`,
          bodyHtml: html,
          onMount(el, close) {
            el.querySelector("#ug-save").addEventListener("click", async () => {
              const group_ids = [...el.querySelectorAll("input:checked")].map((i) => Number(i.value));
              try {
                await api(`/users/${u.id}/groups`, { method: "PUT", body: { group_ids } });
                toast("用户组已更新", "ok");
                close();
                await renderUsers(body);
              } catch (e) { toast(e.message, "error"); }
            });
          },
        });
      });
    });
  });
}

// ---------------------------------------------------------------- 用户组

async function renderGroups(body) {
  const [groups, users] = await Promise.all([api("/groups"), api("/users")]);
  body.innerHTML = `
    <h2>用户组</h2>
    <div class="form-row">
      <input id="ng-name" placeholder="组名" style="width:150px">
      <input id="ng-desc" placeholder="描述（可选）" style="width:230px">
      <button class="btn primary" id="ng-create">创建组</button>
    </div>
    ${groups.map((g) => `
      <div class="grant-item" data-gid="${g.id}" style="margin-bottom:12px">
        <div class="form-row"><b>${escapeHtml(g.name)}</b>
          ${g.is_system ? `<span class="pill">系统组</span>` : ""}
          ${g.description ? `<span class="muted">${escapeHtml(g.description)}</span>` : ""}</div>
        <div class="checkbox-row">
          ${ALL_CAPS.map((c) => `<label><input type="checkbox" data-cap="${c}"
            ${g.capabilities.includes(c) ? "checked" : ""} ${g.is_system ? "disabled" : ""}> ${CAP_LABEL[c]}</label>`).join("")}
        </div>
        <div class="form-row">成员：${g.members.map((m) => `
          <span class="pill">${escapeHtml(m.username)}<button class="icon-btn" data-rm-user="${m.id}" title="移出">✕</button></span>`).join("") || `<span class="muted">（无）</span>`}
          <input class="add-user" list="user-dl" placeholder="加成员" style="width:130px">
          <button class="btn small" data-add-user>加入</button>
        </div>
        ${g.is_system ? "" : `<div class="form-row">
          <button class="btn small" data-save-caps>保存能力</button>
          <button class="btn small danger" data-del-group>删除组</button></div>`}
      </div>`).join("")}
    <datalist id="user-dl">${users.map((u) => `<option value="${escapeHtml(u.username)}">`).join("")}</datalist>`;

  body.querySelector("#ng-create").addEventListener("click", async () => {
    const name = body.querySelector("#ng-name").value.trim();
    const description = body.querySelector("#ng-desc").value.trim() || null;
    try {
      await api("/groups", { method: "POST", body: { name, description } });
      toast(`已创建组：${name}`, "ok");
      await renderGroups(body);
    } catch (e) { toast(e.message, "error"); }
  });

  body.querySelectorAll(".grant-item[data-gid]").forEach((card) => {
    const g = groups.find((x) => String(x.id) === card.dataset.gid);
    card.querySelectorAll("[data-rm-user]").forEach((btn) => btn.addEventListener("click", async () => {
      const remaining = g.members.map((m) => m.id).filter((id) => id !== Number(btn.dataset.rmUser));
      try {
        await api(`/users/${btn.dataset.rmUser}/groups`, { method: "PUT", body: { group_ids: remaining } });
        await renderGroups(body);
      } catch (e) { toast(e.message, "error"); }
    }));
    card.querySelector("[data-add-user]").addEventListener("click", async () => {
      const name = card.querySelector(".add-user").value.trim();
      const u = users.find((x) => x.username === name);
      if (!u) { toast("找不到该用户", "error"); return; }
      const ids = [...new Set([...g.members.map((m) => m.id), u.id])];
      try {
        await api(`/users/${u.id}/groups`, { method: "PUT", body: { group_ids: ids } });
        await renderGroups(body);
      } catch (e) { toast(e.message, "error"); }
    });
    const saveBtn = card.querySelector("[data-save-caps]");
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      const caps = [...card.querySelectorAll("input[data-cap]:checked")].map((i) => i.dataset.cap);
      try {
        await api(`/groups/${g.id}`, { method: "PATCH", body: { capabilities: caps } });
        toast("已保存", "ok");
      } catch (e) { toast(e.message, "error"); }
    });
    const delBtn = card.querySelector("[data-del-group]");
    if (delBtn) delBtn.addEventListener("click", async () => {
      if (!(await uiConfirm("删除组", `确定删除组「${g.name}」？成员关系会一并移除。`))) return;
      try {
        await api(`/groups/${g.id}`, { method: "DELETE" });
        toast("已删除", "ok");
        await renderGroups(body);
      } catch (e) { toast(e.message, "error"); }
    });
  });
}

// ---------------------------------------------------------------- 库

async function renderLibraries(body) {
  const [libs, groups] = await Promise.all([api("/libraries"), api("/groups")]);
  body.innerHTML = `
    <h2>库</h2>
    <div class="form-row">
      <input id="nl-name" placeholder="库名" style="width:140px">
      <input id="nl-path" placeholder="/绝对路径/到/图片目录" style="width:320px">
      <input id="nl-desc" placeholder="描述（可选）">
      <button class="btn primary" id="nl-create">注册库</button>
    </div>
    ${libs.map((l) => `
      <div class="grant-item" style="margin-bottom:12px">
        <div class="form-row">
          <b>${escapeHtml(l.name)}</b>
          <span class="muted">${escapeHtml(l.root_path)}</span>
          <span class="pill">${l.image_count} 张 · ${l.album_count} 专辑</span>
          <button class="btn small" data-scan="${l.id}">扫描更新</button>
        </div>
        <div class="grant-grid" data-grants="${l.id}">加载授权…</div>
      </div>`).join("") || `<p class="empty">还没有库。在上方注册图片目录。</p>`}`;

  body.querySelector("#nl-create").addEventListener("click", async () => {
    const name = body.querySelector("#nl-name").value.trim();
    const root_path = body.querySelector("#nl-path").value.trim();
    const description = body.querySelector("#nl-desc").value.trim() || null;
    try {
      await api("/libraries", { method: "POST", body: { name, root_path, description } });
      toast(`已注册库：${name}`, "ok");
      await renderLibraries(body);
    } catch (e) { toast(e.message, "error"); }
  });

  body.querySelectorAll("[data-scan]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "扫描中…";
    try {
      const s = await api(`/libraries/${btn.dataset.scan}/scan`, { method: "POST" });
      toast(`扫描完成：新增 ${s.added}，更新 ${s.updated}，移动 ${s.moved}，缺失 ${s.missing}`, "ok");
    } catch (e) { toast(e.message, "error"); }
    await renderLibraries(body);
  }));

  // 授权矩阵
  body.querySelectorAll("[data-grants]").forEach(async (el) => {
    const lid = el.dataset.grants;
    try {
      const grants = await api(`/libraries/${lid}/grants`);
      const byGroup = Object.fromEntries(grants.map((g) => [String(g.group_id), g.access]));
      el.innerHTML = groups.map((g) => `
        <label class="grant-item" style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <span>${escapeHtml(g.name)}</span>
          <select data-grant-gid="${g.id}">
            <option value="" ${!byGroup[g.id] ? "selected" : ""}>无</option>
            <option value="view" ${byGroup[g.id] === "view" ? "selected" : ""}>查看</option>
            <option value="edit" ${byGroup[g.id] === "edit" ? "selected" : ""}>编辑</option>
            <option value="manage" ${byGroup[g.id] === "manage" ? "selected" : ""}>管理</option>
          </select>
        </label>`).join("");
      el.querySelectorAll("select[data-grant-gid]").forEach((sel) => sel.addEventListener("change", async () => {
        try {
          await api(`/libraries/${lid}/grants`, {
            method: "PUT",
            body: { group_id: Number(sel.dataset.grantGid), access: sel.value || null },
          });
          toast("授权已更新", "ok");
        } catch (e) { toast(e.message, "error"); }
      }));
    } catch (e) {
      el.textContent = "(无权限查看授权)";
    }
  });
}

// ---------------------------------------------------------------- 托管导入（v0.2）

async function renderImport(body) {
  const libs = await api("/libraries");
  body.innerHTML = `
    <h2>托管导入</h2>
    <p class="hint">把外部目录的图片导入到托管的库。默认「硬链接」：原文件不动、库内出现规范化命名、同盘零额外空间（跨盘自动降级为复制）。先「预演」，确认无误后再「执行」。</p>
    <div class="form-row">
      <input id="imp-src" placeholder="源目录（绝对路径）" style="min-width:300px;flex:1">
      <select id="imp-lib">${libs.map((l) => `<option value="${l.id}">${escapeHtml(l.name)}（${l.image_count} 张）</option>`).join("")}</select>
      <select id="imp-mode">
        <option value="hardlink">硬链接（推荐 · 原文件不动）</option>
        <option value="copy">复制（原库留档）</option>
        <option value="move">移动（整理 · 原位置不保留）</option>
      </select>
      <label class="muted"><input type="checkbox" id="imp-rename" checked> 文件名规范化</label>
    </div>
    <div class="form-row">
      <button class="btn primary" id="imp-plan">预演</button>
      <button class="btn danger" id="imp-exec" disabled>执行</button>
      ${libs.length ? "" : `<span class="muted">提示：先到「库」标签页注册一个目标库。</span>`}
    </div>
    <div id="imp-out" class="muted">尚未预演。</div>`;

  let plan = null;
  const out = body.querySelector("#imp-out");
  const execBtn = body.querySelector("#imp-exec");
  const payload = () => ({
    source_dir: body.querySelector("#imp-src").value.trim(),
    library_id: Number(body.querySelector("#imp-lib").value),
    mode: body.querySelector("#imp-mode").value,
    rename: body.querySelector("#imp-rename").checked,
  });

  body.querySelector("#imp-plan").addEventListener("click", async () => {
    const p = payload();
    if (!p.source_dir) { toast("请填写源目录", "error"); return; }
    const btn = body.querySelector("#imp-plan");
    btn.disabled = true;
    btn.textContent = "预演中…";
    try {
      plan = await api("/import/plan", { method: "POST", body: p });
      renderPlan(plan, out);
      const newN = plan.stats.hardlink + plan.stats.copy + plan.stats.move;
      execBtn.disabled = newN === 0;
    } catch (e) {
      toast(e.message, "error");
      out.textContent = "预演失败：" + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "预演";
    }
  });

  execBtn.addEventListener("click", async () => {
    const p = payload();
    const newN = plan ? plan.stats.hardlink + plan.stats.copy + plan.stats.move : 0;
    const modeText = { hardlink: "硬链接", copy: "复制", move: "移动" }[p.mode] || p.mode;
    const warn = p.mode === "move" ? "注意：移动模式下原位置的文件将被移走，不再保留。" : "";
    if (!(await uiConfirm("确认执行导入", `将处理 ${newN} 个文件（模式：${modeText}）。${warn} 将写入目标库目录。`))) return;
    execBtn.disabled = true;
    execBtn.textContent = "执行中…";
    try {
      const r = await api("/import/execute", { method: "POST", body: { ...p, confirm: true } });
      toast(`执行完成：硬链 ${r.done.hardlink}，复制 ${r.done.copy}，移动 ${r.done.move}，跳过 ${r.done.skip}，错误 ${r.done.error}`, "ok");
      renderDone(r, out);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      execBtn.disabled = false;
      execBtn.textContent = "执行";
    }
  });
}

function renderPlan(plan, out) {
  const s = plan.stats;
  const labels = { hardlink: "硬链", copy: "复制", move: "移动", skip: "跳过", error: "错误" };
  const rows = plan.items.slice(0, 100).map((it) => `
    <tr><td>${labels[it.action] || it.action}</td>
      <td class="src">${escapeHtml(it.src)}</td>
      <td class="dst">${escapeHtml(it.dst || "—")}</td>
      <td>${escapeHtml(it.reason || "")}</td></tr>`).join("");
  out.innerHTML = `
    <p>共 <b>${plan.items_total}</b> 个文件：新导入 <b>${s.hardlink + s.copy + s.move}</b>，跳过 <b>${s.skip}</b>（重复/已存在），错误 <b>${s.error}</b>。
    ${plan.items_truncated ? `（列表只显示前 ${plan.items.length} 项）` : ""}</p>
    <div class="table-wrap"><table class="data imp-table"><thead><tr><th>动作</th><th>源</th><th>目标</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint">以上为预演结果，未改动任何文件。确认后点「执行」。</p>`;
}

function renderDone(r, out) {
  const d = r.done;
  out.innerHTML = `<p>执行完成：硬链 <b>${d.hardlink}</b>，复制 <b>${d.copy}</b>，移动 <b>${d.move}</b>，跳过 <b>${d.skip}</b>，错误 <b>${d.error}</b>。
  ${r.scan ? `已扫描入库：新增 <b>${r.scan.added}</b> 张。` : ""}</p>
  ${d.errors && d.errors.length ? `<details><summary>错误明细（${d.errors.length}）</summary><pre>${escapeHtml(d.errors.join("\n"))}</pre></details>` : ""}`;
}
