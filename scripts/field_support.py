#!/usr/bin/env python3
"""Field-learning event ledger for Senior AI Build OS v1.16.

Records normalized operational friction/failure signals without source code. It does
not self-edit policy: it produces evidence-backed upgrade candidates for owner review.
"""
from __future__ import annotations
import hashlib, json, os, statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from telemetry_support import load as load_telemetry, delegation_feedback

REL=Path('.ai/field/events.jsonl')
GLOBAL_REL=Path.home()/'.ai-build-os'/'field'/'events.jsonl'
TYPES={
 'CLI_RETRY','WORKFLOW_RETRY','SCOPE_AMEND','UNEXPECTED_RISK_ESCALATION','REVIEWER_DEFECT_FOUND',
 'FIRST_PASS_FAILURE','ESCAPED_DEFECT','LATER_REWORK','OWNER_INTERRUPT','AUTO_DECISION_OVERRIDDEN',
 'CIRCUIT_BREAKER','POLICY_OVERRIDE','GUARD_FALSE_POSITIVE','GUARD_FALSE_NEGATIVE','SCOUT_WASTE',
 'DUPLICATE_DISCOVERY','VERIFICATION_FLAKE','REGRESSION_TIMEOUT','CONTEXT_REDISCOVERY','DIRECT_GOVERNANCE_WRITE_ATTEMPT','EVIDENCE_INFRA_FAILURE'
}
SEVERITY_WEIGHT={'LOW':1.0,'MEDIUM':2.0,'HIGH':4.0,'CRITICAL':8.0}


def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')

def _project_id(root:Path)->str:
    p=root/'.ai/PROJECT.md'
    if not p.is_file():return 'UNINITIALIZED'
    for line in p.read_text(encoding='utf-8',errors='ignore').splitlines():
        if line.startswith('Project ID:'):return line.split(':',1)[1].strip() or 'UNSET'
    return 'UNSET'

def record(root:Path,event_type:str,*,phase:str='UNKNOWN',severity:str='LOW',trigger:str='',automatic:bool=True,extra_wall_seconds:float=0,extra_input_tokens:int=0,extra_output_tokens:int=0,extra_provider_cost:float=0,evidence_code:str='',metadata:dict[str,Any]|None=None)->dict[str,Any]:
    event_type=event_type.upper(); severity=severity.upper()
    if event_type not in TYPES:raise ValueError(f'Unknown field event type: {event_type}')
    if severity not in SEVERITY_WEIGHT:raise ValueError(f'Unknown severity: {severity}')
    obj={'schema_version':1,'at':now(),'event_type':event_type,'os_version':(root/'VERSION').read_text().strip() if (root/'VERSION').is_file() else 'UNKNOWN','project_id':_project_id(root),'phase':phase.upper(),'severity':severity,'automatic':bool(automatic),'trigger':str(trigger)[:500],'cost':{'extra_wall_seconds':float(extra_wall_seconds or 0),'extra_input_tokens':int(extra_input_tokens or 0),'extra_output_tokens':int(extra_output_tokens or 0),'extra_provider_cost':float(extra_provider_cost or 0)},'evidence_code':str(evidence_code)[:120],'metadata':metadata or {}}
    path=root/REL; path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as h:h.write(json.dumps(obj,ensure_ascii=False,sort_keys=True)+'\n')
    # Optional privacy-preserving cross-project learning. Disabled by default.
    try:
        cfg=json.loads((root/'config/assurance.json').read_text(encoding='utf-8'))
    except Exception:
        cfg={}
    fl=cfg.get('field_learning') or {}
    if fl.get('global_enabled') is True:
        mirrored=dict(obj)
        if fl.get('redact_project_id_in_global',True):
            mirrored['project_id']='sha256:'+hashlib.sha256(str(obj.get('project_id')).encode()).hexdigest()[:16]
        mirrored['metadata']={k:v for k,v in (mirrored.get('metadata') or {}).items() if k in {'work_class','surface','risk','lane','reason_code'}}
        GLOBAL_REL.parent.mkdir(parents=True,exist_ok=True)
        with GLOBAL_REL.open('a',encoding='utf-8') as h:h.write(json.dumps(mirrored,ensure_ascii=False,sort_keys=True)+'\n')
    return obj

def load(root:Path,last:int|None=None)->list[dict[str,Any]]:
    p=root/REL
    if not p.is_file():return []
    out=[]
    for line in p.read_text(encoding='utf-8',errors='ignore').splitlines():
        try:out.append(json.loads(line))
        except json.JSONDecodeError:continue
    return out[-last:] if last else out

