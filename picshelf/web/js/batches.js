import {api} from "./api.js";
import {escapeHtml, toast, uiConfirm} from "./app.js";
import {t} from "./i18n.js";
import {ImageSelection} from "./selection.js";
import {formatTimestamp} from "./format.js";

const operations = [
  ["tags_add", "添加标签"], ["tags_remove", "移除标签"],
  ["artist_add", "关联艺术家"], ["artist_remove", "取消艺术家关联"],
  ["album_add", "加入手工专辑"], ["album_remove", "移出手工专辑"],
  ["rating", "设置评分"], ["rating_clear", "清除评分"],
  ["favorite_on", "添加收藏"], ["favorite_off", "取消收藏"],
  ["workflow_done", "标记已整理"], ["workflow_inbox", "标记待整理"],
];
const states = {preview:"待确认",queued:"已排队",running:"执行中",succeeded:"已完成",partial:"部分完成",
  cancelling:"正在取消",cancelled:"已取消",interrupted:"已中断",undoing:"正在撤销",undone:"已撤销",undo_conflict:"撤销存在冲突"};
const itemStates = {pending:"待处理",succeeded:"已修改",unchanged:"无需修改",conflict:"执行冲突",undone:"已撤销",undo_conflict:"撤销冲突"};
const watchers = new WeakMap();

function actionLabel(batch) {
  const action=batch.action;
  if(action.kind==="rating")return action.value===null?t("清除评分"):t("评分：{n}",{n:action.value});
  if(action.kind==="favorite")return t(action.value?"添加收藏":"取消收藏");
  if(action.kind==="workflow")return t(action.value==="done"?"标记已整理":"标记待整理");
  return t(operations.find(([value])=>value===action.kind)?.[1] || action.kind)+": "+(action.names?.join(", ") || batch.target_snapshot?.name || "");
}

function summary(batch) {
  const c = batch.counts;
  return t("目标 {total} 张 · 已修改 {changed} · 无需修改 {unchanged} · 待处理 {pending} · 已撤销 {undone} · 冲突 {conflicts}",
    {total:batch.total,changed:c.succeeded,unchanged:c.unchanged,pending:c.pending,undone:c.undone,conflicts:c.conflict+c.undo_conflict});
}

function statusHtml(batch) {
  return `<p><strong>${escapeHtml(actionLabel(batch))}</strong> · ${escapeHtml(formatTimestamp(batch.created_at))}</p>
    <p role="status">${escapeHtml(t(states[batch.state] || batch.state))} · ${escapeHtml(summary(batch))}</p>
    <p>${escapeHtml(t("撤销仅恢复本批次且未被后续修改的元数据；冲突项目保持当前值。"))}</p>
    <div class="form-row">
      <button class="btn" data-batch-cancel ${["succeeded","undone","undo_conflict"].includes(batch.state)?"disabled":""}>${t("取消未完成项")}</button>
      <button class="btn" data-batch-resume ${!["cancelled","interrupted"].includes(batch.state)?"disabled":""}>${t("继续未完成项")}</button>
      <button class="btn danger" data-batch-undo ${!batch.counts.succeeded?"disabled":""}>${t("撤销已完成项")}</button>
      <a class="btn" href="#/batch/${batch.id}">${t("查看逐项结果")}</a>
    </div>`;
}

function watchStatus(panel, initial, onChange) {
  watchers.get(panel)?.();
  let active = true;
  let timer;
  watchers.set(panel,()=>{active=false;clearTimeout(timer);});
  let current = initial;
  const paint = batch => {
    current = batch;
    panel.innerHTML = statusHtml(batch);
    const run = async (name,body) => {
      try {
        paint(await api(`/batches/${batch.id}/${name}`, {method:"POST",body}));
        await onChange?.();
        schedule();
      } catch (error) {toast(error.message,"error");}
    };
    panel.querySelector("[data-batch-cancel]").onclick = () => run("cancel");
    panel.querySelector("[data-batch-resume]").onclick = () => run(batch.phase === "undo"?"undo":"execute", batch.phase === "undo"?{confirm:true}:{confirm:true,retry:true,background:batch.total>100});
    panel.querySelector("[data-batch-undo]").onclick = async () => {
      if (await uiConfirm(t("撤销已完成项"),t("后续修改会保留并报告冲突。"))) await run("undo",{confirm:true});
    };
  };
  function schedule() {
    clearTimeout(timer);
    if (!["queued","running","cancelling","undoing"].includes(current.state)) return;
    timer = setTimeout(async () => {
      if (!active || !panel.isConnected) return;
      try {
        const result = await api(`/batches/${current.id}`);
        if (!active || !panel.isConnected) return;
        paint(result);
        if (["succeeded","partial","cancelled","interrupted"].includes(result.state)) await onChange?.();
        schedule();
      } catch (error) {toast(error.message,"error");}
    },700);
  }
  paint(initial); schedule();
}

