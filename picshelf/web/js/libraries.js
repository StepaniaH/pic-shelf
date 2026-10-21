import {api} from "./api.js";
import {escapeHtml, toast, showModal} from "./app.js";
import {t} from "./i18n.js";
import {jobKey, scanSummary, watchJob} from "./jobs.js";
import {renderImport, openImport} from "./importer.js";

export function openLibraryForm(defaultMode = "managed", onCreated = () => {}, {files} = {}) {
  let created = null, importer;
  return showModal({
    title: t("添加图库"),
    onClose() { importer?.dispose(); if (created) onCreated(); },
    bodyHtml: `<form class="stack-form" id="nl-form" data-library-form>
      <label>${t("名称")}<input id="nl-name" required maxlength="200" autocomplete="off"></label>
      <p class="error" role="alert" data-library-error hidden></p>
    </form><div data-library-import></div>
      <details class="stack-form collection-options" data-library-options><summary>${t("高级选项")}</summary>
      <label>${t("用途")}<select id="nl-mode" form="nl-form"><option value="indexed">${t("浏览已有目录")}</option><option value="managed">${t("接收导入")}</option></select></label>
      <label><span id="nl-path-label">${t("服务端目录")}</span><input id="nl-path" form="nl-form" placeholder="/path/to/images" required spellcheck="false" aria-describedby="library-path-hint"></label>
      <p class="hint" id="library-path-hint"></p>
      <label>${t("描述（可选）")}<input id="nl-desc" form="nl-form"></label>
      </details>
      <div data-library-progress></div>
      <div class="modal-actions" data-library-actions><button type="button" class="btn" data-close>${t("取消")}</button><button type="submit" form="nl-form" class="btn primary" id="nl-create">${t("预览导入")}</button></div>`,
    onMount(el, close) {
      const form = el.querySelector("form"), submit = el.querySelector("#nl-create");
      const mode = el.querySelector("#nl-mode"), error = el.querySelector("[data-library-error]");
      const importBody = el.querySelector('[data-library-import]');
      el.classList.add('import-modal');
      let busy = false;
      const save = async () => {
        if (created) return created;
        if (!form.reportValidity()) return null;
        const values = {name: el.querySelector("#nl-name").value.trim(), root_path: el.querySelector("#nl-path").value.trim(),
          description: el.querySelector("#nl-desc").value.trim() || null, mode: mode.value};
        const fields = [...form.elements].filter(input => input.matches('input, select'));
        fields.forEach(input => { input.disabled = true; });
        try { created = await api('/libraries', {method:'POST', body:values}); }
        catch (failure) { fields.forEach(input => { input.disabled = false; }); throw failure; }
        return created;
      };
      const loadImporter = async () => {
        importer = await renderImport(importBody, '0', {locked:true, files, primaryButton:submit, settingsHost:el.querySelector('[data-library-options]'),
          libraries:[{id:0,name:'',image_count:0,mode:'managed'}], close,
          getSourceLibraryId:() => created?.id ?? null,
          prepareTarget:async () => { const library = await save(); return library && {library}; },
          onReview:review => {
            el.querySelector('[data-library-actions]').hidden = review;
            el.querySelector('[data-library-options]').hidden = review;
          },
          onSelection:selected => { if (mode.value === 'managed') submit.textContent = t(selected ? '预览导入' : '创建'); },
        });
        if (!el.isConnected) importer?.dispose();
        else importBody.querySelector('#imp-plan').hidden = true;
      };
      mode.onchange = () => {
        const managed = mode.value === "managed";
        el.querySelector("#nl-path").required = !managed;
        el.querySelector("#nl-path-label").textContent = t(managed ? "保存位置（可选）" : "服务端目录");
        el.querySelector("#library-path-hint").textContent = t(managed ? "留空时保存到应用数据目录。" : "Docker 请使用容器内路径。");
        const settings = el.querySelector('[data-import-settings]');
        if (settings) settings.hidden = !managed;
        importBody.hidden = !managed;
        if (!managed) el.querySelector('[data-library-options]').open = true;
        submit.textContent = t(managed ? "创建" : "添加并扫描");
      };
      mode.value = defaultMode === "indexed" ? "indexed" : "managed";
      mode.onchange();
      loadImporter().catch(failure => { error.textContent = failure.message; error.hidden = false; });
      el.querySelector("#nl-name").focus();
      form.onsubmit = async event => {
        event.preventDefault();
        if (busy) return;
        if (mode.value === 'managed' && importer?.hasSelection()) { importer.preview(); return; }
        busy = true; submit.disabled = true; error.hidden = true;
        const inputs = [...form.elements].filter(input => input.matches('input, select'));
        try {
          if (!await save()) return;
          if (mode.value === 'managed') { if (el.isConnected) close(); return; }
          const job = await api(`/libraries/${created.id}/scan-jobs`, {method:"POST", body:{request_key: `initial-scan:${created.id}`}});
          const status = await watchJob(job.id, el.querySelector('[data-library-progress]'), {detailLink:false});
          if (status?.state === 'succeeded' && el.isConnected) close();
        } catch (failure) {
          error.hidden = false; error.textContent = failure.message;
          if (created) {
            submit.textContent = t("重试扫描");
            // The library exists already; a scan retry must not create it twice.
            if (!el.isConnected) onCreated();
          } else inputs.forEach(input => { input.disabled = false; });
        } finally { busy = false; submit.disabled = false; }
      };
    },
  });
}

// ---------------------------------------------------------------- 库

