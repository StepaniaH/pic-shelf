"""Real browser import flows without typing source paths."""
import base64
import os

import pytest

from tests.conftest import make_image
from tests.test_web import browser, page, web_server, sign_in

pytestmark = [pytest.mark.browser, pytest.mark.skipif(
    os.environ.get('PICSHELF_BROWSER_TESTS') != '1', reason='opt-in browser suite')]


def open_import(page, server, target, name):
    target.mkdir()
    response = page.request.post(server.url + '/api/v1/libraries',
                                 headers={'Origin':server.url},
                                 data={'name':name, 'root_path':str(target), 'mode':'managed'})
    assert response.ok, response.text()
    lid = response.json()['id']
    page.goto(server.url + f'/#/library/{lid}')
    page.locator('[data-import]').click()
    page.locator('#imp-drop').wait_for()
    return lid


@pytest.mark.parametrize('language,width', [('en',1280), ('zh',390)])
def test_choose_images_imports_without_paths(page, web_server, tmp_path, language, width):
    from playwright.sync_api import expect
    page.set_viewport_size({'width':width, 'height':900})
    sign_in(page, web_server, language)
    target = tmp_path / 'target'
    open_import(page, web_server, target, f'File selection {language}')
    original_url = page.url
    expect(page.locator('#imp-src')).to_be_hidden()
    source = make_image(tmp_path / 'local.png', (10,70,190))
    with page.expect_file_chooser() as chooser:
        page.locator('#imp-pick-files').click()
    chooser.value.set_files(str(source))
    expect(page.locator('.import-thumbnails figcaption')).to_have_text('local.png')
    page.locator('#imp-plan').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    expect(page.locator('#imp-out')).to_contain_text('local.png')
    assert not (target / 'local.png').exists()
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
    assert (target / 'local.png').read_bytes() == source.read_bytes()
    assert page.url == original_url
    page.locator('#imp-another').click()
    expect(page.locator('#imp-pick-files')).to_be_focused()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('.modal-head [data-close]').click()
    expect(page.locator('[data-import]')).to_be_focused()
    expect(page.locator('#grid .card')).to_have_count(1)


def test_choose_folder_preserves_nested_paths(page, web_server, tmp_path):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    target = tmp_path / 'target'
    open_import(page, web_server, target, 'Folder selection')
    source = make_image(tmp_path / 'Trip' / 'Day 1' / 'blue.png')
    (tmp_path / 'Trip' / 'notes.txt').write_text('not an image')
    with page.expect_file_chooser() as chooser:
        page.locator('#imp-pick-folder').click()
    chooser.value.set_files(str(tmp_path / 'Trip'))
    expect(page.locator('.import-thumbnails figcaption')).to_have_attribute('title', 'Trip/Day 1/blue.png')
    expect(page.locator('#imp-selection')).to_contain_text('Skipped 1')
    page.locator('#imp-plan').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
    assert (target / 'Trip' / 'Day 1' / 'blue.png').read_bytes() == source.read_bytes()
    assert not list(target.rglob('*.txt'))


def test_drop_images_then_browse_server_without_typing_paths(page, web_server, tmp_path):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    target = tmp_path / 'target'
    lid = open_import(page, web_server, target, 'Drop selection')
    source = make_image(tmp_path / 'dropped.png', (70,150,200))
    second = make_image(tmp_path / 'other' / 'dropped.png', (180,40,70))
    page.evaluate('''data => {
      const transfer = new DataTransfer();
      for (const content of data) transfer.items.add(new File([Uint8Array.from(atob(content), c => c.charCodeAt(0))], 'dropped.png', {type:'image/png'}));
      document.querySelector('#imp-drop').dispatchEvent(new DragEvent('drop', {dataTransfer:transfer, bubbles:true, cancelable:true}));
    }''', [base64.b64encode(image.read_bytes()).decode() for image in (source, second)])
    expect(page.locator('.import-thumbnails figcaption')).to_have_text(['dropped.png', 'dropped.png'])
    page.locator('#imp-plan').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
    assert (target / 'dropped.png').read_bytes() == source.read_bytes()
    assert (target / 'dropped-2.png').read_bytes() == second.read_bytes()
    page.locator('#imp-another').click()
    source_dir = tmp_path / 'server-source'
    source_image = make_image(source_dir / 'server.png', (200,50,90))
    response = page.request.post(web_server.url + '/api/v1/import-sources',
                                 headers={'Origin':web_server.url}, data={'root_path':str(source_dir)})
    assert response.ok, response.text()
    page.locator('#imp-server').click()
    page.locator('.import-directories button').filter(has_text='server-source').click()
    page.locator('[data-use]').click()
    page.locator('#imp-plan').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
    assert (target / 'server.png').stat().st_ino == source_image.stat().st_ino


