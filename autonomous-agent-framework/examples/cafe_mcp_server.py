from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


server = FastMCP("sample-cafe")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_contact() -> dict[str, str]:
    """Return the sample café's contact email address."""
    return {"email": "cafe@example.com"}


@server.resource("help://sample-cafe/contact")
def contact_help() -> str:
    return "Call get_contact without arguments to retrieve the sample café's contact email address."


if __name__ == "__main__":
    server.run(transport="stdio")
