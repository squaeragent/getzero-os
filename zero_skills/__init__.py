"""zero-skills — trading capabilities for AI agents.

Install: pip install zero-skills

Skills:
    RegimeDetector  — classify market regime (trending, ranging, volatile, chaotic)
    ConsensusEngine — multi-indicator consensus for entry signals
    ConvictionSizer — position sizing based on conviction level
    ExitIntelligence — exit signal detection (regime shift, consensus flip, time decay)
    ImmuneProtocol  — continuous position protection (stop verification, replacement)
    NetworkClient   — collective intelligence sync (report trades, receive network weights)
"""

__version__ = "0.1.0"

from zero_skills.regime import RegimeDetector
from zero_skills.consensus import ConsensusEngine
from zero_skills.conviction import ConvictionSizer
from zero_skills.exit_intel import ExitIntelligence
from zero_skills.immune import ImmuneProtocol
from zero_skills.network import NetworkClient

__all__ = [
    "RegimeDetector",
    "ConsensusEngine", 
    "ConvictionSizer",
    "ExitIntelligence",
    "ImmuneProtocol",
    "NetworkClient",
]
