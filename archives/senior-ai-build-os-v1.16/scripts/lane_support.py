#!/usr/bin/env python3
"""Risk/uncertainty-aware routing between FAST, STANDARD and GOAL execution lanes."""
from __future__ import annotations
import re
from typing import Any
from delegation_support import scope_features

UNCERTAINTY=("unknown","investigate","diagnose","root cause","flaky","intermittent","unclear","không rõ","nguyên nhân","điều tra","chẩn đoán")

def recommend_lane(*, outcome:str, acceptance:list[str], modify:str, risk:str="auto", dependency_count:int=0, acceptance_surfaces:int=1, parallel_opportunity:bool=False) -> dict[str,Any]:
    feat=scope_features(modify); text=(outcome+" "+" ".join(acceptance)).casefold(); uncertain=any(x in text for x in UNCERTAINTY) or feat["broad"]
    explicit=feat["explicit_file_count"]; risk=risk.upper()
    reasons=[]
    if dependency_count>0 or parallel_opportunity or acceptance_surfaces>1 or uncertain or feat["broad"]:
        if dependency_count>0: reasons.append("dependency graph exists")
        if parallel_opportunity: reasons.append("independent branches may reduce wall-clock")
        if acceptance_surfaces>1: reasons.append("multiple acceptance surfaces")
        if uncertain: reasons.append("implementation/root-cause uncertainty")
        return {"lane":"GOAL","reasons":reasons,"orchestration":"DAG + cost-aware delegation"}
    if risk in {"R0","R1","AUTO"} and explicit in {1,2} and not feat["has_wildcard"]:
        return {"lane":"FAST","reasons":["small explicit scope and one bounded outcome"],"orchestration":"single Worker"}
    return {"lane":"STANDARD","reasons":["bounded single outcome without DAG benefit"],"orchestration":"single Worker using task kernel"}
