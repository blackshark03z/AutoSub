#!/usr/bin/env python3
"""Codebase-health ratchet: prevent entropy growth without blocking low-risk delivery on aesthetics."""
from __future__ import annotations
import csv, fnmatch, json, os, subprocess
from pathlib import Path
from typing import Any

CONFIG_REL=Path('config/codebase_health.json'); SNAP_REL=Path('.ai/health/CODEBASE_HEALTH.json')

def load_config(root:Path)->dict[str,Any]: return json.loads((root/CONFIG_REL).read_text(encoding='utf-8'))


def _os_owned_paths(root:Path)->set[str]:
    inv=root/'PACKAGE_INVENTORY.txt'
    if not inv.is_file(): return set()
    out=set()
    for line in inv.read_text(encoding='utf-8',errors='ignore').splitlines():
        if line.strip(): out.add(line.split('\t',1)[0].replace('\\','/'))
    return out

def _ignored(rel:str, cfg:dict[str,Any])->bool:
    rel=rel.replace('\\','/').lstrip('./')
    return any(rel==d or rel.startswith(d.rstrip('/')+'/') for d in cfg.get('ignore_dirs',[]))

def _product_files(root:Path,cfg:dict[str,Any]):
    """Yield product-tree files without descending into ignored heavy directories."""
    for current, dirs, files in os.walk(root):
        base=Path(current)
        rel_dir=base.relative_to(root).as_posix() if base!=root else ''
        kept=[]
        for d in dirs:
            rel=(f"{rel_dir}/{d}" if rel_dir else d).replace('\\','/')
            if rel=='.ai' or _ignored(rel,cfg):
                continue
            kept.append(d)
        dirs[:]=kept
        for name in files:
            p=base/name; rel=p.relative_to(root).as_posix()
            if _ignored(rel,cfg): continue
            yield p,rel

def _source_files(root:Path,cfg:dict[str,Any])->list[Path]:
    exts=set(cfg.get('source_extensions',[])); os_owned=_os_owned_paths(root)
    return [p for p,rel in _product_files(root,cfg) if rel not in os_owned and p.suffix.casefold() in exts]

def _runtime_dependencies(root:Path)->int:
    total=0; pkg=root/'package.json'
    if pkg.is_file():
        try: total+=len((json.loads(pkg.read_text(encoding='utf-8')).get('dependencies') or {}))
        except json.JSONDecodeError: pass
    pp=root/'pyproject.toml'
    if pp.is_file():
        text=pp.read_text(encoding='utf-8',errors='ignore')
        # Conservative TOML-independent count for project.dependencies array only.
        in_deps=False
        for line in text.splitlines():
            s=line.strip()
            if s.startswith('dependencies') and '[' in s: in_deps=True; continue
            if in_deps:
                if ']' in s: in_deps=False; continue
                if s.startswith(('"',"'")): total+=1
    return total

def _git_size_mb(root:Path)->float|None:
    if not (root/'.git').exists(): return None
    try:
        r=subprocess.run(['git','count-objects','-v'],cwd=root,text=True,capture_output=True,timeout=10)
        if r.returncode==0:
            values={}
            for line in r.stdout.splitlines():
                if ':' in line:
                    k,v=line.split(':',1); values[k.strip()]=v.strip()
            kib=float(values.get('size',0) or 0)+float(values.get('size-pack',0) or 0)
            return round(kib/1024,3)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass
    return None

def _tracked(root:Path)->list[str]:
    try:
        r=subprocess.run(['git','ls-files'],cwd=root,text=True,capture_output=True,timeout=10)
        if r.returncode==0: return [x for x in r.stdout.splitlines() if x]
    except (OSError,subprocess.TimeoutExpired): pass
    return []

def _inspect_paths(root:Path, paths:list[str])->dict[str,Any]:
    cfg=load_config(root); errors=[]; warnings=[]
    for rel in paths:
        p=root/rel; normalized=rel.replace('\\','/')
        if _matches(normalized,cfg.get('hard_forbidden_tracked_patterns',[])):
            errors.append(f'forbidden repository artifact: {normalized}')
        if p.is_file() and _binary(p):
            mb=p.stat().st_size/1024/1024
            if mb>float(cfg.get('max_new_binary_mb',5)): errors.append(f'large binary {normalized}={mb:.1f}MB exceeds {cfg.get("max_new_binary_mb",5)}MB')
        if _matches(normalized,cfg.get('generated_warn_patterns',[])): warnings.append(f'generated/archive artifact added or modified: {normalized}')
    errors.extend(architecture_violations(root,paths))
    return {'errors':sorted(set(errors)),'warnings':sorted(set(warnings))}