@pytest.mark.parametrize('language', ['en', 'zh'])
def test_create_destination_and_import_with_only_a_name(page, web_server, tmp_path, language):
    from pathlib import Path
    from playwright.sync_api import expect
    page.set_viewport_size({'width':390 if language == 'zh' else 1280, 'height':900})
    sign_in(page, web_server, language)
    original_url = page.url
    page.locator('[data-new-library]').first.click()
    expect(page.locator('.modal').get_by_text('高级选项' if language == 'zh' else 'Advanced options', exact=True)).to_have_count(1)
    expect(page.locator('.modal')).not_to_contain_text('来源与方式' if language == 'zh' else 'Source and method')
    assert page.evaluate('''() => document.querySelector('#imp-src').form.id === 'imp-form'
      && document.querySelector('#nl-path').form.id === 'nl-form' ''')
    expect(page.locator('#nl-path')).not_to_have_attribute('required', '')
    for selector in ('#imp-pick-files', '#imp-pick-folder', '#imp-server', '[data-library-options] > summary'):
        page.keyboard.press('Tab')
        expect(page.locator(selector)).to_be_focused()
    assert page.locator('.modal .btn.primary:visible').count() == 1
    name = 'Automatic destination ' + language
    page.locator('#nl-name').fill(name)
    expect(page.locator('#imp-pick-files')).to_be_visible()
    source = make_image(tmp_path / 'only-name.png')
    page.locator('#imp-files').set_input_files(str(source))
    expect(page.locator('#imp-mode')).to_be_hidden()
    page.wait_for_function("document.querySelector('.import-thumbnails img').naturalWidth > 0")
    assert page.evaluate('''() => {
      const order = ['#nl-name', '#imp-drop', '.import-thumbnails', '[data-library-options]', '[data-library-actions]'].map(selector => document.querySelector(selector));
      return order.every((node, index) => !index || (order[index - 1].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING))
        && order.every((node, index) => !index || order[index - 1].getBoundingClientRect().bottom <= node.getBoundingClientRect().top)
        && document.documentElement.scrollWidth <= innerWidth;
    }''')
    expect(page.locator('#nl-create')).to_be_in_viewport()
    page.locator('#nl-create').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
    libraries = page.request.get(web_server.url + '/api/v1/libraries').json()
    library = next(lib for lib in libraries if lib['name'] == name)
    assert (Path(library['root_path']) / source.name).read_bytes() == source.read_bytes()
    assert page.url == original_url
    page.locator('#imp-browse').click()
    expect(page.locator('.modal-backdrop')).to_have_count(0)
    expect(page.locator('.lib-card').filter(has_text=name)).to_have_count(1)


def test_album_creation_import_and_existing_album_import_stay_in_dialog(page, web_server, tmp_path):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    target = tmp_path / 'target'
    lid = open_import(page, web_server, target, 'Album imports')
    page.locator('.modal-head [data-close]').click()
    page.goto(web_server.url + '/#/albums')
    original_url = page.url
    page.locator('[data-new-album]').click()
    form = page.locator('.catalog-form')
    form.locator('[name=name]').fill('Trip album')
    form.locator('[name=library_id]').select_option(str(lid))
    source = make_image(tmp_path / 'trip' / 'one.png', (50,120,200))
    page.locator('#imp-folder').set_input_files(str(tmp_path / 'trip'))
    page.locator('[data-album-options] > summary').click()
    page.locator('[data-album-options] [name=category]').fill('Travel')
    page.locator('[data-album-options] [name=description]').fill('Pictures from our trip.')
    assert page.locator('.modal').get_by_text('Advanced options', exact=True).count() == 1
    assert page.evaluate('''() => document.querySelector('.import-thumbnails').getBoundingClientRect().bottom
      <= document.querySelector('[data-album-options]').getBoundingClientRect().top
      && document.querySelector('[data-album-options]').getBoundingClientRect().bottom
      <= document.querySelector('[data-album-actions]').getBoundingClientRect().top''')
    page.locator('[data-album-submit]').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
    assert page.url == original_url
    page.locator('#imp-browse').click()
    card = page.locator('.album-grid .card').filter(has_text='Trip album')
    expect(card).to_have_count(1)
    card.click()
    expect(page.locator('#grid .card')).to_have_count(1)
    album_id = int(page.url.split('/album/')[1].split('?')[0])
    album = page.request.get(web_server.url + f'/api/v1/albums/{album_id}').json()
    assert album['category'] == 'Travel'
    assert album['description'] == 'Pictures from our trip.'
    original_url = page.url
    second = make_image(tmp_path / 'two.png', (160,40,100))
    page.locator('[data-import]').click()
    page.locator('#imp-files').set_input_files(str(second))
    page.locator('#imp-plan').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
    page.locator('#imp-browse').click()
    assert page.url == original_url
    expect(page.locator('#grid .card')).to_have_count(2)
    expect(page.locator('[data-import]')).to_be_focused()