def _candidate(kind:str,count:int,total:int,pain:float)->dict[str,Any]:
    mapping={
      'CLI_RETRY':('Simplify agent-facing CLI','Collapse common lifecycle operations behind scripts/ai.py and machine-readable ACTION_REQUIRED responses.'),
      'WORKFLOW_RETRY':('Reduce workflow friction','Inspect top blocked/retry reasons; remove ceremony that does not change quality outcomes.'),
      'UNEXPECTED_RISK_ESCALATION':('Expand risk semantics','Add project risk-map entries or a semantic risk adapter for repeatedly missed mutation surfaces.'),
      'ESCAPED_DEFECT':('Raise local quality rigor','Strengthen acceptance/review only on the surface associated with escaped defects.'),
      'LATER_REWORK':('Reduce change amplification','Inspect hotspot/architecture patterns behind repeated rework before adding global gates.'),
      'OWNER_INTERRUPT':('Tune owner interrupt threshold','Separate reversible auto-decisions from true product/authority decisions and digest them.'),
      'SCOUT_WASTE':('Tighten Scout routing','Disable auto-Scout for work classes with negative total delegated economics.'),
      'GUARD_FALSE_POSITIVE':('Relax noisy guard locally','Use evidence to narrow the specific guard instead of globally disabling protection.'),
      'GUARD_FALSE_NEGATIVE':('Strengthen missing guard','Add a targeted detector or trusted-runtime check for the missed failure mode.'),
      'CONTEXT_REDISCOVERY':('Improve context capsule','Persist only the repeated high-value facts that agents rediscover across sessions.'),
      'REGRESSION_TIMEOUT':('Split regression feedback loop','Move broad matrices to release/nightly and keep default feedback bounded.')}
    title,hyp=mapping.get(kind,(f'Investigate {kind}',f'Review recurring {kind} events and test a bounded policy/runtime change.'))
    return {'event_type':kind,'title':title,'evidence':{'count':count,'share':round(count/max(1,total),3),'pain_score':round(pain,2)},'hypothesis':hyp,'promotion_rule':'Experiment first; owner promotes only after before/after evidence. Never self-edit stable kernel.'}

def load_global(last:int|None=None)->list[dict[str,Any]]:
    if not GLOBAL_REL.is_file(): return []
    out=[]
    for line in GLOBAL_REL.read_text(encoding='utf-8',errors='ignore').splitlines():
        try: out.append(json.loads(line))
        except json.JSONDecodeError: continue
    return out[-last:] if last else out

def _percentile(values:list[float], p:float)->float|None:
    vals=sorted(float(v) for v in values if v is not None)
    if not vals:return None
    idx=max(0,min(len(vals)-1,int((len(vals)-1)*p+0.999999)))
    return vals[idx]

def empirical_budgets(telemetry:list[dict[str,Any]])->list[dict[str,Any]]:
    groups=defaultdict(list)
    for row in telemetry:
        role=str(row.get('role') or '').upper(); wc=str(row.get('work_class') or 'UNCLASSIFIED')
        if role: groups[(role,wc)].append(row)
    out=[]
    for (role,wc),items in sorted(groups.items()):
        if len(items)<5: continue
        inp=[float(r['input_tokens']) for r in items if r.get('input_tokens') is not None]
        outp=[float(r['output_tokens']) for r in items if r.get('output_tokens') is not None]
        wall=[float(r['wall_seconds']) for r in items if r.get('wall_seconds') is not None]
        if not inp and not outp and not wall: continue
        out.append({'role':role,'work_class':wc,'n':len(items),'input_tokens':{'p50':_percentile(inp,.50),'p75':_percentile(inp,.75),'p95':_percentile(inp,.95)},'output_tokens':{'p50':_percentile(outp,.50),'p75':_percentile(outp,.75),'p95':_percentile(outp,.95)},'wall_seconds':{'p50':_percentile(wall,.50),'p75':_percentile(wall,.75),'p95':_percentile(wall,.95)},'recommendation':'Treat p75 as an empirical starting budget plus a bounded safety margin; do not auto-change policy.'})
    return out

