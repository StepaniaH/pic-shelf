import {api} from "./api.js";
import {escapeHtml, toast, uiConfirm, can} from "./app.js";
import {t} from "./i18n.js";
import {ImageSelection} from "./selection.js";
import {formatTimestamp} from "./format.js";
import {pickEntity} from "./catalog-ui.js";

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
  const counts = [["已修改 {n}", c.succeeded], ["无需修改 {n}", c.unchanged],
    ["待处理 {n}", c.pending], ["已撤销 {n}", c.undone], ["冲突 {n}", c.conflict + c.undo_conflict]];
  return [t("共 {v0} 张", {v0: batch.total}), ...counts.filter(([, n]) => n > 0).map(([label, n]) => t(label, {n}))].join(" · ");
}

function statusHtml(batch) {
  const busy = ["queued","running","cancelling","undoing"].includes(batch.state);
  return `<p><strong>${escapeHtml(actionLabel(batch))}</strong> · ${escapeHtml(formatTimestamp(batch.created_at))}</p>
    <p role="status">${escapeHtml(t(states[batch.state] || batch.state))} · ${escapeHtml(summary(batch))}</p>
    <div class="form-row">
      <button class="btn" data-batch-cancel ${!["preview","queued","running","undoing"].includes(batch.state)?"disabled hidden":""}>${t("取消剩余操作")}</button>
      <button class="btn" data-batch-resume ${!["cancelled","interrupted"].includes(batch.state) || !(batch.phase === "undo" ? batch.counts.succeeded : batch.counts.pending)?"disabled hidden":""}>${t("继续")}</button>
      <button class="btn danger" data-batch-undo ${!batch.counts.succeeded || busy?"disabled hidden":""}>${t("撤销修改")}</button>
      <a class="btn" href="#/batch/${batch.id}">${t("查看结果")}</a>
    </div><p class="error" data-status-error role="status" hidden></p><button class="btn" data-status-retry hidden>${t("重试")}</button>`;
}

function watchStatus(panel, initial, onChange) {
  watchers.get(panel)?.();
  let active = true;
  let timer, controller, revision = 0;
  const cancelRead = () => {
    revision++; clearTimeout(timer); controller?.abort(); controller = null;
  };
  watchers.set(panel,()=>{active=false;cancelRead();});
  const connected = () => active && panel.isConnected;
  let current = initial;
  let acting = false;
  const showError = error => {
    if (!connected()) return;
    panel.querySelector("[data-status-error]").textContent = error.message;
    panel.querySelector("[data-status-error]").hidden = false;
    panel.querySelector("[data-status-retry]").hidden = false;
  };
  const paint = batch => {
    if (!connected()) return;
    current = batch;
    panel.innerHTML = statusHtml(batch);
    if (acting) panel.querySelectorAll("button").forEach(button => { button.disabled = true; });
    const run = async (name,body) => {
      if (acting || !connected()) return;
      acting = true; cancelRead();
      panel.querySelectorAll("button").forEach(button => { button.disabled = true; });
      try {
        const result = await api(`/batches/${batch.id}/${name}`, {method:"POST",body});
        if (!connected()) return;
        paint(result);
        await onChange?.();
        schedule();
      } catch (error) { if (connected()) { paint(current); showError(error); } }
      finally {
        acting = false;
        if (connected()) panel.querySelectorAll("button").forEach(button => { button.disabled = button.hidden; });
      }
    };
    panel.querySelector("[data-batch-cancel]").onclick = () => run("cancel");
    panel.querySelector("[data-batch-resume]").onclick = () => run(batch.phase === "undo"?"undo":"execute", batch.phase === "undo"?{confirm:true}:{confirm:true,retry:true,background:batch.total>100});
    panel.querySelector("[data-batch-undo]").onclick = async () => {
      if (await uiConfirm(t("撤销修改"),t("撤销这次修改，保留之后的改动。"))) await run("undo",{confirm:true});
    };
    panel.querySelector("[data-status-retry]").onclick = () => poll(true);
  };
  async function poll(forceRefresh = false) {
    if (!connected() || acting || controller) return;
    clearTimeout(timer);
    const requested = ++revision;
    controller = new AbortController();
    panel.querySelector("[data-status-retry]").disabled = true;
    try {
      const result = await api(`/batches/${current.id}?include_items=false`, {signal:controller.signal});
      if (!connected() || requested !== revision) return;
      const previous = current.state;
      paint(result);
      if (forceRefresh || previous !== result.state && !["queued","running","cancelling","undoing"].includes(result.state)) await onChange?.();
      schedule();
    } catch (error) {
      if (!connected() || requested !== revision || error.name === "AbortError") return;
      showError(error); panel.querySelector("[data-status-retry]").disabled = false;
    } finally { if (requested === revision) controller = null; }
  }
  function schedule() {
    clearTimeout(timer);
    if (!connected() || !["queued","running","cancelling","undoing"].includes(current.state)) return;
    timer = setTimeout(poll,700);
  }
  paint(initial); schedule();
}

