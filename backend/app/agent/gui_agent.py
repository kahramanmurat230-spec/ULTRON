"""Compatibility exports for the bounded GUI agent.

The implementation lives in app.automation.gui_agent; this module keeps the
Agent-layer import path stable for tests and integrations.
"""
from app.automation.gui_agent import GUIAction, GUIAgent, GUIAutomationMatcher

__all__ = ["GUIAction", "GUIAgent", "GUIAutomationMatcher"]
