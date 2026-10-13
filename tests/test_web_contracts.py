"""Media URL contracts independent of a browser process."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_algorithm_rebuild_changes_gallery_cache_key_but_keeps_original_url():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend module contracts")
    module = (Path(__file__).parents[1] / "picshelf/web/js/api.js").as_uri()
    script = """
globalThis.localStorage = {getItem: () => 'en'};
const {imageMediaUrl} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const image = {id: 7, sha256: '0123456789abcdef0123456789abcdef', derivative_version: 2};
assert.equal(imageMediaUrl('thumb', image), '/api/v1/media/thumb/7?v=0123456789abcdef-d2');
assert.equal(imageMediaUrl('preview', {...image, derivative_version: 3}), '/api/v1/media/preview/7?v=0123456789abcdef-d3');
assert.equal(imageMediaUrl('original', image), '/api/v1/media/original/7?v=0123456789abcdef');
assert.equal(imageMediaUrl('original', {...image, derivative_version: 3}), '/api/v1/media/original/7?v=0123456789abcdef');
assert.equal(imageMediaUrl('preview', {...image, preview_url: '/api/v1/media/preview/7?v=server-version'}), '/api/v1/media/preview/7?v=server-version');
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module], text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_user_links_cannot_be_active_code_or_contain_credentials():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend module contracts")
    module = (Path(__file__).parents[1] / "picshelf/web/js/format.js").as_uri()
    script = r"""
const {externalUrl} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
for (const value of ['javascript:alert(1)', 'data:text/html,payload', '//example.org', 'https://user:secret@example.org/', 'https://example.org/\u000aalert', null]) {
  assert.equal(externalUrl(value), null);
}
assert.equal(externalUrl('https://example.org/work?q=hello#details'), 'https://example.org/work?q=hello#details');
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_displayed_times_use_explicit_utc_without_guessing_naive_dates():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend module contracts")
    module = (Path(__file__).parents[1] / "picshelf/web/js/format.js").as_uri()
    script = """
