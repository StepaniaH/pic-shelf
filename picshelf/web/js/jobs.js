import { api } from "./api.js";
import { escapeHtml, toast, uiConfirm } from "./app.js";
import { t } from "./i18n.js";
import { formatTimestamp } from "./format.js";

const ACTIVE = new Set(["queued", "running", "cancelling"]);
const LABELS = {
  queued: "待执行", running: "运行中", cancelling: "取消中", cancelled: "已取消",
  succeeded: "成功", partial: "部分失败", failed: "失败", interrupted: "已中断",
  pending: "待处理", skipped: "已跳过", conflict: "冲突", writing: "写入中", ready: "已写入",
};
const KINDS = { scan: "扫描", rebuild: "重建缩略图与指纹", import: "导入" };
const label = (value) => t(LABELS[value] || value);

export function scanSummary(result) {
  const counts = [["新增 {n}", result.added], ["更新 {n}", result.updated],
    ["移动 {n}", result.moved], ["缺失 {n}", result.missing]];
  const changes = counts.filter(([, n]) => n > 0).map(([key, n]) => t(key, {n}));
  return changes.length ? t("扫描完成") + " · " + changes.join(" · ") : t("扫描完成，没有变化");
}

export function jobKey() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => n.toString(16).padStart(2, "0")).join("");
}

const jobUrl = (id) => `#/admin?tab=jobs&job=${encodeURIComponent(id)}`;

// Item journals are fetched only while expanded. Polling reads counters alone.
function itemBrowser(jobId, details) {
  const panel = details.querySelector("[data-job-items]");
  let revision = 0, offset = 0;
  async function load(page = offset) {
    const request = ++revision;
    panel.setAttribute("aria-busy", "true");
    try {
      const job = await api(`/jobs/${encodeURIComponent(jobId)}?offset=${page}&limit=100`);
      if (!panel.isConnected || request !== revision) return;
      offset = job.offset;
      panel.innerHTML = job.items.length ? `<ul tabindex="0">${job.items.map(item => `<li>${escapeHtml(item.item_key)} · ${escapeHtml(label(item.state))}${item.detail ? ` — ${escapeHtml(item.detail)}` : ""}</li>`).join("")}</ul>` : `<p class="muted">${t("暂无逐项结果")}</p>`;
      if (!job.offset && job.next_offset === null) return;
      const nav = document.createElement("div"); nav.className = "form-row";
      for (const [text, next] of [["上一页", job.offset > 0 ? Math.max(0, job.offset - job.limit) : null], ["下一页", job.next_offset]]) {
        const button = document.createElement("button");
        button.className = "btn small"; button.textContent = t(text); button.disabled = next === null;
        button.onclick = () => load(next); nav.append(button);
      }
      panel.append(nav);
    } catch (error) {
      if (!panel.isConnected || request !== revision) return;
      panel.innerHTML = `<p class="error" role="alert">${escapeHtml(error.message)}</p><button class="btn" data-retry-items>${t("重试")}</button>`;
      panel.querySelector("button").onclick = () => load(page);
    } finally { if (request === revision) panel.removeAttribute("aria-busy"); }
  }
  details.addEventListener("toggle", () => { if (details.open) load(); });
  return () => { if (details.open) load(); };
}

export async function watchJob(jobId, target, {onUpdate, detailLink = true} = {}) {
  const panel = document.createElement("section");
  panel.className = "job-progress";
  panel.innerHTML = `<p role="status" aria-live="polite"></p>
    <div class="form-row"><button class="btn" data-cancel>${t("取消任务")}</button>
    ${detailLink ? `<a class="btn" href="${jobUrl(jobId)}">${t("查看任务")}</a>` : ""}</div>
    <p class="error" role="alert" data-connection hidden></p><button class="btn" data-reconnect hidden>${t("重试")}</button>
    <div data-job-error></div>
    <details><summary>${t("任务详情")}</summary><p class="muted">${t("任务 ID：{job_id}", {job_id: escapeHtml(jobId)})}</p><div data-job-items></div></details>`;
  target.replaceChildren(panel);
  const summary = panel.querySelector('[role="status"]'), cancel = panel.querySelector("[data-cancel]");
  const connection = panel.querySelector("[data-connection]"), reconnect = panel.querySelector("[data-reconnect]");
  const refreshItems = itemBrowser(jobId, panel.querySelector("details"));
  let wake, delay = 500;
  reconnect.onclick = () => wake?.();
  cancel.onclick = async () => {
    cancel.disabled = true;
    try { await api(`/jobs/${encodeURIComponent(jobId)}/cancel`, {method:"POST"}); wake?.(); }
    catch (error) { toast(error.message, "error"); cancel.disabled = false; }
  };
  while (panel.isConnected) {
    try {
      const job = await api(`/jobs/${encodeURIComponent(jobId)}?include_items=false`);
      if (!panel.isConnected) return null;
      connection.hidden = reconnect.hidden = true;
      summary.textContent = t("{state} · 已处理 {completed}/{total}", {
        state:label(job.state), completed:job.completed, total:job.total ?? t("待统计"),
      });
      if (job.failed) summary.textContent += " · " + t("失败 {n}", {n:job.failed});
      if (job.phase_error_count) summary.textContent += " · " + t("扫描错误：{count}", {count:job.phase_error_count});
      cancel.disabled = job.state === "cancelling" || !ACTIVE.has(job.state);
      cancel.hidden = !ACTIVE.has(job.state);
      onUpdate?.(job);
      if (!ACTIVE.has(job.state)) {
        const error = panel.querySelector("[data-job-error]");
        if (job.error) {
          const text = document.createElement("p"); text.className = "error";
          text.textContent = `${job.error.detail} ${job.error.error?.next_step || ""}`; error.append(text);
        }
        if (job.result?.scan?.errors?.length) {
          const text = document.createElement("pre"); text.textContent = job.result.scan.errors.join("\n"); error.append(text);
        }
        refreshItems();
        return job;
      }
    } catch (error) {
      if (!panel.isConnected) return null;
      if (error.status && error.status < 500) {
        panel.replaceChildren();
        throw error;
      }
      connection.textContent = error.message + " " + error.nextStep;
      connection.hidden = reconnect.hidden = false;
      cancel.disabled = true;
    }
    await new Promise(resolve => {
      const timer = setTimeout(() => { wake = null; resolve(); }, delay);
      wake = () => { clearTimeout(timer); wake = null; resolve(); };
    });
    delay = Math.min(3000, delay * 1.5);
  }
  return null;
}

