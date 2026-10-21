import {api} from "./api.js";
import {escapeHtml, navigate, toast, showModal} from "./app.js";
import {Source, renderGridInto, loadSource} from "./views.js";
import {t} from "./i18n.js";

const labels={tag:"标签",artist:"艺术家",album:"专辑",rating:"我的评分",avg:"平均分",fav:"收藏",
  ext:"文件扩展名",file:"文件名",rated:"已评分",width:"宽度（像素）",height:"高度（像素）",
  orientation:"图片方向",imported:"首次入库时间",workflow:"整理状态",missing:"文件缺失"};
const valueLabels={true:"是",false:"否",any:"全部",landscape:"横向",portrait:"纵向",square:"方形",inbox:"待整理",done:"已整理"};
const revisions=new WeakMap();
let fieldSchema;

export async function attachSearchTools(view, source, rule = null, {open = false} = {}) {
  if (!view.isConnected) return;
  const section = document.createElement("details");
  section.className = "query-editor";
  section.open = open || !!source.errors.length;
  section.innerHTML = `<summary>${t("筛选")}${source.q ? `<span class="query-summary">${escapeHtml(source.q)}</span>` : ""}</summary>
    <form data-filter-form>
      <div class="form-row" data-condition>
        <label>${t("条件")}<select name="field" disabled></select></label>
        <label data-value>${t("值")}<input name="value"></label>
        <label class="checkbox-row"><input type="checkbox" name="exclude">${t("排除")}</label>
        <button type="submit" class="btn primary" data-add disabled>${t("应用条件")}</button>
      </div><p class="hint" data-field-hint></p>
    </form>
    <details class="query-syntax" ${source.errors.length ? "open" : ""}><summary>${t("编辑搜索语法")}</summary>
      <form data-expression-form><label class="sr-only" for="query-expression">${t("搜索条件")}</label><textarea id="query-expression" name="expression" maxlength="4096" rows="2" spellcheck="false"></textarea>
        <div class="form-row"><button class="btn" type="submit">${t("查看结果")}</button><button class="btn" type="button" data-clear>${t("清空条件")}</button></div></form>
    </details>
    <p class="error" role="status" data-rule-error></p>`;
  view.querySelector(".collection-toolbar").after(section);
  const save = document.createElement("button");
  save.className = "btn small"; save.dataset.saveQuery = "";
  save.textContent = t(rule ? "保存修改" : "保存查询");
  view.querySelector(".collection-toolbar").appendChild(save);
  const fields = section.querySelector("[data-filter-form]").elements;
  const expression = section.querySelector("[name=expression]");
  expression.value = source.q;
  const error = section.querySelector("[data-rule-error]");
  let busy = false, schema;
  section.querySelector("[data-filter-form]").onsubmit = event => event.preventDefault();
  async function apply(query) {
    if (!section.isConnected) return;
    const revision = (revisions.get(view) || 0) + 1;
    revisions.set(view, revision);
    const candidate = new Source(rule?.name || t(query ? "搜索结果" : "全部图片"), source.context, {q:query, sort:source.sort, filters:source.filters});
    candidate.display = {...source.display, density:view.querySelector("#density-select").value};
    if (!await loadSource(view, candidate) || !section.isConnected || revisions.get(view) !== revision) return;
    if (candidate.errors.length) { error.textContent = candidate.errors.join(" "); return; }
    source.cancel();
    if (!rule) {
      history.pushState(null, "", `#/search?q=${encodeURIComponent(query)}&sort=${source.sort}`);
      document.querySelector("#search-input").value = query;
    }
    await mountQueryGrid(view, candidate, rule, {open:true});
    view.querySelector(".query-editor > summary").focus();
  }
  async function run(operation) {
    if (busy) return;
    busy = true; error.textContent = "";
    save.disabled = true;
    section.querySelectorAll("input, select, textarea, button").forEach(control => { control.disabled = true; });
    try { await operation(); }
    catch (failure) { if (section.isConnected) error.textContent = failure.message; }
    finally {
      busy = false; save.disabled = false;
      section.querySelectorAll("input, select, textarea, button").forEach(control => { control.disabled = false; });
      fields.field.disabled = section.querySelector("[data-add]").disabled = !schema;
    }
  }
  section.querySelector("[data-expression-form]").onsubmit = event => { event.preventDefault(); run(() => apply(expression.value)); };
  section.querySelector("[data-clear]").onclick = () => run(() => apply(""));
  save.onclick = () => showModal({
    title:t(rule ? "保存修改" : "保存查询"),
    bodyHtml:`<form class="stack-form" data-save-form>
      <label>${t("名称")}<input name="name" required maxlength="200"></label>
      <label>${t("保存为")}<select name="kind"><option value="query">${t("保存的查询")}</option><option value="smart">${t("智能专辑")}</option></select></label>
      <p class="query-expression">${escapeHtml(expression.value || t("全部图片"))}</p>
      <details><summary>${t("显示设置")}</summary>
        <label>${t("图片密度")}<select name="density"><option value="comfortable">${t("宽松")}</option><option value="compact">${t("紧凑")}</option></select></label>
        <label class="checkbox-row"><input type="checkbox" name="show_filename">${t("显示文件名")}</label></details>
      <p class="error" role="alert" data-save-error></p>
      <div class="modal-actions">${rule ? `<a class="btn" href="/api/v1/queries/${rule.id}/export" download="query-${rule.id}.json">${t("导出规则")}</a>` : ""}
      <button type="button" class="btn" data-close>${t("取消")}</button><button type="submit" class="btn primary" data-save>${t("保存")}</button></div>
    </form>`,
    onMount(el, close) {
      const form = el.querySelector("form"), values = form.elements, message = el.querySelector("[data-save-error]");
      const query = expression.value;
      values.name.value = rule?.name || "";
      values.kind.value = rule?.kind || "query";
      values.density.value = view.querySelector("#density-select").value;
      values.show_filename.checked = source.display?.show_filename ?? true;
      values.name.focus();
      form.onsubmit = async event => {
        event.preventDefault();
        const button = el.querySelector("[data-save]");
        if (button.disabled) return;
        button.disabled = true; message.textContent = "";
        try {
          const body = {name:values.name.value, query, sort:source.sort, kind:values.kind.value,
            display:{density:values.density.value, show_filename:values.show_filename.checked}};
          if (rule) body.version = rule.version;
          const saved = await api(rule ? `/queries/${rule.id}` : "/queries", {method:rule ? "PATCH" : "POST", body});
          if (!el.isConnected || !section.isConnected) return;
          close(); toast(t("查询已保存"), "ok");
          if (location.hash === `#/query/${saved.id}`) await renderSavedQuery(view, saved.id);
          else navigate(`#/query/${saved.id}`);
        } catch (failure) { message.textContent = failure.message; }
        finally { button.disabled = false; }
      };
    },
  });
  try { schema = await (fieldSchema ||= api("/search/fields").catch(error => { fieldSchema = null; throw error; })); }
  catch (failure) { error.textContent = failure.message; return; }
  if (!section.isConnected) return;
  fields.field.innerHTML = schema.fields.map(field => `<option value="${field.kind}">${escapeHtml(t(labels[field.kind] || field.kind))}</option>`).join("");
  fields.field.disabled = false; section.querySelector("[data-add]").disabled = false;
  function selectField() {
    const field = schema.fields.find(field => field.kind === fields.field.value);
    const hint = section.querySelector("[data-field-hint]");
    hint.hidden = field.values.length > 0;
    hint.textContent = hint.hidden ? "" : t(field.kind === "imported" ? "例如 >=now-7d，表示最近七天。" : "多个值用逗号分隔；含逗号的名称用引号括起。");
    section.querySelector("[data-value]").innerHTML = `${t("值")}${field.values.length ? `<select name="value">${field.values.map(value => `<option value="${value}">${escapeHtml(t(valueLabels[value] || value))}</option>`).join("")}</select>` : `<input name="value" required placeholder="${escapeHtml(field.example)}">`}`;
  }
  fields.field.onchange = selectField; selectField();
  section.querySelector("[data-filter-form]").onsubmit = event => {
    event.preventDefault();
    run(async () => {
      const appended = await api("/search/append", {method:"POST", body:{query:expression.value,
        kind:fields.field.value, values:fields.value.value, neg:fields.exclude.checked}});
      await apply(appended.query);
    });
  };
}

