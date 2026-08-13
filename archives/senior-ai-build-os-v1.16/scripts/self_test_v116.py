#!/usr/bin/env python3
"""v1.16 regressions: namespace hardening, stale-lock recovery, secret redaction and lightweight state-hazard proofs."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
PACKAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PACKAGE/'scripts'))
from evidence_support import sanitize_output


def run(root:Path,*args:str,expect:int=0,timeout:int=30):
    p=subprocess.run([sys.executable,*args],cwd=root,text=True,capture_output=True,timeout=timeout)
    if p.returncode!=expect:
        raise AssertionError(f'{args} rc={p.returncode} expected={expect}\nOUT={p.stdout}\nERR={p.stderr}')
    return p

def cmd(root:Path,*args:str):subprocess.run(args,cwd=root,check=True,capture_output=True,text=True,timeout=20)

def fresh(base:Path,name:str)->Path:
    r=base/name; shutil.copytree(PACKAGE,r,ignore=shutil.ignore_patterns(
        '.git', '.ai', '__pycache__', '*.pyc', 'addons', 'app', 'build', 'build_temp',
        'data', 'docs', 'evidence', 'input', 'logs', 'models', 'release', 'temp', 'tests',
        'tools', 'wheelhouse', 'pip-cache', '.pip-cache', '.cache', '.venv', 'venv',
    ))
    ai = r / '.ai'; ai.mkdir()
    for source, destination in {
        PACKAGE / 'templates' / 'PROJECT_TEMPLATE.md': ai / 'PROJECT.md',
        PACKAGE / 'templates' / 'STATE_TEMPLATE.md': ai / 'STATE.md',
        PACKAGE / 'templates' / 'CONTEXT_CAPSULE_TEMPLATE.md': ai / 'CONTEXT_CAPSULE.md',
        PACKAGE / '.ai' / 'COST_LEDGER.csv': ai / 'COST_LEDGER.csv',
        PACKAGE / '.ai' / 'DECISIONS.md': ai / 'DECISIONS.md',
        PACKAGE / '.ai' / '.gitignore': ai / '.gitignore',
    }.items():
        shutil.copy2(source, destination)
    task_template = (PACKAGE / 'templates' / 'TASK_LEAN.md').read_text(encoding='utf-8')
    task_template = task_template.replace('Task Status: READY', 'Task Status: NOT_CREATED').replace('Task ID: TASK-XXX', 'Task ID: NONE')
    (ai / 'ACTIVE_TASK.md').write_text(task_template, encoding='utf-8')
    cmd(r,'git','init','-q'); cmd(r,'git','config','user.email','t@example.com'); cmd(r,'git','config','user.name','t'); cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','base')
    run(r,'scripts/ai_os.py','init','--project-id','P','--owner','o','--problem','p','--target-user','u','--primary-action','a','--observable-result','r','--mvp-goal','g')
    cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','init')
    return r

def test_namespace_and_lock(base:Path):
    r=fresh(base,'hardening')
    bad=run(r,'scripts/ai_os.py','begin','--task-id','../../ESCAPE','--outcome','x','--risk','R0','--success-criterion','SC-001','--delivery-delta','NO_DELTA','--modify','src/x.py',expect=1)
    assert 'Invalid task ID' in (bad.stdout+bad.stderr) and not (r.parent/'ESCAPE').exists()
    bad=run(r,'scripts/ai_os.py','goal','begin','--goal-id','../../GOAL_ESCAPE','--goal','x','--accept','observable output',expect=1)
    assert 'Invalid goal ID' in (bad.stdout+bad.stderr) and not (r.parent/'GOAL_ESCAPE').exists()
    (r/'.ai/.lifecycle.lock').write_text('pid=99999999 started=1\n')
    run(r,'scripts/ai_os.py','begin','--task-id','LOCK1','--outcome','lock recovery','--risk','R0','--success-criterion','SC-001','--delivery-delta','NO_DELTA','--modify','docs/x.md')
    assert not (r/'.ai/.lifecycle.lock').exists()
    run(r,'scripts/ai_os.py','abort')

def test_redaction():
    sample='''Authorization: Bearer abc.def.ghi\napi_key=supersecret\neyJabcdefghij.abcdefghij.abcdefghij\nghp_abcdefghijklmnopqrstuvwxyz123456\n-----BEGIN PRIVATE KEY-----\nsecret-body\n-----END PRIVATE KEY-----\npostgres://user:pass@example.com/db\n'''
    redacted,_,_=sanitize_output(sample)
    for secret in ['abc.def.ghi','supersecret','eyJabcdefghij','ghp_abcdefghijklmnopqrstuvwxyz','secret-body','user:pass@']:
        assert secret not in redacted

def test_state_hazard_reuse_and_debug(base:Path):
    r=fresh(base,'state')
    # Stateless work remains zero-ceremony.
    run(r,'scripts/ai_os.py','begin','--task-id','CSS1','--outcome','adjust button spacing','--risk','R0','--success-criterion','SC-001','--delivery-delta','NO_DELTA','--modify','styles/button.css')
    st=json.loads(run(r,'scripts/ai_os.py','status','--json').stdout); assert st['state_hazard']=='S0'
    run(r,'scripts/ai_os.py','abort')
    # Strong draft + polling signal must declare only the tiny pre-code contract.
    bad=run(r,'scripts/ai_os.py','begin','--task-id','STATE1','--outcome','preserve dirty draft during polling refresh','--risk','R1','--success-criterion','SC-001','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/state.py',expect=1)
    assert 'minimal pre-code state contract' in (bad.stdout+bad.stderr)
    state_args=['--state-authority','persisted project.effects','--state-transition','SAVED -> EDIT -> DIRTY -> SAVE -> SAVED','--state-invariant','background refresh must not overwrite DIRTY','--state-dependency','src/state.py']
    run(r,'scripts/ai_os.py','begin','--task-id','STATE1','--outcome','preserve dirty draft during polling refresh','--risk','R1','--success-criterion','SC-001','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/state.py','--create','src/state.py',*state_args)
    (r/'src').mkdir(exist_ok=True); (r/'src/state.py').write_text('def reconcile(dirty, local, remote):\n    return local if dirty else remote\n')
    if os.name == 'nt':
        success = 'cmd /c exit 0'
        transition = 'cmd /c echo transition-pass'
        temporal = 'cmd /c echo temporal-pass'
    else:
        success = 'true'
        transition = 'printf transition-pass'
        temporal = 'printf temporal-pass'
    run(r,'scripts/ai_os.py','done','--outcome','preserved dirty draft','--focused-command',success,'--state-transition-command',transition,'--state-temporal-command',temporal,'--output-inspected-by','agent:test')
    m1=json.loads((r/'.ai/evidence/STATE1/r001/manifest.json').read_text())
    assert m1['state_hazard_level']=='S3' and any(c.get('kind')=='state_transition' and not c.get('reused') for c in m1['checks'])
    # Same contract + dependency content => state proofs are reused across tasks.
    run(r,'scripts/ai_os.py','begin','--task-id','STATE2','--outcome','verify dirty draft during polling refresh','--risk','R1','--success-criterion','SC-001','--delivery-delta','NO_DELTA','--modify','src/state.py',*state_args)
    run(r,'scripts/ai_os.py','done','--outcome','state contract still holds','--focused-command',success,'--state-transition-command',transition,'--state-temporal-command',temporal,'--output-inspected-by','agent:test')
    m2=json.loads((r/'.ai/evidence/STATE2/r001/manifest.json').read_text())
    reused=[c for c in m2['checks'] if c.get('kind') in {'state_transition','state_temporal'}]
    assert len(reused)==2 and all(c.get('reused') is True for c in reused)
    # Bounded debug memory + two-strike evidence-infrastructure stop-loss.
    run(r,'scripts/ai_os.py','begin','--task-id','BUG1','--outcome','diagnose flaky UI verifier','--risk','R1','--success-criterion','SC-001','--delivery-delta','NO_DELTA','--modify','tests/ui.py')
    run(r,'scripts/ai_os.py','debug','state-failure','--state','DIRTY','--event','POLL','--expected','preserve draft','--observed','draft replaced','--hazard-class','competing_writer','--suspect','hydrateProject')
    sig=r/'.ai/runtime/state_failures/BUG1-r001.json'; assert sig.is_file() and json.loads(sig.read_text())['event']=='POLL'
    run(r,'scripts/ai_os.py','debug','evidence-infra-failure','--method','playwright','--note','browser boot failed')
    second=run(r,'scripts/ai_os.py','debug','evidence-infra-failure','--method','playwright','--note','session failed')
    assert 'STOP_LOSS' in second.stdout
    nxt=run(r,'scripts/ai_os.py','next'); assert 'change acceptance method' in nxt.stdout.lower()

def main():
    test_redaction()
    with tempfile.TemporaryDirectory(prefix='ai-os-v116-') as td:
        base=Path(td); test_namespace_and_lock(base); test_state_hazard_reuse_and_debug(base)
    print('SELF_TEST_V116: PASS')

if __name__=='__main__': main()