export function attachBatchControls(view,source,grid,refresh) {
  const selection = new ImageSelection();
  const bar = document.createElement("section");
  bar.className = "batch-controls";
  bar.setAttribute("aria-label",t("批量整理"));
  bar.innerHTML = `<div class="form-row"><strong>${t("批量整理")}</strong><span data-selection-count role="status"></span>
    <button class="btn small" data-select-loaded>${t("选择当前已加载图片")}</button>
    <button class="btn small" data-select-all>${t("选择全部匹配结果")}</button>
    <button class="btn small" data-select-clear>${t("清空选择")}</button><a href="#/batches">${t("批次记录")}</a></div>
    <p class="hint">${t("选择可跨加载页保留；全部匹配结果在预演时固定，预演后新增图片不进入批次。")}</p>
    <div class="form-row"><label>${t("操作")} <select data-batch-operation>${operations.map(([value,label])=>`<option value="${value}">${t(label)}</option>`).join("")}</select></label>
    <label data-batch-value-label>${t("标签（每行一个）")} <textarea data-batch-names rows="2"></textarea></label>
    <label data-batch-target-label hidden>${t("目标")} <select data-batch-target></select></label>
    <label data-batch-rating-label hidden>${t("评分")} <input type="number" min="0" max="10" value="0" data-batch-rating></label>
    <button class="btn primary" data-batch-preview>${t("预演选中范围")}</button></div>
    <div data-batch-review></div><div data-batch-status></div>`;
  grid.before(bar);
  let preview = null;
  let revision = 0;
  const review = bar.querySelector("[data-batch-review]");
  const operation = bar.querySelector("[data-batch-operation]");
  const invalidate = () => {revision++;preview=null;review.replaceChildren();};
  const update = () => {
    bar.querySelector("[data-selection-count]").textContent = selection.allMatching
      ?t("全部匹配：{n} 张（预演时固定）",{n:source.total}):t("已选 {n} 张",{n:selection.ids.size});
    grid.querySelectorAll("[data-select-image]").forEach(input=>{input.checked=selection.allMatching || selection.ids.has(Number(input.dataset.selectImage));});
    bar.querySelector("[data-batch-preview]").disabled = !selection.allMatching && !selection.ids.size;
  };
  grid.addEventListener("change",event=>{
    const input=event.target.closest("[data-select-image]");
    if(input){selection.toggle(Number(input.dataset.selectImage),input.checked);invalidate();update();}
  });
  for(const [name,fn] of [["loaded",()=>selection.loaded(source.items)],["all",()=>selection.all()],["clear",()=>selection.clear()]]) {
    bar.querySelector(`[data-select-${name}]`).onclick=()=>{fn();invalidate();update();};
  }
  bar.querySelectorAll("input,textarea,select").forEach(input=>input.addEventListener("input",invalidate));
  operation.onchange=async()=>{
    invalidate();
    const kind=operation.value;
    bar.querySelector("[data-batch-value-label]").hidden=!kind.startsWith("tags_");
    bar.querySelector("[data-batch-rating-label]").hidden=kind!=="rating";
    const entity=kind.startsWith("artist_")?"artist":kind.startsWith("album_")?"album":null;
    bar.querySelector("[data-batch-target-label]").hidden=!entity;
    if(entity) {
      try {
        const items=await api(entity==="artist"?"/artists?limit=500":"/albums?kind=manual");
        if(operation.value!==kind)return;
        bar.querySelector("[data-batch-target]").innerHTML=items.map(item=>`<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
      } catch(error){toast(error.message,"error");}
    }
  };
  bar.querySelector("[data-batch-preview]").onclick=async()=>{
    const requestedRevision=revision;
    let kind=operation.value,action={kind};
    if(kind.startsWith("tags_"))action.names=bar.querySelector("[data-batch-names]").value.split("\n").map(s=>s.trim()).filter(Boolean);
    else if(kind.startsWith("artist_"))action.artist_id=Number(bar.querySelector("[data-batch-target]").value);
    else if(kind.startsWith("album_"))action.album_id=Number(bar.querySelector("[data-batch-target]").value);
    else if(kind.startsWith("rating"))action={kind:"rating",value:kind==="rating_clear"?null:Number(bar.querySelector("[data-batch-rating]").value)};
    else if(kind.startsWith("favorite"))action={kind:"favorite",value:kind==="favorite_on"};
    else action={kind:"workflow",value:kind==="workflow_done"?"done":"inbox"};
    try {
      const result=await api("/batches/preview",{method:"POST",body:{action,...selection.payload(source),request_key:crypto.randomUUID()}});
      if(requestedRevision!==revision || !bar.isConnected)return;
      preview=result;
      review.innerHTML=`<p role="status">${escapeHtml(t("预演已固定 {n} 张图片",{n:preview.total}))} · ${escapeHtml(t(operations.find(([value])=>value===kind)[1]))}</p>
        <p>${escapeHtml(t(preview.selection.mode==="query"?"范围：全部匹配快照":"范围：明确选中的图片"))}</p>
        <ul class="batch-targets">${preview.items.slice(0,20).map(item=>`<li>${escapeHtml(item.library_name)} / ${escapeHtml(item.rel_path)}</li>`).join("")}</ul>
        ${preview.total>20?`<p>${escapeHtml(t("显示前 20 张；逐项结果包含全部固定目标。"))}</p>`:""}
        <button class="btn primary" data-batch-apply>${t("确认执行此批次")}</button>`;
      review.querySelector("[data-batch-apply]").onclick=async event=>{
        if(!preview)return;
        event.target.disabled=true;
        try {
          const value=await api(`/batches/${preview.id}/execute`,{method:"POST",body:{confirm:true,background:preview.total>100}});
          invalidate();
          watchStatus(bar.querySelector("[data-batch-status]"),value,refresh);
          await refresh();
        }catch(error){toast(error.message,"error");event.target.disabled=false;}
      };
    }catch(error){toast(error.message,"error");}
  };
  update();
  return update;
}

export async function renderBatchHistory(view,id=null) {
  if(!id){
    view.innerHTML=`<h1>${t("批次记录")}</h1><div data-batch-list></div><button class="btn" data-history-more>${t("加载更多")}</button>`;
    const list=view.querySelector("[data-batch-list]"),more=view.querySelector("[data-history-more]");
    let offset=0;
    const load=async()=>{
      more.disabled=true;
      try {
        const data=await api(`/batches?offset=${offset}`);
        for(const batch of data.items){
          const article=document.createElement("article");article.className="batch-controls";
          article.innerHTML=`<a href="#/batch/${batch.id}">${escapeHtml(actionLabel(batch))}</a> · ${escapeHtml(t(states[batch.state] || batch.state))}<p>${escapeHtml(summary(batch))}</p><p>${escapeHtml(formatTimestamp(batch.created_at))}</p>`;
          list.appendChild(article);
        }
        more.hidden=data.next_offset===null;offset=data.next_offset;
        if(!list.children.length)list.textContent=t("暂无批次");
      } finally {more.disabled=false;}
    };
    more.onclick=()=>load().catch(error=>toast(error.message,"error"));
    await load();
    return;
  }
  const batch=await api(`/batches/${id}`);
  view.innerHTML=`<h1>${t("批次记录")}</h1><div data-batch-status></div><p>${t("逐项结果")}</p><div class="table-wrap"><table class="data"><thead><tr><th>${t("图片")}</th><th>${t("状态")}</th></tr></thead><tbody></tbody></table></div><button class="btn" data-batch-more>${t("加载更多")}</button>`;
  const rows=view.querySelector("tbody");
  let offset=0;
  let loading=false;
  const append=async()=>{
    if(loading)return;
    loading=true;
    try {
      const page=await api(`/batches/${id}?offset=${offset}`);
      for(const item of page.items){const tr=document.createElement("tr");tr.innerHTML=`<td><a href="#/view/${item.image_id}">${escapeHtml(item.library_name)} / ${escapeHtml(item.rel_path)}</a></td><td>${escapeHtml(t(itemStates[item.state] || item.state))}${item.error_code?" · "+escapeHtml(item.error_code):""}</td>`;rows.appendChild(tr);}
      offset+=page.items.length;
      view.querySelector("[data-batch-more]").hidden=offset>=page.total;
    } finally {loading=false;}
  };
  const refresh=async()=>{offset=0;rows.replaceChildren();await append();};
  watchStatus(view.querySelector("[data-batch-status]"),batch,refresh);
  view.querySelector("[data-batch-more]").onclick=()=>append().catch(error=>toast(error.message,"error"));
  await append();
}
