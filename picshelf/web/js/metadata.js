import {api} from "./api.js";
import {escapeHtml, toast} from "./app.js";
import {t} from "./i18n.js";
import {formatTimestamp} from "./format.js";

const ORIGINS = {manual: "人工填写", imported: "导入记录", extracted: "提取结果", suggested: "待确认建议"};

export function metadataHtml(image) {
  const data = image.metadata;
  if (!data) return "";
  const done = data.workflow.state === "done";
  return `<section><h3>${t("我的整理状态")}</h3>
    <p>${done ? t("已整理") : t("待整理")} <button class="btn small" data-workflow="${done ? "inbox" : "done"}">${done ? t("标记为待整理") : t("标记为已整理")}</button></p>
    <p class="muted">${t("整理状态不会改变评分、收藏或文件缺失状态。")}</p>
    </section><section><h3>${t("来源记录")}</h3>
    ${data.sources.map(record => `<div class="comment"><p>${t(ORIGINS[record.origin] || record.origin)} · ${escapeHtml(record.original_filename || "—")}</p>
      ${record.url ? `<p>${escapeHtml(record.url)}</p>` : ""}${record.note ? `<p>${escapeHtml(record.note)}</p>` : ""}
      <p class="muted">${t("收纳时间")} ${formatTimestamp(record.collected_at)}<br>${t("创作时间")} ${formatTimestamp(record.created_time)}</p></div>`).join("") || `<p class="muted">${t("尚无来源记录")}</p>`}
    <p class="muted">${t("来源记录不代表使用许可；不会自动访问来源地址。")}</p>
    ${image.metadata_editable ? `<form data-source-form>
      <label>${t("原始文件名")}<input name="original_filename" maxlength="1000"></label>
      <label>${t("来源地址")}<input name="url" type="url" maxlength="4096"></label>
      <label>${t("来源说明")}<textarea name="note" maxlength="10000" rows="2"></textarea></label>
      <label>${t("收纳时间（含时区，可选）")}<input name="collected_at" placeholder="2026-10-10T12:00:00+08:00"></label>
      <label>${t("创作时间（含时区，可选）")}<input name="created_time" placeholder="2026-10-10T12:00:00+08:00"></label>
      <button class="btn small" type="submit">${t("添加来源记录")}</button>
    </form>` : ""}</section>
    <section><h3>${t("标签来源与确认")}</h3>
    ${data.tag_claims.map(claim => `<p>${escapeHtml(claim.name)} · ${t(ORIGINS[claim.origin] || claim.origin)} · ${claim.retired_at ? t("已移除") : claim.confirmed ? t("已确认") : t("待确认")}
      ${image.metadata_editable && !claim.confirmed && !claim.retired_at ? `<button class="btn small" data-confirm-claim="${claim.id}">${t("确认标签")}</button>` : ""}</p>`).join("") || `<p class="muted">${t("（无）")}</p>`}
    </section>`;
}

export function bindMetadata(panel, image, refresh) {
  const workflow = panel.querySelector("[data-workflow]");
  if (workflow) workflow.onclick = async () => {
    workflow.disabled = true;
    try {
      await api("/metadata/workflow", {method: "POST", body: {image_ids: [image.id], state: workflow.dataset.workflow}});
      if (workflow.isConnected) await refresh();
    } catch (error) { toast(error.message, "error"); workflow.disabled = false; }
  };
  const form = panel.querySelector("[data-source-form]");
  if (form) form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    const body = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value.trim() || null]));
    try {
      await api(`/images/${image.id}/sources`, {method: "POST", body});
      if (form.isConnected) await refresh();
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  };
  panel.querySelectorAll("[data-confirm-claim]").forEach(button => {
    button.onclick = async () => {
      button.disabled = true;
      try {
        await api(`/tag-claims/${button.dataset.confirmClaim}/confirm`, {method: "POST"});
        if (button.isConnected) await refresh();
      } catch (error) { toast(error.message, "error"); button.disabled = false; }
    };
  });
}
