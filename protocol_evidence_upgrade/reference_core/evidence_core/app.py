"""Local reference service: no ASR, LLM, or camera is claimed here."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .audit import audit
from .ledger import replay
from .export_docx import render

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title='Protocol Evidence Reference', version='0.1.0', docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','testserver'])

@app.middleware('http')
async def boundaries(request: Request, call_next):
    if request.method in ('POST','PUT','PATCH'):
        origin = request.headers.get('origin')
        if origin:
            source = urlparse(origin)
            target = urlparse(str(request.url))
            if (source.scheme, source.netloc) != (target.scheme, target.netloc):
                return JSONResponse({'error':'CROSS_ORIGIN_DENIED'}, status_code=403)
        # Bound actual bytes even for transfer-encoding: chunked, before JSON parsing.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 2_000_000:
                return JSONResponse({'error':'PAYLOAD_TOO_LARGE'}, status_code=413)
        request._body = bytes(data)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return response

@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')

@app.get('/api/health')
def health():
    return {'status':'ok','scope':'REFERENCE_CORE_ONLY','models_loaded':False}

@app.get('/api/capabilities')
def capabilities():
    return {'scope':'REFERENCE_CORE_ONLY', 'implemented':['structural_audit','event_replay','docx_from_snapshot'],
            'not_implemented':['asr','diarization','llm_extraction','camera','full_meeting_workflow'],
            'model_quality':'NOT RUN'}

FIXTURES = {'mixed':'01_mixed_deadline.json','missing':'02_ru_missing_fields.json','kazakh':'03_kk_no_new_task.json','ledger':'ledger_mixed.json'}
@app.get('/api/fixtures/{name}')
def fixture(name: str):
    if name not in FIXTURES:
        raise HTTPException(404, 'Неизвестный пример')
    return json.loads((ROOT / 'fixtures' / FIXTURES[name]).read_text(encoding='utf-8'))

@app.post('/api/audit')
def run_audit(protocol: dict):
    return audit(protocol)

class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    bundle: dict
    cutoff_ms: int | None = Field(default=None, ge=0)

@app.post('/api/replay')
def run_replay(payload: ReplayRequest):
    try:
        return replay(payload.bundle, payload.cutoff_ms)
    except (ValueError, ValidationError):
        # A detailed local validator may be used by an operator; HTTP response excludes raw text.
        raise HTTPException(422, 'Нарушена схема или инварианты журнала событий')

@app.post('/api/export.docx')
def export(protocol: dict):
    try:
        content = render(protocol)
    except ValueError:
        raise HTTPException(422, 'Снимок не прошёл структурную проверку')
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Content-Disposition':'attachment; filename="protocol-draft.docx"'})

app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
