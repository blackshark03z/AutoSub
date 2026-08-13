#!/usr/bin/env python3
"""Small agent-facing facade over the full ai_os.py kernel.

Common agents should need only: start, finish, status, next. The full CLI remains an
internal/admin surface for exceptional workflows.
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
KERNEL=HERE/'ai_os.py'

def run(argv:list[str])->int:return subprocess.run([sys.executable,str(KERNEL),*argv],check=False).returncode

def run_capture(argv:list[str]):
    return subprocess.run([sys.executable,str(KERNEL),*argv],check=False,capture_output=True,text=True)

def main():
    p=argparse.ArgumentParser(description='AI Build OS compact agent facade')
    p.add_argument('--root',type=Path,default=HERE.parent)
    s=p.add_subparsers(dest='command',required=True)
    st=s.add_parser('start'); st.add_argument('--task-id',required=True); st.add_argument('--outcome',required=True); st.add_argument('--modify',required=True); st.add_argument('--accept',action='append',default=[]); st.add_argument('--risk',default='auto',choices=['auto','R0','R1','R2','R3']); st.add_argument('--delivery-delta',default='EXECUTABLE_CAPABILITY',choices=['USER_VISIBLE_BEHAVIOR','EXECUTABLE_CAPABILITY','RISK_RETIREMENT','DOCUMENTATION_ONLY','NO_DELTA']); st.add_argument('--session-label',default='AI-WORKER'); st.add_argument('--state-hazard',default='auto',choices=['auto','S0','S1','S2','S3','S4']); st.add_argument('--state-signal',action='append',default=[]); st.add_argument('--state-authority',default=''); st.add_argument('--state-transition',action='append',default=[]); st.add_argument('--state-invariant',action='append',default=[]); st.add_argument('--state-dependency',action='append',default=[])
    f=s.add_parser('finish'); f.add_argument('--outcome',required=True); f.add_argument('--check',action='append',default=[]); f.add_argument('--negative',action='append',default=[]); f.add_argument('--integration',action='append',default=[]); f.add_argument('--transition',action='append',default=[]); f.add_argument('--temporal',action='append',default=[]); f.add_argument('--review-report'); f.add_argument('--review-attestation'); f.add_argument('--inspect-by',default='agent:worker'); f.add_argument('--first-pass',choices=['yes','no','unknown'],default='unknown')
    ss=s.add_parser('status'); ss.add_argument('--json',action='store_true'); s.add_parser('next')
    a=p.parse_args(); base=['--root',str(a.root.resolve())]
    if a.command=='start':
        acceptance=a.accept or [a.outcome]
        argv=base+['start','--task-id',a.task_id,'--outcome',a.outcome,'--modify',a.modify,'--risk',a.risk,'--success-criterion','SC-001','--delivery-delta',a.delivery_delta,'--session-label',a.session_label,'--state-hazard',a.state_hazard]
        for x in acceptance:argv+=['--accept',x]
        for x in a.state_signal:argv+=['--state-signal',x]
        if a.state_authority:argv+=['--state-authority',a.state_authority]
        for x in a.state_transition:argv+=['--state-transition',x]
        for x in a.state_invariant:argv+=['--state-invariant',x]
        for x in a.state_dependency:argv+=['--state-dependency',x]
        raise SystemExit(run(argv))
    if a.command=='finish':
        checks=list(a.check)
        if not checks:
            # Do not guess silently. Ask kernel status and return a machine-readable next action.
            status=subprocess.run([sys.executable,str(KERNEL),*base,'status','--json'],capture_output=True,text=True,check=False)
            try: st=json.loads(status.stdout)
            except json.JSONDecodeError: st={}
            print(json.dumps({'status':'ACTION_REQUIRED','reason':'FOCUSED_CHECK_REQUIRED','risk':st.get('risk'),'next_action':'rerun `scripts/ai.py finish --check <focused command>`; R2/R3 may also require negative/integration/reviewer evidence'},ensure_ascii=False,indent=2))
            raise SystemExit(2)
        argv=base+['done','--outcome',a.outcome,'--output-inspected-by',a.inspect_by,'--first-pass-accepted',a.first_pass]
        for x in checks:argv+=['--focused-command',x]
        for x in a.negative:argv+=['--negative-command',x]
        for x in a.integration:argv+=['--integration-command',x]
        for x in a.transition:argv+=['--state-transition-command',x]
        for x in a.temporal:argv+=['--state-temporal-command',x]
        if a.review_report:argv+=['--review-report',a.review_report]
        if a.review_attestation:argv+=['--review-attestation',a.review_attestation]
        proc=run_capture(argv)
        if proc.returncode==0:
            if proc.stdout: print(proc.stdout,end='')
            if proc.stderr: print(proc.stderr,end='',file=sys.stderr)
            raise SystemExit(0)
        error=(proc.stderr or proc.stdout or 'kernel rejected finish').strip()
        reason='KERNEL_REQUIREMENT'
        lower=error.casefold()
        if 'requires r2' in lower or 'risk' in lower and 'requires' in lower: reason='RISK_ESCALATION_REQUIRED'
        elif 'review-attestation' in lower or 'signed_guardian' in lower: reason='GUARDIAN_REVIEW_REQUIRED'
        elif 'negative-command' in lower: reason='NEGATIVE_CHECK_REQUIRED'
        elif 'state-transition-command' in lower: reason='STATE_TRANSITION_PROOF_REQUIRED'
        elif 'state-temporal-command' in lower: reason='STATE_TEMPORAL_PROOF_REQUIRED'
        elif 'integration-command' in lower: reason='INTEGRATION_CHECK_REQUIRED'
        elif 'dependency' in lower: reason='DEPENDENCY_DECISION_REQUIRED'
        print(json.dumps({'status':'ACTION_REQUIRED','reason':reason,'kernel_error':error,'next_action':'satisfy the named requirement and rerun the same compact finish command'},ensure_ascii=False,indent=2))
        raise SystemExit(proc.returncode)
    if a.command=='status':raise SystemExit(run(base+['status']+(['--json'] if a.json else [])))
    raise SystemExit(run(base+['next']))
if __name__=='__main__':main()
