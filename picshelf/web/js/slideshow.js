// Reusable rules create snapshots; resuming never silently starts a new queue.
import {api, imageMediaUrl} from "./api.js";
import {escapeHtml, toast, showModal, state} from "./app.js";
import {t} from "./i18n.js";
import {activateDialog, isTopDialog} from "./dialog.js";
import {PlaybackClock} from "./playback-clock.js";
import {jobKey} from "./jobs.js";
import {PreviewCache, decodeImage} from "./viewer-load.js";

const orders={sequential:"顺序",shuffle:"乱序",random:"随机（不重复）",shuffle_each_round:"每轮重新打乱",sample:"随机抽取（可重复）"};
const sorts={path:"按文件名",newest:"最新导入",oldest:"最早导入",rating:"按我的评分",avg:"按平均分"};
const storageKey=()=>`picshelf.play.${state.user?.id}`;
function remember(token){try{localStorage.setItem(storageKey(),token);}catch{/* Playback still works without local storage. */}}
function remembered(){try{return localStorage.getItem(storageKey());}catch{return null;}}

function sourceSpec(source) {
  const values={...source.context,...source.filters},spec={};
  for(const key of ["album_id","artist_id","library_id","tag","rating_min","workflow"]) {
    if(values[key]!==undefined && values[key]!==null)spec[key]=values[key];
  }
  if(values.favorites===true || values.favorites==="true")spec.favorites=true;
  return spec;
}

function sourceDescription(spec, libraries = []) {
  const source = spec?.source || {}, parts = [];
  if (source.library_id) parts.push(libraries.find(library => library.id === source.library_id)?.name || t("图库 #{n}", {n:source.library_id}));
  if (source.album_id) parts.push(t("专辑 #{n}", {n:source.album_id}));
  if (source.artist_id) parts.push(t("艺术家 #{n}", {n:source.artist_id}));
  if (source.tag) parts.push(t("标签：{name}", {name:source.tag}));
  if (source.favorites) parts.push(t("我的收藏"));
  if (source.rating_min != null) parts.push(t("我的评分 ≥ {n}", {n:source.rating_min}));
  if (source.workflow) parts.push(t(source.workflow === "done" ? "已整理" : "待整理"));
  if (spec?.query) parts.push(spec.query);
  return parts.join(" · ") || t("全部图片");
}

