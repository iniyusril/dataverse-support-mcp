"""End-to-end smoke test: start the server as a subprocess (the way Hermes does) and talk MCP to it."""

import asyncio
import os
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "list_new_emails", "get_email", "find_contact", "create_contact",
    "search_knowledge", "create_reply", "escalate_to_human", "mark_processed",
}
ENV = {
    "PATH": os.environ.get("PATH", ""),
    "DV_URL": "https://org.crm5.dynamics.com",
    "DV_TENANT_ID": "t",
    "DV_CLIENT_ID": "c",
    "DV_CLIENT_SECRET": "s",
}


def test_server_registers_all_tools_and_reports_tool_errors_cleanly():
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-m", "dataverse_support_mcp"], env=ENV)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                bad = await session.call_tool("get_email", {"email_id": "not-a-guid"})
                return init, {t.name for t in tools.tools}, bad

    init, names, bad = asyncio.run(scenario())
    assert init.instructions and "untrusted" in init.instructions
    assert names == EXPECTED_TOOLS
    # mcp 1.x calls the flag isError, mcp 2.x is_error
    is_error = getattr(bad, "is_error", None) or getattr(bad, "isError", None)
    assert is_error
    # the model must be able to read *why* a call failed (mcp 2.x hides ordinary exceptions)
    assert "must be a GUID" in bad.content[0].text


def test_server_exits_with_code_2_and_clear_message_when_not_configured():
    proc = subprocess.run(
        [sys.executable, "-m", "dataverse_support_mcp"],
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert "DV_URL" in proc.stderr and proc.stdout == ""
