// Run before stylesheets so stored appearance is applied before the first paint.
(() => {
  const palettes = [
    {"id": "builtin", "name": "内置", "dark": ["#0f141b", "#171e27", "#1d2631", "#f1f5f9", "#b9c4d0", "#38bdf8", "#082331", "#9bd65d", "#f7c65c"], "light": ["#f5f7fa", "#ffffff", "#f0f3f7", "#182230", "#465568", "#0369a1", "#ffffff", "#3f701b", "#8a5a00"]},
    {"id": "gruvbox", "name": "Gruvbox", "dark": ["#1d2021", "#282828", "#32302f", "#fbf1c7", "#ebdbb2", "#fe8019", "#1d2021", "#b8bb26", "#fabd2f"], "light": ["#fbf1c7", "#fff9df", "#f5ebc4", "#282828", "#504945", "#076678", "#ffffff", "#526100", "#815400"]},
    {"id": "catppuccin", "name": "Catppuccin", "dark": ["#292c3c", "#303446", "#383c50", "#f1f3ff", "#c6d0f5", "#d6a8ee", "#292c3c", "#a6d189", "#e5c890"], "light": ["#f3f4f8", "#ffffff", "#f0f1f6", "#303247", "#4c4f69", "#6f2dbd", "#ffffff", "#25751d", "#8a5a00"]},
    {"id": "solarized", "name": "Solarized", "dark": ["#002b36", "#073642", "#0a3f4c", "#fdf6e3", "#c7d2ce", "#75c1ef", "#002b36", "#b4c94a", "#e6bd55"], "light": ["#fdf6e3", "#fffdf4", "#f4edda", "#263f46", "#455f66", "#00629f", "#ffffff", "#536b00", "#7a5a00"]},
    {"id": "nord", "name": "Nord", "dark": ["#242933", "#2e3440", "#374050", "#f5f7fb", "#d8dee9", "#9acbd8", "#242933", "#aec99a", "#ebcb8b"], "light": ["#e9edf2", "#f8fafc", "#eef2f6", "#2e3440", "#3d4858", "#326783", "#ffffff", "#41651f", "#765400"]},
    {"id": "dracula", "name": "Dracula", "dark": ["#20212b", "#282a36", "#303241", "#f8f8f2", "#d7d8e2", "#c8a4fb", "#282a36", "#50fa7b", "#f1fa8c"], "light": ["#fffbeb", "#fffefa", "#f5f2e6", "#1f1f1f", "#494637", "#644ac9", "#ffffff", "#14710a", "#705c0f"]},
    {"id": "tokyo-night", "name": "Tokyo Night", "dark": ["#15161f", "#1a1b26", "#202334", "#edf0ff", "#c0caf5", "#7aa2f7", "#15161f", "#9ece6a", "#e0af68"], "light": ["#e1e2e7", "#f7f8fb", "#e9ebf2", "#253156", "#3d4b78", "#1f54aa", "#ffffff", "#3f6228", "#6c4d13"]},
    {"id": "macchiato", "name": "Macchiato", "dark": ["#1e2030", "#24273a", "#2c3045", "#f1f3ff", "#cad3f5", "#c6a0f6", "#24273a", "#a6da95", "#eed49f"], "light": ["#f1f3f8", "#ffffff", "#eceff6", "#303247", "#484c68", "#245eb5", "#ffffff", "#3e701e", "#805700"]},
    {"id": "mocha", "name": "Mocha", "dark": ["#181825", "#1e1e2e", "#262638", "#f4f5ff", "#cdd6f4", "#cba6f7", "#1e1e2e", "#a6e3a1", "#f9e2af"], "light": ["#f4f1f7", "#fffaff", "#eee9f2", "#312c37", "#4f4658", "#7536a3", "#ffffff", "#426d24", "#805700"]}
  ];
  const storageKey = 'picshelf.appearance';
  const modes = ['system', 'dark', 'light'];
  const eventName = 'picshelf:appearance';
  const root = document.documentElement;
  const schemeQuery = matchMedia('(prefers-color-scheme: dark)');
  let current, stored = true;
  const normalize = value => ({
    mode:modes.includes(value?.mode) ? value.mode : 'system',
    palette:palettes.some(palette => palette.id === value?.palette) ? value.palette : 'builtin',
  });
  const read = () => {
    try { return normalize(JSON.parse(localStorage.getItem(storageKey))); }
    catch { return normalize(); }
  };
  function blend(foreground, background, amount) {
    const channels = hex => [1,3,5].map(index => parseInt(hex.slice(index,index + 2),16));
    return '#' + channels(foreground).map((channel,index) =>
      Math.round(channel * amount + channels(background)[index] * (1 - amount)).toString(16).padStart(2,'0')).join('');
  }
  function colors(paletteId, scheme) {
    const palette = palettes.find(palette => palette.id === paletteId) || palettes[0];
    const [bg,panel,raised,fg,muted,accent,contrast,ok,warn] = palette[scheme === 'light' ? 'light' : 'dark'];
    const dark = scheme !== 'light';
    return {
      '--bg':bg, '--panel':panel, '--panel-2':raised, '--fg':fg, '--muted':muted,
      '--accent':accent, '--accent-soft':blend(accent,panel,dark ? .18 : .10),
      '--button-bg':accent, '--button-fg':contrast, '--button-active':blend(accent,contrast,.88), '--ok':ok, '--warn':warn,
      '--warn-soft':blend(warn,panel,.14), '--border':blend(fg,panel,dark ? .17 : .20),
      '--control-border':blend(fg,panel,.40), '--control-open':blend(accent,panel,.65),
      '--menu-bg':raised, '--menu-border':blend(fg,raised,.28), '--menu-hover':blend(fg,raised,.08),
      '--shadow':dark ? '0 10px 30px rgb(0 0 0 / .45)' : '0 10px 30px rgb(0 0 0 / .12)',
      '--menu-shadow':dark ? '0 12px 32px rgb(0 0 0 / .35), 0 2px 6px rgb(0 0 0 / .2)' : '0 12px 32px rgb(0 0 0 / .12), 0 2px 6px rgb(0 0 0 / .06)',
      '--overlay':dark ? 'rgb(5 7 10 / .7)' : 'rgb(24 34 48 / .35)',
    };
  }
  function apply({notify = true} = {}) {
    const scheme = current.mode === 'system' ? (schemeQuery.matches ? 'dark' : 'light') : current.mode;
    const values = colors(current.palette,scheme);
    for (const [property,value] of Object.entries(values)) root.style.setProperty(property,value);
    root.style.colorScheme = scheme;
    root.dataset.scheme = scheme;
    root.dataset.palette = current.palette;
    root.dataset.themeMode = current.mode;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = values['--bg'];
    if (notify) window.dispatchEvent(new CustomEvent(eventName, {detail:get()}));
    return get();
  }
  function get() { return {...current, scheme:root.dataset.scheme, stored}; }
  function set(next) {
    current = normalize({...current,...next});
    try { localStorage.setItem(storageKey,JSON.stringify(current)); stored = true; }
    catch { stored = false; }
    return apply();
  }
  current = read();
  apply({notify:false});
  window.addEventListener('storage', event => {
    if (event.key !== storageKey && event.key !== null) return;
    current = read(); stored = true; apply();
  });
  schemeQuery.addEventListener('change', () => { if (current.mode === 'system') apply(); });
  window.PicShelfTheme = Object.freeze({get,set,colors,eventName,
    palettes:Object.freeze(palettes.map(palette => Object.freeze({id:palette.id,name:palette.name}))),
  });
})();
