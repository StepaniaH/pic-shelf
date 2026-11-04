import {t} from "./i18n.js";

// The keyboard dispatcher and its help use the same keys and action names.
export const VIEWER_SHORTCUTS = [
  {action: "previous", keys: ["ArrowLeft"], label: () => t("上一张")},
  {action: "next", keys: ["ArrowRight", " "], label: () => t("下一张")},
  {action: "close", keys: ["Escape"], label: () => t("关闭当前层级或返回图片列表")},
  {action: "favorite", keys: ["f"], label: () => t("收藏或取消收藏"), permission: "rate"},
  {action: "info", keys: ["i"], label: () => t("展开或收起信息")},
  {action: "size", keys: ["z"], label: () => t("切换图片尺寸")},
];

export const VIEWER_CONTROL_SHORTCUTS = [
  {context: "rating", selector: "#v-rating", keys: ["ArrowLeft", "ArrowDown", "ArrowRight", "ArrowUp", "Home", "End"], label: () => t("评分滑轨聚焦时：方向键调分，Home/End 到最小/最大值")},
  {context: "add", selector: "#tag-input, #artist-input", keys: ["Enter"], label: () => t("标签或艺术家输入框中：添加输入的内容")},
];

export function viewerControlHasKey(context, key) {
  return VIEWER_CONTROL_SHORTCUTS.some(shortcut => shortcut.context === context && shortcut.keys.includes(key));
}

export function viewerShortcutForKey(key) {
  return VIEWER_SHORTCUTS.find(shortcut => shortcut.keys.includes(key));
}

export function viewerShortcutKeyLabel(key) {
  return ({ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Escape: "Esc"})[key] || (key === " " ? t("空格") : key);
}
