#!/usr/bin/env python3
"""v1.14 regressions: architecture decision, capability contract, structured dependencies, anti-monster ratchet."""
from __future__ import annotations
import json, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
PACKAGE=Path(__file__).resolve().parents[1]

def run(root:Path,*args:str,expect:int=0,timeout:int=25):
    p=subprocess.run([sys.executable,*args],cwd=root,text=True,capture_output=True,timeout=timeout)
    if p.returncode!=expect: raise AssertionError(f'{args} rc={p.returncode} expected={expect}\nOUT={p.stdout}\nERR={p.stderr}')
    return p

def cmd(root:Path,*args:str): subprocess.run(args,cwd=root,check=True,capture_output=True,timeout=20)

def fresh(base:Path,name:str,with_product:bool=True)->Path:
    r=base/name; shutil.copytree(PACKAGE,r,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc'))
    if with_product:
        (r/'package.json').write_text(json.dumps({'name':'demo','version':'1.0.0','scripts':{'test':'node -e "console.log(\\"PASS\\")"'},'dependencies':{}},indent=2))
    cmd(r,'git','init','-q'); cmd(r,'git','config','user.email','t@example.com'); cmd(r,'git','config','user.name','t'); cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','base')
    run(r,'scripts/ai_os.py','init','--project-id','P','--owner','o','--problem','p','--target-user','u','--primary-action','a','--observable-result','r','--mvp-goal','g')
    cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','init'); return r

def test_architecture_decision(base:Path):
    r=fresh(base,'arch')
    bad=run(r,'scripts/ai_os.py','health','check','--ci',expect=1); assert 'architecture decision missing' in bad.stdout+bad.stderr
    run(r,'scripts/ai_os.py','health','architecture-decision','--no-boundaries-reason','Single-module prototype has no stable internal dependency boundary yet')
    run(r,'scripts/ai_os.py','health','check','--ci')

def test_quality_capabilities(base:Path):
    r=fresh(base,'quality')
    p=r/'.ai/PROJECT.md'; body=p.read_text(); body=body.replace('- Test command: npm run test','- Test command: PROJECT_SPECIFIC').replace('- CI quality command: npm run test','- CI quality command: PROJECT_SPECIFIC').replace('- CI quality capabilities: test','- CI quality capabilities: UNSET'); p.write_text(body)
    # Autodetection still sees package.json test and satisfies required test capability.
    run(r,'scripts/project_ci.py','--ci')
    data=json.loads((r/'package.json').read_text()); data['scripts'].pop('test'); (r/'package.json').write_text(json.dumps(data))
    bad=run(r,'scripts/project_ci.py','--ci',expect=2); assert 'missing required quality capability: test' in bad.stdout+bad.stderr
    pol=json.loads((r/'config/quality_policy.json').read_text()); pol['capability_waivers']['test']='Prototype currently has no stable behavior; test requirement expires before first release'; (r/'config/quality_policy.json').write_text(json.dumps(pol,indent=2))
    # A waiver satisfies the capability policy, but no executable quality command still fails closed.
    bad=run(r,'scripts/project_ci.py','--ci',expect=2); assert 'no canonical or standard product quality checks' in bad.stdout+bad.stderr

def test_structured_dependency(base:Path):
    r=fresh(base,'dep')
    run(r,'scripts/ai_os.py','begin','--task-id','D1','--outcome','Add dependency','--risk','R1','--success-criterion','SC-001','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','package.json')
    data=json.loads((r/'package.json').read_text()); data['dependencies']['left-pad']='1.3.0'; (r/'package.json').write_text(json.dumps(data))
    bad=run(r,'scripts/ai_os.py','done','--outcome','x','--focused-command','node -e "console.log(1)"','--output-inspected-by','agent:w','--dependency-justification','because needed',expect=1)
    assert 'structured dependency decision incomplete' in bad.stdout+bad.stderr
    run(r,'scripts/ai_os.py','done','--outcome','x','--focused-command','node -e "console.log(1)"','--output-inspected-by','agent:w','--dependency-capability','required compatibility behavior','--dependency-alternatives-considered','existing standard library cannot provide required behavior','--dependency-removal-cost','isolated import can be removed with one adapter change')

def test_monster_file_gate(base:Path):
    r=fresh(base,'monster'); (r/'src').mkdir(exist_ok=True)
    run(r,'scripts/ai_os.py','begin','--task-id','M1','--outcome','Add source','--risk','R1','--success-criterion','SC-001','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/**','--create','src/**')
    (r/'src/monster.js').write_text('\n'.join(f'const x{i}={i};' for i in range(1100))+'\n')
    bad=run(r,'scripts/ai_os.py','done','--outcome','x','--focused-command','node -e "console.log(1)"','--output-inspected-by','agent:w',expect=1)
    assert 'new source file too large' in bad.stdout+bad.stderr

def main():
    with tempfile.TemporaryDirectory(prefix='ai-os-v114-') as td:
        b=Path(td); tests=[test_architecture_decision,test_quality_capabilities,test_structured_dependency,test_monster_file_gate]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures=[ex.submit(t,b) for t in tests]
            for f in futures:f.result()
    print('SELF_TEST_V114: PASS')
if __name__=='__main__':main()