def snapshot(root:Path, *, file_loc_patterns:list[str]|None=None)->dict[str,Any]:
    cfg=load_config(root); files=_source_files(root,cfg); loc=0; largest=[]; source_bytes=0
    for p in files:
        try:
            b=p.stat().st_size; source_bytes+=b; n=sum(1 for _ in p.open(encoding='utf-8',errors='ignore'))
        except OSError: continue
        loc+=n; largest.append((n,p.relative_to(root).as_posix()))
    largest.sort(reverse=True); tracked=_tracked(root); tracked_bytes=0
    scoped_locs={path:n for n,path in largest if file_loc_patterns and _matches(path,file_loc_patterns)}
    os_owned=_os_owned_paths(root)
    product_tracked=[rel for rel in tracked if rel not in os_owned and not rel.startswith('.ai/')]
    for rel in product_tracked:
        try: tracked_bytes+=(root/rel).stat().st_size
        except OSError: pass
    # Product-tree size includes untracked task output too, unlike git ls-files.
    # This catches archive/generated bloat before commit while ignoring configured
    # caches/build directories and the Build OS package itself.
    product_tree_bytes=0
    for p,rel in _product_files(root,cfg):
        if rel in os_owned: continue
        try: product_tree_bytes+=p.stat().st_size
        except OSError: pass
    health_now=_inspect_paths(root,product_tracked)
    return {
      'schema_version':2,'source_files':len(files),'source_loc':loc,'source_bytes':source_bytes,
      'largest_source_files':[{'path':p,'loc':n} for n,p in largest[:10]],
      'largest_source_file_loc':largest[0][0] if largest else 0,'runtime_dependencies':_runtime_dependencies(root),
      'tracked_files':len(product_tracked),'tracked_repo_mb':round(tracked_bytes/1024/1024,3),
      'product_tree_mb':round(product_tree_bytes/1024/1024,3),'git_repo_mb':_git_size_mb(root),
      'hard_violations':health_now['errors'],'scoped_source_file_locs':scoped_locs
    }

def save_snapshot(root:Path,snap:dict[str,Any]|None=None)->dict[str,Any]:
    snap=snap or snapshot(root); path=root/SNAP_REL; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(snap,indent=2,sort_keys=True)+'\n',encoding='utf-8'); return snap

def load_baseline(root:Path)->dict[str,Any]:
    p=root/SNAP_REL
    if not p.is_file(): return {}
    try:return json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError:return {}

def _matches(path:str,patterns:list[str])->bool:
    p=path.replace('\\','/').lstrip('./'); return any(fnmatch.fnmatch(p,pat) for pat in patterns)

def _binary(path:Path)->bool:
    try:
        data=path.read_bytes()[:4096]
    except OSError:return False
    return b'\0' in data

def _executable_project(root:Path)->bool:
    markers=('package.json','pyproject.toml','setup.py','setup.cfg','requirements.txt','go.mod','Cargo.toml','composer.json')
    return any((root/m).is_file() for m in markers) or any((root/d).exists() for d in ('src','app','server','packages'))

def architecture_policy_findings(root:Path)->list[str]:
    cfg=load_config(root); policy=cfg.get('architecture_policy') or {}
    if not policy.get('require_explicit_decision_for_executable',False) or not _executable_project(root): return []
    if cfg.get('architecture_boundaries'): return []
    reason=str(policy.get('no_boundaries_reason') or '').strip(); min_chars=int(policy.get('minimum_waiver_reason_chars',24) or 24)
    if len(reason)>=min_chars:return []
    return [f'architecture decision missing: configure architecture_boundaries or record a >= {min_chars}-char architecture_policy.no_boundaries_reason']

def set_architecture_waiver(root:Path,reason:str)->dict[str,Any]:
    cfg=load_config(root); policy=cfg.setdefault('architecture_policy',{}); min_chars=int(policy.get('minimum_waiver_reason_chars',24) or 24)
    if len(reason.strip())<min_chars: raise SystemExit(f'Architecture waiver reason must be at least {min_chars} characters')
    policy['no_boundaries_reason']=reason.strip(); (root/CONFIG_REL).write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8'); return cfg