def stable_core_promotion_readiness(root:Path, rows:list[dict[str,Any]])->dict[str,Any]:
    try:
        contract=json.loads((root/'config/kernel_contract.json').read_text(encoding='utf-8'))
    except Exception:
        contract={}
    rd=contract.get('release_discipline') or {}
    min_days=int(rd.get('minimum_field_window_days_before_stable_core_promotion') or 14)
    min_projects=int(rd.get('minimum_distinct_projects_recommended') or 3)
    projects={str(r.get('project_id') or '') for r in rows if str(r.get('project_id') or '').strip()}
    times=[]
    for r in rows:
        raw=str(r.get('at') or '')
        try: times.append(datetime.fromisoformat(raw.replace('Z','+00:00')))
        except ValueError: pass
    window_days=((max(times)-min(times)).total_seconds()/86400.0) if len(times)>=2 else 0.0
    enough_days=window_days>=min_days; enough_projects=len(projects)>=min_projects
    return {'status':'ELIGIBLE_FOR_STABLE_CORE_REVIEW' if enough_days and enough_projects else 'FREEZE_STABLE_CORE','observed_field_window_days':round(window_days,2),'distinct_projects':len(projects),'minimum_field_window_days':min_days,'minimum_distinct_projects_recommended':min_projects,'reason':'Field evidence threshold met; human review still required.' if enough_days and enough_projects else 'Collect more real field evidence; tune policy only when justified and avoid stable-core version churn.'}

def report(root:Path,last:int=500,*,global_scope:bool=False)->dict[str,Any]:
    rows=load_global(last) if global_scope else load(root,last); total=len(rows); by=defaultdict(list)
    for r in rows:by[str(r.get('event_type') or 'UNKNOWN')].append(r)
    ranked=[]
    for kind,items in by.items():
        pain=0.0
        for r in items:
            c=r.get('cost') or {}; cost=float(c.get('extra_provider_cost') or 0); wall=float(c.get('extra_wall_seconds') or 0); toks=int(c.get('extra_input_tokens') or 0)+int(c.get('extra_output_tokens') or 0)
            pain+=SEVERITY_WEIGHT.get(str(r.get('severity') or 'LOW'),1.0)*(1.0+min(4.0,cost*10)+min(3.0,wall/300)+min(3.0,toks/10000))
        ranked.append((pain,kind,items))
    ranked.sort(reverse=True)
    telemetry=[] if global_scope else load_telemetry(root); gov_roles={'SCOUT','REVIEWER','ORCHESTRATOR','GOVERNANCE','JUDGE'}
    gov_tokens=sum(int(r.get('input_tokens') or 0)+int(r.get('output_tokens') or 0) for r in telemetry if str(r.get('role') or '').upper() in gov_roles)
    all_tokens=sum(int(r.get('input_tokens') or 0)+int(r.get('output_tokens') or 0) for r in telemetry)
    gov_wall=sum(float(r.get('wall_seconds') or 0) for r in telemetry if str(r.get('role') or '').upper() in gov_roles)
    all_wall=sum(float(r.get('wall_seconds') or 0) for r in telemetry)
    candidates=[_candidate(kind,len(items),total,pain) for pain,kind,items in ranked[:5]]
    scout_economics=[]
    if not global_scope:
        work_classes=sorted({str(r.get('work_class') or '') for r in telemetry if str(r.get('work_class') or '').strip()})
        for wc in work_classes:
            fb=delegation_feedback(root,wc)
            if fb.get('status')=='OBSERVED':
                scout_economics.append(fb)
                if fb.get('verdict')=='NEGATIVE':
                    candidates.append({'event_type':'SCOUT_WASTE','title':'Tighten Scout routing','evidence':fb,'hypothesis':f'Disable or narrow auto-Scout for work_class={wc}; measured delegated economics are negative.','promotion_rule':'Experiment on the affected work class only; compare total cost + cycle time + defect rate before owner promotion.'})
    return {'schema_version':1,'scope':'global' if global_scope else 'project','events':total,'top_pain':[{'event_type':kind,'count':len(items),'pain_score':round(pain,2)} for pain,kind,items in ranked[:10]],'upgrade_candidates':candidates[:8],'scout_economics':scout_economics,'empirical_budgets':[] if global_scope else empirical_budgets(telemetry),'governance_overhead':{'token_ratio':round(gov_tokens/all_tokens,3) if all_tokens else None,'wall_ratio':round(gov_wall/all_wall,3) if all_wall else None,'telemetry_records':len(telemetry)},'stable_core_promotion_readiness':stable_core_promotion_readiness(root,rows),'note':'Candidates and empirical budgets are recommendations only. Stable kernel/policy changes require experiment + owner promotion.'}
