import {api} from "./api.js";
import {escapeHtml, showModal, toast} from "./app.js";
import {t} from "./i18n.js";
import {renderImport} from "./importer.js";

// Search stays inside the picker so choosing a target never loses image selection.
export function pickEntity(kind, {libraryId, allowCreate = false} = {}) {
  return new Promise(resolve => {
    let chosen = null, controller, timer, revision = 0, offset = 0, lastReset = true;
    showModal({
      title: t(kind === "albums" ? "选择专辑" : "选择艺术家"),
      bodyHtml: `<form role="search" class="entity-search"><input type="search" maxlength="300" aria-label="${t("搜索名称")}" placeholder="${t("搜索名称")}"></form>
        ${allowCreate ? `<button class="btn" data-new-album>${t("新建专辑")}</button>` : ""}
        <p class="error" role="status" hidden></p><button class="btn" data-retry hidden>${t("重试")}</button>
        <div class="picker-results" aria-live="polite"></div><button class="btn" data-more hidden>${t("加载更多")}</button>`,
      onClose() { clearTimeout(timer); controller?.abort(); revision++; resolve(chosen); },
      onMount(el, close) {
        const input = el.querySelector("input"), list = el.querySelector(".picker-results");
        const more = el.querySelector("[data-more]"), retry = el.querySelector("[data-retry]"), error = el.querySelector(".error");
        const choose = item => { chosen = item; close(); };
        async function load(reset = false) {
          lastReset = reset;
          if (reset) { controller?.abort(); revision++; }
          const requested = revision;
          controller = new AbortController(); more.disabled = true; retry.hidden = true; error.hidden = true;
          try {
            const params = {q:input.value.trim(), limit:101, offset:reset ? 0 : offset};
            if (kind === "albums") Object.assign(params, {kind:"manual", access:"edit"});
            const items = await api(`/${kind}?${new URLSearchParams(params)}`, {signal:controller.signal});
            if (requested !== revision || !el.isConnected) return;
            if (reset) { list.replaceChildren(); offset = 0; }
            for (const item of items.slice(0, 100)) {
              const button = document.createElement("button");
              button.type = "button"; button.className = "entity-row";
              button.innerHTML = `<span class="entity-name">${escapeHtml(item.name)}</span>
                ${item.library_name ? `<span class="muted">${escapeHtml(item.library_name)}</span>` : ""}`;
              button.onclick = () => choose(item); list.append(button);
            }
            offset += Math.min(items.length, 100); more.hidden = items.length <= 100;
            if (!offset) list.innerHTML = `<p class="empty">${t("没有匹配的结果")}</p>`;
          } catch (failure) {
            if (failure.name === "AbortError" || requested !== revision) return;
            error.textContent = failure.message; error.hidden = false; retry.hidden = false;
          } finally { if (requested === revision) more.disabled = false; }
        }
        input.oninput = () => { clearTimeout(timer); controller?.abort(); revision++; more.disabled = true; timer = setTimeout(() => load(true), 200); };
        el.querySelector("form").onsubmit = event => { event.preventDefault(); clearTimeout(timer); load(true); };
        more.onclick = () => load(); retry.onclick = () => load(lastReset);
        const create = el.querySelector("[data-new-album]");
        if (create) create.onclick = async () => {
          create.disabled = true;
          try { const album = await editAlbum(null, libraryId); if (album && el.isConnected) choose(album); }
          catch (failure) { error.textContent = failure.message; error.hidden = false; }
          finally { create.disabled = false; }
        };
        input.focus(); load(true);
      },
    });
  });
}

