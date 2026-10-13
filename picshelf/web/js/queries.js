import {api} from "./api.js";
import {escapeHtml, navigate, toast} from "./app.js";
import {Source, renderGridInto} from "./views.js";
import {t} from "./i18n.js";

const labels={tag:"标签",artist:"艺术家",album:"专辑",rating:"我的评分",avg:"平均分",fav:"收藏",
  ext:"文件扩展名",file:"文件名",rated:"已评分",width:"宽度（像素）",height:"高度（像素）",
  orientation:"图片方向",imported:"首次入库时间",workflow:"整理状态",missing:"文件缺失"};
const valueLabels={true:"是",false:"否",any:"全部",landscape:"横向",portrait:"纵向",square:"方形",inbox:"待整理",done:"已整理"};
const revisions=new WeakMap();

export async function attachSearchTools(view,source,rule=null) {
  const section=document.createElement("details");
  section.className="query-editor";
  section.open=!!rule || !!source.errors.length;
  section.innerHTML=`<summary>${t("编辑搜索条件与保存规则")}</summary>
    <form>
      <label>${t("检索表达式")}<textarea name="expression" maxlength="4096" rows="3"></textarea></label>
      <div class="form-row" data-condition>
        <label>${t("条件")}<select name="field"></select></label>
        <label data-value>${t("参数")}<input name="value"></label>
        <label><input type="checkbox" name="exclude">${t("排除")}</label>
        <button type="button" class="btn" data-add>${t("添加条件")}</button>
      </div>
      <p class="hint">${t("日期请带时区，或使用 now-7d。同一条件的多个值用逗号连接，表示 OR。")}</p>
      <label>${t("规则名称")}<input name="name" maxlength="200"></label>
      <div class="form-row">
        <label>${t("规则类型")}<select name="kind"><option value="query">${t("保存的查询")}</option><option value="smart">${t("智能专辑")}</option></select></label>
        <label>${t("图片密度")}<select name="density"><option value="comfortable">${t("宽松")}</option><option value="compact">${t("紧凑")}</option></select></label>
        <label><input type="checkbox" name="show_filename">${t("显示文件名")}</label>
      </div>
      <p>${t("保存规则会重新计算匹配结果；手工专辑保存固定成员。个人评分与收藏使用当前账户。")}</p>
      <div class="form-row"><button class="btn" type="submit">${t("查看当前条件")}</button>
      <button class="btn primary" type="button" data-save>${t(rule?"保存规则修改":"保存规则")}</button>
      <a class="btn" href="#/queries">${t("保存的查询")}</a>
      ${rule?`<a class="btn" href="/api/v1/queries/${rule.id}/export" download="query-${rule.id}.json">${t("导出规则")}</a>`:""}</div>
      <p class="error" role="status" data-rule-error></p>
    </form>`;
  view.querySelector(".page-head").after(section);
  const form=section.querySelector("form"), fields=form.elements;
  fields.expression.value=source.q;
  fields.name.value=rule?.name || "";
  fields.kind.value=rule?.kind || "query";
  fields.density.value=rule?.display?.density || "comfortable";
  fields.show_filename.checked=rule?.display?.show_filename ?? true;
  const error=section.querySelector("[data-rule-error]");
  form.onsubmit=event=>{event.preventDefault();navigate(`#/search?q=${encodeURIComponent(fields.expression.value)}&sort=${source.sort}`);};
  section.querySelector("[data-save]").onclick=async event=>{
    const button=event.currentTarget;button.disabled=true;error.textContent="";
    try {
      const body={name:fields.name.value,query:fields.expression.value,sort:source.sort,kind:fields.kind.value,
        display:{density:fields.density.value,show_filename:fields.show_filename.checked}};
      if(rule)body.version=rule.version;
      const saved=await api(rule?`/queries/${rule.id}`:"/queries",{method:rule?"PATCH":"POST",body});
      if(!section.isConnected)return;
      toast(t("规则已保存"),"ok");
      if(location.hash===`#/query/${saved.id}`)await renderSavedQuery(view,saved.id);
      else navigate(`#/query/${saved.id}`);
    } catch(e) {error.textContent=e.message;}
    finally {button.disabled=false;}
  };
  let schema;
  try {schema=await api("/search/fields");}
  catch(e){error.textContent=e.message;return;}
  if(!section.isConnected)return;
  fields.field.innerHTML=schema.fields.map(f=>`<option value="${f.kind}">${escapeHtml(t(labels[f.kind]||f.kind))}</option>`).join("");
  function selectField() {
    const field=schema.fields.find(f=>f.kind===fields.field.value);
    const label=section.querySelector("[data-value]");
    label.innerHTML=`${t("参数")}${field.values.length?`<select name="value">${field.values.map(v=>`<option value="${v}">${escapeHtml(t(valueLabels[v]||v))}</option>`).join("")}</select>`:`<input name="value" placeholder="${escapeHtml(field.example)}">`}`;
  }
  fields.field.onchange=selectField;selectField();
  section.querySelector("[data-add]").onclick=async event=>{
    const button=event.currentTarget;button.disabled=true;error.textContent="";
    try {
      const before=fields.expression.value;
      const appended=await api("/search/append",{method:"POST",body:{query:before,
        kind:fields.field.value,values:[fields.value.value],neg:fields.exclude.checked}});
      if(fields.expression.value===before)fields.expression.value=appended.query;
    }catch(e){error.textContent=e.message;}
    finally{button.disabled=false;}
  };
}

