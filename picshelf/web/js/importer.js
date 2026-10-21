import {api} from "./api.js";
import {escapeHtml, toast, uiConfirm, state, showModal} from "./app.js";
import {t} from "./i18n.js";
import {watchJob} from "./jobs.js";
import {droppedFiles, selectedFiles, imageFiles} from "./import-files.js";

// ---------------------------------------------------------------- 托管导入

export function openImport({libraryId, albumId, targetName, onClose, onComplete} = {}) {
  let controller;
  const modal = showModal({title:t("导入图片"), bodyHtml:`<div data-import-body>${t("加载中…")}</div>`,
    onClose() { controller?.dispose(); onClose?.(); },
    onMount(el, close) {
      el.classList.add("import-modal");
      const body = el.querySelector("[data-import-body]");
      renderImport(body, libraryId, {albumId, locked:!!libraryId, close, onComplete,
        onTarget:library => {
          if (!libraryId) return;
          const title = t('导入到 {name}', {name:targetName || library.name});
          el.querySelector('.modal-head h2').textContent = title;
          el.closest('[role=dialog]').setAttribute('aria-label', title);
        },
        onCreateLibrary:files => {
          close();
          import('./libraries.js').then(({openLibraryForm}) => openLibraryForm('managed', onClose, {files}))
            .catch(error => toast(error.message, 'error'));
        },
      }).then(value => {
        controller = value; if (!body.isConnected) controller?.dispose();
      }).catch(error => { if (body.isConnected) body.textContent = error.message; });
    },
  });
  return modal;
}