const {formatTimestamp} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
assert.equal(formatTimestamp('2026-10-10T21:35:00+08:00'), '2026-10-10 13:35:00 UTC');
assert.equal(formatTimestamp('2020-01-02T03:04:05-05:00'), '2020-01-02 08:04:05 UTC');
assert.equal(formatTimestamp('2020-01-02T03:04:05'), '—');
assert.equal(formatTimestamp(null), '—');
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_selection_keeps_explicit_cross_page_ids_and_separates_all_matching():
    node=shutil.which("node")
    if not node:pytest.skip("Node.js is required for selection contracts")
    module=(Path(__file__).parents[1]/"picshelf/web/js/selection.js").as_uri()
    script="""
const {ImageSelection}=await import(process.argv[1]);
const {strict:assert}=await import('node:assert');
const selected=new ImageSelection();
const source={q:'tag:blue',sort:'path',filters:{library_id:3}};
selected.toggle(7,true);selected.toggle(131,true);
assert.deepEqual(selected.payload(source),{image_ids:[7,131]});
selected.loaded([{id:7},{id:9}]);
assert.deepEqual(selected.payload(source),{image_ids:[7,131,9]});
selected.toggle(7,false);
assert.deepEqual(selected.payload(source),{image_ids:[131,9]});
selected.all();
assert.deepEqual(selected.payload(source),{query:{query:'tag:blue',sort:'path',library_id:3}});
selected.clear();assert.deepEqual(selected.payload(source),{image_ids:[]});
"""
    result=subprocess.run([node,"--input-type=module","-e",script,module],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_playback_clock_waits_for_display_and_pauses_without_duplicate_ticks():
    node=shutil.which('node')
    if not node:pytest.skip('Node.js is required for playback timer scenarios')
    module=(Path(__file__).parents[1]/'picshelf/web/js/playback-clock.js').as_uri()
    script="""
const {PlaybackClock}=await import(process.argv[1]);
const {strict:assert}=await import('node:assert');
let now=0,serial=0;const timers=new Map(),ticks=[];
const fake={setTimer:(fn,ms)=>{timers.set(++serial,{at:now+ms,fn});return serial;},clearTimer:id=>timers.delete(id)};
function advance(ms){const end=now+ms;while(true){const next=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();}now=end;}
const clock=new PlaybackClock(3,()=>ticks.push(now),fake);
advance(10000);assert.deepEqual(ticks,[]);
clock.update({ready:true});advance(1000);clock.update({hidden:true});advance(10000);
assert.deepEqual(ticks,[]);clock.update({hidden:false});advance(2999);assert.deepEqual(ticks,[]);
advance(1);assert.deepEqual(ticks,[24000]);advance(5000);assert.deepEqual(ticks,[24000]);
clock.update({ready:true});clock.update({paused:true});advance(7000);assert.deepEqual(ticks,[24000]);
clock.update({paused:false});advance(3000);assert.deepEqual(ticks,[24000,39000]);
clock.update({ready:true});clock.close();advance(10000);assert.deepEqual(ticks,[24000,39000]);
const manual=new PlaybackClock(0,()=>ticks.push('bad'),fake);manual.update({ready:true});advance(10000);
assert.deepEqual(ticks,[24000,39000]);
"""
    result=subprocess.run([node,'--input-type=module','-e',script,module],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_api_errors_distinguish_offline_and_preserve_recovery_advice():
    """A connection failure must not masquerade as an expired login."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for API error scenarios")
    module = (Path(__file__).parents[1] / "picshelf/web/js/api.js").as_uri()
    script = r'''
globalThis.localStorage = {getItem: () => 'en'};
const {api, setAuthFailHandler} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
let logouts = 0;
setAuthFailHandler(() => logouts++);
globalThis.fetch = async () => {throw new TypeError('Failed to fetch')};
await assert.rejects(api('/auth/me'), e => e.status === 0 && e.code === 'network_error'
  && /connection/i.test(e.message) && /retry/i.test(e.nextStep));
assert.equal(logouts, 0);
globalThis.fetch = async () => new Response(JSON.stringify({detail:'Access denied',
  error:{code:'library_forbidden',next_step:'Ask the library owner.'}}), {status:403});
await assert.rejects(api('/images/7'), e => e.status === 403 && e.code === 'library_forbidden'
  && e.nextStep === 'Ask the library owner.');
assert.equal(logouts, 0);
globalThis.fetch = async () => new Response('unavailable', {status:503});
await assert.rejects(api('/stats'), e => e.status === 503 && e.nextStep.length > 0);
globalThis.fetch = async () => new Response('', {status:401});
await assert.rejects(api('/auth/me'), e => e.status === 401);
assert.equal(logouts, 1);
'''
    result = subprocess.run([node, "--input-type=module", "-e", script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_viewer_frames_never_publish_late_or_half_loaded_images():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for viewer race scenarios")
    module = (Path(__file__).parents[1] / "picshelf/web/js/viewer-load.js").as_uri()
    script = r'''
const {ViewerLoader} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b}); return {promise,resolve,reject};};
const reads = new Map(), decodes = new Map(), signals = new Map();
const loader = new ViewerLoader({
 read:(id,signal)=>{signals.set(id,signal);const d=deferred();reads.set(id,d);return d.promise;},
 decode:(detail,mode)=>{const d=deferred();decodes.set(detail.id,{...d,mode});return d.promise;}
});
const first=loader.load(7,'fit');reads.get(7).resolve({id:7,filename:'A'});await Promise.resolve();
const second=loader.load(9,'original');reads.get(9).resolve({id:9,filename:'B'});await Promise.resolve();
assert(signals.get(7).aborted);
let published=false;second.then(()=>published=true);await Promise.resolve();assert.equal(published,false);
decodes.get(9).resolve('pixels B');assert.deepEqual(await second,{detail:{id:9,filename:'B'},image:'pixels B'});
decodes.get(7).resolve('pixels A');assert.equal(await first,null);
assert.equal(decodes.get(9).mode,'original');
const broken=loader.load(11,'fit');reads.get(11).resolve({id:11});await Promise.resolve();
decodes.get(11).reject(new Error('bad pixels'));await assert.rejects(broken,/bad pixels/);
const gone=loader.load(12,'fit');loader.cancel();reads.get(12).resolve({id:12});assert.equal(await gone,null);
assert(!decodes.has(12));
'''
    result = subprocess.run([node, "--input-type=module", "-e", script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_range_selection_uses_only_loaded_order_and_preserves_other_pages():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for selection scenarios")
    module = (Path(__file__).parents[1] / "picshelf/web/js/selection.js").as_uri()
    script = r'''
const {ImageSelection}=await import(process.argv[1]);const {strict:assert}=await import('node:assert');
const selection=new ImageSelection(), items=[{id:9},{id:4},{id:7},{id:2}];
selection.toggle(101,true);
selection.range(4,true,items,false);selection.range(2,true,items,true);
assert.deepEqual([...selection.ids],[101,4,7,2]);
selection.range(7,false,items,true);
assert.deepEqual([...selection.ids],[101,4]);
selection.range(9,true,[{id:9}],true);
assert.deepEqual([...selection.ids],[101,4,9]);
'''
    result = subprocess.run([node, "--input-type=module", "-e", script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