def test_cancel_add_library_keeps_page_and_does_not_create_or_start_import(page, web_server, tmp_path):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    original_url = page.url
    page.locator('[data-new-library]').first.click()
    page.locator('#nl-name').fill('Cancelled destination')
    page.evaluate('''() => {
      window.previewUrls = {created:[], revoked:[]};
      const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
      URL.createObjectURL = file => { const url = create(file); previewUrls.created.push(url); return url; };
      URL.revokeObjectURL = url => { previewUrls.revoked.push(url); revoke(url); };
    }''')
    source = make_image(tmp_path / 'cancel.png')
    page.locator('#imp-files').set_input_files(str(source))
    page.locator('#imp-files').set_input_files(str(source))
    assert page.evaluate('previewUrls.created.length === 2 && previewUrls.revoked.length === 1')
    page.keyboard.press('Escape')
    assert page.evaluate('JSON.stringify(previewUrls.created) === JSON.stringify(previewUrls.revoked)')
    expect(page.locator('.modal-backdrop')).to_have_count(0)
    assert page.url == original_url
    expect(page.locator('[data-new-library]').first).to_be_focused()
    libraries = page.request.get(web_server.url + '/api/v1/libraries').json()
    assert not any(library['name'] == 'Cancelled destination' for library in libraries)


def test_server_folder_selection_defers_new_library_until_preview(page, web_server, tmp_path):
    from urllib.parse import parse_qs, urlparse
    from playwright.sync_api import expect
    sign_in(page, web_server)
    original_url = page.url
    source_dir = tmp_path / 'deferred-library-source'
    make_image(source_dir / 'one.png')
    response = page.request.post(web_server.url + '/api/v1/import-sources',
                                 headers={'Origin':web_server.url}, data={'root_path':str(source_dir)})
    assert response.ok, response.text()
    before = page.request.get(web_server.url + '/api/v1/libraries').json()
    for cancel in (True, False):
        page.locator('[data-new-library]').first.click()
        with page.expect_response(lambda response: '/api/v1/import/sources' in response.url) as browsing:
            page.locator('#imp-server').click()
        assert browsing.value.ok
        assert 'library_id' not in parse_qs(urlparse(browsing.value.url).query)
        expect(page.locator('#nl-name')).to_be_enabled()
        expect(page.locator('#nl-name')).to_have_value('')
        page.locator('.import-directories button').filter(has_text=source_dir.name).click()
        page.locator('[data-use]').click()
        assert page.request.get(web_server.url + '/api/v1/libraries').json() == before
        page.locator('#nl-name').fill('Deferred server library')
        if cancel:
            page.locator('.modal-head [data-close]').click()
            expect(page.locator('.modal-backdrop')).to_have_count(0)
            assert page.request.get(web_server.url + '/api/v1/libraries').json() == before
        else:
            page.locator('#nl-create').click()
            expect(page.locator('#imp-exec')).to_be_enabled()
            libraries = page.request.get(web_server.url + '/api/v1/libraries').json()
            assert len(libraries) == len(before) + 1
            assert libraries[-1]['name'] == 'Deferred server library'
        assert page.url == original_url


