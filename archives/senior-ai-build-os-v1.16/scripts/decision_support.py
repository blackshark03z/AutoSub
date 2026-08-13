#!/usr/bin/env python3
"""Goal assumption/decision ledger and owner digest for v1.16."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from goal_support import GOALS_DIR, load_goal
from runtime_support import confined_child

TYPES={'ASSUMPTION','AUTO_DECISION','OWNER_DECISION','RISK_ESCALATION','POLICY_EXCEPTION'}

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')

def _path(root:Path,goal_id:str)->Path:
    p=confined_child(root,GOALS_DIR,goal_id,'goal ID')/'decisions.jsonl'; p.parent.mkdir(parents=True,exist_ok=True); p.touch(exist_ok=True); return p

def record(root:Path,kind:str,text:str,*,confidence:float|None=None,reversibility:str='UNKNOWN',owner_impact:str='LOW',reason:str='')->dict[str,Any]:
    goal=load_goal(root); gid=str(goal.get('goal_id') or 'NONE')
    if gid=='NONE':raise SystemExit('No active Goal for decision ledger')
    kind=kind.upper()
    if kind not in TYPES:raise SystemExit(f'Unsupported decision type: {kind}')
    item={'at':now(),'type':kind,'decision':text.strip(),'confidence':confidence,'reversibility':reversibility.upper(),'owner_impact':owner_impact.upper(),'reason':reason.strip()}
    with _path(root,gid).open('a',encoding='utf-8') as h:h.write(json.dumps(item,ensure_ascii=False,sort_keys=True)+'\n')
    return item

def load(root:Path,goal_id:str|None=None)->list[dict[str,Any]]:
    goal=load_goal(root); gid=goal_id or str(goal.get('goal_id') or 'NONE'); p=_path(root,gid)
    out=[]
    for line in p.read_text(encoding='utf-8',errors='ignore').splitlines():
        try:out.append(json.loads(line))
        except json.JSONDecodeError:continue
    return out

def digest(root:Path,goal_id:str|None=None)->dict[str,Any]:
    rows=load(root,goal_id); counts={}
    for r in rows:counts[r.get('type','UNKNOWN')]=counts.get(r.get('type','UNKNOWN'),0)+1
    attention=[]
    for r in rows:
        conf=r.get('confidence'); impact=str(r.get('owner_impact') or 'LOW').upper(); typ=str(r.get('type') or '')
        if typ in {'POLICY_EXCEPTION','RISK_ESCALATION'} or impact in {'HIGH','CRITICAL'} or (conf is not None and float(conf)<0.70):attention.append(r)
    return {'records':len(rows),'counts':counts,'attention_required':attention[-20:],'recent':rows[-20:]}
