"""main click group for zero os CLI."""

import click

from scanner.zeroos_cli import __version__
from scanner.zeroos_cli.console import console, logo, spacer, rule, section
from scanner.zeroos_cli.init_cmd import init_cmd
from scanner.zeroos_cli.start_cmd import start
from scanner.zeroos_cli.stop_cmd import stop
from scanner.zeroos_cli.status_cmd import status
from scanner.zeroos_cli.config_cmd import config
from scanner.zeroos_cli.dashboard_cmd import dashboard
from scanner.zeroos_cli.logs_cmd import logs
from scanner.zeroos_cli.emergency_cmd import emergency_close
from scanner.zeroos_cli.score_cmd import score
from scanner.zeroos_cli.agent_cmd import agent
from scanner.zeroos_cli.evaluate_cmd import evaluate
from scanner.zeroos_cli.brief_cmd import brief
from scanner.zeroos_cli.invite_cmd import invite
from scanner.zeroos_cli.fees_cmd import fees
from scanner.zeroos_cli.weights_cmd import weights
from scanner.zeroos_cli.backtest_cmd import backtest
from scanner.zeroos_cli.observe_cmd import observe
from scanner.zeroos_cli.discoveries_cmd import discoveries
from scanner.zeroos_cli.proof_cmd import proof
from scanner.zeroos_cli.simulate_cmd import simulate
from scanner.zeroos_cli.conviction_cmd import conviction
from scanner.zeroos_cli.suggest_cmd import suggest
from scanner.zeroos_cli.think_cmd import think
from scanner.zeroos_cli.replay_cmd import replay
from scanner.zeroos_cli.race_cmd import race
from scanner.zeroos_cli.feedback_cmd import feedback


class ZeroGroup(click.Group):
    """Custom group with redesigned help output."""

    def format_help(self, ctx, formatter):
        spacer()
        console.print("  [header]◆ zero▮[/header] [mid]commands[/mid]")
        spacer()
        rule()
        spacer()
        section("CORE")
        console.print("  [lime]zeroos init[/lime]             [dim]set up zero on this machine[/dim]")
        console.print("  [lime]zeroos start[/lime]            [dim]boot the os and start agents[/dim]")
        console.print("  [lime]zeroos stop[/lime]             [dim]clean shutdown[/dim]")
        console.print("  [lime]zeroos status[/lime]           [dim]system and agent health[/dim]")
        spacer()
        section("INTELLIGENCE")
        console.print("  [lime]zeroos evaluate \\[COIN][/lime]  [dim]see the reasoning engine think[/dim]")
        console.print("  [lime]zeroos brief[/lime]            [dim]morning brief (daily summary)[/dim]")
        console.print("  [lime]zeroos score[/lime]            [dim]your zero score + breakdown[/dim]")
        console.print("  [lime]zeroos observe[/lime]          [dim]what the network is seeing[/dim]")
        console.print("  [lime]zeroos think \\[COIN][/lime]     [dim]live reasoning stream[/dim]")
        spacer()
        section("AGENTS")
        console.print("  [lime]zeroos agent add[/lime]        [dim]add another agent[/dim]")
        console.print("  [lime]zeroos agent list[/lime]       [dim]view running agents[/dim]")
        console.print("  [lime]zeroos agent pause[/lime]      [dim]pause an agent[/dim]")
        console.print("  [lime]zeroos agent resume[/lime]     [dim]resume an agent[/dim]")
        spacer()
        section("NETWORK")
        console.print("  [lime]zeroos invite[/lime]           [dim]generate referral code[/dim]")
        console.print("  [lime]zeroos weights[/lime]          [dim]view current collective weights[/dim]")
        console.print("  [lime]zeroos fees[/lime]             [dim]view fee history[/dim]")
        console.print("  [lime]zeroos conviction[/lime]       [dim]computed conviction index[/dim]")
        spacer()
        rule()
        spacer()
        console.print("  [dim]docs: getzero.dev/docs[/dim]")
        console.print("  [dim]support: t.me/zero_operators[/dim]")
        spacer()


@click.group(cls=ZeroGroup)
@click.version_option(version=__version__, prog_name="zeroos")
def cli():
    """zero os — the collective intelligence network."""
    pass


cli.add_command(init_cmd, "init")
cli.add_command(start)
cli.add_command(stop)
cli.add_command(status)
cli.add_command(config)
cli.add_command(dashboard)
cli.add_command(logs)
cli.add_command(emergency_close, "emergency-close")
cli.add_command(score)
cli.add_command(agent)
cli.add_command(evaluate)
cli.add_command(brief)
cli.add_command(invite)
cli.add_command(fees)
cli.add_command(weights)
cli.add_command(backtest)
cli.add_command(observe)
cli.add_command(discoveries)
cli.add_command(proof)
cli.add_command(simulate)
cli.add_command(conviction)
cli.add_command(suggest)
cli.add_command(think)
cli.add_command(replay)
cli.add_command(race)
cli.add_command(feedback)


# Track operator behavior on every CLI invocation
_TRACKABLE = {
    "status": "cli_status", "think": "cli_think", "score": "cli_score",
    "brief": "brief_read", "evaluate": "cli_status",
}


def _track_invocation():
    import sys
    try:
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        event = _TRACKABLE.get(cmd)
        if event:
            from hidden_gems import track_event
            track_event(event, {"command": cmd})
    except Exception:
        pass


def _check_update():
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            "https://pypi.org/pypi/zeroos/json",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read())
            latest = data.get("info", {}).get("version", __version__)
            if latest != __version__:
                spacer()
                console.print(f"  [dim]zeroos {latest} available. you're on {__version__}.[/dim]")
                console.print("  [dim]upgrade:[/dim] [lime]pip install --upgrade zeroos[/lime]")
                spacer()
    except Exception:
        pass


if __name__ == "__main__":
    _track_invocation()
    cli()
    _check_update()
