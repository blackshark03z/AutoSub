#!/usr/bin/env python3
"""Fail-closed Product CI with explicit quality capabilities and conservative autodetection."""
from __future__ import annotations
import argparse, json, re, shlex, shutil, subprocess, sys
from pathlib import Path

DEFAULT_ROOT=Path(__file__).resolve().parents[1]
PLACEHOLDERS={'','UNSET','PROJECT_SPECIFIC','NONE_REQUIRED_OR_PROJECT_SPECIFIC','AUTO_DETECT_ON_FIRST_TASK','NONE_DETECTED'}
CAPS={'test','lint','typecheck','build'}

def _field(body:str,name:str,default:str='')->str:
    m=re.search(rf"^-?\s*{re.escape(name)}:\s*(.*?)\s*$",body,re.M|re.I); return m.group(1).strip() if m else default

def project_contract(root:Path)->dict[str,str]:
    p=root/'.ai/PROJECT.md'; body=p.read_text(encoding='utf-8') if p.is_file() else ''
    keys=('Install command','Test command','Lint command','Typecheck command','Build command','CI quality command','CI quality capabilities','Language/runtime','Package manager')
    return {k:_field(body,k) for k in keys}

def executable_project(root:Path)->bool:
    markers=('package.json','pyproject.toml','setup.py','setup.cfg','requirements.txt','go.mod','Cargo.toml','composer.json')
    return any((root/m).is_file() for m in markers) or any((root/d).exists() for d in ('src','app','server','packages'))

def _cmd(value:str)->list[str]|None:
    if not value or value.strip().upper() in PLACEHOLDERS:return None
    return shlex.split(value)

def _declared_caps(raw:str)->set[str]:
    if not raw or raw.strip().upper() in PLACEHOLDERS:return set()
    return {x.strip().casefold() for x in re.split(r'[,;\s]+',raw) if x.strip()}

def quality_policy(root:Path)->dict:
    p=root/'config/quality_policy.json'
    if not p.is_file(): return {'required_for_executable':['test'],'recommended_for_executable':[],'capability_waivers':{},'minimum_waiver_reason_chars':24}
    return json.loads(p.read_text(encoding='utf-8'))

def autodetect(root:Path)->list[tuple[str,str,list[str]]]:
    checks=[]; package=root/'package.json'
    if package.is_file():
        try:scripts=json.loads(package.read_text(encoding='utf-8')).get('scripts',{}) or {}
        except json.JSONDecodeError:scripts={}
        if (root/'pnpm-lock.yaml').is_file(): runner='pnpm'
        elif (root/'yarn.lock').is_file(): runner='yarn'
        else: runner='npm'
        if shutil.which(runner):
            mapping=(('lint','lint'),('typecheck','typecheck'),('check','typecheck'),('test','test'),('build','build'))
            seen=set()
            for n,cap in mapping:
                if n in scripts and cap not in seen:
                    checks.append((f'node:{n}',cap,[runner,'run',n] if runner!='yarn' else ['yarn',n])); seen.add(cap)
    if any((root/n).is_file() for n in ('pyproject.toml','setup.py','setup.cfg','requirements.txt')):
        if (root/'tests').exists(): checks.append(('python:pytest','test',[sys.executable,'-m','pytest','-q']))
    if (root/'go.mod').is_file(): checks += [('go:vet','lint',['go','vet','./...']),('go:test','test',['go','test','./...']),('go:build','build',['go','build','./...'])]
    if (root/'Cargo.toml').is_file(): checks += [('rust:clippy','lint',['cargo','clippy','--all-targets','--','-D','warnings']),('rust:test','test',['cargo','test','--all-targets']),('rust:build','build',['cargo','build'])]
    return checks

def install_command(root:Path)->list[str]|None:
    c=project_contract(root); explicit=_cmd(c.get('Install command',''))
    if explicit:return explicit
    if (root/'pnpm-lock.yaml').is_file():return ['pnpm','install','--frozen-lockfile']
    if (root/'yarn.lock').is_file():return ['yarn','install','--frozen-lockfile']
    if (root/'package-lock.json').is_file():return ['npm','ci']
    if (root/'uv.lock').is_file():return ['uv','sync','--all-extras','--dev']
    if (root/'requirements.txt').is_file():return [sys.executable,'-m','pip','install','-r','requirements.txt']
    if (root/'pyproject.toml').is_file():return [sys.executable,'-m','pip','install','-e','.']
    if (root/'go.mod').is_file():return ['go','mod','download']
    if (root/'Cargo.toml').is_file():return ['cargo','fetch']
    return None

