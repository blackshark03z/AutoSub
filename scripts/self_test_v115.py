#!/usr/bin/env python3
"""v1.15 regressions: assurance/Guardian, field learning, decision digest, compact CLI and risk uncertainty."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
PACKAGE=Path(__file__).resolve().parents[1]

def run(root:Path,*args:str,expect:int=0,env:dict|None=None,timeout:int=30):
    merged=os.environ.copy(); merged.update(env or {})
    p=subprocess.run([sys.executable,*args],cwd=root,text=True,capture_output=True,timeout=timeout,env=merged)
    if p.returncode!=expect:raise AssertionError(f'{args} rc={p.returncode} expected={expect}\nOUT={p.stdout}\nERR={p.stderr}')
    return p

def cmd(root:Path,*args:str):subprocess.run(args,cwd=root,check=True,capture_output=True,timeout=20)

def fresh(base:Path,name:str)->Path:
    r=base/name; shutil.copytree(PACKAGE,r,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc'))
    cmd(r,'git','init','-q'); cmd(r,'git','config','user.email','t@example.com'); cmd(r,'git','config','user.name','t'); cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','base')
    run(r,'scripts/ai_os.py','init','--project-id','P','--owner','o','--problem','p','--target-user','u','--primary-action','a','--observable-result','r','--mvp-goal','g')
    cmd(r,'git','add','.'); cmd(r,'git','commit','-qm','init'); return r

def test_assurance_and_guardian(base:Path):
    r=fresh(base,'guardian'); a=json.loads(run(r,'scripts/ai_os.py','assurance').stdout); assert a['level']=='A1'
    keys=base/'keys'; keys.mkdir(); priv=keys/'priv.pem'; pub=keys/'pub.pem'
    run(r,'scripts/guardian.py','keygen','--private',str(priv),'--public',str(pub))
    verify_env={'AI_BUILD_OS_GUARDIAN_PUBLIC_KEY':str(pub)}
    a=json.loads(run(r,'scripts/ai_os.py','assurance',env=verify_env).stdout); assert a['level']=='A1'
    env={'AI_BUILD_OS_GUARDIAN_PUBLIC_KEY':str(pub),'AI_BUILD_OS_GUARDIAN_EXTERNAL_ATTESTED':'true'}
    a=json.loads(run(r,'scripts/ai_os.py','assurance',env=env).stdout); assert a['level']=='A2'
    run(r,'scripts/ai_os.py','begin','--task-id','R3','--outcome','secure','--risk','R3','--success-criterion','SC-001','--delivery-delta','RISK_RETIREMENT','--modify','src/security.py','--create','src/security.py','--owner-authorization','APPROVED','--authorization-reference','T')
    (r/'src').mkdir(exist_ok=True); (r/'src/security.py').write_text('def safe(x): return x != "bad"\n')
    status=json.loads(run(r,'scripts/ai_os.py','status','--json').stdout)
    (r/'.ai/reviews').mkdir(exist_ok=True); review=r/'.ai/reviews/review.md'
    review.write_text('# Independent Review Report\n\n- Task ID: R3\n- Task revision: 1\n- Reviewed snapshot SHA256: '+status['snapshot_sha256']+'\n- Reviewer identity: REVIEWER-1\n- Reviewer role: SECURITY_REVIEWER\n- Independent from writer: yes\n- Writer identity: AI-WORKER\n- Verdict: PASS\n- Reviewed at: 2026-08-09T00:00:00+00:00\n')
    unsigned=base/'unsigned.json'; unsigned.write_text(json.dumps({'task_id':'R3','task_revision':1,'reviewed_snapshot_sha256':status['snapshot_sha256'],'reviewer_session_id':'REVIEWER-SESSION','writer_session_id':'AI-WORKER','verdict':'PASS','reviewed_at':'2026-08-09T00:00:00+00:00','issuer':'fake'}))
    common=['scripts/ai_os.py','done','--outcome','secure','--focused-command','true','--negative-command','true','--integration-command','true','--rollback-command','true','--full-suite-command','true','--review-report','.ai/reviews/review.md','--output-inspected-by','human:r']
    bad=run(r,*common,'--review-attestation',str(unsigned),expect=1,env=env); assert 'signature' in (bad.stdout+bad.stderr).lower()
    att=base/'signed.json'
    run(r,'scripts/guardian.py','run-reviewer','--private',str(priv),'--public',str(pub),'--out',str(att),'--task-id','R3','--task-revision','1','--snapshot-sha256',status['snapshot_sha256'],'--writer-session-id','AI-WORKER','--review-report',str(review),'--cwd',str(r),'--',sys.executable,'-c','import os; assert os.environ["AI_BUILD_OS_REVIEWER_SESSION_ID"].startswith("REVIEWER-")')
    signed=json.loads(att.read_text()); assert signed['reviewer_session_id']!='AI-WORKER' and signed['reviewer_process']['pid_observed']
    original_review=review.read_text()
    review.write_text(original_review+'\npost-signature tamper\n')
    tampered=run(r,*common,'--review-attestation',str(att),expect=1,env=env)
    assert 'report hash mismatch' in (tampered.stdout+tampered.stderr).lower()
    review.write_text(original_review)
    run(r,*common,'--review-attestation',str(att),env=env)
    m=json.loads((r/'.ai/evidence/R3/r001/manifest.json').read_text()); assert m['review']['trust']=='SIGNED_GUARDIAN'
    wrong_priv=keys/'wrong-priv.pem'; wrong_pub=keys/'wrong-pub.pem'
    run(r,'scripts/guardian.py','keygen','--private',str(wrong_priv),'--public',str(wrong_pub))
    wrong=run(r,'scripts/ai_os.py','check',expect=1,env={'AI_BUILD_OS_GUARDIAN_PUBLIC_KEY':str(wrong_pub)})
    assert 'fingerprint mismatch' in (wrong.stdout+wrong.stderr).lower() or 'signature verification failed' in (wrong.stdout+wrong.stderr).lower()
    run(r,'scripts/ai_os.py','check',env=env)

    # R2 with a triggered/required review is also signature-verified by CI/check.
    run(r,'scripts/ai_os.py','begin','--task-id','R2G','--outcome','guarded change','--risk','R2','--success-criterion','SC-002','--delivery-delta','EXECUTABLE_CAPABILITY','--modify','src/r2.py','--create','src/r2.py')
    (r/'src/r2.py').write_text('def guarded(): return True\n')
    s2=json.loads(run(r,'scripts/ai_os.py','status','--json').stdout)
    review2=r/'.ai/reviews/review-r2.md'
    review2.write_text('# Independent Review Report\n\n- Task ID: R2G\n- Task revision: 1\n- Reviewed snapshot SHA256: '+s2['snapshot_sha256']+'\n- Reviewer identity: REVIEWER-R2\n- Reviewer role: CODE_REVIEWER\n- Independent from writer: yes\n- Writer identity: AI-WORKER\n- Verdict: PASS\n- Reviewed at: 2026-08-09T00:00:00+00:00\n')
    att2=base/'signed-r2.json'
    run(r,'scripts/guardian.py','sign-review','--private',str(priv),'--public',str(pub),'--out',str(att2),'--task-id','R2G','--task-revision','1','--snapshot-sha256',s2['snapshot_sha256'],'--writer-session-id','AI-WORKER','--reviewer-session-id','REVIEWER-R2','--review-report',str(review2))
    run(r,'scripts/ai_os.py','done','--outcome','guarded change','--focused-command','true','--negative-command','true','--integration-command','true','--review-report','.ai/reviews/review-r2.md','--review-attestation',str(att2),'--output-inspected-by','human:r','--first-pass-accepted','no',env=env)
    run(r,'scripts/ai_os.py','check',env=env)

def test_field_and_decisions(base:Path):
    r=fresh(base,'field')
    run(r,'scripts/ai_os.py','field','record','--event-type','CLI_RETRY','--phase','CLI','--severity','LOW','--trigger','bad flag','--extra-input-tokens','1200')
    run(r,'scripts/ai_os.py','field','record','--event-type','ESCAPED_DEFECT','--phase','OPERATIONS','--severity','HIGH','--trigger','missed edge')
    rep=json.loads(run(r,'scripts/ai_os.py','field','report').stdout); assert rep['events']>=2 and rep['upgrade_candidates']
    usage=base/'usage.json'
    usage.write_text(json.dumps([
        *[{'role':'WORKER','work_class':'bounded_backend','input_tokens':1000+i*100,'output_tokens':200,'wall_seconds':30+i} for i in range(5)],
        *[{'role':'REVIEWER','work_class':'bounded_backend','input_tokens':300+i*20,'output_tokens':80,'wall_seconds':10+i} for i in range(5)]
    ]))
    run(r,'scripts/ai_os.py','telemetry','ingest','--file',str(usage))
    rep=json.loads(run(r,'scripts/ai_os.py','field','report').stdout)
    assert rep['empirical_budgets'] and rep['governance_overhead']['token_ratio'] is not None and rep['stable_core_promotion_readiness']['status']=='FREEZE_STABLE_CORE'
    run(r,'scripts/ai_os.py','goal','begin','--goal','ship','--accept','observable output passes','--risk-ceiling','R1')
    run(r,'scripts/ai_os.py','goal','decision','--type','AUTO_DECISION','--text','Reuse existing store','--confidence','0.91','--reversibility','HIGH','--owner-impact','LOW','--reason','bounded and reversible')
    run(r,'scripts/ai_os.py','goal','decision','--type','ASSUMPTION','--text','Input remains UTF-8','--confidence','0.55','--reversibility','MEDIUM','--owner-impact','MEDIUM')
    d=json.loads(run(r,'scripts/ai_os.py','goal','digest').stdout); assert d['counts']['AUTO_DECISION']==1 and len(d['attention_required'])==1

def test_compact_cli_and_uncertainty(base:Path):
    r=fresh(base,'compact')
    run(r,'scripts/ai.py','start','--task-id','U1','--outcome','persist item','--modify','src/service.py')
    (r/'src').mkdir(exist_ok=True); (r/'src/service.py').write_text('def update(repo, item):\n    return repo.save(item)\n')
    action=run(r,'scripts/ai.py','finish','--outcome','persist item',expect=2); assert 'ACTION_REQUIRED' in action.stdout
    bad=run(r,'scripts/ai.py','finish','--outcome','persist item','--check','true',expect=1); assert 'requires R2' in (bad.stdout+bad.stderr)
    rep=json.loads(run(r,'scripts/ai_os.py','field','report').stdout); assert any(x['event_type']=='UNEXPECTED_RISK_ESCALATION' for x in rep['top_pain'])

def main():
    with tempfile.TemporaryDirectory(prefix='ai-os-v115-') as td:
        b=Path(td); test_assurance_and_guardian(b); test_field_and_decisions(b); test_compact_cli_and_uncertainty(b)
    print('SELF_TEST_V115: PASS')
if __name__=='__main__':main()
