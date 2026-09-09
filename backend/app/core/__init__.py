"""Core package compatibility hooks."""
from __future__ import annotations

def _install_local_multi_model_order_fix():
    from .local_multi_model import LocalMultiModel
    if getattr(LocalMultiModel, "_ultron_order_fix", False): return
    def race_and_judge(self, models, messages, **kwargs):
        raw=self.race(models,messages,temperature=kwargs.get("temperature",.2),max_tokens=kwargs.get("max_tokens",2048))
        ranked=self.score_results(raw,kwargs.get("task","")); successful=[r for r in ranked if r.ok and r.content]
        if not successful:return None,raw
        mq=float(kwargs.get("min_quality_score",0.0)); eligible=[r for r in successful if r.score>=mq] if mq>0 else successful
        if not eligible:return None,raw
        leader=eligible[0]; runner=eligible[1] if len(eligible)>1 else None; jm=kwargs.get("judge_model"); delta=float(kwargs.get("liquid_min_delta",8.0))
        if not jm or len(eligible)==1 or (runner and leader.score-runner.score>=delta):return leader,raw
        chosen=self.judge(eligible,judge_model=jm,system=kwargs.get("system",""),temperature=min(kwargs.get("temperature",.2),.15),max_tokens=kwargs.get("max_tokens",2048))
        return (chosen or leader),raw
    LocalMultiModel.race_and_judge=race_and_judge; LocalMultiModel._ultron_order_fix=True
_install_local_multi_model_order_fix()
