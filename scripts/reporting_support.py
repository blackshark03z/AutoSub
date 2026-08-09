#!/usr/bin/env python3
"""Operational quality/cost/codebase reporting kept separate from lifecycle mutation."""
from __future__ import annotations
import csv, json, re, statistics
from pathlib import Path
from typing import Any
from state_runtime import load_history
from telemetry_support import summarize as telemetry_summarize
from health_support import report as health_report

GOALS_DIR=Path('.ai/goals')

def _docs_or_test_only_path(path:str)->bool:
    p=path.replace('\\','/').casefold(); name=p.rsplit('/',1)[-1]
    if p.startswith(('docs/','doc/','tests/','test/')) or '/__tests__/' in f'/{p}':return True
    if name.startswith(('readme','license','changelog','contributing')):return True
    if name.endswith(('.md','.mdx','.rst','.txt')):return True
    return bool(re.search(r'(^|/)(test_[^/]+\.py|[^/]+_(?:test|tests)\.py|[^/]+\.(?:test|spec)\.[^/]+)$',p))

def classify_surface(paths:list[str])->str:
    joined='\n'.join(path.casefold().replace('\\','/') for path in paths)
    buckets=[('auth/security',('auth','security','permission','access','identity')),('finance',('payment','billing','refund','charge','balance','price','amount','wallet','invoice')),('data/persistence',('db/','database','schema','migration','persistence','repository','models/')),('api/integration',('api/','routes/','controller','webhook','integration','client/')),('background',('worker','queue','job','scheduler','cron')),('ui',('ui/','components/','pages/','views/','frontend','app/')),('infra/release',('infra/','terraform','deploy','k8s','docker','.github/')),('shared/core',('shared/','core/','common/','lib/'))]
    for name,needles in buckets:
        if any(n in joined for n in needles):return name
    if paths and all(_docs_or_test_only_path(p) for p in paths):return 'docs/tests'
    return 'other'

def history_task_surface(root:Path,record:dict[str,Any])->str:
    ev=str(record.get('evidence_bundle') or '')
    if ev:
        p=root/ev/'manifest.json'
        try:return classify_surface([str(x) for x in json.loads(p.read_text(encoding='utf-8')).get('task_delta_files',[])])
        except (OSError,json.JSONDecodeError):pass
    return 'unknown'

def print_quality_segment(title:str,groups:dict[str,list[dict[str,str]]])->None:
    print(title)
    for key,rows in sorted(groups.items(),key=lambda item:(-len(item[1]),item[0])):
        fp=[r for r in rows if r.get('first_pass_accepted') in {'yes','no'}]; rw=[r for r in rows if r.get('later_rework') in {'yes','no'}]; de=[r for r in rows if r.get('escaped_defect') in {'yes','no'}]
        fp_text=f"{sum(r['first_pass_accepted']=='yes' for r in fp)}/{len(fp)}" if fp else 'unknown'; rw_text=f"{sum(r['later_rework']=='yes' for r in rw)}/{len(rw)}" if rw else 'unreconciled'; de_text=f"{sum(r['escaped_defect']=='yes' for r in de)}/{len(de)}" if de else 'unreconciled'
        cost=sum(float(r.get('estimated_ai_cost') or 0)+float(r.get('provider_cost') or 0) for r in rows); print(f'  {key}: n={len(rows)} first_pass={fp_text} rework={rw_text} defects={de_text} cost={cost:.6f}')

