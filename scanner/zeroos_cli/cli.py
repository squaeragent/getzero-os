"""Main click group for ZERO OS CLI."""

import click

from scanner.zeroos_cli import __version__
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


@click.group()
@click.version_option(version=__version__, prog_name="zeroos")
def cli():
    """ZERO OS — The operating system for trading agents."""
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


if __name__ == "__main__":
    cli()
