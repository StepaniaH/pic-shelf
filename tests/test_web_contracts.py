"""Frontend data, localization and interaction contracts without a browser."""
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.module_contract


def test_static_web_messages_have_english_translations():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend module contracts")
    root = Path(__file__).parents[1] / "picshelf/web"
    keys = set()
    literal = r'\bt\(\s*("(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27)'
    for path in (root / "js").glob("*.js"):
        if path.name == "i18n.js":
            continue
        keys.update(ast.literal_eval(match[1]) for match in re.finditer(literal, path.read_text()))
    keys.update(re.findall(r'data-i18n(?:-label|-placeholder)?="([^"]+)"', (root / "index.html").read_text()))
    keys = sorted(key for key in keys if re.search('[\u4e00-\u9fff]', key))
    script = """
const {EN} = await import(process.argv[1]);
const {readFileSync} = await import('node:fs');
const {strict: assert} = await import('node:assert');
const keys = JSON.parse(readFileSync(0, 'utf8'));
const missing = keys.filter(key => !Object.hasOwn(EN, key) || !EN[key]);
assert.deepEqual(missing, [], `Missing translations: ${missing.join(', ')}`);
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, (root / "js/i18n.js").as_uri()],
                            input=json.dumps(keys, ensure_ascii=False), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_gallery_pagination_handles_live_overlap_empty_pages_and_superseded_reads():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for gallery data contracts")
    module = (Path(__file__).parents[1] / "picshelf/web/js/source.js").as_uri()
    script = r'''
globalThis.localStorage = {getItem: () => 'en'};
const {Source} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const response = (ids, total) => new Response(JSON.stringify({items:ids.map(id=>({id})),total,errors:[]}));
const requests = [];
globalThis.fetch = async url => {
  requests.push(new URL(url, 'http://example.test').searchParams.get('offset'));
  return requests.length === 1 ? response([1,2,3], 6) : response([3,4,5], 6);
};
const source = new Source('Gallery');
await source.load();
await Promise.all([source.loadMore(), source.loadMore()]);
assert.deepEqual(source.items.map(item=>item.id), [1,2,3,4,5]);
assert.deepEqual(requests, ['0','3']);
assert.equal(source.loadedAll, true);
globalThis.fetch = async () => response([], 99);
await source.load();
assert.equal(source.loadedAll, true);
assert.equal(await source.loadMore(), false);
let release, calls=0;
globalThis.fetch = async () => ++calls === 1 ? new Promise(resolve=>{release=resolve}) : response([77],1);
const late = source.load();
await source.load();
release(response([66],1));
await late;
assert.deepEqual(source.items.map(item=>item.id),[77]);
source.nextOffset = 133;
let refreshedLimit;
globalThis.fetch = async url => {
  refreshedLimit = Number(new URL(url, 'http://example.test').searchParams.get('limit'));
  return response([2,3],2);
};
await source.refresh();
assert.equal(refreshedLimit,133);
assert.deepEqual(source.items.map(item=>item.id),[2,3]);
assert.equal(source.total,2);
assert.equal(source.nextOffset,2);
globalThis.fetch = async () => {throw new TypeError('offline')};
await assert.rejects(source.load(), /connection/i);
assert.deepEqual(source.items.map(item=>item.id),[2,3]);
'''
    result = subprocess.run([node, "--input-type=module", "-e", script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


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


def test_preview_cache_shares_decodes_retries_failures_and_bounds_retained_images():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for image loading contracts')
    module = (Path(__file__).parents[1] / 'picshelf/web/js/viewer-load.js').as_uri()
    script = r'''
const {PreviewCache} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const requests = [];
const cache = new PreviewCache({limit:3, decode:(url, signal) => new Promise((resolve, reject) => {
  requests.push({url, resolve, reject, signal});
  signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));
})});
cache.warm(['next']);
const controller = new AbortController();
const cancelled = cache.load('next', controller.signal);
controller.abort();
await assert.rejects(cancelled, {name:'AbortError'});
const foreground = cache.load('next');
assert.equal(requests.length,1);
requests[0].resolve({pixels:'next'});
assert.deepEqual(await foreground,{pixels:'next'});
assert.equal(requests[0].signal.aborted,false);
cache.warm(['previous','later']);
await cache.load('next');
cache.warm(['last']);
assert.equal(requests[1].signal.aborted,true);
assert.equal(cache.entries.size,3);
requests[3].reject(new Error('offline'));
await Promise.resolve(); await Promise.resolve();
const retry=cache.load('last');
assert.equal(requests.length,5);
requests[4].resolve({pixels:'last'}); await retry;
cache.clear();
assert.equal(cache.entries.size,0);
assert.equal(requests[2].signal.aborted,true);
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_refresh_preserves_more_than_ten_thousand_images_and_is_atomic_on_failure():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for gallery contracts')
    module = (Path(__file__).parents[1] / 'picshelf/web/js/source.js').as_uri()
    script = r'''
globalThis.localStorage = {getItem: () => 'en'};
const {Source} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const source = new Source('Large gallery');
source.items = [{id:'old'}]; source.nextOffset = 10120;
const requests = [];
let release;
globalThis.fetch = async url => {
  const p = new URL(url,'http://example.test').searchParams;
  const offset = Number(p.get('offset')), limit=Number(p.get('limit'));
  requests.push([offset,limit]);
  if(offset) await new Promise(resolve=>{release=resolve});
  return new Response(JSON.stringify({items:Array.from({length:limit},(_,n)=>({id:offset+n+1})),total:50000,errors:[]}));
};
const refresh = source.refresh();
while(!release) await new Promise(resolve=>setTimeout(resolve,0));
assert.deepEqual(source.items,[{id:'old'}]);
release(); await refresh;
assert.deepEqual(requests,[[0,10000],[10000,120]]);
assert.equal(source.items.length,10120);
assert.equal(source.items.at(-1).id,10120);
const previous=source.items;
globalThis.fetch = async url => {
  const offset=Number(new URL(url,'http://example.test').searchParams.get('offset'));
  if(offset) throw new Error('offline');
  return new Response(JSON.stringify({items:Array.from({length:10000},(_,n)=>({id:n+2})),total:50000,errors:[]}));
};
await assert.rejects(source.refresh(), /connection/i);
assert.equal(source.items,previous);
assert.equal(source.nextOffset,10120);
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('interrupt', ['cancel', 'reset'])
def test_pagination_waiting_for_refresh_cannot_restart_after_cancellation(interrupt):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for gallery contracts')
    module = (Path(__file__).parents[1] / 'picshelf/web/js/source.js').as_uri()
    script = r'''
globalThis.localStorage = {getItem: () => 'en'};
const {Source} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const response = id => new Response(JSON.stringify({items:[{id}],total:100,errors:[]}));
const source = new Source('Gallery');
globalThis.fetch = async () => response(1);
await source.load();
let requests = 0;
globalThis.fetch = async (url, {signal}) => {
  requests++;
  if (requests > 1) return response(7);
  return new Promise((resolve, reject) => signal.addEventListener('abort',
    () => reject(new DOMException('Aborted', 'AbortError')), {once:true}));
};
const refresh = source.refresh(), more = source.loadMore();
if (process.argv[2] === 'cancel') source.cancel();
else await source.load();
await refresh;
assert.equal(await more, false);
assert.equal(requests, process.argv[2] === 'cancel' ? 1 : 2);
assert.deepEqual(source.items.map(item => item.id), [process.argv[2] === 'cancel' ? 1 : 7]);
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, module, interrupt],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('late_result', ['response', 'failure'])
def test_refresh_after_write_replaces_reads_started_before_the_write(late_result):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for gallery contracts')
    module = (Path(__file__).parents[1] / 'picshelf/web/js/source.js').as_uri()
    script = r'''
globalThis.localStorage = {getItem: () => 'en'};
const {Source} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
const source = new Source('Gallery');
let current = 0, reads = 0, release, oldSignal;
globalThis.fetch = async (url, {signal}) => {
  const captured = current;
  if (++reads === 1) {
    oldSignal = signal;
    await new Promise(resolve => {release = resolve;});
    if (process.argv[2] === 'failure') throw new Error('Late connection failure');
  }
  return new Response(JSON.stringify({items:[{id:1,my_rating:captured}],total:1,errors:[]}));
};
const before = source.refresh();
assert.equal(source.refresh(), before);
current = 8;
await source.refresh({afterWrite:true});
assert.equal(oldSignal.aborted, true);
assert.equal(reads, 2);
assert.equal(source.items[0].my_rating, 8);
release();
assert.equal(await before, null);
assert.equal(source.items[0].my_rating, 8);
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, module, late_result],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_refresh_keeps_loaded_range_when_membership_shrinks_then_returns():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for gallery contracts')
    module = (Path(__file__).parents[1] / 'picshelf/web/js/source.js').as_uri()
    script = r'''
globalThis.localStorage = {getItem: () => 'en'};
const {Source} = await import(process.argv[1]);
const {strict: assert} = await import('node:assert');
let members = [1,2,3,4,5,6,7,8];
const requests = [];
globalThis.fetch = async url => {
  const p = new URL(url, 'http://example.test').searchParams;
  const offset = Number(p.get('offset')), limit = Number(p.get('limit'));
  requests.push({offset, limit, workflow:p.get('workflow')});
  return new Response(JSON.stringify({items:members.slice(offset, offset+limit).map(id=>({id})), total:members.length, errors:[]}));
};
const source = new Source('Inbox', {}, {filters:{workflow:'inbox'}});
await source.load(true, {limit:8});
members = members.slice(0,7);
await source.refresh({afterWrite:true});
assert.equal(source.items.length, 7);
members.push(8);
await source.refresh({afterWrite:true});
assert.deepEqual(source.items.map(item=>item.id), members);
assert.equal(source.loadedAll, true);
assert.equal(requests.at(-1).limit, 8);
assert.equal(requests.at(-1).workflow, 'inbox');
// An explicit new load starts a new range, such as changing the sort.
await source.load(true, {limit:3});
await source.refresh();
assert.deepEqual(source.items.map(item=>item.id), [1,2,3]);
assert.equal(requests.at(-1).limit, 3);
'''
    result = subprocess.run([node, '--input-type=module', '-e', script, module],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
