"""Directory drops must drain every browser batch and retain colliding names."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.module_contract
def test_directory_batches_selection_fallback_and_name_collisions():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend module contracts')
    module = (Path(__file__).parents[1] / 'picshelf/web/js/import-files.js').as_uri()
    script = r'''
const {droppedFiles, selectedFiles, imageFiles} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const entry = (name, data = name) => ({name, isFile:true, file:ok=>ok({name,data})});
const batch = Array.from({length:100}, (_,i)=>entry(`${i}.png`));
const folder = {name:'Trip', isDirectory:true, createReader:()=>{
  const batches = [batch, [entry('last.png')], []];
  return {readEntries:ok=>ok(batches.shift())};
}};
const files = await droppedFiles({files:[], items:[{webkitGetAsEntry:()=>folder}]});
assert.equal(files.length,101);
assert.equal(files.at(-1).path,'Trip/last.png');
const plain = [{name:'plain.png'}, {name:'nested.png',webkitRelativePath:'Folder/nested.png'}];
assert.deepEqual(await droppedFiles({files:plain,items:[]}),selectedFiles(plain));
const chosen = imageFiles([
  {path:'Photo.png',file:{data:'one'}}, {path:'photo.PNG',file:{data:'two'}},
  {path:'Photo-2.png',file:{data:'three'}}, {path:'note.txt',file:{data:'ignored'}}
]);
assert.equal(chosen.length,3);
assert.equal(new Set(chosen.map(item=>item.path.toLowerCase())).size,3);
assert.deepEqual(chosen.map(item=>item.file.data),['one','two','three']);
await assert.rejects(droppedFiles({files:[],items:[{webkitGetAsEntry:()=>({
  name:'Bad',isDirectory:true,createReader:()=>({readEntries:(ok,fail)=>fail(new Error('unreadable'))})
})}]}),/unreadable/);
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
