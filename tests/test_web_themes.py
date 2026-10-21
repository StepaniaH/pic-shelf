"""Theme preferences apply immediately across the actual Web interface."""
import os

import pytest

from tests.test_web import browser, page, web_server, sign_in

pytestmark = [pytest.mark.browser, pytest.mark.skipif(
    os.environ.get('PICSHELF_BROWSER_TESTS') != '1', reason='opt-in browser suite')]


def open_appearance(page):
    page.locator('.account-menu > summary').click()
    page.locator('[data-appearance]').click()
    page.locator('.appearance-modal').wait_for()


def choose(page, group, value):
    page.locator(f'.theme-choice:has(input[name="theme-{group}"][value="{value}"])').click()


@pytest.mark.parametrize('language,width', [('en',1280),('zh',390)])
def test_theme_cards_persist_and_leave_page_and_focus_intact(page, web_server, language, width):
    from playwright.sync_api import expect
    page.set_viewport_size({'width':width,'height':900})
    page.emulate_media(color_scheme='dark')
    sign_in(page,web_server,language)
    page.goto(web_server.url + '/#/search?sort=path')
    expect(page.locator('#sort-select')).to_have_value('path')
    original_url = page.url
    open_appearance(page)
    expect(page.locator('input[name=theme-mode]')).to_have_count(3)
    expect(page.locator('input[name=theme-palette]')).to_have_count(9)
    choose(page,'palette','gruvbox')
    choose(page,'mode','light')
    expect(page.locator('html')).to_have_attribute('data-palette','gruvbox')
    expect(page.locator('html')).to_have_attribute('data-scheme','light')
    assert page.locator('.theme-preview-system [data-preview-scheme]').count() == 2
    for preview in page.locator('[data-preview-palette]').all():
        assert preview.evaluate("el => getComputedStyle(el).getPropertyValue('--swatch-bg')")
    page.keyboard.press('Escape')
    expect(page.locator('.modal')).to_have_count(0)
    expect(page.locator('.account-menu > summary')).to_be_focused()
    assert page.url == original_url
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-scheme','light')
    expect(page.locator('html')).to_have_attribute('data-palette','gruvbox')
    expect(page.locator('#sort-select')).to_have_value('path')
    assert page.locator('body').evaluate('el => getComputedStyle(el).backgroundColor') == 'rgb(251, 241, 199)'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_system_mode_tracks_os_and_storage_updates_other_tabs(browser, web_server):
    from playwright.sync_api import expect
    context = browser.new_context()
    page = context.new_page()
    try:
        page.emulate_media(color_scheme='dark')
        sign_in(page,web_server)
        open_appearance(page)
        choose(page,'palette','solarized')
        page.emulate_media(color_scheme='light')
        expect(page.locator('html')).to_have_attribute('data-scheme','light')
        choose(page,'mode','dark')
        page.emulate_media(color_scheme='light')
        expect(page.locator('html')).to_have_attribute('data-scheme','dark')
        second = context.new_page()
        try:
            second.goto(web_server.url)
            expect(second.locator('html')).to_have_attribute('data-palette','solarized')
            second.locator('#view .page-head h1').wait_for()
            open_appearance(second)
            choose(page,'palette','nord')
            expect(second.locator('html')).to_have_attribute('data-palette','nord')
            expect(second.locator('input[name=theme-palette][value=nord]')).to_be_checked()
            choose(page,'mode','light')
            expect(second.locator('html')).to_have_attribute('data-scheme','light')
        finally:
            second.close()
    finally:
        context.close()


def test_saved_theme_applies_without_waiting_for_application_module(page, web_server):
    from playwright.sync_api import expect
    page.add_init_script("localStorage.setItem('picshelf.appearance',JSON.stringify({mode:'light',palette:'mocha'}))")
    page.route('**/js/app.js',lambda route:route.abort())
    page.goto(web_server.url)
    expect(page.locator('html')).to_have_attribute('data-scheme','light')
    expect(page.locator('html')).to_have_attribute('data-palette','mocha')
    assert page.locator('body').evaluate('el => getComputedStyle(el).backgroundColor') == 'rgb(244, 241, 247)'
    assert page.evaluate('''() => [...document.head.children].indexOf(document.querySelector('script[src="/js/theme.js"]'))
        < [...document.head.children].indexOf(document.querySelector('link[rel=stylesheet]'))''')


def test_blocked_storage_keeps_selection_and_explains_how_to_save(page, web_server):
    from playwright.sync_api import expect
    page.add_init_script("""(() => {
      const original=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key,value){
        if(key==='picshelf.appearance')throw new DOMException('Storage blocked','QuotaExceededError');
        return original.call(this,key,value);
      };
    })()""")
    sign_in(page,web_server)
    open_appearance(page)
    choose(page,'palette','dracula')
    expect(page.locator('html')).to_have_attribute('data-palette','dracula')
    expect(page.locator('.appearance-status')).to_contain_text('Allow site storage')
    expect(page.locator('input[name=theme-palette][value=dracula]')).to_be_checked()


def test_theme_radio_keyboard_and_double_text_size(page, web_server):
    from playwright.sync_api import expect
    page.set_viewport_size({'width':390,'height':844})
    sign_in(page,web_server)
    page.evaluate("document.documentElement.style.fontSize='32px'")
    open_appearance(page)
    page.keyboard.press('ArrowRight')
    expect(page.locator('input[name=theme-mode][value=dark]')).to_be_checked()
    page.keyboard.press('Tab')
    expect(page.locator('input[name=theme-palette][value=builtin]')).to_be_focused()
    page.keyboard.press('ArrowLeft')
    expect(page.locator('html')).to_have_attribute('data-palette','mocha')
    expect(page.locator('input[name=theme-palette][value=mocha]')).to_be_focused()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('.appearance-modal').evaluate('el => el.scrollWidth <= el.clientWidth')
    page.keyboard.press('Escape')
    expect(page.locator('.account-menu > summary')).to_be_focused()


@pytest.mark.parametrize('scheme',['dark','light'])
def test_each_palette_covers_forms_menus_and_viewer_header(page, web_server, scheme):
    from playwright.sync_api import expect
    sign_in(page,web_server)
    open_appearance(page)
    choose(page,'mode',scheme)
    for palette in ['builtin','gruvbox','catppuccin','solarized','nord','dracula','tokyo-night','macchiato','mocha']:
        choose(page,'palette',palette)
        assert page.evaluate('''() => {
          const theme=PicShelfTheme.get(), expected=PicShelfTheme.colors(theme.palette,theme.scheme);
          return getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim()===expected['--menu-bg'];
        }''')
    page.keyboard.press('Escape')
    page.goto(web_server.url + '/#/search')
    page.locator('#sort-select').click()
    page.locator('#sort-select option[value=path]').click()
    expect(page.locator('#sort-select')).to_have_value('path')
    page.locator('#grid .card').first.click()
    expect(page.locator('.viewer-backdrop')).to_be_visible()
    expected = page.evaluate("PicShelfTheme.colors('mocha','"+scheme+"')['--panel']")
    actual = page.locator('.viewer-top').evaluate('el => getComputedStyle(el).backgroundColor')
    rgb = tuple(int(expected[index:index+2],16) for index in (1,3,5))
    assert actual == 'rgb(%d, %d, %d)' % rgb
    page.locator('#v-close').click()
    expect(page.locator('.viewer-backdrop')).to_have_count(0)
