#!/usr/bin/env python3
"""Provider-neutral runtime telemetry ingestion and conservative delegation feedback."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

REL=Path('.ai/runtime/usage.jsonl')
REQUIRED={'role'}

def ingest(root:Path, records:list[dict[str,Any]])->int:
    path=root/REL; path.parent.mkdir(parents=True,exist_ok=True); count=0
    with path.open('a',encoding='utf-8') as h:
        for rec in records:
            if not REQUIRED.issubset(rec): raise ValueError('telemetry record requires role')
            normalized={k:rec.get(k) for k in ('goal_id','node_id','task_id','session','role','model','work_class','input_tokens','cached_input_tokens','output_tokens','provider_cost','wall_seconds','first_pass_accepted')}
            normalized['role']=str(normalized['role']).upper(); h.write(json.dumps(normalized,ensure_ascii=False,sort_keys=True)+'\n'); count+=1
    return count

def load(root:Path)->list[dict[str,Any]]:
    path=root/REL
    if not path.is_file(): return []
    out=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        try: out.append(json.loads(line))
        except json.JSONDecodeError: continue
    return out

def summarize(root:Path)->dict[str,Any]:
    rows=load(root); by_role={}
    for r in rows:
        role=r.get('role','UNKNOWN'); g=by_role.setdefault(role,{'n':0,'input_tokens':0,'output_tokens':0,'provider_cost':0.0,'wall_seconds':0.0,'observed_cost_n':0})
        g['n']+=1; g['input_tokens']+=int(r.get('input_tokens') or 0); g['output_tokens']+=int(r.get('output_tokens') or 0); g['wall_seconds']+=float(r.get('wall_seconds') or 0)
        if r.get('provider_cost') is not None: g['provider_cost']+=float(r['provider_cost']); g['observed_cost_n']+=1
    return {'records':len(rows),'by_role':by_role}

def delegation_feedback(root:Path, work_class:str)->dict[str,Any]:
    rows=[r for r in load(root) if str(r.get('work_class') or '')==work_class and r.get('goal_id')]
    scouts=[r for r in rows if r.get('role')=='SCOUT']
    workers=[r for r in rows if r.get('role')=='WORKER' and r.get('input_tokens') is not None]
    scout_by_goal={}
    for r in scouts: scout_by_goal.setdefault(r.get('goal_id'),[]).append(r)
    with_scout=[r for r in workers if r.get('goal_id') in scout_by_goal]
    without=[r for r in workers if r.get('goal_id') not in scout_by_goal]
    if len(with_scout)<3 or len(without)<3:
        return {'status':'INSUFFICIENT_DATA','work_class':work_class,'with_scout_n':len(with_scout),'without_scout_n':len(without)}
    aw=sum(int(r.get('input_tokens') or 0) for r in with_scout)/len(with_scout)
    an=sum(int(r.get('input_tokens') or 0) for r in without)/len(without)
    scout_avg=sum(int(r.get('input_tokens') or 0) for r in scouts)/max(1,len(scouts))
    reduction=(an-aw)/an if an>0 else 0.0

    # If provider cost is observed, compare total delegated cost (Scout + Worker)
    # against direct Worker cost. Missing telemetry stays unknown rather than zero.
    with_cost=[]
    for worker in with_scout:
        gid=worker.get('goal_id'); sr=scout_by_goal.get(gid,[])
        if worker.get('provider_cost') is None or not sr or any(x.get('provider_cost') is None for x in sr): continue
        with_cost.append(float(worker['provider_cost'])+sum(float(x['provider_cost']) for x in sr))
    without_cost=[float(r['provider_cost']) for r in without if r.get('provider_cost') is not None]
    avg_with_cost=(sum(with_cost)/len(with_cost)) if len(with_cost)>=3 else None
    avg_without_cost=(sum(without_cost)/len(without_cost)) if len(without_cost)>=3 else None
    cost_ratio=(avg_with_cost/avg_without_cost) if avg_with_cost is not None and avg_without_cost and avg_without_cost>0 else None

    with_wall=[]
    for worker in with_scout:
        gid=worker.get('goal_id'); sr=scout_by_goal.get(gid,[])
        if worker.get('wall_seconds') is None or not sr or any(x.get('wall_seconds') is None for x in sr): continue
        with_wall.append(float(worker['wall_seconds'])+sum(float(x['wall_seconds']) for x in sr))
    without_wall=[float(r['wall_seconds']) for r in without if r.get('wall_seconds') is not None]
    avg_with_wall=(sum(with_wall)/len(with_wall)) if len(with_wall)>=3 else None
    avg_without_wall=(sum(without_wall)/len(without_wall)) if len(without_wall)>=3 else None
    wall_ratio=(avg_with_wall/avg_without_wall) if avg_with_wall is not None and avg_without_wall and avg_without_wall>0 else None

    verdict='NEUTRAL'
    # Cost is the stronger signal when sufficiently observed. Token saving can still
    # justify Scout when pricing is unavailable, but never if measured total cost
    # regresses materially.
    if cost_ratio is not None:
        if cost_ratio<=0.90 and reduction>=0.15: verdict='POSITIVE'
        elif cost_ratio>=1.20 and reduction<0.20: verdict='NEGATIVE'
        elif reduction>=0.30 and cost_ratio<=1.10: verdict='POSITIVE'
    else:
        if reduction>=0.30: verdict='POSITIVE'
        elif reduction<=0.0: verdict='NEGATIVE'
    return {
      'status':'OBSERVED','verdict':verdict,'work_class':work_class,
      'with_scout_n':len(with_scout),'without_scout_n':len(without),
      'avg_worker_input_with_scout':round(aw,1),'avg_worker_input_without_scout':round(an,1),
      'avg_scout_input':round(scout_avg,1),'worker_input_reduction_ratio':round(reduction,3),
      'avg_total_cost_with_scout':round(avg_with_cost,6) if avg_with_cost is not None else None,
      'avg_worker_cost_without_scout':round(avg_without_cost,6) if avg_without_cost is not None else None,
      'delegated_cost_ratio':round(cost_ratio,3) if cost_ratio is not None else None,
      'avg_sequential_wall_with_scout':round(avg_with_wall,2) if avg_with_wall is not None else None,
      'avg_worker_wall_without_scout':round(avg_without_wall,2) if avg_without_wall is not None else None,
      'sequential_wall_ratio':round(wall_ratio,3) if wall_ratio is not None else None,
    }
