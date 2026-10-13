import { api } from "./api.js";
import { escapeHtml, toast, uiConfirm } from "./app.js";
import { t } from "./i18n.js";

const ACTIVE = new Set(["queued", "running", "cancelling"]);
const LABELS = {
  queued: "待执行", running: "运行中", cancelling: "取消中", cancelled: "已取消",
  succeeded: "成功", partial: "部分失败", failed: "失败", interrupted: "已中断",
  pending: "待处理", skipped: "已跳过", conflict: "冲突", writing: "写入中", ready: "已落盘",
};
const KINDS = { scan: "扫描", rebuild: "重建派生图", import: "导入" };
const label = (value) => t(LABELS[value] || value);

export function jobKey() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => n.toString(16).padStart(2, "0")).join("");
}

async function showItems(job, panel) {
  panel.innerHTML = `<ul>${job.items.map((item) => `<li>${escapeHtml(item.item_key)} · ${escapeHtml(label(item.state))}${item.detail ? ` — ${escapeHtml(item.detail)}` : ""}</li>`).join("")}</ul>`;
  for (const [text, offset] of [[t("上一页"), job.offset > 0 ? Math.max(0, job.offset - job.limit) : null], [t("下一页"), job.next_offset]]) {
    const button = document.createElement("button");
    button.className = "btn small";
    button.textContent = text;
    button.disabled = offset === null;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const next = await api(`/jobs/${job.id}?offset=${offset}&limit=${job.limit}`);
        if (panel.isConnected) await showItems(next, panel);
      } catch (error) { toast(error.message, "error"); button.disabled = false; }
    });
    panel.appendChild(button);
  }
}

export async function watchJob(jobId, target) {
  const panel = document.createElement("section");
  panel.className = "job-progress";
  panel.innerHTML = `<p>${t("任务 ID：{job_id}", {job_id: escapeHtml(jobId)})}</p><p role="status" aria-live="polite"></p><button class="btn">${t("取消任务")}</button>`;
  target.replaceChildren(panel);
  const summary = panel.querySelector('[role="status"]');
  const cancel = panel.querySelector("button");
  cancel.addEventListener("click", async () => {
    cancel.disabled = true;
    try { await api(`/jobs/${jobId}/cancel`, {method: "POST"}); }
    catch (error) { toast(error.message, "error"); cancel.disabled = false; }
  });
  let delay = 500;
  while (panel.isConnected) {
    const job = await api(`/jobs/${jobId}`);
    if (!panel.isConnected) return null;
    summary.textContent = t("{state} · 已处理 {completed}/{total} · 成功 {succeeded} · 失败 {failed}", {
      state: label(job.state), completed: job.completed, total: job.total ?? t("待统计"),
      succeeded: job.succeeded, failed: job.failed,
    });
    if (job.phase_error_count) summary.textContent += " · " + t("扫描错误：{count}", {count: job.phase_error_count});
    cancel.disabled = job.state === "cancelling" || !ACTIVE.has(job.state);
    if (!ACTIVE.has(job.state)) {
      if (job.error) {
        const error = document.createElement("p");
        error.textContent = `${job.error.detail} ${job.error.error?.next_step || ""}`;
        panel.appendChild(error);
      }
      if (job.result?.scan?.errors?.length) {
        const errors = document.createElement("pre");
        errors.textContent = job.result.scan.errors.join("\n");
        panel.appendChild(errors);
      }
      const details = document.createElement("details");
      const heading = document.createElement("summary");
      heading.textContent = t("逐项结果");
      const items = document.createElement("div");
      details.append(heading, items);
      panel.appendChild(details);
      await showItems(job, items);
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(3000, delay * 1.5);
  }
  return null;
}

export async function renderJobs(body, offset = 0) {
  const data = await api(`/jobs?offset=${offset}`);
  body.innerHTML = `<h2>${t("后台任务")}</h2><p class="hint">${t("取消会保留已完成项目；重试仅处理未完成或失败项目。")}</p><button class="btn" data-refresh>${t("刷新")}</button>
    <div class="table-wrap"><table class="data"><thead><tr><th>${t("任务")}</th><th>${t("状态")}</th><th>${t("进度")}</th><th>${t("操作")}</th></tr></thead><tbody>
    ${data.items.map((job) => `<tr data-job="${job.id}"><td>${escapeHtml(t(KINDS[job.kind] || job.kind))} #${job.library_id}<br><small>${escapeHtml(job.id)}</small></td><td>${escapeHtml(label(job.state))}</td><td>${job.completed}/${job.total ?? "—"}</td><td><button class="btn small" data-view>${t("查看进度")}</button> ${!ACTIVE.has(job.state) && job.state !== "succeeded" ? `<button class="btn small" data-retry>${t("重试未完成项")}</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div><div data-detail></div>`;
  const detail = body.querySelector("[data-detail]");
  body.querySelector("[data-refresh]").onclick = () => renderJobs(body, offset).catch((error) => toast(error.message, "error"));
  body.querySelectorAll("[data-job]").forEach((row) => {
    row.querySelector("[data-view]").onclick = () => watchJob(row.dataset.job, detail).catch((error) => toast(error.message, "error"));
    const retry = row.querySelector("[data-retry]");
    if (retry) retry.onclick = async () => {
      if (!(await uiConfirm(t("重试未完成项"), t("将继续原任务的范围，已完成项目保持不变。")))) return;
      retry.disabled = true;
      try {
        await api(`/jobs/${row.dataset.job}/retry`, {method: "POST"});
        await watchJob(row.dataset.job, detail);
      } catch (error) { toast(error.message, "error"); }
      finally { retry.disabled = false; }
    };
  });
  for (const [text, page] of [[t("上一页"), offset > 0 ? Math.max(0, offset - 50) : null], [t("下一页"), data.next_offset]]) {
    const button = document.createElement("button");
    button.className = "btn";
    button.textContent = text;
    button.disabled = page === null;
    button.onclick = () => renderJobs(body, page).catch((error) => toast(error.message, "error"));
    body.appendChild(button);
  }
}
