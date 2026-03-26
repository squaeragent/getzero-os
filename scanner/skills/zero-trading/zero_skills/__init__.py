"""
zero-skills — Trading agent skills for the zero platform.

Install skills for Claude Code, OpenClaw, or any MCP-compatible agent:

    pip install zero-skills

Skills are automatically placed in the correct directory for your agent.
For manual installation, copy the skill folders to your agent's skills directory.

Usage with Claude Code:
    cp -r $(python -c "import zero_skills; print(zero_skills.SKILLS_DIR)")/* .claude/skills/zero-trading/

Usage with OpenClaw:
    cp -r $(python -c "import zero_skills; print(zero_skills.SKILLS_DIR)")/* ~/.openclaw/workspace/skills/zero-trading/
"""

__version__ = "1.0.0"

import os
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")


def get_skill_path(skill_name: str = "") -> str:
    """Get the path to a skill folder or the root skills directory."""
    if skill_name:
        return os.path.join(SKILLS_DIR, skill_name)
    return SKILLS_DIR


def list_skills() -> list[str]:
    """List all available skill names."""
    if not os.path.exists(SKILLS_DIR):
        return []
    return [
        d for d in os.listdir(SKILLS_DIR)
        if os.path.isdir(os.path.join(SKILLS_DIR, d))
    ]


def install_to(target_dir: str) -> int:
    """Copy all skills to a target directory. Returns number of skills installed."""
    import shutil
    os.makedirs(target_dir, exist_ok=True)
    
    # Copy master SKILL.md
    master = os.path.join(SKILLS_DIR, "SKILL.md")
    if os.path.exists(master):
        shutil.copy2(master, os.path.join(target_dir, "SKILL.md"))
    
    count = 0
    for skill in list_skills():
        src = os.path.join(SKILLS_DIR, skill)
        dst = os.path.join(target_dir, skill)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        count += 1
    
    return count
