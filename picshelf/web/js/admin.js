// 管理页：用户 / 用户组 / 库（注册、授权、扫描）。
import { api } from "./api.js";
import { escapeHtml, toast, uiPrompt, uiConfirm, state } from "./app.js";
import { t } from "./i18n.js";
import {renderJobs} from "./jobs.js";
import {renderLibraries} from "./libraries.js";
import {openImport} from "./importer.js";

const CAP_LABEL = {
  view: t("查看"), rate: t("评分"), comment: t("评论"), note: t("笔记"),
  tag_edit: t("标签/专辑编辑"), manage_library: t("库管理"), admin: t("管理权限"),
};
const ALL_CAPS = Object.keys(CAP_LABEL);

export async function renderAdmin(view, params = new URLSearchParams()) {
  const tabs = state.user.is_admin
    ? [["libraries", "图库"], ["jobs", "后台任务"], ["users", "用户"], ["groups", "用户组"]]
    : [["jobs", "后台任务"]];
  const selected = tabs.some(([key]) => key === params.get("tab")) ? params.get("tab") : tabs[0][0];
  view.innerHTML = `<div class="page-head"><h1>${t(state.user.is_admin ? "管理" : "图库操作")}</h1></div>
    <nav class="admin-tabs" aria-label="${t("管理")}">${tabs.map(([key, label]) =>
      `<a class="btn ${key === selected ? "active" : ""}" data-tab="${key}" href="#/admin?tab=${key}" ${key === selected ? 'aria-current="page"' : ""}>${t(label)}</a>`).join("")}</nav>
    <div id="admin-body"></div>`;
  const body = view.querySelector("#admin-body");
  if (selected === "users") await renderUsers(body);
  else if (selected === "groups") await renderGroups(body);
  else if (selected === "jobs") await renderJobs(body, {jobId:params.get("job"), offset:Math.max(0, Number(params.get("offset")) || 0)});
  else await renderLibraries(body, {add: params.get("add") === "1", mode: params.get("mode")});
  if (params.get('tab') === 'import' && body.isConnected) openImport({libraryId:params.get('library')});
}

// ---------------------------------------------------------------- 用户