def architecture_violations(root:Path,changed_paths:list[str])->list[str]:
    cfg=load_config(root); violations=[]
    for rule in cfg.get('architecture_boundaries',[]):
        src_pat=str(rule.get('from') or ''); forbidden=[str(x) for x in rule.get('forbid_import_contains',[])]
        for rel in changed_paths:
            if not fnmatch.fnmatch(rel,src_pat): continue
            p=root/rel
            if not p.is_file(): continue
            text=p.read_text(encoding='utf-8',errors='ignore')
            for needle in forbidden:
                if needle and needle in text: violations.append(f"{rel} violates {src_pat}: forbidden dependency marker {needle!r}")
    return violations

def check_delta(root:Path,changed_paths:list[str],*,baseline:dict[str,Any]|None=None,hard_fail:bool=True)->dict[str,Any]:
    result=_inspect_paths(root,changed_paths); cfg=load_config(root); hard=cfg.get('hard_ratchets') or {}; baseline=baseline or {}
    before={str(k):int(v) for k,v in (baseline.get('scoped_source_file_locs') or {}).items()}; exts=set(cfg.get('source_extensions',[]))
    soft=int(hard.get('existing_large_file_soft_floor_loc',800) or 800); hotspot_candidates=[]
    for rel in changed_paths:
        p=root/rel
        if not p.is_file() or p.suffix.casefold() not in exts: continue
        try: now=sum(1 for _ in p.open(encoding='utf-8',errors='ignore'))
        except OSError: continue
        prior=before.get(rel)
        if prior is None:
            limit=int(hard.get('new_source_file_loc_max',1000) or 1000)
            if now>limit: result['errors'].append(f'new source file too large: {rel}={now} LOC exceeds hard limit {limit}; split by responsibility or explicitly revise health policy')
            continue
        growth=now-prior; max_growth=int(hard.get('existing_large_file_growth_loc_max',300) or 300)
        if now>soft and growth>max_growth:
            result['errors'].append(f'large source file grew too much in one task: {rel} {prior}→{now} LOC (+{growth}, max +{max_growth})')
        if now>soft and growth>0: hotspot_candidates.append((rel,growth))
    # History scanning is intentionally lazy: ordinary small-file tasks pay no hotspot-reporting cost.
    if hotspot_candidates:
        hotspot_rows={r['path']:r for r in hotspots(root,limit=1000)}
        high=int((cfg.get('hotspot') or {}).get('high_score',12) or 12); hot_growth=int(hard.get('hotspot_large_file_growth_loc_max',150) or 150)
        for rel,growth in hotspot_candidates:
            hotspot=hotspot_rows.get(rel) or {}
            if int(hotspot.get('score') or 0)>=high and growth>hot_growth:
                result['errors'].append(f'hotspot growth hard gate: {rel} score={hotspot.get("score")} grew +{growth} LOC (max +{hot_growth})')
    result['errors']=sorted(set(result['errors']))
    if hard_fail and result['errors']: raise SystemExit('Codebase health hard gate: '+'; '.join(result['errors']))
    return result