export async function renderImport(body, selectedLibrary, options = {}) {
  let libs = (options.libraries || await api("/libraries?access=manage")).filter(library => library.mode !== "indexed");
  if (!body.isConnected) return;
  let albumId = options.albumId;
  const requests = new AbortController();
  const request = (path, settings = {}) => api(path, {...settings, signal:requests.signal});
  body.innerHTML = `
    <div class="import-config" id="imp-config">
      <form id="imp-form">
        <div class="form-row" ${options.locked ? 'hidden' : ''}>
          <label>${t("目标图库")}<select id="imp-lib">${libs.map(lib => `<option value="${lib.id}">${escapeHtml(lib.name)}${t("（{n} 张）", {n:lib.image_count})}</option>`).join("")}</select></label>
        </div>
        <div class="import-drop" id="imp-drop" aria-label="${t("添加图片")}">
          <strong>${t("拖入图片或文件夹")}</strong>
          <div class="import-source-actions"><button type="button" class="btn" id="imp-pick-files">${t("选择图片")}</button>
          <button type="button" class="btn" id="imp-pick-folder">${t("选择文件夹")}</button>
          <button type="button" class="btn import-server-choice" id="imp-server">${t("服务器文件夹")}</button></div>
          <p class="hint" data-local-hint>${t("保留原文件和文件夹结构。")}</p>
          <input type="file" id="imp-files" accept=".jpg,.jpeg,.jfif,.png,.gif,.webp,.bmp,.tif,.tiff,.avif" multiple hidden>
          <input type="file" id="imp-folder" webkitdirectory multiple hidden>
        </div>
        <p id="imp-selection" role="status" class="import-selection" hidden></p>
        <div class="import-thumbnails" aria-label="${t('所选图片')}" hidden></div>
        <section id="imp-server-browser" class="import-server" aria-label="${t("服务器文件夹")}" hidden></section>
        <details class="import-options"><summary>${t("高级选项")}</summary>
          <label>${t("服务器文件夹")}<input id="imp-src" placeholder="/path/to/incoming" spellcheck="false" aria-describedby="import-path-hint"></label>
          <p class="hint" id="import-path-hint">${t("Docker 请使用容器内路径。")}</p>
          <label data-server-option>${t("导入方式")}<select id="imp-mode"><option value="hardlink">${t("自动")}</option><option value="copy">${t("复制")}</option><option value="move">${t("移动")}</option></select></label>
          <p class="hint" data-import-mode-hint></p>
          <label class="checkbox-row"><input type="checkbox" id="imp-rename" checked>${t("替换文件名中的非法字符")}</label>
        </details>
        <div class="form-row"><button type="submit" class="btn primary" id="imp-plan">${t("预览导入")}</button>
        ${!libs.length ? (state.user.is_admin ? `<button type="button" class="btn" data-create-library>${t("创建托管库")}</button>` : `<p class="hint">${t("没有可导入的图库，请联系管理员。")}</p>`) : ""}</div>
      </form>
    </div>
    <div class="import-review-source" hidden><p data-source-summary></p><button type="button" class="btn" id="imp-change">${t("重新选择")}</button></div>
    <div id="imp-out"></div>
    <div class="import-actions"><button class="btn primary" id="imp-exec" hidden disabled>${t("开始导入")}</button>
      <button type="button" class="btn primary" id="imp-browse" hidden>${t("完成")}</button>
      <button class="btn" id="imp-another" hidden>${t("继续导入")}</button>
      <span class="hint" id="imp-jobs" hidden>${t("关闭窗口不会中断导入。")}</span></div>`;

  if (options.settingsHost) {
    const sourceOptions = body.querySelector('.import-options');
    const settings = document.createElement('fieldset');
    settings.className = 'import-options'; settings.dataset.importSettings = '';
    settings.setAttribute('aria-label', t('导入设置'));
    settings.append(...[...sourceOptions.children].filter(child => child.tagName !== 'SUMMARY'));
    settings.querySelectorAll('input, select').forEach(input => input.setAttribute('form', 'imp-form'));
    settings.hidden = body.hidden;
    options.settingsHost.append(settings);
    sourceOptions.remove();
  }
  const find = selector => body.querySelector(selector) || options.settingsHost?.querySelector(selector);

  let plan = null, plannedInput = null, executing = false, planning = false, reading = false;
  let files = [], selectionVersion = 0, browseVersion = 0;
  let stagedUpload = null;
  const config = body.querySelector("#imp-config"), form = body.querySelector("#imp-form");
  const out = body.querySelector("#imp-out"), execBtn = body.querySelector("#imp-exec"), previewBtn = body.querySelector("#imp-plan");
  const browse = body.querySelector("#imp-browse"), another = body.querySelector("#imp-another"), jobs = body.querySelector("#imp-jobs");
  const librarySelect = body.querySelector("#imp-lib");
  const modes = {hardlink:"自动", copy:"复制", move:"移动"};
  const sourceInput = find("#imp-src"), selection = body.querySelector("#imp-selection");
  const reviewSource = body.querySelector('.import-review-source');
  const thumbnails = body.querySelector('.import-thumbnails');
  let thumbnailUrls = [];
  const clearThumbnails = () => {
    thumbnailUrls.forEach(url => URL.revokeObjectURL(url)); thumbnailUrls = [];
    thumbnails.replaceChildren(); thumbnails.hidden = true;
  };
  const drop = body.querySelector("#imp-drop"), serverBrowser = body.querySelector("#imp-server-browser");
  const inputControls = [...form.elements, options.primaryButton].filter(input => input?.matches('input, select, button'));
  const fields = inputControls.filter(input => input.matches('input, select'));
  const payload = () => ({source_dir: sourceInput.value.trim(), library_id:Number(librarySelect.value),
    mode:files.length ? "hardlink" : find("#imp-mode").value, rename:find("#imp-rename").checked,
    selection: selectionVersion});
  const prepareTarget = async () => {
    if (!options.prepareTarget) return true;
    const target = await options.prepareTarget();
    if (!target || !body.isConnected) return false;
    libs = [target.library]; albumId = target.albumId;
    librarySelect.innerHTML = `<option value="${target.library.id}">${escapeHtml(target.library.name)}</option>`;
    return true;
  };
  const describeMode = () => {
    const mode = find("#imp-mode").value;
    find("#imp-mode").disabled = files.length > 0;
    find('[data-server-option]').hidden = files.length > 0;
    find('[data-import-mode-hint]').hidden = files.length > 0;
    find("[data-import-mode-hint]").textContent = t({hardlink:"硬链接共享文件内容；跨磁盘时复制。", copy:"复制到图库，保留源文件。", move:"移动到图库，并移除源文件。"}[mode]);
    body.querySelector('[data-local-hint]').hidden = !files.length && !!sourceInput.value.trim();
  };
  const applyDefault = () => {
    find("#imp-mode").value = "hardlink";
    describeMode();
  };
  if (libs.some(lib => String(lib.id) === String(selectedLibrary))) librarySelect.value = String(selectedLibrary);
  if (libs.length) options.onTarget?.(libs.find(lib => String(lib.id) === librarySelect.value));
  applyDefault();
  librarySelect.addEventListener("change", applyDefault);
  find("#imp-mode").addEventListener("change", describeMode);
  previewBtn.disabled = !libs.length;
  const invalidate = () => {
    plan = null; plannedInput = null; execBtn.disabled = true;
    browse.hidden = another.hidden = true;
    out.textContent = out.childElementCount || out.textContent ? t("设置已更改，请重新预览。") : "";
    config.hidden = false; reviewSource.hidden = true;
    options.onReview?.(false);
    options.onSelection?.(files.length > 0 || !!sourceInput.value.trim());
  };
  fields.forEach(input => input.addEventListener("input", invalidate));
  const select = incoming => {
    if (planning || executing) return;
    const images = imageFiles(incoming);
    const bytes = images.reduce((total, item) => total + item.file.size, 0);
    if (images.length > 10000 || bytes > 2 * 1024 ** 3 || images.some(item => item.file.size > 256 * 1024 ** 2)) {
      toast(t("所选图片超过限制：每次最多 10000 张、总计 2 GiB，单张最多 256 MiB。"), "error"); return;
    }
    if (!images.length) { toast(t("没有找到支持的图片，请选择图片或包含图片的文件夹。"), "error"); return; }
    files = images; selectionVersion++; sourceInput.value = ""; serverBrowser.hidden = true; browseVersion++;
    selection.hidden = false;
    selection.removeAttribute('title');
    selection.textContent = t("已选择 {n} 张图片", {n:files.length}) + (incoming.length > images.length ? " · " + t("跳过 {n} 个非图片文件", {n:incoming.length - images.length}) : "");
    const folders = new Set(images.map(item => item.path.includes('/') ? item.path.split('/')[0] : null));
    if (folders.size === 1 && !folders.has(null)) selection.append(' · ' + [...folders][0]);
    clearThumbnails(); thumbnails.hidden = false;
    for (const item of images.slice(0, 4)) {
      const figure = document.createElement('figure'), img = document.createElement('img'), caption = document.createElement('figcaption');
      const url = URL.createObjectURL(item.file); thumbnailUrls.push(url);
      img.src = url; img.alt = ''; img.decoding = 'async';
      img.onerror = () => { img.remove(); figure.classList.add('import-file-preview'); };
      caption.textContent = item.file.name; caption.title = item.path;
      figure.append(img, caption); thumbnails.append(figure);
    }
    if (images.length > 4) {
      const more = document.createElement('span'); more.className = 'import-thumbnail-more';
      more.textContent = `+${images.length - 4}`; thumbnails.append(more);
    }
    invalidate(); describeMode(); (options.primaryButton || previewBtn).focus();
  };
  for (const kind of ["files", "folder"]) {
    const input = body.querySelector(`#imp-${kind}`);
    body.querySelector(`#imp-pick-${kind}`).onclick = () => input.click();
    input.onchange = () => { select(selectedFiles(input.files)); input.value = ""; };
  }
  sourceInput.addEventListener("input", () => {
    clearThumbnails();
    files = []; selectionVersion++;
    selection.textContent = sourceInput.value.trim(); selection.hidden = !sourceInput.value.trim();
    describeMode();
    options.onSelection?.(!!sourceInput.value.trim());
  });
  for (const event of ["dragenter", "dragover", "dragleave", "drop"]) drop.addEventListener(event, e => {
    e.preventDefault();
    if (event === 'dragenter' || event === 'dragover') drop.classList.add('dragging');
    else if (event === 'drop' || !drop.contains(e.relatedTarget)) drop.classList.remove('dragging');
  });
  drop.addEventListener("drop", async event => {
    if (reading || planning || executing) return;
    reading = true; inputControls.forEach(input => { input.disabled = true; });
    try { const incoming = await droppedFiles(event.dataTransfer); if (body.isConnected) select(incoming); }
    catch { toast(t("无法读取拖入的文件夹，请使用“选择文件夹”重试。"), "error"); }
    finally {
      reading = false; inputControls.forEach(input => { input.disabled = false; });
      describeMode(); previewBtn.disabled = !libs.length;
    }
  });
  const sourceLibraryId = () => options.getSourceLibraryId ? options.getSourceLibraryId() : librarySelect.value || null;
  const resetSourceBrowser = () => { browseVersion++; serverBrowser.hidden = true; };
  const browseServer = async path => {
    const version = ++browseVersion, library = sourceLibraryId();
    serverBrowser.hidden = false;
    serverBrowser.textContent = t("正在读取文件夹…");
    try {
      const query = new URLSearchParams();
      if (library !== null) query.set('library_id', library);
      if (path) query.set("path", path);
      const result = await request(`/import/sources?${query}`);
      if (!body.isConnected || version !== browseVersion || library !== sourceLibraryId()) return;
      serverBrowser.innerHTML = `<p class="import-server-path">${escapeHtml(result.path || t("选择服务器上的文件夹"))}</p>
        <div class="form-row"><button type="button" class="btn" data-roots>${t("起始位置")}</button>
        ${result.parent ? `<button type="button" class="btn" data-parent>${t("上一级")}</button>` : ""}
        ${result.path ? `<button type="button" class="btn primary" data-use>${t("选择此文件夹")}</button>` : ""}
        <button type="button" class="btn" data-close>${t("关闭")}</button></div><div class="import-directories"></div>`;
      serverBrowser.querySelector("[data-roots]").onclick = () => browseServer();
      serverBrowser.querySelector("[data-parent]")?.addEventListener("click", () => browseServer(result.parent));
      serverBrowser.querySelector("[data-close]").onclick = () => { serverBrowser.hidden = true; browseVersion++; };
      serverBrowser.querySelector("[data-use]")?.addEventListener("click", () => {
        clearThumbnails();
        files = []; selectionVersion++; sourceInput.value = result.path;
        selection.textContent = result.path.split(/[\\/]/).filter(Boolean).pop() || result.path;
        selection.title = result.path; selection.hidden = false; serverBrowser.hidden = true; browseVersion++;
        invalidate(); describeMode(); (options.primaryButton || previewBtn).focus();
      });
      const list = serverBrowser.querySelector(".import-directories");
      const directories = result.path ? result.directories : result.roots;
      for (const directory of directories) {
        const button = document.createElement("button"); button.type = "button"; button.className = "btn";
        button.textContent = directory.name; button.title = directory.path;
        button.onclick = () => browseServer(directory.path); list.appendChild(button);
      }
      if (!directories.length) list.textContent = t("没有可浏览的子文件夹。");
      if (result.truncated) {
        const hint = document.createElement("p"); hint.textContent = t("文件夹过多，仅显示前 500 项；可在高级选项中指定路径。"); list.appendChild(hint);
      }
    } catch (error) {
      if (version !== browseVersion || !body.isConnected) return;
      if (error.name !== "AbortError") serverBrowser.textContent = error.message + " " + error.nextStep;
    }
  };
  body.querySelector("#imp-server").onclick = () => browseServer();
  librarySelect.addEventListener("change", resetSourceBrowser);

  form.onsubmit = async event => {
    event.preventDefault();
    if (reading || planning || executing || !libs.length) return;
    if (!sourceInput.value.trim() && !files.length) { out.innerHTML = `<p class="error" role="alert">${t("请选择图片或文件夹")}</p>`; return; }
    planning = true; previewBtn.disabled = true; execBtn.disabled = true;
    plan = null; plannedInput = null; browse.hidden = another.hidden = jobs.hidden = true;
    previewBtn.textContent = t("正在预览…");
    inputControls.forEach(input => { input.disabled = true; });
    serverBrowser.hidden = true; browseVersion++;
    try {
      if (!await prepareTarget()) return;
      const p = payload();
      if (stagedUpload) {
        await request(`/import/uploads/${stagedUpload}/discard`, {method:"POST"});
        stagedUpload = null;
      }
      let saved;
      if (files.length) {
        const upload = await request("/import/uploads", {method:"POST", body:{library_id:p.library_id}});
        stagedUpload = upload.id;
        for (const [index, item] of files.entries()) {
          if (!body.isConnected) return;
          out.textContent = t("正在上传 {current}/{total}：{name}", {current:index + 1, total:files.length, name:item.path});
          await request(`/import/uploads/${upload.id}/file?path=${encodeURIComponent(item.path)}`, {method:"PUT", body:item.file, raw:true});
        }
        saved = await request(`/import/uploads/${upload.id}/plan`, {method:"POST", body:{rename:p.rename, album_id:albumId || null}});
      } else {
        const {selection: _selection, ...request} = p;
        saved = await api("/import/plan", {method:"POST", body:{...request, album_id:albumId || null}, signal:requests.signal});
      }
      if (!body.isConnected) return;
      if (JSON.stringify(payload()) !== JSON.stringify(p)) { out.textContent = t("设置已更改，请重新预览。"); return; }
      plan = saved; plannedInput = p;
      renderPlan(plan, out, () => plan?.plan_id === saved.plan_id && plan.state === "preview" && !executing);
      execBtn.hidden = false; execBtn.disabled = plan.stats.hardlink + plan.stats.copy + plan.stats.move + (albumId ? plan.stats.skip : 0) === 0;
      execBtn.textContent = t("开始导入");
      execBtn.classList.toggle("danger", p.mode === "move");
      execBtn.classList.toggle("primary", p.mode !== "move");
      const summary = body.querySelector("[data-source-summary]");
      summary.textContent = `${files.length ? t("已选择 {n} 张图片", {n:files.length}) : p.source_dir.split("/").filter(Boolean).pop() || p.source_dir} → ${libs.find(lib => lib.id === p.library_id).name}${files.length ? "" : " · " + t(modes[p.mode])}`;
      summary.title = p.source_dir;
      config.hidden = true; reviewSource.hidden = false;
      options.onReview?.(true);
      (execBtn.disabled ? body.querySelector('#imp-change') : execBtn).focus();
    } catch (error) {
      if (error.name === "AbortError" || !body.isConnected) return;
      const message = t("无法预览：") + error.message + (error.code === "import_source_forbidden" ? " " + error.nextStep : "");
      out.innerHTML = `<p class="error" role="alert">${escapeHtml(message)}</p>`;
    }
    finally {
      planning = false; inputControls.forEach(input => { input.disabled = false; }); describeMode();
      previewBtn.disabled = !libs.length; previewBtn.textContent = t("预览导入");
    }
  };

  execBtn.onclick = async () => {
    if (executing || !plan || JSON.stringify(payload()) !== JSON.stringify(plannedInput)) return;
    const selectedPlan = plan, p = plannedInput;
    if (p.mode === "move" && !await uiConfirm(t("确认导入"), t("源目录中的文件将被移走。"))) return;
    if (plan !== selectedPlan || JSON.stringify(payload()) !== JSON.stringify(p)) return;
    executing = true; execBtn.disabled = true; execBtn.textContent = t("正在导入…");
    stagedUpload = null;
    inputControls.forEach(input => { input.disabled = true; });
    config.hidden = reviewSource.hidden = true; jobs.hidden = false;
    try {
      const retry = ["partial", "cancelled", "interrupted", "failed"].includes(selectedPlan.state);
      const queued = await api(`/import/plans/${selectedPlan.plan_id}/jobs`, {method:"POST", body:{confirm:true, request_key:"import:" + selectedPlan.plan_id}});
      if (retry) await api(`/jobs/${queued.id}/retry`, {method:"POST"});
      const status = await watchJob(queued.id, out, {detailLink:false});
      if (!status || !body.isConnected) return;
      selectedPlan.state = status.state;
      if (status.result) renderDone(status.result, out);
      browse.hidden = another.hidden = false;
      options.onComplete?.(status);
      if (status.state === "succeeded") browse.focus();
    } catch (error) { if (body.isConnected) toast(error.message, "error"); }
    finally {
      executing = false;
      inputControls.forEach(input => { input.disabled = false; });
      describeMode();
      execBtn.disabled = !plan || plan.state === "succeeded";
      execBtn.hidden = plan?.state === "succeeded";
      execBtn.textContent = ["partial", "cancelled", "interrupted", "failed"].includes(plan?.state) ? t("重试未完成项") : t("继续查看进度");
    }
  };
  another.onclick = () => {
    clearThumbnails();
    plan = null; plannedInput = null; config.hidden = false; reviewSource.hidden = true;
    out.replaceChildren(); execBtn.hidden = browse.hidden = another.hidden = jobs.hidden = true;
    execBtn.disabled = true; body.querySelector("[data-source-summary]").textContent = "";
    files = []; selectionVersion++; sourceInput.value = ""; selection.textContent = ""; selection.hidden = true;
    describeMode(); body.querySelector("#imp-pick-files").focus();
    options.onReview?.(false);
    options.onSelection?.(false);
  };
  browse.onclick = () => options.close?.();
  body.querySelector('#imp-change').onclick = () => { invalidate(); body.querySelector('#imp-pick-files').focus(); };
  body.querySelector('[data-create-library]')?.addEventListener('click', () => options.onCreateLibrary?.(files));
  if (options.files?.length) select(options.files);
  return {
    hasSelection: () => files.length > 0 || !!sourceInput.value.trim(),
    preview: () => form.requestSubmit(),
    resetSourceBrowser,
    dispose() {
      clearThumbnails();
      requests.abort(); browseVersion++;
      if (stagedUpload) api(`/import/uploads/${stagedUpload}/discard`, {method:"POST"}).catch(() => {});
    },
  };
}