export function openSlideshow(source, {startImage = null, rule = null, editRule = false} = {}) {
  let preferences = {};
  try { preferences = JSON.parse(localStorage.getItem("picshelf.slideshow") || "{}"); } catch { /* Optional preferences. */ }
  const saved = {interval:5, order:"sequential", size:"fit", captions:true, loop:true, limit:0,
    ...preferences, sort:source.sort in sorts ? source.sort : "path", ...rule?.spec};
  const title = editRule ? (rule ? "编辑播放规则" : "新建播放规则") : "幻灯片设置";
  let currentRule = rule;
  const nameField = `<label>${t("名称")}<input id="ss-name" maxlength="200" ${editRule ? "required" : ""}></label>`;
  const queryField = `<label class="play-query">${t("搜索条件")}<textarea id="ss-query" rows="2" maxlength="4096"></textarea></label>`;
  showModal({title:t(title), bodyHtml:`<form class="stack-form" data-play-form>
    ${editRule ? `${nameField}<label>${t("图库")}<select id="ss-library"><option value="">${t("全部图片")}</option></select></label>${queryField}` : `<p>${escapeHtml(source.title || t("当前列表"))}</p>`}
    ${startImage ? `<label class="checkbox-row"><input type="checkbox" id="ss-from-current" checked>${t("从当前图片开始")} · ${escapeHtml(startImage.filename)}</label>` : ""}
    <div class="form-row"><label>${t("间隔（秒；0 为手动）")} <input id="ss-interval" type="number" min="0" max="86400" required></label>
    <label>${t("播放顺序")} <select id="ss-order">${Object.entries(orders).map(([k,v]) => `<option value="${k}">${t(v)}</option>`).join("")}</select></label></div>
    <details class="play-advanced"><summary>${t("更多设置")}</summary>
      ${editRule ? "" : queryField}
      <div class="form-row"><label>${t("数量（0 为全部，最多 10000）")} <input id="ss-count" type="number" min="0" max="1000000" required></label>
      <label>${t("排序")} <select id="ss-sort">${Object.entries(sorts).map(([k,v]) => `<option value="${k}">${t(v)}</option>`).join("")}</select></label></div>
      <div class="form-row"><label><input type="checkbox" id="ss-loop">${t("循环播放")}</label>
      <label><input type="checkbox" id="ss-captions">${t("显示文件名和标签")}</label></div>
      <label>${t("尺寸")} <select id="ss-size">${[["fit","适应"],["fill","填满"],["original","原图"]].map(([k,v]) => `<option value="${k}">${t(v)}</option>`).join("")}</select></label>
      ${editRule ? "" : `${nameField}<button type="button" class="btn" id="ss-save">${t("保存播放规则")}</button>`}
    </details>
    <p class="error" role="alert" id="ss-error"></p>
    <div class="modal-actions"><button type="button" class="btn" data-close>${t("取消")}</button>
      ${editRule ? `<button type="submit" class="btn primary" id="ss-save">${t("保存")}</button>` : `<button type="submit" class="btn primary" id="ss-start">${t("开始播放")}</button>`}</div>
    </form>`, onMount(el, close) {
      const form = el.querySelector("form"), error = el.querySelector("#ss-error");
      const scope = {...(rule?.spec?.source || sourceSpec(source))};
      form.addEventListener("invalid", event => {
        const details = event.target.closest("details");
        if (details) details.open = true;
      }, true);
      if (editRule && Object.keys(scope).some(key => key !== "library_id" && scope[key])) {
        const context = document.createElement("p"); context.className = "muted";
        context.textContent = sourceDescription({source:{...scope, library_id:null}});
        el.querySelector("#ss-library").closest("label").after(context);
      }
      for (const key of ["interval","order","size","sort"]) el.querySelector(`#ss-${key}`).value = saved[key];
      el.querySelector("#ss-count").value = saved.limit || 0;
      el.querySelector("#ss-captions").checked = saved.captions; el.querySelector("#ss-loop").checked = saved.loop;
      el.querySelector("#ss-query").value = rule?.spec?.query ?? source.q ?? "";
      const name = el.querySelector("#ss-name"); name.value = rule?.name || "";
      const library = el.querySelector("#ss-library");
      if (library) {
        if (scope.library_id) library.add(new Option(t("图库 #{n}", {n:scope.library_id}), scope.library_id));
        library.value = scope.library_id || "";
        api("/libraries").then(libraries => {
          if (!el.isConnected) return;
          const selected = library.value;
          for (const item of libraries) {
            const existing = [...library.options].find(option => option.value === String(item.id));
            if (existing) existing.textContent = item.name;
            else library.add(new Option(item.name, item.id));
          }
          library.value = selected;
        }).catch(failure => { if (el.isConnected) error.textContent = failure.message; });
      }
      const read = () => {
        const selectedSource = {...scope};
        if (library) {
          delete selectedSource.library_id;
          if (library.value) selectedSource.library_id = Number(library.value);
        }
        return {...(Number.isSafeInteger(saved.seed) ? {seed:saved.seed} : {}), source:selectedSource,
          query:el.querySelector("#ss-query").value, order:el.querySelector("#ss-order").value, sort:el.querySelector("#ss-sort").value,
          interval:Number(el.querySelector("#ss-interval").value), limit:Number(el.querySelector("#ss-count").value),
          captions:el.querySelector("#ss-captions").checked, loop:el.querySelector("#ss-loop").checked, size:el.querySelector("#ss-size").value};
      };
      let busy = false;
      async function run(action) {
        if (busy) return;
        busy = true; error.textContent = "";
        const buttons = [...form.querySelectorAll("button:not([data-close])")]; buttons.forEach(button => { button.disabled = true; });
        try { await action(); }
        catch (failure) { if (el.isConnected) error.textContent = failure.message + (failure.code === "play_start_unavailable" ? " " + failure.nextStep : ""); }
        finally { busy = false; buttons.forEach(button => { button.disabled = false; }); }
      }
      async function save() {
        const body = {name:name.value, spec:read()};
        if (currentRule) body.version = currentRule.version;
        currentRule = await api(currentRule ? `/play/rules/${currentRule.id}` : "/play/rules", {method:currentRule ? "PATCH" : "POST", body});
        toast(t("规则已保存"), "ok");
        if (editRule) close();
        Promise.resolve(source.onRuleSaved?.()).catch(failure => toast(failure.message, "error"));
      }
      if (!editRule) el.querySelector("#ss-save").onclick = () => {
        name.required = true;
        if (form.reportValidity()) run(save);
        name.required = false;
      };
      form.onsubmit = event => {
        event.preventDefault();
        run(editRule ? save : async () => {
          const spec = read();
          const session = await api("/play/sessions", {method:"POST", body:{...spec,
            ...(el.querySelector("#ss-from-current")?.checked ? {start_image_id:startImage.id} : {})}});
          if (!el.isConnected) return;
          if (!session.total) { error.textContent = t("没有可播放的图片，请调整筛选条件。"); return; }
          try { localStorage.setItem("picshelf.slideshow", JSON.stringify({interval:spec.interval,order:spec.order,size:spec.size,captions:spec.captions,loop:spec.loop})); } catch { /* Optional preferences. */ }
          close(); await showPlayback(session);
        });
      };
      if (editRule) name.focus();
    }});
}

