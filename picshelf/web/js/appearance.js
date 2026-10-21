import {showModal} from "./app.js";
import {t} from "./i18n.js";

const preview = `<span class="theme-preview-bar"></span><span class="theme-preview-colors"><i></i><i></i><i></i></span>`;

export function openAppearance() {
  const theme = window.PicShelfTheme;
  let unsubscribe = () => {};
  return showModal({
    title:t('外观'),
    onClose:() => unsubscribe(),
    bodyHtml:`<fieldset class="appearance-picker"><legend>${t('明暗模式')}</legend>
      <div class="theme-mode-options">${[['system','跟随系统'],['dark','深色'],['light','浅色']].map(([mode,label]) => `
        <label class="theme-choice"><input type="radio" name="theme-mode" value="${mode}">
          <span class="theme-preview ${mode === 'system' ? 'theme-preview-system' : ''}" aria-hidden="true" ${mode !== 'system' ? `data-preview-scheme="${mode}"` : ''}>
            ${mode === 'system' ? `<span class="theme-preview-half" data-preview-scheme="dark">${preview}</span><span class="theme-preview-half" data-preview-scheme="light">${preview}</span>` : preview}
          </span><span class="theme-choice-name">${t(label)}</span>
        </label>`).join('')}</div>
      </fieldset>
      <fieldset class="appearance-picker"><legend>${t('配色')}</legend>
        <div class="theme-palette-options">${theme.palettes.map(palette => `
          <label class="theme-choice"><input type="radio" name="theme-palette" value="${palette.id}">
            <span class="theme-preview" aria-hidden="true" data-preview-palette="${palette.id}">${preview}</span>
            <span class="theme-choice-name">${t(palette.name)}</span>
          </label>`).join('')}</div>
      </fieldset>
      <p class="hint appearance-status" role="status"></p>
      <div class="modal-actions"><button type="button" class="btn" data-close>${t('完成')}</button></div>`,
    onMount(el) {
      el.classList.add('appearance-modal');
      const sync = () => {
        const current = theme.get();
        el.querySelectorAll('input[name="theme-mode"]').forEach(input => { input.checked = input.value === current.mode; });
        el.querySelectorAll('input[name="theme-palette"]').forEach(input => { input.checked = input.value === current.palette; });
        el.querySelectorAll('[data-preview-scheme], [data-preview-palette]').forEach(preview => {
          const values = theme.colors(preview.dataset.previewPalette || current.palette, preview.dataset.previewScheme || current.scheme);
          for (const [property,token] of [['--swatch-bg','--bg'],['--swatch-panel','--panel-2'],['--swatch-accent','--accent'],['--swatch-warn','--warn'],['--swatch-ok','--ok']]) {
            preview.style.setProperty(property,values[token]);
          }
        });
        const status = el.querySelector('.appearance-status');
        status.textContent = t(current.stored ? '保存在当前浏览器。' : '无法保存主题。请允许浏览器保存站点数据后重试。');
        status.classList.toggle('error',!current.stored);
      };
      el.addEventListener('change', event => {
        const input = event.target;
        if (input.name === 'theme-mode') theme.set({mode:input.value});
        else if (input.name === 'theme-palette') theme.set({palette:input.value});
      });
      window.addEventListener(theme.eventName,sync);
      unsubscribe = () => window.removeEventListener(theme.eventName,sync);
      sync();
      el.querySelector('input[name="theme-mode"]:checked').focus();
    },
  });
}