def report(root:Path,last:int)->None:
    with (root/'.ai/COST_LEDGER.csv').open(encoding='utf-8',newline='') as h: rows=list(csv.DictReader(h))[-last:]
    if not rows:print('No task ledger data yet.')
    accepted=[r for r in rows if r['accepted']=='yes']; cycles=[float(r['cycle_minutes']) for r in accepted if r['cycle_minutes']]; first=[r for r in accepted if r['first_pass_accepted'] in {'yes','no'}]; rework=[r for r in accepted if r['later_rework'] in {'yes','no'}]; defects=[r for r in accepted if r['escaped_defect'] in {'yes','no'}]
    ai=sum(float(r['estimated_ai_cost'] or 0) for r in accepted); pc=sum(float(r['provider_cost'] or 0) for r in accepted)
    print(f'Accepted outcomes: {len(accepted)}'); print(f'Median cycle minutes: {statistics.median(cycles):.2f}' if cycles else 'Median cycle minutes: unknown'); print(f"First-pass acceptance: {sum(r['first_pass_accepted']=='yes' for r in first)}/{len(first)}" if first else 'First-pass acceptance: unknown'); print(f"Later rework: {sum(r['later_rework']=='yes' for r in rework)}/{len(rework)}" if rework else 'Later rework: unreconciled'); print(f"Escaped defects: {sum(r['escaped_defect']=='yes' for r in defects)}/{len(defects)}" if defects else 'Escaped defects: unreconciled'); print(f'Recorded AI/provider cost: {ai:.6f} + {pc:.6f}')
    keys={(r.get('task_id'),int(r.get('task_revision') or 0)) for r in accepted}; history=[x for x in load_history(root) if (x.get('task_id'),int(x.get('task_revision') or 0)) in keys]; overrides=[x for x in history if x.get('breaker_override') is True]; print(f'Shipping breaker overrides: {len(overrides)}/{len(accepted)}')
    by_risk={}; [by_risk.setdefault(r.get('risk_tier') or 'unknown',[]).append(r) for r in accepted]
    if by_risk: print_quality_segment('Quality by risk:',by_risk)
    hist={(x.get('task_id'),int(x.get('task_revision') or 0)):x for x in history}; by_surface={}
    for r in accepted: by_surface.setdefault(history_task_surface(root,hist.get((r.get('task_id'),int(r.get('task_revision') or 0)),{})),[]).append(r)
    if by_surface:print_quality_segment('Quality by surface:',by_surface)
    goals=[]
    for p in sorted((root/GOALS_DIR).glob('G-*/result.json'))[-last:]:
        try:goals.append(json.loads(p.read_text(encoding='utf-8')))
        except (OSError,json.JSONDecodeError):pass
    if goals:
        fp=[g for g in goals if isinstance(g.get('goal_first_pass_accepted'),bool)]; gc=[g for g in goals if int((g.get('metrics') or {}).get('cost_telemetry_rows') or 0)>0 or (g.get('metrics') or {}).get('delegation_provider_cost') is not None]; total=sum(float((g.get('metrics') or {}).get('estimated_ai_cost') or 0)+float((g.get('metrics') or {}).get('provider_cost') or 0)+float((g.get('metrics') or {}).get('delegation_provider_cost') or 0) for g in gc); cyc=[float((g.get('metrics') or {}).get('goal_cycle_minutes')) for g in goals if (g.get('metrics') or {}).get('goal_cycle_minutes') is not None]
        print(f'Accepted Goals: {len(goals)}'); print(f"Goal first-pass acceptance: {sum(bool(g.get('goal_first_pass_accepted')) for g in fp)}/{len(fp)}" if fp else 'Goal first-pass acceptance: unknown'); print(f'Median Goal cycle minutes: {statistics.median(cyc):.2f}' if cyc else 'Median Goal cycle minutes: unknown'); print(f'Cost / accepted Goal (observed n={len(gc)}): {total/len(gc):.6f}' if gc else 'Cost / accepted Goal: unmeasured')
    tel=telemetry_summarize(root); print(f"Runtime telemetry records: {tel.get('records',0)}")
    health=health_report(root); hs=health.get('snapshot',{}); print(f"Codebase health: source_loc={hs.get('source_loc',0)} source_files={hs.get('source_files',0)} runtime_deps={hs.get('runtime_dependencies',0)} tracked_mb={hs.get('tracked_repo_mb',0)} git_mb={hs.get('git_repo_mb')} hotspots={len(health.get('hotspots',[]))}")
    for w in (health.get('ratchet',{}) or {}).get('warnings',[])[:5]:print('Health recommendation:',w)
    for surface,rs in by_surface.items():
        reconciled=[r for r in rs if r.get('later_rework') in {'yes','no'} and r.get('escaped_defect') in {'yes','no'}]
        if len(reconciled)>=5:
            bad=sum(r['later_rework']=='yes' or r['escaped_defect']=='yes' for r in reconciled)
            if bad/len(reconciled)>=.20:print(f'Recommendation: {surface} has {bad}/{len(reconciled)} reconciled outcomes with rework/defect; raise rigor locally, not globally.')
    if len(first)>=3 and sum(r['first_pass_accepted']=='yes' for r in first)/len(first)<.6:print('Recommendation: acceptance criteria or focused checks are too weak; strengthen preflight before adding broader gates.')
    r1=[r for r in accepted if r['risk_tier'] in {'R0','R1'}]
    if r1 and sum(int(r['integration_test_runs'] or 0) for r in r1)>len(r1):print('Recommendation: Fast Lane is over-testing; make integration trigger-based for R0/R1.')
    if accepted and len(overrides)/len(accepted)>.20:print('Recommendation: shipping-breaker overrides exceed 20% of recent accepted work; inspect threshold/policy abuse.')
    unreconciled=sum(r['later_rework']=='unknown' or r['escaped_defect']=='unknown' for r in accepted)
    if unreconciled:print(f'Recommendation: reconcile {unreconciled} accepted outcome(s) after operational use.')