async function mountQueryGrid(view, source, rule, options = {}) {
  if (!view.isConnected) return;
  renderGridInto(view, source);
  view.querySelector("#grid").classList.toggle("hide-filenames", source.display?.show_filename === false);
  if (rule) {
    const reload = view.querySelector("[data-refresh]");
    reload.setAttribute("data-reload-rule", "");
    reload.onclick = async () => {
      reload.disabled = true;
      try { await renderSavedQuery(view, rule.id); }
      catch (failure) { toast(failure.message, "error"); reload.disabled = false; }
    };
  }
  await attachSearchTools(view, source, rule, options);
}

export async function renderSavedQuery(view,id,sort) {
  const revision=(revisions.get(view)||0)+1;revisions.set(view,revision);
  const rule=await api(`/queries/${id}`);
  const source=new Source(rule.name,{title:rule.name},{q:rule.query,sort:sort || rule.sort});
  source.ruleErrors=rule.errors;
  source.display=rule.display;
  if(rule.errors.length)source.errors=rule.errors;
  else if (!await loadSource(view, source)) return;
  if(revisions.get(view)!==revision)return;
  await mountQueryGrid(view,source,rule);
}

export async function renderQueries(view) {
  view.innerHTML=`<div class="page-head"><h1>${t("保存的查询")}</h1><a class="btn primary" href="#/search">${t("新建查询")}</a></div>
    <div class="lib-cards" data-rules></div><button class="btn" data-more>${t("加载更多")}</button>`;
  const rows=view.querySelector("[data-rules]"),button=view.querySelector("[data-more]");
  let offset=0;
  async function load() {
    button.disabled=true;
    try {
      const result=await api(`/queries?offset=${offset}`);
      rows.insertAdjacentHTML("beforeend",result.items.map(rule=>`<article class="lib-card">
        <h2><a href="#/query/${rule.id}">${escapeHtml(rule.name)}</a></h2>
        <p>${t(rule.kind==="smart"?"智能专辑":"保存的查询")}</p>
        <p class="query-expression">${escapeHtml(rule.query || t("全部图片"))}</p>
        ${rule.errors.length?`<p class="error">${escapeHtml(rule.errors.join(" "))}</p>`:""}</article>`).join(""));
      if(offset===0&&!result.items.length)rows.textContent=t("还没有保存的查询");
      offset=result.next_offset;button.hidden=offset===null;
    }finally{button.disabled=false;}
  }
  button.onclick=()=>load().catch(e=>toast(e.message,"error"));
  await load();
}
