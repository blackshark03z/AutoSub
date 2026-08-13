#!/usr/bin/env python3
"""Focused v1.12 regression matrix for Goal judge, Scout handoff, budgets and verification dedup."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]

def run(root: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, *args], cwd=root, capture_output=True, text=True, timeout=30)
    if p.returncode != expect:
        raise AssertionError(f"command {args} rc={p.returncode} expected={expect}\nOUT={p.stdout}\nERR={p.stderr}")
    return p

def fresh(base: Path, name: str) -> Path:
    dst = base / name
    shutil.copytree(PACKAGE, dst, ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc'))
    subprocess.run(['git','init','-q'], cwd=dst, check=True, timeout=20)
    subprocess.run(['git','config','user.email','test@example.com'], cwd=dst, check=True, timeout=20)
    subprocess.run(['git','config','user.name','test'], cwd=dst, check=True, timeout=20)
    subprocess.run(['git','add','.'], cwd=dst, check=True, timeout=20)
    subprocess.run(['git','commit','-qm','base'], cwd=dst, check=True, timeout=20)
    run(dst,'scripts/ai_os.py','init','--project-id','P1','--owner','o','--problem','p','--target-user','u','--primary-action','a','--observable-result','r','--mvp-goal','g','--acceptance-threshold','pass','--demo-method','cli','--milestone-id','M-001')
    subprocess.run(['git','add','.'], cwd=dst, check=True, timeout=20); subprocess.run(['git','commit','-qm','init'], cwd=dst, check=True, timeout=20)
    return dst

def commit(root: Path, msg: str='fixture') -> None:
    subprocess.run(['git','add','.'], cwd=root, check=True, timeout=20); subprocess.run(['git','commit','-qm',msg], cwd=root, check=True, timeout=20)

def test_goal_judge(base: Path) -> None:
    r=fresh(base,'goal-judge'); (r/'src').mkdir(); (r/'tests').mkdir()
    (r/'src/app.py').write_text("print('WRONG_OUTPUT')\n")
    (r/'tests/goal_probe.py').write_text("import subprocess,sys\np=subprocess.run([sys.executable,'src/app.py'],capture_output=True,text=True)\nprint(p.stdout.strip())\nassert p.stdout.strip()=='HELLO_WORLD'\n")
    commit(r)
    run(r,'scripts/ai_os.py','goal','begin','--goal','CLI works','--accept','CLI prints HELLO_WORLD exactly','--risk-ceiling','R1','--max-auto-scouts','0')
    bad=run(r,'scripts/ai_os.py','goal','bind-acceptance','--criterion','1','--command','true',expect=1)
    assert 'vacuous' in bad.stdout+bad.stderr
    run(r,'scripts/ai_os.py','goal','bind-acceptance','--criterion','1','--command',f'{sys.executable} tests/goal_probe.py','--expected-output','HELLO_WORLD','--probe-file','tests/goal_probe.py')
    run(r,'scripts/ai_os.py','goal','add-task','--node','W1','--outcome','Implement CLI','--risk','R1','--delivery-delta','USER_VISIBLE_BEHAVIOR','--modify','src/app.py','--accept','implemented')
    run(r,'scripts/ai_os.py','goal','start','--node','W1')
    (r/'src/app.py').write_text("# touched\nprint('WRONG_OUTPUT')\n")
    run(r,'scripts/ai_os.py','done','--outcome','claimed','--focused-command','true','--output-inspected-by','agent:w','--first-pass-accepted','yes')
    assert run(r,'scripts/ai_os.py','goal','done','--acceptance-command','true','--output-inspected-by','agent:o',expect=2)
    fail=run(r,'scripts/ai_os.py','goal','done','--output-inspected-by','agent:o',expect=1)
    assert 'Goal acceptance attempt' in fail.stdout+fail.stderr
    (r/'tests/goal_probe.py').write_text("print('HELLO_WORLD')\n")
    tamper=run(r,'scripts/ai_os.py','goal','done','--output-inspected-by','agent:o',expect=1)
    assert 'probe changed after freeze' in tamper.stdout+tamper.stderr

def test_scout_handoff_and_budget(base: Path) -> None:
    r=fresh(base,'scout'); (r/'src').mkdir(); (r/'tests').mkdir()
    (r/'src/session.py').write_text('x=1\n'); (r/'src/other.py').write_text('y=1\n'); (r/'tests/g.py').write_text("print('OK')\n"); commit(r)
    run(r,'scripts/ai_os.py','goal','begin','--goal','Fix unknown session regression','--accept','Session behavior is correct','--risk-ceiling','R1','--scout-input-token-budget','6000','--scope-growth-limit','30')
    run(r,'scripts/ai_os.py','goal','bind-acceptance','--criterion','1','--command',f'{sys.executable} tests/g.py','--expected-output','OK','--probe-file','tests/g.py')
    run(r,'scripts/ai_os.py','goal','add-task','--node','W1','--outcome','Diagnose unknown intermittent session bug','--risk','R1','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/**','--accept','fixed')
    over=run(r,'scripts/ai_os.py','goal','scout-done','--node','W1__SCOUT','--summary','root','--input-tokens','7000',expect=1)
    assert 'input usage exceeds' in over.stdout+over.stderr
    run(r,'scripts/ai_os.py','goal','scout-done','--node','W1__SCOUT','--summary','ROOT_CAUSE_SENTINEL stale branch','--affected-file','src/session.py','--invariant','preserve rotation','--entry-point','src/session.py:1','--confidence','HIGH','--recommended-scope','src/session.py','--input-tokens','5000','--output-tokens','100','--wall-minutes','1')
    run(r,'scripts/ai_os.py','goal','start','--node','W1')
    cap=(r/'.ai/CONTEXT_CAPSULE.md').read_text(); task=(r/'.ai/ACTIVE_TASK.md').read_text()
    assert 'ROOT_CAUSE_SENTINEL' in cap and 'preserve rotation' in cap and 'Modify: src/session.py' in task
    growth=run(r,'scripts/ai_os.py','amend','--add-modify','src/other.py','--reason','expand',expect=1)
    assert 'SPLIT_OR_REPLAN' in growth.stdout+growth.stderr

def test_dedup_and_parallel(base: Path) -> None:
    r=fresh(base,'dedup'); (r/'src/api').mkdir(parents=True); (r/'tests').mkdir()
    (r/'src/api/app.py').write_text("print('BAD')\n")
    (r/'tests/check.py').write_text("import subprocess,sys\np=subprocess.run([sys.executable,'src/api/app.py'],capture_output=True,text=True)\nprint(p.stdout.strip())\nassert p.stdout.strip()=='OK'\n")
    commit(r)
    run(r,'scripts/ai_os.py','goal','begin','--goal','API output OK','--accept','API CLI returns OK','--risk-ceiling','R2','--max-auto-scouts','0')
    run(r,'scripts/ai_os.py','goal','bind-acceptance','--criterion','1','--command',f'{sys.executable} tests/check.py','--expected-output','OK','--probe-file','tests/check.py')
    run(r,'scripts/ai_os.py','goal','add-task','--node','W1','--outcome','Fix API output','--risk','R2','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/api/app.py','--accept','returns OK','--acceptance-command',f'{sys.executable} tests/check.py','--expected-output','OK','--probe-file','tests/check.py','--negative-required')
    run(r,'scripts/ai_os.py','goal','start','--node','W1'); (r/'src/api/app.py').write_text("print('OK')\n")
    run(r,'scripts/ai_os.py','done','--outcome','fixed','--focused-command',f'{sys.executable} tests/check.py','--negative-command',f'{sys.executable} tests/check.py','--integration-command',f'{sys.executable} tests/check.py','--output-inspected-by','agent:w','--first-pass-accepted','yes')
    m=json.loads((r/'.ai/evidence/G-001-W1/r001/manifest.json').read_text())
    assert len(m['checks'])==1 and set(m['checks'][0]['satisfies'])=={'focused','negative','integration','acceptance_contract'}
    sys.path.insert(0,str(PACKAGE/'scripts'))
    from delegation_support import scopes_overlap, select_parallel_writers
    assert scopes_overlap('NONE','NONE') is True
    a={'node_id':'A','modify':'NONE','create':'NONE','outcome':'x'}; b={'node_id':'B','modify':'NONE','create':'NONE','outcome':'y'}
    assert len(select_parallel_writers([a,b],2)[0])==1


def test_revision_budget_and_r3_attestation(base: Path) -> None:
    # Goal revision budget is an execution guard, not display-only metadata.
    r=fresh(base,'revision-budget'); (r/'src').mkdir(); (r/'tests').mkdir()
    (r/'src/app.py').write_text('x=1\n'); (r/'tests/g.py').write_text("print('OK')\n"); commit(r)
    run(r,'scripts/ai_os.py','goal','begin','--goal','Bounded repair','--accept','Repair is valid','--risk-ceiling','R1','--max-revisions','1','--max-auto-scouts','0')
    run(r,'scripts/ai_os.py','goal','bind-acceptance','--criterion','1','--command',f'{sys.executable} tests/g.py','--expected-output','OK','--probe-file','tests/g.py')
    run(r,'scripts/ai_os.py','goal','add-task','--node','W1','--outcome','Repair app','--risk','R1','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/app.py','--accept','valid')
    # Simulate one previously consumed revision for this stable task id.
    ev=r/'.ai/evidence/G-001-W1/r001'; ev.mkdir(parents=True); (ev/'manifest.json').write_text(json.dumps({'first_pass_accepted':'no'}))
    blocked=run(r,'scripts/ai_os.py','goal','start','--node','W1',expect=1)
    assert 'revision budget' in (blocked.stdout+blocked.stderr).lower() or 'maximum revisions' in (blocked.stdout+blocked.stderr).lower()

    # R3 cannot be closed by repo-controlled review Markdown alone.
    q=fresh(base,'r3-attestation')
    run(q,'scripts/ai_os.py','begin','--task-id','TASK-R3','--outcome','Secure result','--risk','R3','--success-criterion','SC-001','--delivery-delta','RISK_RETIREMENT','--modify','src/security.py','--create','src/security.py','--owner-authorization','APPROVED','--authorization-reference','TICKET-2')
    (q/'src').mkdir(); (q/'src/security.py').write_text("def safe(v): return v != 'bad'\n")
    (q/'.ai/reviews').mkdir(); status=json.loads(run(q,'scripts/ai_os.py','status','--json').stdout)
    review=q/'.ai/reviews/valid.md'; review.write_text('# Independent Review Report\n\n- Task ID: TASK-R3\n- Task revision: 1\n'+f"- Reviewed snapshot SHA256: {status['snapshot_sha256']}\n"+'- Reviewer identity: HUMAN-SECURITY\n- Reviewer role: SECURITY_REVIEWER\n- Independent from writer: yes\n- Writer identity: AI-WORKER\n- Verdict: PASS\n- Reviewed at: 2026-08-08T01:30:00+00:00\n')
    blocked=run(q,'scripts/ai_os.py','done','--outcome','Secure result','--focused-command','true','--negative-command','true','--integration-command','true','--rollback-command','true','--full-suite-command','true','--review-report','.ai/reviews/valid.md','--output-inspected-by','human:reviewer',expect=1)
    assert 'attestation' in (blocked.stdout+blocked.stderr).lower()
    priv=q.parent/'guardian-private.pem'; pub=q.parent/'guardian-public.pem'; att=q.parent/'r3-review-attestation.json'
    run(q,'scripts/guardian.py','keygen','--private',str(priv),'--public',str(pub))
    os.environ['AI_BUILD_OS_GUARDIAN_PUBLIC_KEY']=str(pub)
    run(q,'scripts/guardian.py','sign-review','--private',str(priv),'--public',str(pub),'--out',str(att),'--task-id','TASK-R3','--task-revision','1','--snapshot-sha256',status['snapshot_sha256'],'--writer-session-id','AI-WORKER','--reviewer-session-id','HUMAN-SECURITY-SESSION','--review-report',str(review),'--issuer','focused-test-guardian')
    run(q,'scripts/ai_os.py','done','--outcome','Secure result','--focused-command','true','--negative-command','true','--integration-command','true','--rollback-command','true','--full-suite-command','true','--review-report','.ai/reviews/valid.md','--review-attestation',str(att),'--output-inspected-by','human:reviewer')
    m=json.loads((q/'.ai/evidence/TASK-R3/r001/manifest.json').read_text()); assert m['review']['trust']=='SIGNED_GUARDIAN'

def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ai-os-v112-') as td:
        base=Path(td); test_goal_judge(base); test_scout_handoff_and_budget(base); test_dedup_and_parallel(base)
    print('SELF_TEST_V112: PASS')

if __name__=='__main__': main()