function renderPlan(plan, out, isCurrent) {
  const s = plan.stats;
  const labels = { hardlink: plan.browser_upload ? t("导入") : t("硬链"), copy: plan.browser_upload ? t("导入") : t("复制"), move: t("移动"), skip: t("跳过"), error: t("错误") };
  const relative = (path, root) => {
    const prefix = root.endsWith("/") ? root : root + "/";
    return path?.startsWith(prefix) ? path.slice(prefix.length) : path;
  };
  const hasReasons = plan.items.some(item => item.reason);
  const rows = plan.items.map(item => {
    const source = relative(item.src, plan.source), destination = relative(item.dst, plan.root);
    return `<tr><td>${labels[item.action] || item.action}</td>
      <td class="import-file"><span class="src" title="${escapeHtml(item.src)}">${escapeHtml(source)}</span>
        ${destination && destination !== source ? `<span class="import-destination" title="${escapeHtml(item.dst)}">→ ${escapeHtml(destination)}</span>` : ""}</td>
      ${hasReasons ? `<td>${escapeHtml(item.reason || "")}</td>` : ""}</tr>`;
  }).join("");
  out.innerHTML = `<p role="status">${escapeHtml(importSummary(s, true))}</p>
    <details class="import-paths"><summary>${t("完整路径")}</summary><dl>${plan.browser_upload ? "" : `<dt>${t("源目录")}</dt><dd>${escapeHtml(plan.source)}</dd>`}<dt>${t("目标目录")}</dt><dd>${escapeHtml(plan.root)}</dd></dl></details>
    <div class="table-wrap"><table class="data imp-table"><thead><tr><th>${t("操作")}</th><th>${t("文件")}</th>${hasReasons ? `<th>${t("说明")}</th>` : ""}</tr></thead><tbody>${rows}</tbody></table></div>`;
  if (plan.offset === 0 && plan.next_offset === null) return;
  const range = document.createElement("p");
  range.textContent = t("显示第 {start}–{end} 项，共 {total} 项", {start: plan.offset + 1, end: plan.offset + plan.items.length, total: plan.items_total});
  out.appendChild(range);
  const navigation = document.createElement("div");
  navigation.className = "form-row";
  for (const [label, offset] of [[t("上一页"), plan.offset > 0 ? Math.max(0, plan.offset - plan.limit) : null], [t("下一页"), plan.next_offset]]) {
    const button = document.createElement("button");
    button.className = "btn";
    button.textContent = label;
    button.disabled = offset === null;
    button.addEventListener("click", async () => {
      if (!isCurrent()) return;
      navigation.querySelectorAll("button").forEach((item) => { item.disabled = true; });
      try {
        const next = await api(`/import/plans/${plan.plan_id}?offset=${offset}&limit=${plan.limit}`);
        if (isCurrent()) renderPlan(next, out, isCurrent);
      } catch (error) {
        if (isCurrent()) { toast(error.message, "error"); renderPlan(plan, out, isCurrent); }
      }
    });
    navigation.appendChild(button);
  }
  out.appendChild(navigation);
}

function importSummary(counts, preview = false) {
  const imported = counts.hardlink + counts.copy + counts.move;
  const parts = [t(preview ? "待导入 {n} 个文件" : "已导入 {n} 个文件", {n: imported})];
  if (counts.skip) parts.push(t("跳过 {n}", {n: counts.skip}));
  if (counts.error) parts.push(t("失败 {n}", {n: counts.error}));
  return parts.join(" · ");
}

function renderDone(r, out) {
  const d = r.done;
  const states = {succeeded: "导入完成", partial: "部分导入失败", cancelled: "导入已取消", interrupted: "导入已中断", failed: "导入失败"};
  out.innerHTML = `<p role="status"><strong>${t(states[r.state] || "导入已结束")}</strong> · ${escapeHtml(importSummary(d))}</p>
    ${d.errors?.length ? `<details open><summary>${t("失败原因")}</summary><pre>${escapeHtml(d.errors.join("\n"))}</pre></details>` : ""}`;
  if (r.scan?.errors?.length) {
    const scanErrors = document.createElement("pre");
    scanErrors.textContent = t("扫描错误：") + "\n" + r.scan.errors.join("\n");
    out.appendChild(scanErrors);
  }
}
