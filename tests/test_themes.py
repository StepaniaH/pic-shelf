"""Theme bootstrap remains usable before the application or stylesheet loads."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.module_contract
def test_theme_palettes_contrast_storage_and_system_resolution():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for theme contracts')
    source = Path(__file__).parents[1] / 'picshelf/web/js/theme.js'
    script = r"""
const {readFileSync} = await import('node:fs');
const {runInNewContext} = await import('node:vm');
const {strict: assert} = await import('node:assert');
const code = readFileSync(process.argv[1], 'utf8');
function boot(initial, dark = true, blocked = false) {
  const storage = new Map(initial == null ? [] : [['picshelf.appearance',initial]]);
  const listeners = new Map();
  const query = {matches:dark, addEventListener:(name,listener) => listeners.set('scheme',listener)};
  const root = {dataset:{},style:{setProperty(key,value){this[key] = value}}};
  const meta = {};
  const window = {addEventListener:(name,listener) => listeners.set(name,listener), dispatchEvent:event => listeners.get(event.type)?.(event)};
  runInNewContext(code, {window, document:{documentElement:root,querySelector:()=>meta}, matchMedia:()=>query,
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail}},
    localStorage:{getItem:key => storage.get(key) ?? null, setItem:(key,value) => {if(blocked)throw Error('blocked');storage.set(key,value)}}});
  return {theme:window.PicShelfTheme,root,meta,storage,listeners,query};
}
const luminance = hex => hex.slice(1).match(/../g).map(value => parseInt(value,16)/255)
  .map(value => value <= .04045 ? value/12.92 : ((value+.055)/1.055)**2.4)
  .reduce((sum,value,index) => sum + value * [.2126,.7152,.0722][index],0);
const contrast = (first,second) => {const a=luminance(first),b=luminance(second);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
const {theme} = boot();
assert.equal(theme.palettes.length,9);
for (const palette of theme.palettes) {
  for (const scheme of ['dark','light']) {
    const colors=theme.colors(palette.id,scheme);
    for (const foreground of ['--fg','--muted','--accent','--warn','--ok']) {
      for (const background of ['--bg','--panel','--panel-2']) {
        assert.ok(contrast(colors[foreground],colors[background]) >= 4.5,
          `${palette.id} ${scheme}: ${foreground} on ${background}`);
      }
    }
    for (const background of ['--button-bg','--button-active']) {
      assert.ok(contrast(colors['--button-fg'],colors[background]) >= 4.5,`${palette.id} ${scheme} button`);
    }
    assert.notEqual(theme.colors(palette.id,'dark')['--bg'],theme.colors(palette.id,'light')['--bg']);
  }
}
const state=boot('{broken',false);
assert.equal(state.theme.get().mode,'system');
assert.equal(state.root.dataset.scheme,'light');
state.theme.set({palette:'gruvbox'});
assert.equal(state.root.dataset.palette,'gruvbox');
state.query.matches=true; state.listeners.get('scheme')();
assert.equal(state.root.dataset.scheme,'dark');
state.theme.set({mode:'light'});
state.listeners.get('scheme')(); assert.equal(state.root.dataset.scheme,'light');
const restored=boot(state.storage.get('picshelf.appearance'));
assert.equal(restored.theme.get().palette,'gruvbox'); assert.equal(restored.root.dataset.scheme,'light');
assert.equal(restored.meta.content,restored.root.style['--bg']);
restored.storage.set('picshelf.appearance',JSON.stringify({mode:'dark',palette:'nord'}));
restored.listeners.get('storage')({key:'picshelf.appearance'});
assert.equal(restored.theme.get().palette,'nord'); assert.equal(restored.root.dataset.scheme,'dark');
restored.storage.clear();restored.listeners.get('storage')({key:null});
assert.equal(restored.theme.get().mode,'system');assert.equal(restored.theme.get().palette,'builtin');
const denied=boot(null,true,true);denied.theme.set({mode:'light',palette:'mocha'});
assert.equal(denied.root.dataset.scheme,'light'); assert.equal(denied.theme.get().stored,false);
const invalid=boot(JSON.stringify({mode:'unknown',palette:'not-a-theme'}));
assert.equal(invalid.theme.get().mode,'system');assert.equal(invalid.theme.get().palette,'builtin');
"""
    result = subprocess.run([node, '--input-type=module', '-e', script, str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