async function renderUsers(body) {
  const [users, groups] = await Promise.all([api("/users"), api("/groups")]);
  body.innerHTML = t("\n    <h2>用户</h2>\n    <div class=\"form-row\">\n      <input id=\"nu-name\" placeholder=\"用户名\" style=\"width:150px\">\n      <input id=\"nu-pass\" type=\"password\" placeholder=\"密码（≥6 位）\" style=\"width:170px\">\n      <label class=\"muted\"><input type=\"checkbox\" id=\"nu-admin\"> 管理员</label>\n      <button class=\"btn primary\" id=\"nu-create\">创建用户</button>\n    </div>\n    <div class=\"table-wrap\">\n    <table class=\"data\"><thead><tr><th>ID</th><th>用户名</th><th>状态</th><th>用户组</th><th>操作</th></tr></thead>\n    <tbody>{v0}\n    </tbody></table>\n    </div>", {v0: users.map((u) => `
      <tr data-uid="${u.id}">
        <td>${u.id}</td><td>${escapeHtml(u.username)}</td>
        <td>${u.is_admin ? t("管理员") : t("普通用户")}${u.disabled ? t(" · 已禁用") : ""}</td>
        <td>${u.groups.map((g) => escapeHtml(g.name)).join("、") || "—"}</td>
        <td>
          <button class="btn small" data-act="admin">${u.is_admin ? t("取消管理员") : t("设为管理员")}</button>
          <button class="btn small" data-act="disable">${u.disabled ? t("启用") : t("禁用")}</button>
          <button class="btn small" data-act="passwd">${t("重置密码")}</button>
          <button class="btn small" data-act="groups">${t("用户组")}</button>
        </td>
      </tr>`).join("")});

  body.querySelector("#nu-create").addEventListener("click", async () => {
    const username = body.querySelector("#nu-name").value.trim();
    const password = body.querySelector("#nu-pass").value;
    const is_admin = body.querySelector("#nu-admin").checked;
    try {
      await api("/users", { method: "POST", body: { username, password, is_admin } });
      toast(t("已创建用户：{username}", {username: username}), "ok");
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
      const pw = await uiPrompt(t("为 {v0} 设置新密码（≥6 位）", {v0: u.username}), { type: "password" });
      if (!pw) return;
      try {
        await api(`/users/${u.id}`, { method: "PATCH", body: { password: pw } });
        toast(t("密码已重置"), "ok");
      } catch (e) { toast(e.message, "error"); }
    });
    tr.querySelector('[data-act="groups"]').addEventListener("click", () => {
      const memberIds = new Set(u.groups.map((g) => g.id));
      const html = t("\n        <div class=\"checkbox-row\">{v0}</div>\n        <div class=\"modal-actions\"><button class=\"btn primary\" id=\"ug-save\">保存</button><button class=\"btn\" data-close>取消</button></div>", {v0: groups.map((g) => `
          <label><input type="checkbox" value="${g.id}" ${memberIds.has(g.id) ? "checked" : ""}> ${escapeHtml(g.name)}</label>`).join("")});
      import("./app.js").then(({ showModal }) => {
        showModal({
          title: t("用户组：{v0}", {v0: u.username}),
          bodyHtml: html,
          onMount(el, close) {
            el.querySelector("#ug-save").addEventListener("click", async () => {
              const group_ids = [...el.querySelectorAll("input:checked")].map((i) => Number(i.value));
              try {
                await api(`/users/${u.id}/groups`, { method: "PUT", body: { group_ids } });
                toast(t("用户组已更新"), "ok");
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
  body.innerHTML = t("\n    <h2>用户组</h2>\n    <div class=\"form-row\">\n      <input id=\"ng-name\" placeholder=\"组名\" style=\"width:150px\">\n      <input id=\"ng-desc\" placeholder=\"描述（可选）\" style=\"width:230px\">\n      <button class=\"btn primary\" id=\"ng-create\">创建组</button>\n    </div>\n    {v0}\n    <datalist id=\"user-dl\">{v1}</datalist>", {v0: groups.map((g) => `
      <div class="grant-item" data-gid="${g.id}" style="margin-bottom:12px">
        <div class="form-row"><b>${escapeHtml(g.name)}</b>
          ${g.is_system ? `<span class="pill">${t("系统组")}</span>` : ""}
          ${g.description ? `<span class="muted">${escapeHtml(g.description)}</span>` : ""}</div>
        <div class="checkbox-row">
          ${ALL_CAPS.map((c) => `<label><input type="checkbox" data-cap="${c}"
            ${g.capabilities.includes(c) ? "checked" : ""} ${g.is_system ? "disabled" : ""}> ${CAP_LABEL[c]}</label>`).join("")}
        </div>
        <div class="form-row">${t("成员：")}${g.members.map((m) => `
          <span class="pill">${escapeHtml(m.username)}<button class="icon-btn" data-rm-user="${m.id}" title="${t("移出")}">✕</button></span>`).join("") || `<span class="muted">${t("（无）")}</span>`}
          <input class="add-user" list="user-dl" placeholder="${t("添加成员")}" style="width:130px">
          <button class="btn small" data-add-user>${t("加入")}</button>
        </div>
        ${g.is_system ? "" : `<div class="form-row">
          <button class="btn small" data-save-caps>${t("保存权限")}</button>
          <button class="btn small danger" data-del-group>${t("删除组")}</button></div>`}
      </div>`).join(""), v1: users.map((u) => `<option value="${escapeHtml(u.username)}">`).join("")});

  body.querySelector("#ng-create").addEventListener("click", async () => {
    const name = body.querySelector("#ng-name").value.trim();
    const description = body.querySelector("#ng-desc").value.trim() || null;
    try {
      await api("/groups", { method: "POST", body: { name, description } });
      toast(t("已创建组：{name}", {name: name}), "ok");
      await renderGroups(body);
    } catch (e) { toast(e.message, "error"); }
  });

  body.querySelectorAll(".grant-item[data-gid]").forEach((card) => {
    const g = groups.find((x) => String(x.id) === card.dataset.gid);
    card.querySelectorAll("[data-rm-user]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api(`/groups/${g.id}/members/${btn.dataset.rmUser}`, { method: "DELETE" });
        await renderGroups(body);
      } catch (e) { toast(e.message, "error"); }
    }));
    card.querySelector("[data-add-user]").addEventListener("click", async () => {
      const name = card.querySelector(".add-user").value.trim();
      const u = users.find((x) => x.username === name);
      if (!u) { toast(t("找不到该用户"), "error"); return; }
      try {
        await api(`/groups/${g.id}/members/${u.id}`, { method: "PUT" });
        await renderGroups(body);
      } catch (e) { toast(e.message, "error"); }
    });
    const saveBtn = card.querySelector("[data-save-caps]");
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      const caps = [...card.querySelectorAll("input[data-cap]:checked")].map((i) => i.dataset.cap);
      try {
        await api(`/groups/${g.id}`, { method: "PATCH", body: { capabilities: caps } });
        toast(t("已保存"), "ok");
      } catch (e) { toast(e.message, "error"); }
    });
    const delBtn = card.querySelector("[data-del-group]");
    if (delBtn) delBtn.addEventListener("click", async () => {
      if (!(await uiConfirm(t("删除组"), t("确定删除组「{v0}」？成员关系会一并移除。", {v0: g.name})))) return;
      try {
        await api(`/groups/${g.id}`, { method: "DELETE" });
        toast(t("已删除"), "ok");
        await renderGroups(body);
      } catch (e) { toast(e.message, "error"); }
    });
  });
}
