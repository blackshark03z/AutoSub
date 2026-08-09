#!/usr/bin/env python3
"""Fast (<15s target) policy/CI/routing/health regression layer."""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(cwd:Path,*argv:str,expect:int=0):
    p=subprocess.run([sys.executable,*argv],cwd=cwd,text=True,capture_output=True,timeout=15)
    if p.returncode!=expect: raise AssertionError(f'{argv} rc={p.returncode} expected={expect}\n{p.stdout}\n{p.stderr}')
    return p

def main():
    run(ROOT,'scripts/validate_ai_os.py','--template')
    r=run(ROOT,'scripts/ai_os.py','route','--outcome','Fix clear local formatter','--accept','returns normalized value','--modify','src/foo.py','--risk','R1','--json')
    assert json.loads(r.stdout)['lane']=='FAST'
    r=run(ROOT,'scripts/ai_os.py','route','--outcome','Diagnose unknown flaky session failure','--accept','session recovers','--modify','src/**','--risk','R1','--json')
    assert json.loads(r.stdout)['lane']=='GOAL'
    with tempfile.TemporaryDirectory(prefix='ai-os-fast-') as td:
        root=Path(td); (root/'.ai').mkdir(); (root/'tests').mkdir(); (root/'pyproject.toml').write_text('[project]\nname="x"\nversion="0"\n')
        (root/'tests/test_x.py').write_text('def test_x(): assert True\n')
        (root/'.ai/PROJECT.md').write_text('## Technical Baseline\n- Install command: PROJECT_SPECIFIC\n- Test command: missing-test-runner --check\n- Lint command: PROJECT_SPECIFIC\n- Typecheck command: PROJECT_SPECIFIC\n- CI quality capabilities: test\n- Build command: NONE_REQUIRED_OR_PROJECT_SPECIFIC\n- CI quality command: PROJECT_SPECIFIC\n')
        p=run(root,str(ROOT/'scripts/project_ci.py'),'--root',str(root),'--ci',expect=127)
        assert 'FAIL' in p.stdout
    health=run(ROOT,'scripts/ai_os.py','health','check')
    assert 'CODEBASE_HEALTH: PASS' in health.stdout
    print('SELF_TEST_FAST: PASS')
if __name__=='__main__': main()