export async function editAlbum(album = null, libraryId = null) {
  const libraries = album ? [] : await api("/libraries?access=edit");
  const importLibraries = album ? [] : (await api('/libraries?access=manage')).filter(lib => lib.mode !== 'indexed');
  if (!album && !libraries.length) { toast(t("没有可编辑的图库，请联系管理员。"), "error"); return null; }
  return new Promise(resolve => {
    let saved = null, importer;
    showModal({
      title: t(album ? "编辑专辑" : "新建专辑"),
      onClose: () => { importer?.dispose(); resolve(saved); },
      bodyHtml: `<form class="catalog-form" id="album-form">
        <label>${t("名称")}<input name="name" maxlength="300" required value="${escapeHtml(album?.name)}"></label>
        ${!album ? `<label>${t("图库")}<select name="library_id">${libraries.map(lib => `<option value="${lib.id}" ${Number(libraryId) === lib.id ? "selected" : ""}>${escapeHtml(lib.name)}</option>`).join("")}</select></label>` : ""}
        ${album ? `<label>${t("专辑分类")}<input name="category" maxlength="300" value="${escapeHtml(album.category)}"></label>
        <label>${t("描述")}<textarea name="description" rows="3" maxlength="10000">${escapeHtml(album.description)}</textarea></label>` : ''}
        <p class="error" role="status" hidden></p>
      </form>${!album ? `<section data-album-import><h3>${t("添加图片（可选）")}</h3><div data-album-import-body></div></section>
        <details class="stack-form collection-options" data-album-options><summary>${t('高级选项')}</summary>
          <label>${t('专辑分类')}<input name="category" form="album-form" maxlength="300"></label>
          <label>${t('描述')}<textarea name="description" form="album-form" rows="3" maxlength="10000"></textarea></label>
        </details>` : ''}
        <div class="modal-actions" data-album-actions><button class="btn" type="button" data-close>${t("取消")}</button><button class="btn primary" type="submit" form="album-form" data-album-submit>${t(album ? "保存" : "创建")}</button></div>`,
      onMount(el, close) {
        const form = el.querySelector("form"), button = el.querySelector("[data-album-submit]"), error = el.querySelector(".error");
        const save = async () => {
          if (!album && saved) return saved;
          if (!form.reportValidity()) return null;
          const body = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value.trim()]));
          if (!album) body.library_id = Number(body.library_id);
          const fields = [...form.elements].filter(input => input.matches('input,select,textarea'));
          fields.forEach(input => { input.disabled = true; });
          try { saved = await api(album ? `/albums/${album.id}` : '/albums', {method:album ? 'PATCH' : 'POST', body}); }
          catch (failure) { fields.forEach(input => { input.disabled = false; }); throw failure; }
          return saved;
        };
        if (!album && importLibraries.length) {
          el.classList.add('import-modal');
          const section = el.querySelector('[data-album-import]');
          const update = () => {
            importer?.resetSourceBrowser();
            section.hidden = !importLibraries.some(lib => String(lib.id) === form.elements.library_id.value);
            const settings = el.querySelector('[data-import-settings]');
            if (settings) settings.hidden = section.hidden;
            button.textContent = t(!section.hidden && importer?.hasSelection() ? '预览导入' : '创建');
          };
          form.elements.library_id.onchange = update; update();
          renderImport(el.querySelector('[data-album-import-body]'), String(libraryId || libraries[0].id), {
            libraries:importLibraries, locked:true, close, primaryButton:button, settingsHost:el.querySelector('[data-album-options]'),
            getSourceLibraryId:() => form.elements.library_id.value,
            prepareTarget: async () => {
              const library = importLibraries.find(lib => String(lib.id) === form.elements.library_id.value);
              if (!library) return null;
              const target = await save(); return target && {library, albumId:target.id};
            },
            onReview:review => {
              el.querySelector('[data-album-actions]').hidden = review;
              el.querySelector('[data-album-options]').hidden = review;
            },
            onSelection:selected => { button.textContent = t(selected ? '预览导入' : '创建'); },
          }).then(value => {
            importer = value;
            if (!el.isConnected) importer?.dispose();
            else { el.querySelector('#imp-plan').hidden = true; update(); }
          })
            .catch(failure => { if (el.isConnected) { error.textContent = failure.message; error.hidden = false; } });
        } else if (!album) el.querySelector('[data-album-import]').hidden = true;
        form.elements.name.focus();
        form.onsubmit = async event => {
          event.preventDefault(); if (button.disabled) return;
          if (!album && importer?.hasSelection() && !el.querySelector('[data-album-import]').hidden) { importer.preview(); return; }
          button.disabled = true; error.hidden = true;
          try { if (await save() && el.isConnected) close(); }
          catch (failure) { error.textContent = failure.message; error.hidden = false; }
          finally { button.disabled = false; }
        };
      },
    });
  });
}

export function editTag(tag) {
  return new Promise(resolve => {
    let changed = false, revision = 0;
    showModal({
      title: t("管理标签"), onClose: () => { revision++; resolve(changed); },
      bodyHtml: `<form class="catalog-form" data-rename>
        <label>${t("名称")}<input name="name" maxlength="300" required value="${escapeHtml(tag.name)}"></label>
        <p class="hint">${t("重命名会更新所有使用此标签的图片。")}</p>
        <button class="btn" type="submit">${t("保存名称")}</button></form>
        <h3>${t("别名")}</h3><div data-aliases>${(tag.name_aliases || []).map(name => `<span class="pill">${escapeHtml(name)}</span>`).join(" ")}</div>
        <form class="catalog-form" data-alias><label>${t("添加别名")}<input name="alias" maxlength="300" required></label><button class="btn" type="submit">${t("预览")}</button></form>
        <div data-review></div><p class="error" role="status" hidden></p>`,
      onMount(el) {
        const error = el.querySelector(".error"), rename = el.querySelector("[data-rename]"), alias = el.querySelector("[data-alias]"), review = el.querySelector("[data-review]");
        const fail = failure => { error.textContent = failure.message; error.hidden = false; };
        rename.onsubmit = async event => {
          event.preventDefault(); const button = rename.querySelector("button"); if (button.disabled) return;
          button.disabled = true; error.hidden = true; revision++; review.replaceChildren();
          try { tag = {...tag, ...await api(`/tags/${tag.id}`, {method:"PATCH", body:{name:rename.elements.name.value.trim()}})}; changed = true; toast(t("已保存")); }
          catch (failure) { fail(failure); } finally { button.disabled = false; }
        };
        alias.oninput = () => { revision++; review.replaceChildren(); };
        alias.onsubmit = async event => {
          event.preventDefault(); const button = alias.querySelector("button"); if (button.disabled) return;
          button.disabled = true; error.hidden = true; const requested = ++revision;
          try {
            const plan = await api("/metadata/aliases/preview", {method:"POST", body:{kind:"tag", entity_id:tag.id, alias:alias.elements.alias.value.trim()}});
            if (requested !== revision || !el.isConnected) return;
            review.innerHTML = `<p>${escapeHtml(t("将“{alias}”作为“{name}”的别名", {alias:plan.alias, name:tag.name}))} · ${t("共 {v0} 张", {v0:plan.count})}</p><button class="btn primary">${t("确认添加")}</button>`;
            review.querySelector("button").onclick = async event => {
              event.target.disabled = true;
              try {
                const result = await api(`/metadata/plans/${plan.id}/execute`, {method:"POST", body:{confirm:true}});
                changed = true;
                el.querySelector("[data-aliases]").innerHTML = result.aliases.map(name => `<span class="pill">${escapeHtml(name)}</span>`).join(" ");
                alias.reset(); review.replaceChildren(); toast(t("已保存"));
              } catch (failure) { fail(failure); event.target.disabled = false; }
            };
          } catch (failure) { fail(failure); } finally { button.disabled = false; }
        };
      },
    });
  });
}
