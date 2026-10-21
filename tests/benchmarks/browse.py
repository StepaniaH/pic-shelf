"""Measure a large generated gallery in Chromium, including decoded previews."""
import argparse
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import secrets
import statistics
import tempfile
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright

from picshelf import auth, catalog, db, libsvc, play
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.config import Config
from tests.benchmarks.catalog import measure
from tests.datasets import draw_image, metadata_rows
from tests.scenario_support import http_server


def run(root, count=50000, loaded=5040, prefetch=True):
    cfg = Config(root / 'data').ensure()
    originals = root / 'originals'; originals.mkdir()
    draw_image(originals / 'sample.png', 1, (1280, 960))
    with Image.open(originals / 'sample.png') as image:
        image.resize((320, 240)).save(cfg.media_dir / 'sample-thumb.webp')
        image.save(cfg.media_dir / 'sample-preview.webp')
    password = secrets.token_urlsafe(24)
    with closing(db.open_db(cfg.db_path)) as conn:
        auth.ensure_default_groups(conn)
        uid = auth.create_user(conn, 'benchmark', password, is_admin=True)
        lid = libsvc.create_library(conn, 'Scale gallery', str(originals.resolve()))
        rows = list(metadata_rows(count, lid))
        for row in rows:
            row.update(thumb_rel='sample-thumb.webp', preview_rel='sample-preview.webp', width=1280, height=960)
        fields = list(rows[0])
        conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                         [list(row.values()) for row in rows]); conn.commit()
        access = AccessContext(uid)
        session = play.create_for_user(conn, access, {'source':{'library_id':lid}, 'sort':'path', 'loop':True})
        result = {'dataset':{'count':count, 'loaded':loaded, 'preview_size':[1280, 960]}, 'backend':{}}
        result['backend']['navigate_10000'] = measure(lambda: play.navigate(conn, access, session['token'], 'next'), 30)
        for name, spec in [
            ('filename', catalog.ImageQuery(query='file:049', sort='newest', limit=120)),
            ('deep_page', catalog.ImageQuery(sort='path', offset=count * 4 // 5, limit=120)),
        ]:
            result['backend'][name] = measure(lambda: catalog.query_images(conn, access, spec), 30)

    def delayed_app(config):
        app = create_app(config)
        @app.middleware('http')
        async def preview_latency(request, call_next):
            if '/media/preview/' in request.url.path:
                await asyncio.sleep(.15)
            return await call_next(request)
        return app

    with patch('tests.scenario_support.create_app', side_effect=delayed_app), http_server(cfg) as client, sync_playwright() as p:
        options = {'headless':True}
        if os.environ.get('PICSHELF_TEST_BROWSER'): options['executable_path'] = os.environ['PICSHELF_TEST_BROWSER']
        browser = p.chromium.launch(**options)
        result['environment'] = {'browser':browser.version, 'system':platform.system(), 'architecture':platform.machine()}
        page = browser.new_page(viewport={'width':1280, 'height':800})
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('benchmark'); page.locator('#login-password').fill(password)
        page.locator('#login-form button').click(); page.locator('.lib-card').wait_for()
        result['grid'] = page.evaluate('''async loaded => {
          const {Source,renderGridInto} = await import('/js/views.js');
          const {state} = await import('/js/app.js'); state.dispose?.();
          const source = new Source('Scale gallery',{library_id:1},{sort:'path',filters:{library_id:1}});
          await source.load(true,{limit:loaded});
          const start=performance.now();const paint=renderGridInto(document.querySelector('#view'),source);
          const height=document.querySelector('#grid').offsetHeight;
          const render=performance.now()-start;
          await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
          const again=performance.now();paint();document.querySelector('#grid').offsetHeight;
          return {render_ms:render,repaint_ms:performance.now()-again,cards:document.querySelectorAll('#grid .card').length,height};
        }''', loaded)
        page.wait_for_timeout(700)
        result['scroll'] = page.evaluate('''async () => {
          const frames=[]; let last=performance.now();
          for(let i=0;i<100;i++) {scrollBy(0,180);await new Promise(requestAnimationFrame);const now=performance.now();frames.push(now-last);last=now;}
          frames.sort((a,b)=>a-b);return {p95_frame_ms:frames[95],max_frame_ms:frames.at(-1),cards:document.querySelectorAll('#grid .card').length};
        }''')
        page.evaluate('scrollTo(0,0)'); page.wait_for_timeout(300)
        if not prefetch:
            page.evaluate("async () => {(await import('/js/viewer-load.js')).PreviewCache.prototype.warm=()=>{};}")
        page.locator('#slideshow-btn').click(); page.locator('#ss-interval').fill('0')
        page.locator('#ss-start').click(); page.locator('#ss-img:not([hidden])').wait_for()
        times = []
        for _ in range(8):
            page.wait_for_timeout(350)
            times.append(page.evaluate('''async () => {
              const old=document.querySelector('#ss-img').alt;
              const start=performance.now();document.querySelector('#ss-next').click();
              await new Promise(resolve=>{
                const observer=new MutationObserver(()=>{
                  const image=document.querySelector('#ss-img');
                  if(image&&!image.hidden&&image.alt!==old&&image.naturalWidth){observer.disconnect();resolve();}
                });observer.observe(document.querySelector('.slideshow-backdrop'),{subtree:true,childList:true,attributes:true});
              });return performance.now()-start;
            }'''))
        result['slideshow'] = {'prefetch':prefetch, 'media_delay_ms':150, 'switches':len(times),
                               'median_switch_ms':statistics.median(times), 'max_switch_ms':max(times)}
        page.locator('#ss-exit').click()
        result['errors'] = errors
        assert not errors
        browser.close()
    result['limitations'] = ['Generated metadata shares one synthetic preview; not an original-image decode or disk-cache benchmark',
                             '1280×800 Chromium viewport; media responses delayed by 150 ms on the isolated server']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=50000)
    parser.add_argument('--loaded', type=int, default=5040)
    parser.add_argument('--without-prefetch', action='store_true')
    args = parser.parse_args()
    if not 1000 <= args.count <= 100000 or not 240 <= args.loaded <= min(10000, args.count):
        parser.error('use 1,000–100,000 records and 240–10,000 loaded images')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='browse-', dir=args.output.parent))
    report = run(root, args.count, args.loaded, not args.without_prefetch)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key:report[key] for key in ('grid', 'scroll', 'slideshow', 'errors')}))
