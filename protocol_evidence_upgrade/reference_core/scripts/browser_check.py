"""Reference UI smoke test. Start the server first; no AI quality claims."""
from pathlib import Path
import json,os,shutil
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
base=os.environ.get('REFERENCE_URL','http://127.0.0.1:8765')
with sync_playwright() as p:
    executable=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')
    options={'headless':True}
    if executable:options['executable_path']=executable
    # Container-specific test flag; not required by the application or normal browsers.
    if hasattr(os,'geteuid') and os.geteuid()==0:options['args']=['--no-sandbox']
    browser=p.chromium.launch(**options)
    page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
    errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:requests.append(r.url))
    page.goto(base,wait_until='networkidle')
    page.wait_for_selector('.task')
    assert page.locator('.task').count()==2
    assert '2026-09-28' in page.locator('.task').first.inner_text()
    page.locator('#timeline').fill('16000')
    page.locator('#timeline').dispatch_event('input')
    page.wait_for_function("document.querySelector('#tasks').textContent.includes('2026-09-25')")
    assert 'Действующие значения пока сохранены' in page.locator('#tasks').inner_text()
    page.locator('#timeline').fill('48000');page.locator('#timeline').dispatch_event('input')
    page.wait_for_function("document.querySelectorAll('.task').length===2")
    page.locator('.task').first.locator('.evidence').last.click()
    assert page.locator('.utterance.selected').count()==2
    page.screenshot(path=str(ROOT/'reports'/'ui-reference.png'),full_page=True)
    page.locator('#audit-tab').click()
    page.locator('#audit-run').click()
    page.wait_for_selector('.result')
    assert 'PASS / структура' in page.locator('.result').inner_text()
    with page.expect_download() as dl:page.locator('#export-docx').click()
    dl.value.save_as(str(ROOT/'reports'/'ui-downloaded-protocol.docx'))
    expected=json.loads((ROOT/'fixtures'/'02_ru_missing_fields.json').read_text())['meeting']['id']
    page.locator('#fixture').select_option('missing')
    page.wait_for_function('(id)=>document.querySelector("#snapshot").textContent.includes(id)',arg=expected)
    page.locator('#audit-run').click()
    page.wait_for_function("document.querySelector('.result') && document.querySelector('.result').textContent.includes('PASS / структура')")
    assert not errors,errors
    external=[u for u in requests if not u.startswith(base+'/')]
    assert not external,external
    result={'scope':'REFERENCE_UI_ONLY','status':'PASS','browser':browser.version,
        'checks':['loaded_reference_ui','time_slider_preserves_unaccepted_deadline','accepted_deadline_visible','click_evidence_highlights_utterances','structure_audit','docx_download','switch_synthetic_fixture','no_javascript_errors','no_external_requests_observed'],
        'external_requests_observed':external,'full_pipeline_network_isolation':'NOT RUN','model_inference':'NOT RUN'}
    (ROOT/'reports'/'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    browser.close()
    print('BROWSER PASS')
