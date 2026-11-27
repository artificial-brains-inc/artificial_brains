"""Optional standard MCP bridge for Codex, Claude Code, and other MCP clients.

Run with: python -m ab_designer.agent.mcp --project PATH --env PATH/.env
All file and check operations are dispatched by the existing local workspace.
"""
import argparse
import json
from pathlib import Path

import httpx


def main():
    parser=argparse.ArgumentParser(description='Artificial Brains project tools over MCP stdio')
    parser.add_argument('--project',default='.')
    parser.add_argument('--env')
    args=parser.parse_args()
    try:from mcp.server.fastmcp import FastMCP
    except ImportError:raise SystemExit('Install MCP support with: python -m pip install "artificialbrains_sdk[agent-mcp]"')
    from ab_designer.workspace import open_workspace, _directory, _existing
    from .service import TOOLS
    project=Path(args.project).expanduser().resolve()
    env=Path(args.env).expanduser().resolve() if args.env else project/'.env'
    # Workspace informational output must not corrupt the MCP JSON stream.
    import contextlib,sys
    with contextlib.redirect_stdout(sys.stderr):
        open_workspace(project=project,env_path=env,open_browser=False)
    registry=_existing(_directory(project,env))
    if not registry:raise SystemExit('SDK workspace did not start.')
    server=FastMCP('Artificial Brains SDK')

    @server.tool()
    def sdk_tools() -> dict:
        """Get the SDK project tool schemas. Use sdk_reference(prompt) for the complete brain-design instructions."""
        return {'tools':TOOLS,'workflow':'Read SDK reference, write draft files, validate, run configured checks, package. No device build or robot commands.'}

    @server.tool()
    def sdk_tool(name: str, arguments: dict) -> dict:
        """Call one named SDK project tool using the exact argument schema from sdk_tools."""
        with httpx.Client(trust_env=False,timeout=None) as client:
            response=client.post(registry['url']+'api/agent/tool',
                headers={'X-AB-Workspace':registry['token']},json={'name':name,'arguments':arguments})
            data=response.json()
            if response.status_code>=400:raise ValueError(data.get('error','SDK tool failed.'))
            return data

    server.run(transport='stdio')


if __name__=='__main__':main()
