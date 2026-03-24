"""zero terminal design system. used everywhere in CLI output."""


def terminal_width():
    """Get terminal width, default 80."""
    try:
        import shutil
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


class Z:
    """zero terminal colors. used everywhere in CLI output."""

    # 24-bit ANSI escape codes matching z-tokens.css
    LIME = '\033[38;2;200;255;0m'
    BRIGHT = '\033[38;2;255;255;255m'
    MID = '\033[38;2;224;224;224m'
    DIM = '\033[38;2;119;119;119m'
    FAINT = '\033[38;2;68;68;68m'
    RED = '\033[38;2;255;68;68m'
    YELLOW = '\033[38;2;255;170;0m'
    GREEN = '\033[38;2;0;255;136m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

    @staticmethod
    def lime(text):
        return f'{Z.LIME}{text}{Z.RESET}'

    @staticmethod
    def bright(text):
        return f'{Z.BRIGHT}{text}{Z.RESET}'

    @staticmethod
    def mid(text):
        return f'{Z.MID}{text}{Z.RESET}'

    @staticmethod
    def dim(text):
        return f'{Z.DIM}{text}{Z.RESET}'

    @staticmethod
    def faint(text):
        return f'{Z.FAINT}{text}{Z.RESET}'

    @staticmethod
    def red(text):
        return f'{Z.RED}{text}{Z.RESET}'

    @staticmethod
    def yellow(text):
        return f'{Z.YELLOW}{text}{Z.RESET}'

    @staticmethod
    def green(text):
        return f'{Z.GREEN}{text}{Z.RESET}'

    @staticmethod
    def success(text):
        return f'{Z.GREEN}✓{Z.RESET} {Z.MID}{text}{Z.RESET}'

    @staticmethod
    def fail(text):
        return f'{Z.RED}✗{Z.RESET} {Z.MID}{text}{Z.RESET}'

    @staticmethod
    def warn(text):
        return f'{Z.YELLOW}⚠{Z.RESET} {Z.MID}{text}{Z.RESET}'

    @staticmethod
    def info(text):
        return f'{Z.DIM}▸{Z.RESET} {Z.DIM}{text}{Z.RESET}'

    @staticmethod
    def header(text):
        return f'{Z.LIME}{Z.BOLD}{text}{Z.RESET}'

    @staticmethod
    def rule():
        return Z.faint('─' * min(terminal_width(), 60))

    @staticmethod
    def dots(label, value, width=40):
        """Dot-leader: label ............ value"""
        dots_count = width - len(label) - len(str(value))
        dots = '.' * max(dots_count, 3)
        return f'{Z.DIM}{label} {Z.FAINT}{dots}{Z.RESET} {Z.BRIGHT}{value}{Z.RESET}'

    @staticmethod
    def logo():
        return f'{Z.LIME}{Z.BOLD}◆ zero▮{Z.RESET}'

    @staticmethod
    def bar(value, max_val=10.0, width=30):
        """Score bar: ██████████░░░░░░░░░░ (bright filled, faint empty)."""
        ratio = max(0.0, min(1.0, value / max_val))
        filled = int(ratio * width)
        empty = width - filled
        return f'{Z.BRIGHT}{"█" * filled}{Z.FAINT}{"░" * empty}{Z.RESET}'

    @staticmethod
    def bar_small(value, max_val=10.0, width=20):
        """Smaller bar for breakdowns."""
        ratio = max(0.0, min(1.0, value / max_val))
        filled = int(ratio * width)
        empty = width - filled
        return f'{Z.BRIGHT}{"█" * filled}{Z.FAINT}{"░" * empty}{Z.RESET}'

    @staticmethod
    def direction(d):
        """Direction arrow: ↗ long (lime), ↘ short (red), — neutral (dim)."""
        d = d.lower() if isinstance(d, str) else ""
        if d in ("long", "↗"):
            return f'{Z.LIME}↗{Z.RESET}'
        elif d in ("short", "↘"):
            return f'{Z.RED}↘{Z.RESET}'
        return f'{Z.DIM}—{Z.RESET}'

    @staticmethod
    def pnl(value):
        """Format P&L with color."""
        if isinstance(value, (int, float)):
            sign = "+" if value >= 0 else ""
            color = Z.GREEN if value >= 0 else Z.RED
            return f'{color}{sign}${abs(value):,.2f}{Z.RESET}'
        return f'{Z.DIM}—{Z.RESET}'

    @staticmethod
    def pnl_pct(value):
        """Format P&L percentage with color."""
        if isinstance(value, (int, float)):
            sign = "+" if value >= 0 else ""
            color = Z.GREEN if value >= 0 else Z.RED
            return f'{color}{sign}{value:.1f}%{Z.RESET}'
        return f'{Z.DIM}—{Z.RESET}'


# Onboarding messages — print at milestones during agent's first day
ONBOARDING = {
    10: (
        f'\n  {Z.rule()}\n'
        f'  {Z.dim("10 signals evaluated. 10 rejected.")}\n'
        f'  {Z.dim("the machine is being selective. this is normal.")}\n'
        f'  {Z.rule()}\n'
    ),
    50: (
        f'\n  {Z.rule()}\n'
        f'  {Z.dim("50 evaluated. 0 entries.")}\n'
        f'  {Z.dim("the market hasn\'t presented anything good enough.")}\n'
        f'  {Z.dim("that IS the intelligence.")}\n'
        f'  {Z.rule()}\n'
    ),
    100: (
        f'\n  {Z.rule()}\n'
        f'  {Z.dim("100 evaluated. patience is the product.")}\n'
        f'  {Z.dim("when the machine enters, it means something.")}\n'
        f'  {Z.rule()}\n'
    ),
}

# First trade message
FIRST_TRADE = (
    f'\n  {Z.rule()}\n'
    f'  {Z.lime("your agent\'s first trade.")}\n'
    f'  {Z.dim("it evaluated hundreds of signals to find this one.")}\n'
    f'  {Z.dim("the immune system is watching it now.")}\n'
    f'  {Z.rule()}\n'
)
