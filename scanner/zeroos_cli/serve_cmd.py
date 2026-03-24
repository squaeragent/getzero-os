"""zeroos serve — start as MCP server for AI agents."""

import click


@click.command()
def serve():
    """Start zeroos as an MCP server (stdio transport)."""
    try:
        from zeroos.mcp.server import mcp
        mcp.run(transport='stdio')
    except ImportError:
        from scanner.zeroos_cli.console import fail, action
        fail('MCP server requires fastmcp. install it:')
        action('pip install "zeroos[mcp]"')