async function renderJob(body, jobId, offset) {
  body.innerHTML = `<div class="page-head"><h2 data-title>${t("后台任务")}</h2>
    <a class="btn" href="#/admin?tab=jobs&offset=${offset}">${t("全部任务")}</a></div>
    <p class="muted" data-created></p>
    <div class="form-row"><button class="btn" data-retry hidden>${t("重试未完成项")}</button>
    <a class="btn primary" data-library hidden>${t("查看图库")}</a></div><div data-progress></div><p class="error" role="alert" data-error></p>`;
  const retry = body.querySelector("[data-retry]"), browse = body.querySelector("[data-library]");
  const message = body.querySelector("[data-error]");
  async function watch() {
    message.textContent = "";
    try {
      await watchJob(jobId, body.querySelector("[data-progress]"), {detailLink:false, onUpdate(job) {
        body.querySelector("[data-title]").textContent = `${t(KINDS[job.kind] || job.kind)} · ${job.library_name}`;
        body.querySelector("[data-created]").textContent = formatTimestamp(job.created_at);
        retry.hidden = ACTIVE.has(job.state) || job.state === "succeeded";
        browse.href = `#/library/${job.library_id}`; browse.hidden = false;
      }});
    } catch (error) { message.textContent = error.message; retry.hidden = browse.hidden = true; }
  }
  retry.onclick = async () => {
    if (!await uiConfirm(t("重试未完成项"), t("重试失败或尚未处理的文件。"))) return;
    retry.disabled = true;
    try { await api(`/jobs/${encodeURIComponent(jobId)}/retry`, {method:"POST"}); await watch(); }
    catch (error) { message.textContent = error.message; }
    finally { retry.disabled = false; }
  };
  watch();
}

export async function renderJobs(body, {offset = 0, jobId = null} = {}) {
  if (jobId) { await renderJob(body, jobId, offset); return; }
  const data = await api(`/jobs?offset=${offset}&include_items=false`);
  if (!body.isConnected) return;
  body.innerHTML = `<div class="page-head"><h2>${t("后台任务")}</h2><button class="btn" data-refresh>${t("刷新")}</button></div>
    <div class="table-wrap"><table class="data"><thead><tr><th>${t("任务")}</th><th>${t("状态")}</th><th>${t("进度")}</th><th>${t("操作")}</th></tr></thead><tbody>
    ${data.items.map(job => `<tr data-job="${job.id}"><td>${escapeHtml(t(KINDS[job.kind] || job.kind))} · ${escapeHtml(job.library_name)}<br><small>${escapeHtml(formatTimestamp(job.created_at))}</small></td><td>${escapeHtml(label(job.state))}</td><td>${job.completed}/${job.total ?? "—"}</td><td><a class="btn small" data-view href="${jobUrl(job.id)}&offset=${offset}">${t("查看详情")}</a></td></tr>`).join("") || `<tr><td colspan="4">${t("暂无任务")}</td></tr>`}
    </tbody></table></div>`;
  body.querySelector("[data-refresh]").onclick = async event => {
    const button = event.currentTarget; button.disabled = true;
    try { await renderJobs(body, {offset}); } catch (error) { toast(error.message, "error"); }
    finally { button.disabled = false; }
  };
  if (offset === 0 && data.next_offset === null) return;
  const nav = document.createElement("div"); nav.className = "form-row";
  for (const [text, page] of [["上一页", offset > 0 ? Math.max(0, offset - 50) : null], ["下一页", data.next_offset]]) {
    if (page === null) continue;
    const link = document.createElement("a"); link.className = "btn"; link.textContent = t(text);
    link.href = `#/admin?tab=jobs&offset=${page}`; nav.append(link);
  }
  body.append(nav);
}