def compare(root:Path,baseline:dict[str,Any]|None=None,current:dict[str,Any]|None=None)->dict[str,Any]:
    baseline=baseline or load_baseline(root); current=current or snapshot(root); cfg=load_config(root); r=cfg.get('ratchets',{}); warnings=[]
    def growth(key:str)->float:
        b=float(baseline.get(key) or 0); c=float(current.get(key) or 0); return ((c-b)/b*100) if b>0 else 0.0
    if baseline:
        lg=growth('largest_source_file_loc')
        largest_before=int(baseline.get('largest_source_file_loc') or 0); largest_now=int(current.get('largest_source_file_loc') or 0)
        if lg>float(r.get('largest_source_file_growth_percent_warn',30)): warnings.append(f'largest source file grew {lg:.1f}% ({largest_before}→{largest_now})')
        if largest_now>int(r.get('largest_source_file_loc_warn',800)) and largest_now>largest_before:
            warnings.append(f'largest source file reached {largest_now} LOC; prefer bounded extraction only if this area is becoming a hotspot')
        sl=growth('source_loc')
        if sl>float(r.get('source_loc_growth_percent_warn',20)): warnings.append(f'total source LOC grew {sl:.1f}% ({baseline.get("source_loc")}→{current.get("source_loc")})')
        dep=int(current.get('runtime_dependencies') or 0)-int(baseline.get('runtime_dependencies') or 0)
        if dep>int(r.get('runtime_dependency_growth_warn',3)): warnings.append(f'runtime dependencies grew +{dep}')
        newf=int(current.get('source_files') or 0)-int(baseline.get('source_files') or 0)
        if newf>int(r.get('new_source_files_warn',12)): warnings.append(f'source files grew +{newf}')
        net_loc=int(current.get('source_loc') or 0)-int(baseline.get('source_loc') or 0)
        if net_loc>int(r.get('net_loc_per_goal_warn',2000)): warnings.append(f'net source LOC grew +{net_loc}; confirm structural expansion is proportional to accepted capability')
        tree_delta=float(current.get('product_tree_mb') or 0)-float(baseline.get('product_tree_mb') or 0)
        if tree_delta>float(r.get('product_tree_growth_mb_warn',10)): warnings.append(f'product tree grew +{tree_delta:.1f}MB')
        bg=baseline.get('git_repo_mb'); cg=current.get('git_repo_mb')
        if bg is not None and cg is not None and float(cg)-float(bg)>float(r.get('git_repo_growth_mb_warn',25)):
            warnings.append(f'Git object store grew +{float(cg)-float(bg):.1f}MB; inspect large historical artifacts')
    return {'baseline':baseline,'current':current,'warnings':warnings}

def hotspots(root:Path,limit:int=10)->list[dict[str,Any]]:
    quality={}
    ledger=root/'.ai/COST_LEDGER.csv'
    if ledger.is_file():
        with ledger.open(encoding='utf-8',newline='') as h:
            for row in csv.DictReader(h):
                key=(row.get('task_id'),str(row.get('task_revision') or ''))
                quality[key]=(row.get('later_rework')=='yes', row.get('escaped_defect')=='yes')
    touches={}; fp_bad={}; rework_bad={}; defect_bad={}; hist=root/'.ai/history'
    for p in hist.glob('**/*.json') if hist.exists() else []:
        try:r=json.loads(p.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):continue
        ev=root/str(r.get('evidence_bundle') or '')/'manifest.json'
        try: paths=json.loads(ev.read_text(encoding='utf-8')).get('task_delta_files',[])
        except (OSError,json.JSONDecodeError): paths=[]
        key=(str(r.get('task_id') or ''),str(r.get('task_revision') or ''))
        rw,de=quality.get(key,(False,False)); fp=str(r.get('first_pass_accepted') or '').casefold()=='no'
        for rel in paths:
            touches[rel]=touches.get(rel,0)+1
            fp_bad[rel]=fp_bad.get(rel,0)+(1 if fp else 0)
            rework_bad[rel]=rework_bad.get(rel,0)+(1 if rw else 0)
            defect_bad[rel]=defect_bad.get(rel,0)+(1 if de else 0)
    rows=[]
    for rel,n in touches.items():
        score=n+2*fp_bad.get(rel,0)+3*rework_bad.get(rel,0)+5*defect_bad.get(rel,0)
        rows.append({'path':rel,'touches':n,'first_pass_failures':fp_bad.get(rel,0),'reworks':rework_bad.get(rel,0),'escaped_defects':defect_bad.get(rel,0),'score':score})
    return sorted(rows,key=lambda x:(-x['score'],-x['touches'],x['path']))[:limit]

def report(root:Path)->dict[str,Any]:
    current=snapshot(root); delta=compare(root,current=current); return {'snapshot':current,'ratchet':delta,'hotspots':hotspots(root)}

def check_repository(root:Path, *, hard_fail:bool=True)->dict[str,Any]:
    current=_inspect_paths(root,_tracked(root)); baseline=load_baseline(root); grandfathered=set(baseline.get('hard_violations') or [])
    new_errors=[e for e in current['errors'] if e not in grandfathered]
    new_errors.extend(architecture_policy_findings(root))
    result={'errors':new_errors,'warnings':current['warnings'],'grandfathered_violations':sorted(grandfathered & set(current['errors']))}
    if hard_fail and new_errors: raise SystemExit('Codebase health hard gate: '+'; '.join(new_errors))
    return result