def test_server_folder_selection_uses_album_target_without_creating_album(page, web_server, tmp_path):
    from urllib.parse import parse_qs, urlparse
    from playwright.sync_api import expect
    sign_in(page, web_server)
    targets = []
    for number in (1, 2):
        target = tmp_path / f'target-{number}'
        target.mkdir()
        response = page.request.post(web_server.url + '/api/v1/libraries',
                                     headers={'Origin':web_server.url},
                                     data={'name':f'Deferred album target {number}', 'root_path':str(target), 'mode':'managed'})
        assert response.ok, response.text()
        targets.append(response.json()['id'])
    page.goto(web_server.url + '/#/albums')
    original_url = page.url
    before = page.request.get(web_server.url + '/api/v1/albums').json()
    page.locator('[data-new-album]').click()
    form = page.locator('#album-form')
    for library_id in targets:
        form.locator('[name=library_id]').select_option(str(library_id))
        expect(page.locator('#imp-server-browser')).to_be_hidden()
        with page.expect_response(lambda response: '/api/v1/import/sources' in response.url) as browsing:
            page.locator('#imp-server').click()
        assert browsing.value.ok
        assert parse_qs(urlparse(browsing.value.url).query)['library_id'] == [str(library_id)]
        expect(form.locator('[name=name]')).to_be_enabled()
        expect(form.locator('[name=library_id]')).to_be_enabled()
        assert page.request.get(web_server.url + '/api/v1/albums').json() == before
    form.locator('[name=name]').fill('Cancelled server album')
    page.locator('.modal-head [data-close]').click()
    expect(page.locator('.modal-backdrop')).to_have_count(0)
    assert page.url == original_url
    assert page.request.get(web_server.url + '/api/v1/albums').json() == before


def test_close_preview_discards_staging_without_executing(page, web_server, tmp_path):
    from pathlib import Path
    from playwright.sync_api import expect
    sign_in(page, web_server)
    target = tmp_path / 'target'
    open_import(page, web_server, target, 'Discard preview')
    original_url = page.url
    source = make_image(tmp_path / 'original.png')
    page.locator('#imp-files').set_input_files(str(source))
    with page.expect_response(lambda response: '/import/uploads/' in response.url and response.url.endswith('/plan')) as saved:
        page.locator('#imp-plan').click()
    plan = saved.value.json()
    expect(page.locator('#imp-exec')).to_be_enabled()
    page.keyboard.press('Escape')
    expect(page.locator('.modal-backdrop')).to_have_count(0)
    assert page.url == original_url
    page.wait_for_function('''async id => {
      const response = await fetch('/api/v1/import/plans/' + id);
      return (await response.json()).state === 'cancelled';
    }''', arg=plan['plan_id'])
    assert not Path(plan['source']).exists()
    assert list(target.iterdir()) == []
    assert source.is_file()
    expect(page.locator('[data-import]')).to_be_focused()


def test_create_managed_library_from_import_keeps_selected_files_and_route(page, tmp_path):
    from playwright.sync_api import expect
    from picshelf import auth, db, libsvc
    from picshelf.config import Config
    from tests.scenario_support import http_server
    import secrets
    cfg = Config(tmp_path / 'data').ensure()
    password = secrets.token_urlsafe(24)
    with db.open_db(cfg.db_path) as conn:
        auth.ensure_default_groups(conn)
        auth.create_user(conn, 'browser-test', password, is_admin=True)
        existing = tmp_path / 'indexed'; existing.mkdir()
        libsvc.create_library(conn, 'Indexed', str(existing), mode='indexed')
    source = make_image(tmp_path / 'chosen.png')
    from tests.test_web import WebServer
    with http_server(cfg) as client:
        server = WebServer(str(client.base_url), password, tmp_path, tmp_path)
        sign_in(page, server)
        original_url = page.url
        page.locator('[data-import]').click()
        page.locator('#imp-files').set_input_files(str(source))
        page.locator('[data-create-library]').click()
        page.locator('#nl-name').fill('From selection')
        expect(page.locator('.import-thumbnails figcaption')).to_have_text('chosen.png')
        page.locator('#nl-create').click()
        expect(page.locator('#imp-exec')).to_be_enabled()
        page.locator('#imp-exec').click()
        expect(page.locator('#imp-browse')).to_be_visible(timeout=15000)
        assert page.url == original_url
        page.locator('#imp-browse').click()
        expect(page.locator('.lib-card').filter(has_text='From selection')).to_have_count(1)
