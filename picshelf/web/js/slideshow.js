// Reusable rules create snapshots; resuming never silently starts a new queue.
import {api, imageMediaUrl} from "./api.js";
import {escapeHtml, toast, showModal, state} from "./app.js";
import {t} from "./i18n.js";
import {activateDialog, isTopDialog} from "./dialog.js";
import {PlaybackClock} from "./playback-clock.js";
import {jobKey} from "./jobs.js";

const orders={sequential:"顺序",shuffle:"乱序",random:"随机（旧式无放回）",shuffle_each_round:"每轮重新打乱",sample:"独立抽样（允许重复）"};
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

export function openSlideshow(source,startIndex=0,rule=null) {
  let preferences={};
  try {preferences=JSON.parse(localStorage.getItem("picshelf.slideshow")||"{}");}catch{/* Use defaults for invalid preferences. */}
  const saved={interval:5,order:"sequential",size:"fit",captions:true,loop:true,limit:0,
    sort:source.sort in sorts?source.sort:"path",...preferences,...rule?.spec};
  let currentRule=rule;
  showModal({title:t("幻灯片设置"),bodyHtml:`
    <p>${escapeHtml(source.title||t("当前列表"))}</p>
    <label class="play-query">${t("检索表达式")}<textarea id="ss-query" rows="2"></textarea></label>
    <div class="form-row"><label>${t("间隔（秒；0 为手动）")} <input id="ss-interval" type="number" min="0" max="86400"></label>
    <label>${t("顺序")} <select id="ss-order">${Object.entries(orders).map(([k,v])=>`<option value="${k}">${t(v)}</option>`).join("")}</select></label></div>
    <div class="form-row"><label>${t("数量（0 为全部，最多 10000）")} <input id="ss-count" type="number" min="0" max="1000000"></label>
    <label>${t("排序")} <select id="ss-sort">${Object.entries(sorts).map(([k,v])=>`<option value="${k}">${t(v)}</option>`).join("")}</select></label></div>
    <div class="form-row"><label><input type="checkbox" id="ss-loop">${t("循环（轮播）")}</label>
    <label><input type="checkbox" id="ss-captions">${t("显示字幕（文件名/标签）")}</label></div>
    <div class="form-row"><label>${t("尺寸")} <select id="ss-size">${[["fit","适应"],["fill","填满"],["original","原图"]].map(([k,v])=>`<option value="${k}">${t(v)}</option>`).join("")}</select></label></div>
    <p class="hint">${t("开始时固定成员和配置；修改规则不会改变旧会话。每轮打乱和重复抽样只使用本次快照。")}</p>
    <label>${t("规则名称")}<input id="ss-name" maxlength="200"></label>
    <div class="modal-actions"><button class="btn primary" id="ss-start">${t("开始播放")}</button>
    <button class="btn" id="ss-save">${t("保存播放规则")}</button><button class="btn" data-close>${t("取消")}</button></div>
    <p class="error" role="status" id="ss-error"></p>`,onMount(el,close) {
      for(const key of ["interval","order","size","sort"])el.querySelector(`#ss-${key}`).value=saved[key];
      el.querySelector("#ss-count").value=saved.limit||0;
      el.querySelector("#ss-captions").checked=saved.captions;el.querySelector("#ss-loop").checked=saved.loop;
      el.querySelector("#ss-query").value=rule?.spec?.query ?? source.q ?? "";
      el.querySelector("#ss-name").value=rule?.name || "";
      const read=()=>({...(Number.isSafeInteger(saved.seed)?{seed:saved.seed}:{}),source:rule?.spec?.source || sourceSpec(source),query:el.querySelector("#ss-query").value,
        order:el.querySelector("#ss-order").value,sort:el.querySelector("#ss-sort").value,
        interval:Number(el.querySelector("#ss-interval").value),limit:Number(el.querySelector("#ss-count").value),
        captions:el.querySelector("#ss-captions").checked,loop:el.querySelector("#ss-loop").checked,size:el.querySelector("#ss-size").value});
      async function run(button,action){button.disabled=true;el.querySelector("#ss-error").textContent="";
        try{await action();}catch(error){el.querySelector("#ss-error").textContent=error.message;}finally{button.disabled=false;}}
      el.querySelector("#ss-save").onclick=event=>run(event.currentTarget,async()=>{
        const body={name:el.querySelector("#ss-name").value,spec:read()};
        if(currentRule)body.version=currentRule.version;
        currentRule=await api(currentRule?`/play/rules/${currentRule.id}`:"/play/rules",{method:currentRule?"PATCH":"POST",body});
        toast(t("规则已保存"),"ok");
        Promise.resolve(source.onRuleSaved?.()).catch(error=>toast(error.message,"error"));
      });
      el.querySelector("#ss-start").onclick=event=>run(event.currentTarget,async()=>{
        const spec=read();const session=await api("/play/sessions",{method:"POST",body:spec});
        try{localStorage.setItem("picshelf.slideshow",JSON.stringify({interval:spec.interval,order:spec.order,size:spec.size,captions:spec.captions,loop:spec.loop}));}catch{/* Optional preferences. */}
        close();await showPlayback(session);
      });
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
    <button class="btn" data-resume ${remembered()?"":"disabled"}>${t("恢复上次会话")}</button>
    <button class="btn primary" data-new>${t("新建规则")}</button></div>
    <p>${t("恢复保留旧队列与游标，并先暂停；开始规则会按当前目录生成新会话。")}</p>
    <div class="lib-cards" data-rules></div><button class="btn" data-more>${t("加载更多")}</button>`;
  view.querySelector("[data-resume]").onclick=()=>resumeLastPlayback().catch(e=>toast(e.message,"error"));
  view.querySelector("[data-new]").onclick=()=>openSlideshow({title:t("全部图片"),q:"",sort:"path",onRuleSaved:()=>renderPlayRules(view)});
  const list=view.querySelector("[data-rules]"),more=view.querySelector("[data-more]");let offset=0;
  async function load(){more.disabled=true;try{
    const data=await api(`/play/rules?offset=${offset}`);
    for(const rule of data.items){const card=document.createElement("article");card.className="lib-card";
      card.innerHTML=`<h2>${escapeHtml(rule.name)}</h2><p class="query-expression">${escapeHtml(rule.spec?.query||t("全部图片"))}</p>
      <div class="form-row"><button class="btn primary" data-start>${t("开始播放")}</button><button class="btn" data-edit>${t("编辑")}</button>
      <a class="btn" href="/api/v1/play/rules/${rule.id}/export" download="play-rule-${rule.id}.json">${t("导出规则")}</a></div>
      <p class="error">${escapeHtml(rule.errors.join(" "))}</p>`;
      card.querySelector("[data-start]").onclick=async event=>{const button=event.currentTarget;button.disabled=true;
        try{await showPlayback(await api(`/play/rules/${rule.id}/start`,{method:"POST"}));}catch(e){toast(e.message,"error");}finally{button.disabled=false;}};
      card.querySelector("[data-edit]").onclick=async()=>{try{const fresh=await api(`/play/rules/${rule.id}`);openSlideshow({title:fresh.name,onRuleSaved:()=>renderPlayRules(view)},0,fresh);}catch(e){toast(e.message,"error");}};
      list.append(card);
    }
    if(offset===0&&!data.items.length)list.textContent=t("还没有播放规则；可从图库的幻灯片设置保存。");
    offset=data.next_offset;more.hidden=offset===null;
  }finally{more.disabled=false;}}
  more.onclick=()=>load().catch(e=>toast(e.message,"error"));await load();
}

async function showPlayback(session) {
  if(!session.total){toast(t("来源为空，没有可播放的图片"),"error");return;}
  remember(session.token);
  const opts={size:"fit",captions:true,...session.spec};
  if(!["fit","fill","original"].includes(opts.size))opts.size="fit";
  const backdrop=document.createElement("div");backdrop.className="slideshow-backdrop";
  backdrop.setAttribute("aria-label",t("幻灯片"));
  backdrop.innerHTML=`<div class="slideshow-stage size-${opts.size}"><img id="ss-img" alt="" hidden>
    <div class="slideshow-controls"><button class="btn small" id="ss-prev">${t("‹ 上一张")}</button>
    <button class="btn small" id="ss-pause"></button><button class="btn small" id="ss-next">${t("下一张 ›")}</button>
    <button class="btn small" id="ss-retry">${t("重连 / 重试图片")}</button>
    <button class="btn small" id="ss-exit">${t("暂停并退出")}</button></div></div>
    <div class="slideshow-caption"><p id="ss-status" role="status"></p><p id="ss-caption"></p></div>`;
  document.body.append(backdrop);
  let current=session,closed=false,busy=false,pending=null,activeRequest=null,revision=0,cancelImage=null;
  let imgEl=backdrop.querySelector("#ss-img");
  const status=backdrop.querySelector("#ss-status"),caption=backdrop.querySelector("#ss-caption");
  const clock=new PlaybackClock(session.interval,()=>command("next"));
  clock.update({paused:session.paused,hidden:document.hidden});
  const releaseFocus=activateDialog(backdrop,close);
  const warning=session.matched_total===null?t("旧会话未记录完整匹配量；当前队列和游标已保留。"):
    session.warnings?.length?t("匹配 {matched} 张，队列 {total} 张，候选 {candidates} 张；上限 10000，请缩小条件以覆盖所需图片。",{matched:session.matched_total,total:session.total,candidates:session.candidate_count}):"";
  function buttons(){backdrop.querySelector("#ss-pause").textContent=current.paused?t("▶ 继续"):t("⏸ 暂停");
    for(const key of ["prev","pause","next","retry"])backdrop.querySelector(`#ss-${key}`).disabled=busy;}
  function failure(message){current={...current,paused:true};clock.update({paused:true});status.textContent=message;buttons();}
  function hideUnauthorized(error){if([401,403,404].includes(error.status)){++revision;cancelImage?.();imgEl.hidden=true;caption.textContent="";}}
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
      if(!closed){if(error.status)pending=null;hideUnauthorized(error);failure(error.message||t("连接失败，请重连后继续"));}
    }finally{busy=false;activeRequest=null;if(!closed){buttons();clock.update({busy:false});}}
  }
  function present(response) {
    const version=++revision;cancelImage?.();cancelImage=null;clock.update({ready:false});
    imgEl.hidden=true;caption.textContent="";
    if(!response.image || response.unavailable){
      failure(t("图片无法显示，请重试或用下一张跳过"));
      if(!response.paused)queueMicrotask(()=>command("pause",{paused:true}));
      return;
    }
    status.textContent=t("加载中…");
    const image=new Image();image.id="ss-img";image.alt=response.image.filename;
    const timeout=setTimeout(()=>failed(),20000);
    function cleanup(){clearTimeout(timeout);image.onload=null;image.onerror=null;}
    function failed(){cleanup();if(closed||version!==revision)return;
      const shouldPause=!current.paused;
      failure(t("图片无法显示，请重试或用下一张跳过"));
      if(shouldPause)command("pause",{paused:true});
    }
    cancelImage=()=>{cleanup();image.src="";};
    image.onload=async()=>{try{await image.decode();}catch{failed();return;}cleanup();if(closed||version!==revision)return;
      imgEl.replaceWith(image);imgEl=image;cancelImage=null;
      const tags=(response.image.tags||[]).map(tag=>tag.name).slice(0,8).join("、");
      caption.textContent=`${response.position} / ${response.total}`+(opts.captions?` · ${response.image.filename}${tags?" · "+tags:""}`:"");
      status.textContent=response.done?t("播放完成"):warning;
      clock.update({ready:true,paused:current.paused});
    };
    image.onerror=failed;image.src=imageMediaUrl(opts.size==="original"?"original":"preview",response.image);
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
  async function close() {
    if(closed)return;closed=true;++revision;clock.close();cancelImage?.();
    document.removeEventListener("keydown",onKey);document.removeEventListener("visibilitychange",onVisibility);
    backdrop.remove();releaseFocus();
    try {if(activeRequest)await activeRequest;
      const latest=await api(`/play/sessions/${session.token}`);
      await api(`/play/sessions/${session.token}/pause`,{method:"POST",body:{paused:true,expected_version:latest.version,request_key:jobKey()}});
    }catch{toast(t("暂停状态未能保存；恢复时会重新读取会话并先暂停"),"error");}
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
