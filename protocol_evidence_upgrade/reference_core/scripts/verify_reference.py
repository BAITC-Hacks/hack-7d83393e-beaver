"""Run actual reference-core tests; never convert these to model-quality PASS."""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import sys
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
os.environ.setdefault('PYTEST_DISABLE_PLUGIN_AUTOLOAD','1')
import pytest

class Results:
    def __init__(self): self.items={}
    def pytest_runtest_logreport(self,report):
        if report.when=='call' or report.failed or report.skipped:
            outcome={'passed':'PASS','failed':'FAIL','skipped':'NOT RUN'}[report.outcome]
            self.items[report.nodeid]={'test':report.nodeid,'status':outcome,'duration_s':round(report.duration,6)}

collector=Results()
reports=ROOT/'reports';reports.mkdir(exist_ok=True)
code=pytest.main(['-q','tests','--junitxml=reports/reference-junit.xml'],plugins=[collector])
items=list(collector.items.values())
result={'scope':'REFERENCE_CORE_ONLY','generated_at':datetime.now(timezone.utc).isoformat(),
        'python':platform.python_version(),'exit_code':int(code),'tests':items,
        'counts':{status:sum(i['status']==status for i in items) for status in ['PASS','FAIL','NOT RUN']},
        'not_tested':['audio_input','asr_ru','asr_kk','asr_mixed','diarization','llm_extraction','camera','natural_audio_e2e','full_pipeline_offline'],
        'claim':'Tests verify software mechanics on synthetic annotations, NOT AI accuracy or hackathon completion.'}
(reports/'reference-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(int(code))