export function attachBatchControls(view,source,grid,refresh) {
  const selection = new ImageSelection();
  const bar = document.createElement("details");
  bar.className = "batch-controls";
  bar.setAttribute("aria-label",t("批量整理"));
  bar.innerHTML = `<summary>${t("批量整理")} · <span data-selection-count role="status"></span></summary><div class="form-row">
    <button class="btn small" data-select-loaded>${t("选择已加载图片")}</button>
    <button class="btn small" data-select-all>${t("选择全部结果")}</button>
    <button class="btn small" data-select-clear>${t("清空选择")}</button><a href="#/batches">${t("批量修改记录")}</a></div>
    <p class="hint">${t("按住 Shift 可连续选择。")}</p><div class="form-row"><label>${t("操作")} <select data-batch-operation>${operations.filter(([value])=>value.startsWith("workflow") || (value.startsWith("rating") || value.startsWith("favorite") ? can("rate") : can("tag_edit"))).map(([value,label])=>`<option value="${value}">${t(label)}</option>`).join("")}</select></label>
    <label data-batch-value-label>${t("标签（每行一个）")} <textarea data-batch-names rows="2"></textarea></label>
    <div data-batch-target-label hidden><button class="btn" data-batch-target>${t("选择目标")}</button></div>
    <label data-batch-rating-label hidden>${t("评分")} <input type="number" min="0" max="10" value="0" data-batch-rating></label>
    <button class="btn primary" data-batch-preview>${t("预览修改")}</button></div>
    <div data-batch-review></div>`;
  grid.before(bar);
  const status = document.createElement("section"); status.dataset.batchStatus = ""; bar.after(status);
  bar.hidden = true;
  const toggle = view.querySelector("[data-selection-toggle]");
  const selectionCount = bar.querySelector("[data-selection-count]");
  let selectionBar, toggleAnchor;
  if (view.classList.contains("quiet-collection-page")) {
    selectionBar = document.createElement("div");
    selectionBar.className = "collection-selection-bar";
    selectionBar.setAttribute("role", "region");
    selectionBar.setAttribute("aria-label", t("批量整理"));
    selectionBar.hidden = true;
    bar.querySelector("summary").textContent = t("批量整理");
    selectionBar.appendChild(selectionCount);
    view.querySelector(".page-head").after(selectionBar);
    toggleAnchor = document.createComment("selection action position");
    toggle.before(toggleAnchor);
  }
  const showSelection = on => {
    bar.hidden = !on;
    bar.open = on;
    grid.classList.toggle("selecting", on);
    toggle.setAttribute("aria-expanded", String(on));
    toggle.textContent = t(on ? "完成选择" : "选择");
    if (selectionBar) {
      const focused = document.activeElement === toggle;
      selectionBar.hidden = !on;
      if (on) selectionBar.appendChild(toggle);
      else toggleAnchor.after(toggle);
      if (focused) toggle.focus({preventScroll:true});
    }
  };
  toggle.onclick = () => {
    const on = bar.hidden;
    if (!on) { selection.clear(); invalidate(); update(); }
    showSelection(on);
  };
  let preview = null;
  let revision = 0;
  let previewing = false, applying = false, target = null, targetKind = null, requestKey = null;
  const review = bar.querySelector("[data-batch-review]");
  const operation = bar.querySelector("[data-batch-operation]");
  const invalidate = () => {revision++;preview=null;requestKey=null;review.replaceChildren();};
  const update = () => {
    selectionCount.textContent = selection.allMatching
      ?t("已选全部 {n} 张",{n:source.total}):t("已选 {n} 张",{n:selection.ids.size});
    grid.querySelectorAll("[data-select-image]").forEach(input=>{input.checked=selection.allMatching || selection.ids.has(Number(input.dataset.selectImage));input.disabled=selection.allMatching;});
    const needsTarget = operation.value.startsWith("artist_") || operation.value.startsWith("album_");
    bar.querySelector("[data-batch-preview]").disabled = previewing || applying || (!selection.allMatching && !selection.ids.size) || (needsTarget && !target);
  };
  let extendSelection = false;
  grid.addEventListener("click",event=>{if(event.target.matches("[data-select-image]"))extendSelection=event.shiftKey;});
  grid.addEventListener("change",event=>{
    const input=event.target.closest("[data-select-image]");
    if(input){selection.range(Number(input.dataset.selectImage),input.checked,source.items,extendSelection);extendSelection=false;if(input.checked)showSelection(true);invalidate();update();}
  });
  for(const [name,fn] of [["loaded",()=>selection.loaded(source.items)],["all",()=>selection.all()],["clear",()=>selection.clear()]]) {
    bar.querySelector(`[data-select-${name}]`).onclick=()=>{fn();invalidate();update();};
  }
  bar.querySelectorAll("input,textarea,select").forEach(input=>input.addEventListener("input",invalidate));
  view.querySelector("#sort-select")?.addEventListener("change", invalidate);
  view.querySelector("[data-refresh]")?.addEventListener("click", () => { if (selection.allMatching) invalidate(); });
  const targetButton = bar.querySelector("[data-batch-target]");
  operation.onchange=()=>{
    invalidate();
    const kind=operation.value;
    bar.querySelector("[data-batch-value-label]").hidden=!kind.startsWith("tags_");
    bar.querySelector("[data-batch-rating-label]").hidden=kind!=="rating";
    const entity=kind.startsWith("artist_")?"artist":kind.startsWith("album_")?"album":null;
    bar.querySelector("[data-batch-target-label]").hidden=!entity;
    if (entity !== targetKind) { target = null; targetButton.textContent = t(entity === "album" ? "选择专辑" : "选择艺术家"); }
    targetKind = entity; update();
  };
  targetButton.onclick = async () => {
    const requestedKind = targetKind;
    targetButton.disabled = true;
    try {
      const picked = await pickEntity(requestedKind === "album" ? "albums" : "artists", {libraryId:source.filters.library_id, allowCreate:operation.value === "album_add"});
      if (picked && targetKind === requestedKind && bar.isConnected) { target = picked; targetButton.textContent = picked.name; invalidate(); update(); }
    } finally { targetButton.disabled = false; }
  };
  bar.querySelector("[data-batch-preview]").onclick=async()=>{
    if (previewing || applying) return;
    previewing = true; update();
    const requestedRevision=revision;
    let kind=operation.value,action={kind};
    if(kind.startsWith("tags_"))action.names=bar.querySelector("[data-batch-names]").value.split("\n").map(s=>s.trim()).filter(Boolean);
    else if(kind.startsWith("artist_"))action.artist_id=target?.id;
    else if(kind.startsWith("album_"))action.album_id=target?.id;
    else if(kind.startsWith("rating"))action={kind:"rating",value:kind==="rating_clear"?null:Number(bar.querySelector("[data-batch-rating]").value)};
    else if(kind.startsWith("favorite"))action={kind:"favorite",value:kind==="favorite_on"};
    else action={kind:"workflow",value:kind==="workflow_done"?"done":"inbox"};
    try {
      requestKey ||= crypto.randomUUID();
      const result=await api("/batches/preview",{method:"POST",body:{action,...selection.payload(source),request_key:requestKey}});
      if(requestedRevision!==revision || !bar.isConnected)return;
      preview=result;
      review.innerHTML=`<p role="status">${escapeHtml(t("将修改 {n} 张图片",{n:preview.total}))} · ${escapeHtml(actionLabel(preview))}</p>
        <ul class="batch-targets">${preview.items.slice(0,20).map(item=>`<li>${escapeHtml(item.library_name)} / ${escapeHtml(item.rel_path)}</li>`).join("")}</ul>
        ${preview.total>20?`<p>${escapeHtml(t("显示前 20 张"))}</p>`:""}
        <button class="btn primary" data-batch-apply>${t("确认修改")}</button>`;
      review.querySelector("[data-batch-apply]").onclick=async event=>{
        if(!preview)return;
        applying=true;event.target.disabled=true;update();
        try {
          const value=await api(`/batches/${preview.id}/execute`,{method:"POST",body:{confirm:true,background:preview.total>100}});
          invalidate();
          watchStatus(status,value,refresh);
          await refresh();
        }catch(error){toast(error.message,"error");event.target.disabled=false;}finally{applying=false;update();}
      };
    }catch(error){toast(error.message,"error");}finally { previewing = false; update(); }
  };
  operation.onchange();
  update();
  return update;
}

