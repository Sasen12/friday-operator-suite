"""
Tool registry — imports and registers all tool modules with the MCP server.
Add new tool modules here as you build them.
"""

from friday.tools import browser, desktop, macros, office, shorts, system, utils, web, workflows


def register_all_tools(mcp):
    """Register all tool groups onto the MCP server instance."""
    browser.register(mcp)
    desktop.register(mcp)
    office.register(mcp)
    web.register(mcp)
    system.register(mcp)
    workflows.register(mcp)
    macros.register(mcp)
    shorts.register(mcp)
    utils.register(mcp)