def checks(root:Path)->tuple[list[tuple[str,str,list[str]]],str]:
    c=project_contract(root); canonical=[]
    for cap,key in (('lint','Lint command'),('typecheck','Typecheck command'),('test','Test command'),('build','Build command')):
        cmd=_cmd(c.get(key,''))
        if cmd: canonical.append((f'project:{cap}',cap,cmd))
    quality_raw=c.get('CI quality command','')
    if quality_raw and quality_raw.strip().upper() not in PLACEHOLDERS:
        declared=_declared_caps(c.get('CI quality capabilities',''))
        bound={x[1] for x in canonical}
        # Avoid paying twice for the same checks: aggregate CI runs only when it is
        # the sole contract or explicitly covers capabilities without dedicated commands.
        if not canonical or bool(declared-bound):
            canonical.append(('project:canonical-quality','aggregate',shlex.split('sh -c '+shlex.quote(quality_raw))))
    if canonical:return canonical,'contract'
    auto=autodetect(root); return auto,'autodetect'

def capability_findings(root:Path, detected:list[tuple[str,str,list[str]]])->tuple[list[str],list[str]]:
    if not executable_project(root): return [],[]
    policy=quality_policy(root); present={cap for _,cap,_ in detected if cap in CAPS}
    # Aggregate commands may be custom; declared capabilities bind their claimed coverage explicitly.
    present |= _declared_caps(project_contract(root).get('CI quality capabilities',''))
    waivers=policy.get('capability_waivers') or {}; min_chars=int(policy.get('minimum_waiver_reason_chars',24) or 24)
    errors=[]; warnings=[]
    for cap in policy.get('required_for_executable',[]):
        if cap in present: continue
        reason=str(waivers.get(cap) or '').strip()
        if len(reason)>=min_chars: warnings.append(f'required capability {cap} explicitly waived: {reason}')
        else: errors.append(f'missing required quality capability: {cap}; configure a canonical command or a >= {min_chars}-char waiver in config/quality_policy.json')
    for cap in policy.get('recommended_for_executable',[]):
        if cap not in present: warnings.append(f'recommended quality capability not configured: {cap}')
    return errors,warnings

def _tool_exists(argv:list[str])->bool:
    if not argv:return False
    if argv[0] in {sys.executable,'python','python3','sh','bash'}:return True
    return shutil.which(argv[0]) is not None

def run_one(root:Path,name:str,argv:list[str])->int:
    if not _tool_exists(argv): print(f'PROJECT_CI: FAIL {name}: required executable not available: {argv[0]}'); return 127
    print(f'\n== {name} ==\n$ {" ".join(argv)}',flush=True)
    try:r=subprocess.run(argv,cwd=root,timeout=1800)
    except subprocess.TimeoutExpired: print(f'PROJECT_CI: FAIL {name}: timeout'); return 124
    if r.returncode:print(f'PROJECT_CI: FAIL {name} exit={r.returncode}')
    return r.returncode

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,default=DEFAULT_ROOT); p.add_argument('--list',action='store_true'); p.add_argument('--ci',action='store_true'); p.add_argument('--install',action='store_true'); p.add_argument('--allow-no-product-check',action='store_true'); a=p.parse_args(); root=a.root.resolve()
    if a.install:
        cmd=install_command(root)
        if not cmd:
            if executable_project(root) and a.ci: print('PROJECT_CI: FAIL no install command detected for executable project'); return 2
            print('PROJECT_CI: no dependency-install step required/detected'); return 0
        return run_one(root,'install',cmd) if a.ci else (print('install: '+' '.join(cmd)) or 0)
    detected,source=checks(root); errors,warnings=capability_findings(root,detected)
    for w in warnings: print('PROJECT_CI: WARN '+w)
    if errors and a.ci:
        for e in errors: print('PROJECT_CI: FAIL '+e)
        return 2
    if not detected:
        msg='no canonical or standard product quality checks detected'
        if executable_project(root) and a.ci and not a.allow_no_product_check:
            print('PROJECT_CI: FAIL '+msg+'; set canonical capability commands in .ai/PROJECT.md')
            return 2
        print('PROJECT_CI: WARN '+msg); return 0
    print(f'PROJECT_CI source={source} capabilities={",".join(sorted({cap for _,cap,_ in detected if cap in CAPS})) or "aggregate"}')
    for name,cap,argv in detected:print(f'{name}[{cap}]: {" ".join(argv)}')
    if a.list and not a.ci:return 0
    if not a.ci:return 0
    for name,cap,argv in detected:
        code=run_one(root,name,argv)
        if code:return code
    print(f'PROJECT_CI: PASS checks={len(detected)} source={source}'); return 0
if __name__=='__main__':raise SystemExit(main())
