"""Use cases that combine the core modules. No HTTP, no UI."""

from spc.service.analysis import AnalysisRequest, Outcome, analyze, analyze_detailed

__all__ = ["AnalysisRequest", "Outcome", "analyze", "analyze_detailed"]
