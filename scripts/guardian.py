#!/usr/bin/env python3
"""External Guardian helper for Senior AI Build OS v1.16.

Keep private keys and generated attestations OUTSIDE the repository. This helper is
packaged for convenience, but the trust boundary exists only when the private key is
owned by a process/account the Worker cannot access.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os, subprocess, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path


def canonical(value:dict)->bytes:
    payload={k:v for k,v in value.items() if k not in {'signature_b64','signature_alg','key_fingerprint_sha256'}}
    return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def run(cmd:list[str])->None:
    p=subprocess.run(cmd,check=False,capture_output=True,text=True)
    if p.returncode:raise SystemExit((p.stderr or p.stdout or 'Guardian command failed').strip())


def keygen(args):
    private=Path(args.private).expanduser().resolve(); public=Path(args.public).expanduser().resolve()
    private.parent.mkdir(parents=True,exist_ok=True); public.parent.mkdir(parents=True,exist_ok=True)
    if private.exists() or public.exists():raise SystemExit('Refusing to overwrite Guardian keys')
    try:
        run(['openssl','genpkey','-algorithm','ED25519','-out',str(private)])
        run(['openssl','pkey','-in',str(private),'-pubout','-out',str(public)])
    except FileNotFoundError:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError as exc:
            raise SystemExit('Guardian key generation requires OpenSSL or the optional cryptography package') from exc
        key = Ed25519PrivateKey.generate()
        private.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        public.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    try:private.chmod(0o600)
    except OSError:pass
    print(json.dumps({'private_key':str(private),'public_key':str(public),'public_key_sha256':hashlib.sha256(public.read_bytes()).hexdigest()},indent=2))


def sign_payload(value:dict, private:Path, public:Path)->dict:
    with tempfile.TemporaryDirectory(prefix='ai-build-os-sign-') as td:
        data=Path(td)/'payload.json'; sig=Path(td)/'sig.bin'; data.write_bytes(canonical(value))
        try:
            run(['openssl','pkeyutl','-sign','-inkey',str(private),'-rawin','-in',str(data),'-out',str(sig)])
        except FileNotFoundError:
            try:
                from cryptography.hazmat.primitives import serialization
            except ImportError as exc:
                raise SystemExit('Guardian signing requires OpenSSL or the optional cryptography package') from exc
            key = serialization.load_pem_private_key(private.read_bytes(), password=None)
            sig.write_bytes(key.sign(data.read_bytes()))
        value['signature_alg']='ed25519-openssl'; value['signature_b64']=base64.b64encode(sig.read_bytes()).decode(); value['key_fingerprint_sha256']=hashlib.sha256(public.read_bytes()).hexdigest()
    return value

def sign_review(args):
    private=Path(args.private).expanduser().resolve(); public=Path(args.public).expanduser().resolve(); out=Path(args.out).expanduser().resolve()
    if not private.is_file() or not public.is_file():raise SystemExit('Guardian key missing')
    if args.writer_session_id==args.reviewer_session_id:raise SystemExit('Reviewer session must differ from writer session')
    value={'schema_version':3,'task_id':args.task_id,'task_revision':args.task_revision,'reviewed_snapshot_sha256':args.snapshot_sha256,'reviewer_session_id':args.reviewer_session_id,'writer_session_id':args.writer_session_id,'verdict':args.verdict.upper(),'reviewed_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'issuer':args.issuer}
    if args.review_report:
        report=Path(args.review_report).expanduser().resolve()
        if not report.is_file(): raise SystemExit(f'Review report missing: {report}')
        value['review_report_sha256']=hashlib.sha256(report.read_bytes()).hexdigest()
    value=sign_payload(value,private,public)
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(str(out))


def run_reviewer(args):
    private=Path(args.private).expanduser().resolve(); public=Path(args.public).expanduser().resolve(); out=Path(args.out).expanduser().resolve()
    if not private.is_file() or not public.is_file(): raise SystemExit('Guardian key missing')
    if not args.reviewer_command: raise SystemExit('run-reviewer requires command after --')
    reviewer_session='REVIEWER-'+uuid.uuid4().hex[:16]
    env=os.environ.copy()
    env['AI_BUILD_OS_REVIEWER_SESSION_ID']=reviewer_session
    env['AI_BUILD_OS_WRITER_SESSION_ID']=args.writer_session_id
    env.pop('AI_BUILD_OS_GUARDIAN_PRIVATE_KEY',None)
    proc=subprocess.Popen(args.reviewer_command,cwd=args.cwd or None,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    reviewer_pid=proc.pid
    try:
        stdout,stderr=proc.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        proc.kill(); stdout,stderr=proc.communicate(); raise SystemExit(f'Reviewer process timed out after {args.timeout}s; no attestation issued')
    if stdout: print(stdout,end='')
    if stderr: print(stderr,end='',file=os.sys.stderr)
    if proc.returncode!=0: raise SystemExit(f'Reviewer process failed exit={proc.returncode}; no attestation issued')
    value={'schema_version':3,'task_id':args.task_id,'task_revision':args.task_revision,'reviewed_snapshot_sha256':args.snapshot_sha256,'reviewer_session_id':reviewer_session,'writer_session_id':args.writer_session_id,'verdict':'PASS','reviewed_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'issuer':args.issuer,'reviewer_process':{'pid_observed':reviewer_pid,'command_sha256':hashlib.sha256('\0'.join(args.reviewer_command).encode()).hexdigest()}}
    if args.review_report:
        report=Path(args.review_report).expanduser().resolve()
        if not report.is_file(): raise SystemExit(f'Reviewer exited PASS but review report is missing: {report}; no attestation issued')
        value['review_report_sha256']=hashlib.sha256(report.read_bytes()).hexdigest()
    value=sign_payload(value,private,public)
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(f'GUARDIAN_ATTESTATION {out} reviewer_session={reviewer_session}')

def main():
    p=argparse.ArgumentParser(description='AI Build OS external Guardian')
    s=p.add_subparsers(dest='command',required=True)
    k=s.add_parser('keygen'); k.add_argument('--private',required=True); k.add_argument('--public',required=True); k.set_defaults(fn=keygen)
    a=s.add_parser('sign-review');
    for name in ['private','public','out','task-id','snapshot-sha256','writer-session-id','reviewer-session-id']:
        a.add_argument('--'+name,required=True)
    a.add_argument('--task-revision',type=int,required=True); a.add_argument('--review-report'); a.add_argument('--verdict',choices=['PASS','ACCEPTED'],default='PASS'); a.add_argument('--issuer',default='ai-build-os-guardian'); a.set_defaults(fn=sign_review)
    rr=s.add_parser('run-reviewer',help='Launch a separate reviewer process/session and issue attestation only on exit 0')
    for name in ['private','public','out','task-id','snapshot-sha256','writer-session-id']:
        rr.add_argument('--'+name,required=True)
    rr.add_argument('--task-revision',type=int,required=True); rr.add_argument('--review-report'); rr.add_argument('--issuer',default='ai-build-os-guardian'); rr.add_argument('--cwd'); rr.add_argument('--timeout',type=int,default=900); rr.add_argument('reviewer_command',nargs=argparse.REMAINDER); rr.set_defaults(fn=run_reviewer)
    args=p.parse_args();
    if getattr(args,'reviewer_command',None) and args.reviewer_command[0]=='--': args.reviewer_command=args.reviewer_command[1:]
    args.fn(args)
if __name__=='__main__':main()
