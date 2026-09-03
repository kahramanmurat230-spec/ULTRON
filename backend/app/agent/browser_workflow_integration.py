"""Small integration seam for the deterministic browser planner."""
from app.agent.browser_workflow import parse_browser_plan, as_hybrid_plan


def browser_plan(text: str):
    return as_hybrid_plan(parse_browser_plan(text), text)