export async function renderBatchHistory(view,id=null) {
  if(!id){
    view.innerHTML=`<h1>${t("批量修改记录")}</h1><div data-batch-list></div><button class="btn" data-history-more>${t("加载更多")}</button>`;
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
        if(!list.children.length)list.textContent=t("还没有批量修改记录");
      } finally {more.disabled=false;}
    };
    more.onclick=()=>load().catch(error=>toast(error.message,"error"));
    await load();
    return;
  }
  const batch=await api(`/batches/${id}?include_items=false`);
  view.innerHTML=`<h1>${t("批量修改记录")}</h1><div data-batch-status></div><p>${t("逐项结果")}</p><div class="table-wrap"><table class="data"><thead><tr><th>${t("图片")}</th><th>${t("状态")}</th></tr></thead><tbody></tbody></table></div><button class="btn" data-batch-more>${t("加载更多")}</button>`;
  const rows=view.querySelector("tbody");
  let offset=0;
  let revision=0, loading=false;
  const more = view.querySelector("[data-batch-more]");
  const append=async(reset=false)=>{
    if(loading && !reset)return;
    if(reset)revision++;
    const requested=revision;
    loading=true;more.disabled=true;
    try {
      const page=await api(`/batches/${id}?offset=${reset?0:offset}`);
      if(requested!==revision || !view.isConnected)return;
      if(reset){offset=0;rows.replaceChildren();}
      for(const item of page.items){
        const reason = item.error_code === "batch_changed" ? t("图片已被其他操作修改") : item.error_code;
        const tr=document.createElement("tr");tr.innerHTML=`<td><a href="#/view/${item.image_id}">${escapeHtml(item.library_name)} / ${escapeHtml(item.rel_path)}</a></td><td>${escapeHtml(t(itemStates[item.state] || item.state))}${reason?" · "+escapeHtml(reason):""}</td>`;rows.appendChild(tr);
      }
      offset+=page.items.length;
      more.hidden=offset>=page.total;
    } finally {if(requested===revision){loading=false;more.disabled=false;}}
  };
  const refresh=()=>append(true);
  watchStatus(view.querySelector("[data-batch-status]"),batch,refresh);
  more.onclick=()=>append().catch(error=>toast(error.message,"error"));
  await append();
}
