#!/usr/bin/env python3
"""v1.13 regression matrix for fail-closed CI, lane routing, SSOT policy, telemetry and codebase-health ratchets."""
from __future__ import annotations
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
PACKAGE=Path(__file__).resolve().parents[1]

def run(root:Path,*args:str,expect:int=0):
    p=subprocess.run([sys.executable,*args],cwd=root,text=True,capture_output=True,timeout=25)
    if p.returncode!=expect: raise AssertionError(f'{args} rc={p.returncode} expected={expect}\nOUT={p.stdout}\nERR={p.stderr}')
    return p

def cmd(root:Path,*args:str): subprocess.run(args,cwd=root,check=True,capture_output=True,timeout=20)

def fresh(base:Path,name:str,package_json:bool=False)->Path:
    r=base/name; shutil.copytree(PACKAGE,r,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc'))
    if package_json:(r/'package.json').write_text(json.dumps({'name':'demo','version':'1.0.0','scripts':{'test':'node -e "console.log(\\"PASS\\")"'},'dependencies':{}},indent=2))
    cmd(r,'git','init','-q'); cmd(r,'git','config','user.email','t@example.com'); cmd(r,'git','config','user.name','t'); cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','base')
    run(r,'scripts/ai_os.py','init','--project-id','P','--owner','o','--problem','p','--target-user','u','--primary-action','a','--observable-result','r','--mvp-goal','g')
    cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','init'); return r

def test_dependency_and_health(base:Path):
    r=fresh(base,'deps',True)
    run(r,'scripts/ai_os.py','begin','--task-id','T1','--outcome','Add capability','--risk','R1','--success-criterion','SC-001','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','package.json')
    data=json.loads((r/'package.json').read_text()); data['dependencies']['left-pad']='1.3.0'; (r/'package.json').write_text(json.dumps(data,indent=2))
    blocked=run(r,'scripts/ai_os.py','done','--outcome','capability','--focused-command','node -e "console.log(1)"','--output-inspected-by','agent:w',expect=1)
    assert 'structured dependency decision incomplete' in blocked.stdout+blocked.stderr
    run(r,'scripts/ai_os.py','done','--outcome','capability','--focused-command','node -e "console.log(1)"','--output-inspected-by','agent:w','--dependency-capability','runtime compatibility required by supported platform','--dependency-alternatives-considered','standard library and existing packages do not provide compatibility','--dependency-removal-cost','localized adapter allows package removal in one module')

def test_health_hard_gate(base:Path):
    r=fresh(base,'bloat'); (r/'src').mkdir(exist_ok=True); cmd(r,'git','add','.');
    # Create forbidden log inside authorized task delta.
    run(r,'scripts/ai_os.py','begin','--task-id','T2','--outcome','Write source','--risk','R1','--success-criterion','SC-001','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/**','--create','src/**')
    (r/'src/debug.log').write_text('huge accidental log\n')
    bad=run(r,'scripts/ai_os.py','done','--outcome','x','--focused-command','true','--output-inspected-by','agent:w',expect=1)
    assert 'forbidden repository artifact' in bad.stdout+bad.stderr

def test_ci_and_policy(base:Path):
    r=fresh(base,'ci')
    # Explicit broken canonical quality command must fail closed rather than fall back/pass.
    p=r/'.ai/PROJECT.md'; s=p.read_text(); s=s.replace('- CI quality command: PROJECT_SPECIFIC','- CI quality command: definitely-missing-quality-tool --check'); p.write_text(s)
    ci=run(r,'scripts/project_ci.py','--ci',expect=127); assert 'PROJECT_CI: FAIL' in ci.stdout
    # Machine policy is the source of truth; drifted generated docs must fail validator.
    q=r/'.ai/QUALITY_GATES.md'; q.write_text(q.read_text()+'\nDRIFT\n')
    v=run(r,'scripts/validate_ai_os.py','--template',expect=1); assert 'QUALITY_GATES.md drift' in v.stdout+v.stderr

def test_routing_and_telemetry(base:Path):
    r=fresh(base,'telemetry')
    standard=json.loads(run(r,'scripts/ai_os.py','route','--outcome','Update bounded API formatter','--accept','returns normalized response','--modify','src/a.py,src/b.py,tests/test_a.py','--risk','R2','--json').stdout); assert standard['lane']=='STANDARD'
    goal=json.loads(run(r,'scripts/ai_os.py','route','--outcome','Diagnose unknown flaky issue','--accept','root cause removed','--modify','src/**','--risk','R1','--json').stdout); assert goal['lane']=='GOAL'
    records=[]
    for i in range(3):
        records += [
          {'goal_id':f'GS{i}','node_id':'W__SCOUT','role':'SCOUT','work_class':'BUG_UNKNOWN:BROAD','input_tokens':3000,'output_tokens':250,'provider_cost':0.002},
          {'goal_id':f'GS{i}','node_id':'W','role':'WORKER','work_class':'BUG_UNKNOWN:BROAD','input_tokens':6000,'output_tokens':500,'provider_cost':0.02},
          {'goal_id':f'GN{i}','node_id':'W','role':'WORKER','work_class':'BUG_UNKNOWN:BROAD','input_tokens':12000,'output_tokens':500,'provider_cost':0.04},
        ]
    f=r/'usage.json'; f.write_text(json.dumps(records)); run(r,'scripts/ai_os.py','telemetry','ingest','--file',str(f))
    sys.path.insert(0,str(PACKAGE/'scripts')); from telemetry_support import delegation_feedback
    fb=delegation_feedback(r,'BUG_UNKNOWN:BROAD'); assert fb['status']=='OBSERVED' and fb['verdict']=='POSITIVE'

def test_replacement_contract(base:Path):
    r=fresh(base,'replace'); (r/'src').mkdir(exist_ok=True); (r/'src/old.py').write_text('value=1\n'); cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','old')
    run(r,'scripts/ai_os.py','begin','--task-id','TREP','--outcome','Replace old implementation','--risk','R1','--success-criterion','SC-001','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/**','--create','src/new.py','--replaces','src/old.py')
    (r/'src/new.py').write_text('value=2\n')
    bad=run(r,'scripts/ai_os.py','done','--outcome','replaced','--focused-command','true','--output-inspected-by','agent:w',expect=1)
    assert 'Replacement contract not satisfied' in bad.stdout+bad.stderr
    (r/'src/old.py').unlink()
    run(r,'scripts/ai_os.py','done','--outcome','replaced','--focused-command','true','--output-inspected-by','agent:w')


def test_goal_dependency_justification_is_task_local(base:Path):
    r=fresh(base,'goal-dep-local',True)
    run(r,'scripts/ai_os.py','goal','begin','--goal-id','G-001','--goal','Ship bounded capability','--accept','program remains runnable','--risk-ceiling','R1')
    run(r,'scripts/ai_os.py','goal','bind-acceptance','--criterion','1','--command','node -e "console.log(\"PASS\")"','--expected-output','PASS')
    run(r,'scripts/ai_os.py','goal','add-task','--node','N1','--outcome','Add required runtime dependency','--risk','R1','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','package.json','--accept','dependency declared')
    run(r,'scripts/ai_os.py','goal','add-task','--node','N2','--outcome','Add source file','--depends-on','N1','--risk','R1','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/x.js','--create','src/x.js','--accept','source exists')
    run(r,'scripts/ai_os.py','goal','start','--node','N1')
    data=json.loads((r/'package.json').read_text()); data['dependencies']['left-pad']='1.3.0'; (r/'package.json').write_text(json.dumps(data,indent=2))
    run(r,'scripts/ai_os.py','done','--outcome','dependency added','--focused-command','node -e "console.log(1)"','--output-inspected-by','agent:w','--dependency-capability','runtime compatibility required by supported platform','--dependency-alternatives-considered','standard library and existing packages do not provide compatibility','--dependency-removal-cost','localized adapter allows package removal in one module')
    run(r,'scripts/ai_os.py','goal','start','--node','N2')
    (r/'src').mkdir(exist_ok=True); (r/'src/x.js').write_text('module.exports = 1;\n')
    # Must not require the previous task's dependency justification again.
    run(r,'scripts/ai_os.py','done','--outcome','source added','--focused-command','node -e "console.log(1)"','--output-inspected-by','agent:w')

def main():
    with tempfile.TemporaryDirectory(prefix='ai-os-v113-') as td:
        b=Path(td); test_dependency_and_health(b); test_health_hard_gate(b); test_ci_and_policy(b); test_routing_and_telemetry(b); test_goal_dependency_justification_is_task_local(b); test_replacement_contract(b)
    print('SELF_TEST_V113: PASS')
if __name__=='__main__':main()
