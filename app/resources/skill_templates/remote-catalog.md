# Discover and use available tools

Use the connected Catalog's `search_tools` and `call_tool` via the Host's MCP
tool interface. Match the originating connection; do not guess Host prefixes
or connect to additional servers. Authentication is managed by the Host.

## Workflow

1. Translate the user's task into a focused capability query.
2. Inspect the live `search_tools` schema, then search with the appropriate
   query/pattern field. [The tool reference](references/tools.md) documents the
   generation snapshot and is useful only when those input shapes are needed.
3. Choose a matching result using its purpose, inputs and returned source
   identity. Refine the search or explain a missing capability if none fits.
4. A search result is not authorization. Confirm user intent before invoking
   a tool that writes, deletes, sends, publishes, purchases or changes accounts.
5. Inspect the live `call_tool` schema, then pass the selected result's name
   and authorized arguments through this Catalog connection.
6. Report authentication, connection, schema or tool errors. Do not retry a
   side effect blindly, bypass Catalog restrictions or use an unapproved source.

Supporting references are available through Resources under this Skill root.
This workflow calls MCP tools; it does not execute Skill attachments on the
server. Hosts without remote Skill support should use the separately reviewed
local ZIP/CLI variant in a capable local Agent environment.

${safety}
