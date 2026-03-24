"""zero onboarding messages — print at milestones during agent's first day."""

from scanner.zeroos_cli.console import console, rule


MILESTONES = {
    10: [
        "10 signals evaluated. 10 rejected.",
        "the machine is being selective. this is normal.",
    ],
    50: [
        "50 evaluated. 0 entries.",
        "the market hasn't presented anything good enough.",
        "that IS the intelligence.",
    ],
    100: [
        "100 evaluated. patience is the product.",
        "when the machine enters, it means something.",
    ],
}


def check_milestone(count: int):
    """Print an onboarding message if count matches a milestone."""
    lines = MILESTONES.get(count)
    if not lines:
        return
    console.print()
    rule()
    for line in lines:
        console.print(f"  [dim]{line}[/dim]")
    rule()
    console.print()


def first_trade_message():
    """Print the first-trade celebration message."""
    console.print()
    rule()
    console.print("  [lime]your agent's first trade.[/lime]")
    console.print("  [dim]it evaluated hundreds of signals to find this one.[/dim]")
    console.print("  [dim]the immune system is watching it now.[/dim]")
    rule()
    console.print()