export async function renderSavedQuery(view,id) {
  const revision=(revisions.get(view)||0)+1;revisions.set(view,revision);
  const rule=await api(`/queries/${id}`);
  const source=new Source(rule.name,{title:rule.name},{q:rule.query,sort:rule.sort});
  source.ruleErrors=rule.errors;
  if(rule.errors.length)source.errors=rule.errors;
  else await source.load(true);
  if(revisions.get(view)!==revision)return;
  renderGridInto(view,source,{headerExtraHtml:`<button class="btn" data-reload-rule>${t("重新读取规则")}</button>`});
  view.querySelector("[data-reload-rule]").onclick=()=>renderSavedQuery(view,id).catch(e=>toast(e.message,"error"));
  const grid=view.querySelector("#grid");
  grid.classList.toggle("compact",rule.display?.density==="compact");
  grid.classList.toggle("hide-filenames",rule.display?.show_filename===false);
  await attachSearchTools(view,source,rule);
}

export async function renderQueries(view) {
  view.innerHTML=`<div class="page-head"><h1>${t("保存的查询与智能专辑")}</h1><a class="btn primary" href="#/search">${t("新建规则")}</a></div>
    <p>${t("智能专辑按规则动态计算；新增匹配图片会加入，当前无权访问的图片会排除。")}</p>
    <div class="lib-cards" data-rules></div><button class="btn" data-more>${t("加载更多")}</button>`;
  const rows=view.querySelector("[data-rules]"),button=view.querySelector("[data-more]");
  let offset=0;
  async function load() {
    button.disabled=true;
    try {
      const result=await api(`/queries?offset=${offset}`);
      rows.insertAdjacentHTML("beforeend",result.items.map(rule=>`<article class="lib-card">
        <h2><a href="#/query/${rule.id}">${escapeHtml(rule.name)}</a></h2>
        <p>${t(rule.kind==="smart"?"智能专辑 · 动态结果":"保存的查询")}</p>
        <p class="query-expression">${escapeHtml(rule.query || t("全部图片"))}</p>
        ${rule.errors.length?`<p class="error">${escapeHtml(rule.errors.join(" "))}</p>`:""}</article>`).join(""));
      if(offset===0&&!result.items.length)rows.textContent=t("还没有保存的查询；先搜索，再保存规则。");
      offset=result.next_offset;button.hidden=offset===null;
    }finally{button.disabled=false;}
  }
  button.onclick=()=>load().catch(e=>toast(e.message,"error"));
  await load();
}