export async function resumeLastPlayback() {
  const token=remembered();if(!token)return;
  const current=await api(`/play/sessions/${token}`);
  const paused=await api(`/play/sessions/${token}/pause`,{method:"POST",body:{paused:true,expected_version:current.version,request_key:jobKey()}});
  await showPlayback(paused);
}

export async function renderPlayRules(view) {
  view.innerHTML=`<div class="page-head"><h1>${t("播放规则")}</h1>
    <button class="btn" data-resume ${remembered()?"":"disabled"}>${t("打开上次播放")}</button>
    <button class="btn primary" data-new>${t("新建规则")}</button></div>
    <div class="lib-cards" data-rules></div><button class="btn" data-more>${t("加载更多")}</button>`;
  view.querySelector("[data-resume]").onclick=()=>resumeLastPlayback().catch(e=>toast(e.message,"error"));
  view.querySelector("[data-new]").onclick=()=>openSlideshow({title:t("全部图片"),q:"",sort:"path",onRuleSaved:()=>renderPlayRules(view)},{editRule:true});
  const list=view.querySelector("[data-rules]"),more=view.querySelector("[data-more]");let offset=0;
  const libraries=await api("/libraries");
  async function load(){more.disabled=true;try{
    const data=await api(`/play/rules?offset=${offset}`);
    for(const rule of data.items){const card=document.createElement("article");card.className="lib-card";
      card.innerHTML=`<h2>${escapeHtml(rule.name)}</h2><p class="query-expression">${escapeHtml(sourceDescription(rule.spec,libraries))}</p>
      <div class="form-row"><button class="btn primary" data-start>${t("开始播放")}</button><button class="btn" data-edit>${t("编辑")}</button>
      <a class="btn" href="/api/v1/play/rules/${rule.id}/export" download="play-rule-${rule.id}.json">${t("导出规则")}</a></div>
      <p class="error">${escapeHtml(rule.errors.join(" "))}</p>`;
      card.querySelector("[data-start]").onclick=async event=>{const button=event.currentTarget;button.disabled=true;
        try{await showPlayback(await api(`/play/rules/${rule.id}/start`,{method:"POST"}));}catch(e){toast(e.message,"error");}finally{button.disabled=false;}};
      card.querySelector("[data-edit]").onclick=async()=>{try{const fresh=await api(`/play/rules/${rule.id}`);openSlideshow({title:fresh.name,onRuleSaved:()=>renderPlayRules(view)},{rule:fresh,editRule:true});}catch(e){toast(e.message,"error");}};
      list.append(card);
    }
    if(offset===0&&!data.items.length)list.textContent=t("还没有播放规则");
    offset=data.next_offset;more.hidden=offset===null;
  }finally{more.disabled=false;}}
  more.onclick=()=>load().catch(e=>toast(e.message,"error"));await load();
}