export async function renderLibraries(body, {add = false, mode = "managed"} = {}) {
  const libs = await api("/libraries");
  body.innerHTML = `
    <div class="section-head library-list-head"><h2>${t("图库")}</h2>
      <button class="btn primary" data-new-library>${t("添加图库")}</button></div>
    ${libs.map((l) => `
      <div class="grant-item" style="margin-bottom:12px">
        <div class="form-row">
          <a href="#/library/${l.id}"><b>${escapeHtml(l.name)}</b></a>
          <span class="muted">${escapeHtml(l.root_path)}</span>
          <span class="pill">${t("图片 {v0} · 专辑 {v1}", {v0: l.image_count, v1: l.album_count})}</span>
          <button class="btn small" data-scan="${l.id}">${t("扫描更新")}</button>
          ${l.mode !== "indexed" ? `<button class="btn small" data-import="${l.id}">${t("导入图片")}</button>` : ""}
        </div>
        <details data-library-settings><summary>${t("设置与权限")}</summary><div class="form-row">
          <label>${t("用途")} <select data-mode="${l.id}">
            ${l.mode == null ? `<option value="">${t("未设置用途")}</option>` : ""}
            <option value="indexed" ${l.mode === "indexed" ? "selected" : ""}>${t("浏览已有目录")}</option>
            <option value="managed" ${l.mode === "managed" ? "selected" : ""}>${t("接收导入")}</option>
          </select></label>
          <label>${t("默认导入方式")} <select data-default="${l.id}">
            ${["hardlink", "copy", "move"].map(m => `<option value="${m}" ${l.default_import_mode === m ? "selected" : ""}>${t({hardlink:"硬链接",copy:"复制",move:"移动"}[m])}</option>`).join("")}
          </select></label>
          <button class="btn small" data-save-mode="${l.id}">${t("保存设置")}</button>
        </div>
        <div class="grant-grid" data-grants="${l.id}">${t("加载授权…")}</div></details>
      </div>`).join("") || `<p class="empty">${t("还没有图库")}</p>`}`;
  const refresh = () => {
    const button = document.activeElement;
    const selector = button?.dataset.import ? `[data-import="${button.dataset.import}"]` : '[data-new-library]';
    if (body.isConnected) renderLibraries(body).then(() => body.querySelector(selector)?.focus({preventScroll:true})).catch(error => toast(error.message, "error"));
  };
  body.querySelector("[data-new-library]").onclick = () => openLibraryForm("managed", refresh);
  if (add && body.isConnected) openLibraryForm(mode, refresh);
  body.querySelectorAll('[data-import]').forEach(button => {
    button.onclick = () => openImport({libraryId:button.dataset.import, onClose:refresh});
  });
  body.querySelectorAll("[data-save-mode]").forEach(button => button.addEventListener("click", async () => {
    const id = button.dataset.saveMode;
    const mode = body.querySelector(`[data-mode="${id}"]`).value || null;
    const default_import_mode = body.querySelector(`[data-default="${id}"]`).value;
    button.disabled = true;
    try {
      await api(`/libraries/${id}`, {method: "PATCH", body: {mode, default_import_mode}});
      toast(t("库设置已保存"), "ok");
      await renderLibraries(body);
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  }));

  body.querySelectorAll("[data-scan]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = t("扫描中…");
    try {
      const queued = await api(`/libraries/${btn.dataset.scan}/scan-jobs`, { method: "POST", body: {request_key: jobKey()} });
      const target = document.createElement("div");
      btn.parentElement.after(target);
      const status = await watchJob(queued.id, target);
      if (!status || !btn.isConnected) return;
      const s = status.result;
      if (s && status.state === "succeeded") {
        toast(scanSummary(s), "ok");
        await renderLibraries(body);
      }
    } catch (e) { toast(e.message, "error"); }
    if (btn.isConnected) {
      btn.disabled = false;
      btn.textContent = t("扫描更新");
    }
  }));

  // 授权矩阵
  body.querySelectorAll("[data-grants]").forEach(el => {
    const details = el.closest("details");
    let loaded = false;
    details.addEventListener("toggle", async () => {
      if (!details.open || loaded) return;
      loaded = true;
      const lid = el.dataset.grants;
      try {
        const [grants, groups] = await Promise.all([api(`/libraries/${lid}/grants`), api("/groups")]);
        const byGroup = Object.fromEntries(grants.map((g) => [String(g.group_id), g.access]));
        el.innerHTML = groups.map((g) => t("\n        <label class=\"grant-item\" style=\"display:flex;justify-content:space-between;align-items:center;gap:8px\">\n          <span>{v0}</span>\n          <select data-grant-gid=\"{v1}\">\n            <option value=\"\" {v2}>无</option>\n            <option value=\"view\" {v3}>查看</option>\n            <option value=\"edit\" {v4}>编辑</option>\n            <option value=\"manage\" {v5}>管理</option>\n          </select>\n        </label>", {v0: escapeHtml(g.name), v1: g.id, v2: !byGroup[g.id] ? "selected" : "", v3: byGroup[g.id] === "view" ? "selected" : "", v4: byGroup[g.id] === "edit" ? "selected" : "", v5: byGroup[g.id] === "manage" ? "selected" : ""})).join("");
        el.querySelectorAll("select[data-grant-gid]").forEach((sel) => sel.addEventListener("change", async () => {
          const previous = byGroup[sel.dataset.grantGid] || "";
          sel.disabled = true;
          try {
            await api(`/libraries/${lid}/grants`, {
              method: "PUT",
              body: { group_id: Number(sel.dataset.grantGid), access: sel.value || null },
            });
            byGroup[sel.dataset.grantGid] = sel.value;
            toast(t("授权已更新"), "ok");
          } catch (e) { sel.value = previous; toast(e.message, "error"); }
          finally { sel.disabled = false; }
        }));
      } catch (e) {
        loaded = false;
        el.textContent = e.message;
      }
    });
  });
}
