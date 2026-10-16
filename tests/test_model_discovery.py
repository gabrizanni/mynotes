"""Exercise real metadata subprocesses and protocols with disposable CLIs."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mynotes import model_discovery as d


def executable(tmp_path,provider,code):
    path=tmp_path/provider;path.write_text('#!'+sys.executable+'\n'+code);path.chmod(0o700)
    return {**os.environ,'PATH':str(tmp_path)+os.pathsep+os.environ['PATH'],'CODEX_HOME':str(tmp_path/'home'),'TRACE':str(tmp_path/'trace.jsonl')}


RPC='''import sys,json,os
for line in sys.stdin:
 m=json.loads(line)
 with open(os.environ['TRACE'],'a') as trace:trace.write(json.dumps(m)+'\\n')
 method=m['method']
 if method=='initialize':result={'userAgent':'codex-test'}
 elif method=='initialized':continue
 elif method=='account/read':result={'account':{'type':'chatgpt','planType':'plus'}}
 elif method=='model/list':
  if not m['params'].get('cursor'):
   result={'data':[{'model':'future-model','displayName':'Future','supportedReasoningEfforts':[{'reasoningEffort':'deep_next'}],'defaultReasoningEffort':'deep_next','isDefault':True},{'model':'hidden','hidden':True}], 'nextCursor':'next'}
  else:result={'data':[{'model':'second','supportedReasoningEfforts':[{'reasoningEffort':'low'}],'defaultReasoningEffort':'low'}],'nextCursor':None}
 else:raise SystemExit(3)
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
'''


def test_codex_paginates_and_never_starts_thread_or_turn(tmp_path):
    env=executable(tmp_path,'codex',RPC)
    result=d.discover('codex',env=env,timeout=3)
    assert [r['model'] for r in result['entries']]==['future-model','second']
    assert result['entries'][0]['supported_efforts']==['deep_next']
    assert result['account']['plan']=='plus' and 'email' not in result['account']
    calls=[json.loads(s) for s in (tmp_path/'trace.jsonl').read_text().splitlines()]
    assert [c['method'] for c in calls]==['initialize','initialized','account/read','model/list','model/list']
    assert calls[-1]['params']['cursor']=='next'


def test_gui_launch_without_home_uses_os_home_without_changing_parent(monkeypatch):
    import pwd
    monkeypatch.delenv('HOME',raising=False)
    from mynotes.providers import provider_environment
    child=provider_environment()
    assert child['HOME']==pwd.getpwuid(os.getuid()).pw_dir
    assert 'HOME' not in os.environ


def test_agy_includes_cross_family_models_and_reads_future_effort_from_stderr(tmp_path):
    env=executable(tmp_path,'agy','''import sys
if sys.argv[1:] == ['models']: print('future-gemini\\tNew Gemini\\nclaude-new\\tNew Claude\\ngpt-new\\tNew GPT')
elif sys.argv[1:] == ['--help']: print('  --effort Reasoning effort (low|deep_next)',file=sys.stderr)
elif sys.argv[1:] == ['--print','/usage']:print('Gemini Models\\tWeekly Limit Remaining\\t80%\\t2026-10-15T00:00:00Z')
else:raise SystemExit(4)
''')
    result=d.discover('agy',env=env,timeout=3)
    assert [r['model'] for r in result['entries']]==['future-gemini','claude-new','gpt-new']
    assert all(r['supported_efforts']==['low','deep_next'] and r['default_effort']=='' and r['effort_scope']=='agent' for r in result['entries'])
    assert result['account']['status']=='connected' and result['quota'][0]['remaining']=='80%'


def test_claude_initializes_without_user_frame_and_accepts_future_metadata(tmp_path):
    env=executable(tmp_path,'claude','''import sys,json,os
assert '--no-session-persistence' in sys.argv
assert json.loads(sys.argv[sys.argv.index('--settings')+1])['disableAllHooks']
m=json.loads(sys.stdin.readline())
assert m['type']=='control_request' and m['request']['subtype']=='initialize'
open(os.environ['TRACE'],'w').write(json.dumps(m))
print(json.dumps({'type':'control_response','response':{'subtype':'success','request_id':m['request_id'],'response':{'models':[{'value':'claude-future','displayName':'Future Claude','supportedEffortLevels':['future_level'],'defaultEffort':'future_level'}]}}}),flush=True)
sys.stdin.read()
''')
    result=d.discover('claude',env=env,timeout=3)
    assert result['entries'][0]['default_effort']=='future_level'
    assert json.loads((tmp_path/'trace.jsonl').read_text())['type']=='control_request'


def test_duplicate_cursor_is_rejected_and_owned_process_is_reaped(tmp_path):
    env=executable(tmp_path,'codex',RPC.replace("'nextCursor':None","'nextCursor':'next'"))
    with pytest.raises(d.DiscoveryError):d.discover('codex',env=env,timeout=3)


def test_missing_auth_does_not_request_model_catalog(tmp_path):
    env=executable(tmp_path,'codex',RPC.replace("{'account':{'type':'chatgpt','planType':'plus'}}","{'account':None,'requiresOpenaiAuth':True}"))
    with pytest.raises(d.DiscoveryError,match='Accedi'):d.discover('codex',env=env,timeout=3)
    assert 'model/list' not in (tmp_path/'trace.jsonl').read_text()


def test_timeout_reaps_process_without_sending_prompt(tmp_path, monkeypatch):
    original=d.subprocess.Popen; pids=[]
    def spawn(*args, **kwargs):
        process=original(*args, **kwargs);pids.append(process.pid);return process
    monkeypatch.setattr(d.subprocess,"Popen",spawn)
    env=executable(tmp_path,'codex',"import os,time\nopen(os.environ['TRACE'],'w').write(str(os.getpid()))\ntime.sleep(30)\n")
    start=time.monotonic()
    with pytest.raises(d.DiscoveryError,match='Tempo'):d.discover('codex',env=env,timeout=.2)
    assert time.monotonic()-start<2
    assert len(pids)==1
    with pytest.raises(ProcessLookupError):os.kill(pids[0],0)


def test_cancellation_of_catalog_parent_reaps_metadata_child(tmp_path):
    env=executable(tmp_path,'codex',"import os,time\nopen(os.environ['TRACE'],'w').write(str(os.getpid()))\ntime.sleep(30)\n")
    env['MYNOTES_HOME']=str(tmp_path/'runtime')
    parent=subprocess.Popen([sys.executable,'-c',"from mynotes.models import catalog; catalog('codex',refresh=True)"],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+3
        while not (tmp_path/'trace.jsonl').exists() and time.monotonic()<deadline:time.sleep(.03)
        assert (tmp_path/'trace.jsonl').exists()
        pid=int((tmp_path/'trace.jsonl').read_text());parent.terminate();parent.wait(timeout=2)
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
    finally:
        if parent.poll() is None:parent.kill();parent.wait()