async function showPlayback(session) {
  if(!session.total){toast(t("没有可播放的图片，请调整筛选条件。"),"error");return;}
  remember(session.token);
  const opts={size:"fit",captions:true,...session.spec};
  if(!["fit","fill","original"].includes(opts.size))opts.size="fit";
  const backdrop=document.createElement("div");backdrop.className="slideshow-backdrop";
  backdrop.setAttribute("aria-label",t("幻灯片"));
  backdrop.innerHTML=`<div class="slideshow-stage size-${opts.size}"><img id="ss-img" alt="" hidden>
    <div class="slideshow-controls"><button class="btn small" id="ss-prev">${t("‹ 上一张")}</button>
    <button class="btn small" id="ss-pause"></button><button class="btn small" id="ss-next">${t("下一张 ›")}</button>
    <button class="btn small" id="ss-retry" hidden>${t("重试")}</button>
    <button class="btn small" id="ss-exit">${t("暂停并退出")}</button></div></div>
    <div class="slideshow-caption"><p id="ss-status" role="status"></p><p id="ss-caption"></p></div>`;
  document.body.append(backdrop);
  let current=session,closed=false,busy=false,pending=null,activeRequest=null,revision=0,imageController=null;
  const previews=new PreviewCache();
  let imgEl=backdrop.querySelector("#ss-img");
  const status=backdrop.querySelector("#ss-status"),caption=backdrop.querySelector("#ss-caption");
  const clock=new PlaybackClock(session.interval,()=>command("next"));
  clock.update({paused:session.paused,hidden:document.hidden});
  const releaseFocus=activateDialog(backdrop,close,{dismiss:()=>close({force:true})});
  const warning=session.candidate_limited
    ?t("本次从 {n} 张图片中随机抽取；共匹配 {total} 张。",{n:session.candidate_count,total:session.matched_total})
    :session.truncated?t("本次播放 {n} 张，已达到播放上限。",{n:session.total}):"";
  function buttons(){backdrop.querySelector("#ss-pause").textContent=current.paused?t("▶ 继续"):t("⏸ 暂停");
    for(const key of ["prev","pause","next","retry"])backdrop.querySelector(`#ss-${key}`).disabled=busy;}
  function failure(message){backdrop.querySelector("#ss-retry").hidden=false;current={...current,paused:true};clock.update({paused:true});status.textContent=message;buttons();}
  function hideUnauthorized(error){if([401,403,404].includes(error.status)){++revision;imageController?.abort();previews.clear();imgEl.hidden=true;caption.textContent="";}}
  async function command(action,extra={},retry=false) {
    if(closed||busy)return;
    const request=retry&&pending?pending:{action,body:{...extra,request_key:jobKey(),expected_version:current.version}};
    pending=request;busy=true;buttons();clock.update({busy:true});
    try {
      activeRequest=api(`/play/sessions/${session.token}/${request.action}`,{method:"POST",body:request.body});
      const response=await activeRequest;
      if(closed)return;
      current={...response,paused:retry?true:response.paused};pending=null;clock.update({paused:current.paused});
      if(request.action!=="pause")present(response);
      else if(!response.paused)status.textContent=warning;
      if(retry&&!response.paused)queueMicrotask(()=>command("pause",{paused:true}));
    }catch(error){
      if(!closed){if(error.status)pending=null;hideUnauthorized(error);failure(error.message||t("连接失败，请重试。"));}
    }finally{busy=false;activeRequest=null;if(!closed){buttons();clock.update({busy:false});}}
  }
  async function present(response) {
    const version=++revision;imageController?.abort();clock.update({ready:false});
    if(!response.image || response.unavailable){
      imgEl.hidden=true;caption.textContent="";
      failure(t("图片无法显示，请重试或跳到下一张。"));
      if(!response.paused)queueMicrotask(()=>command("pause",{paused:true}));
      return;
    }
    status.textContent=t("加载中…");
    const controller=imageController=new AbortController();
    const original=opts.size==="original";
    const url=imageMediaUrl(original?"original":"preview",response.image);
    try {
      const image=await (original?decodeImage(url,controller.signal):previews.load(url,controller.signal));
      if(closed||version!==revision)return;
      image.id="ss-img";image.alt=response.image.filename;image.hidden=false;
      if(image!==imgEl)imgEl.replaceWith(image);
      imgEl=image;
      const tags=(response.image.tags||[]).map(tag=>tag.name).slice(0,8).join("、");
      caption.textContent=`${response.position} / ${response.total}`+(opts.captions?` · ${response.image.filename}${tags?" · "+tags:""}`:"");
      status.textContent=response.done?t("播放完成"):warning;
      backdrop.querySelector("#ss-retry").hidden=true;
      clock.update({ready:true,paused:current.paused});
      if(!original&&!document.hidden&&!navigator.connection?.saveData&&response.next_image)
        previews.warm([imageMediaUrl("preview",response.next_image)]);
    } catch {
      if(closed||version!==revision)return;
      imgEl.hidden=true;caption.textContent="";
      const shouldPause=!current.paused;
      failure(t("图片无法显示，请重试或跳到下一张。"));
      if(shouldPause)command("pause",{paused:true});
    }
  }
  async function retry() {
    if(closed||busy)return;
    if(pending){await command(pending.action,{},true);return;}
    busy=true;buttons();clock.update({busy:true});
    try {const latest=await api(`/play/sessions/${session.token}`);
      current=await api(`/play/sessions/${session.token}/pause`,{method:"POST",body:{paused:true,expected_version:latest.version,request_key:jobKey()}});
      if(!closed)present(current);}
    catch(error){if(!closed){hideUnauthorized(error);failure(error.message);}}
    finally{busy=false;if(!closed){buttons();clock.update({busy:false});}}
  }
  async function close({force=false}={}) {
    if(closed)return;closed=true;++revision;clock.close();imageController?.abort();previews.clear();
    document.removeEventListener("keydown",onKey);document.removeEventListener("visibilitychange",onVisibility);
    backdrop.remove();releaseFocus();
    if(force)return;
    try {if(activeRequest)await activeRequest;
      const latest=await api(`/play/sessions/${session.token}`);
      await api(`/play/sessions/${session.token}/pause`,{method:"POST",body:{paused:true,expected_version:latest.version,request_key:jobKey()}});
    }catch{toast(t("暂停状态未保存，请重新打开播放检查。"),"error");}
  }
  function onKey(event){if(!isTopDialog(backdrop)||event.ctrlKey||event.metaKey||event.altKey||event.isComposing||event.target.closest("input,textarea,select,[contenteditable=true]"))return;
    if(event.key==="Escape")close();
    else if(event.key===" "){event.preventDefault();command("pause",{paused:!current.paused});}
    else if(event.key==="ArrowRight")command("next");else if(event.key==="ArrowLeft")command("prev");}
  function onVisibility(){clock.update({hidden:document.hidden});}
  backdrop.querySelector("#ss-next").onclick=()=>command("next");backdrop.querySelector("#ss-prev").onclick=()=>command("prev");
  backdrop.querySelector("#ss-pause").onclick=()=>command("pause",{paused:!current.paused});
  backdrop.querySelector("#ss-retry").onclick=retry;backdrop.querySelector("#ss-exit").onclick=close;
  document.addEventListener("keydown",onKey);document.addEventListener("visibilitychange",onVisibility);buttons();
  if(session.cursor<0)await command("next");else present(session);
}
