"""Chromium component test with real FastAPI ASGI calls, not live-network E2E.

The browser's managed network policy in the build environment blocks navigation
to loopback. No policy is disabled: this separate test uses an in-process adapter.
"""
from pathlib import Path
import base64,json,os,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from evidence_core.app import app
from playwright.sync_api import sync_playwright
with TestClient(app) as client, sync_playwright() as p:
    options={'headless':True,'executable_path':os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')}
    if hasattr(os,'geteuid') and os.geteuid()==0:options['args']=['--no-sandbox']
    browser=p.chromium.launch(**options)
    page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def asgi(path, options):
        if not isinstance(path,str) or not path.startswith('/api/'):
            raise ValueError('Only local API routes are allowed by this test adapter')
        result=client.request(options.get('method','GET'),path,headers=options.get('headers',{}),content=options.get('body'))
        return {'status':result.status_code,'headers':dict(result.headers),'b64':base64.b64encode(result.content).decode()}
    page.expose_function('asgiRequest',asgi)
    html=(ROOT/'static'/'index.html').read_text()
    html=html.replace('<link rel="stylesheet" href="/static/style.css">','').replace('<script defer src="/static/app.js"></script>','')
    page.set_content(html)
    page.add_style_tag(content=(ROOT/'static'/'style.css').read_text())
    page.add_script_tag(content="""window.fetch=async(path,options={})=>{const r=await window.asgiRequest(path,options);const data=Uint8Array.from(atob(r.b64),c=>c.charCodeAt(0));return new Response(data,{status:r.status,headers:r.headers});};""")
    page.add_script_tag(content=(ROOT/'static'/'app.js').read_text())
    page.wait_for_selector('.task')
    assert page.locator('.task').count()==2
    assert '2026-09-28' in page.locator('.task').first.inner_text()
    page.locator('#timeline').fill('16000');page.locator('#timeline').dispatch_event('input')
    page.wait_for_function("document.querySelector('#tasks').textContent.includes('2026-09-25')")
    assert 'Действующие значения пока сохранены' in page.locator('#tasks').inner_text()
    page.locator('#timeline').fill('48000');page.locator('#timeline').dispatch_event('input')
    page.wait_for_function("document.querySelectorAll('.task').length===2")
    page.locator('.task').first.locator('.evidence').last.click()
    assert page.locator('.utterance.selected').count()==2
    page.screenshot(path=str(ROOT/'reports'/'ui-reference.png'),full_page=True)
    page.locator('#audit-tab').click();page.locator('#audit-run').click()
    page.wait_for_selector('.result')
    assert 'PASS / структура' in page.locator('.result').inner_text()
    with page.expect_download() as dl:page.locator('#export-docx').click()
    dl.value.save_as(str(ROOT/'reports'/'ui-downloaded-protocol.docx'))
    expected=json.loads((ROOT/'fixtures'/'02_ru_missing_fields.json').read_text())['meeting']['id']
    page.locator('#fixture').select_option('missing')
    page.wait_for_function('(id)=>document.querySelector("#snapshot").textContent.includes(id)',arg=expected)
    page.locator('#audit-run').click()
    page.wait_for_selector('.result')
    assert 'PASS / структура' in page.locator('.result').inner_text()
    assert not errors,errors
    # Browser XSS check on imported untrusted strings; no model-security claim.
    fixture=json.loads((ROOT/'fixtures'/'01_mixed_deadline.json').read_text())
    fixture['tasks'][0]['action']='<img src=x onerror="window.XSS_EXECUTED=true">'
    page.locator('#upload').set_input_files({'name':'xss-test.json','mimeType':'application/json','buffer':json.dumps(fixture).encode()})
    page.wait_for_function("document.querySelector('#snapshot').textContent.includes('XSS_EXECUTED')")
    assert page.evaluate('window.XSS_EXECUTED === undefined')
    result={'scope':'REFERENCE_BROWSER_COMPONENT_ONLY','status':'PASS','transport':'TestClient / in-process ASGI via test fetch adapter','browser':browser.version,
        'checks':['component_render','time_slider','accepted_and_rejected_change','evidence_click','audit_button','docx_download','switch_fixture','escaped_untrusted_text','no_javascript_errors'],
        'live_browser_http':'NOT RUN','live_browser_http_reason':'Managed Chromium policy returned ERR_BLOCKED_BY_ADMINISTRATOR for loopback; no policy changed.',
        'network_isolation':'NOT RUN','model_inference':'NOT RUN'}
    (ROOT/'reports'/'browser-component-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    browser.close()
    print('BROWSER COMPONENT PASS (NOT live-network E2E)')
