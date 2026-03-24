"""zero terminal design system — Rich-powered console module.

replaces raw ANSI escapes with Rich markup for cross-platform
color support and automatic downgrade (24-bit → 256 → 8).
"""

from rich.console import Console
from rich.theme import Theme

# RGB values match z-tokens.css exactly
ZERO_THEME = Theme({
    "lime": "rgb(200,255,0)",
    "bright": "rgb(255,255,255)",
    "mid": "rgb(224,224,224)",
    "dim": "rgb(119,119,119)",
    "faint": "rgb(68,68,68)",
    "error": "rgb(255,68,68)",
    "warning": "rgb(255,170,0)",
    "success": "rgb(0,255,136)",
    "header": "bold rgb(200,255,0)",
    "command": "rgb(200,255,0)",
})

# singleton console — width capped at 64, no Rich auto-highlight
console = Console(theme=ZERO_THEME, width=64, highlight=False)


def logo():
    """Print the zero logo."""
    console.print("  [header]◆ zero▮[/header]")


def tagline():
    """Print the tagline."""
    console.print("  [dim]the collective intelligence network.[/dim]")


def rule():
    """Print a horizontal rule in faint color."""
    console.print(f"  [faint]{'─' * 40}[/faint]")


def spacer():
    """Print a blank line."""
    console.print()


def section(title: str):
    """Print a section header."""
    console.print(f"  [header]{title}[/header]")


def dots(label: str, value, width: int = 40):
    """Dot-leader: label ............ value"""
    val_str = str(value)
    # strip Rich markup for length calculation
    import re
    clean_label = re.sub(r'\[/?[^\]]*\]', '', label)
    clean_value = re.sub(r'\[/?[^\]]*\]', '', val_str)
    dots_count = width - len(clean_label) - len(clean_value)
    dot_str = '.' * max(dots_count, 3)
    console.print(f"  [dim]{label}[/dim] [faint]{dot_str}[/faint] [bright]{value}[/bright]")


def success(text: str):
    """Print a success line: ✓ text"""
    console.print(f"  [success]✓[/success] [mid]{text}[/mid]")


def fail(text: str):
    """Print a failure line: ✗ text"""
    console.print(f"  [error]✗[/error] [mid]{text}[/mid]")


def warn(text: str):
    """Print a warning line: ⚠ text"""
    console.print(f"  [warning]⚠[/warning] [mid]{text}[/mid]")


def info(text: str):
    """Print an info line: ▸ text"""
    console.print(f"  [dim]▸ {text}[/dim]")


def action(cmd: str, desc: str = ""):
    """Print a command suggestion: $ cmd    description"""
    if desc:
        console.print(f"  [command]$ {cmd}[/command]  [dim]{desc}[/dim]")
    else:
        console.print(f"  [command]$ {cmd}[/command]")


def direction_icon(d: str) -> str:
    """Return a Rich-markup direction arrow."""
    d = d.lower() if isinstance(d, str) else ""
    if d in ("long", "↗"):
        return "[lime]↗[/lime]"
    elif d in ("short", "↘"):
        return "[error]↘[/error]"
    return "[dim]—[/dim]"


def bar(value: float, max_val: float = 10.0, width: int = 30) -> str:
    """Score bar: ██████████░░░░░░░░░░ (bright filled, faint empty)."""
    ratio = max(0.0, min(1.0, value / max_val))
    filled = int(ratio * width)
    empty = width - filled
    return f"[bright]{'█' * filled}[/bright][faint]{'░' * empty}[/faint]"


def score_bar(value: float, max_val: float = 10.0, width: int = 20) -> str:
    """Smaller bar for breakdowns."""
    ratio = max(0.0, min(1.0, value / max_val))
    filled = int(ratio * width)
    empty = width - filled
    return f"[bright]{'█' * filled}[/bright][faint]{'░' * empty}[/faint]"


def pnl(value) -> str:
    """Format P&L with Rich color markup."""
    if isinstance(value, (int, float)):
        sign = "+" if value >= 0 else ""
        tag = "success" if value >= 0 else "error"
        return f"[{tag}]{sign}${abs(value):,.2f}[/{tag}]"
    return "[dim]—[/dim]"


def pnl_pct(value) -> str:
    """Format P&L percentage with Rich color markup."""
    if isinstance(value, (int, float)):
        sign = "+" if value >= 0 else ""
        tag = "success" if value >= 0 else "error"
        return f"[{tag}]{sign}{value:.1f}%[/{tag}]"
    return "[dim]—[/dim]"
