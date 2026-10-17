# ${display_name}

Use the tools on the MCP connection that supplied this Skill. The Host may
label or namespace tools differently; match the source connection and actual
tool names rather than guessing a Host prefix. The Host handles this
connection's authentication.

## Workflow

1. Read [the tool reference](references/tools.md) when choosing among the
   documented capabilities. It is a snapshot, not a substitute for live tools.
2. ${service_step}
3. Inspect the live schema of the selected tool on this connection. If it is
   missing or no longer fits, report the limitation instead of substituting
   another server's tool with a similar name.
4. Call it through the Host's MCP tool interface using the user's authorized
   arguments; use the returned data to answer the request.
5. On authentication, connection, or schema errors, stop and report safe
   diagnostics. Do not obtain new credentials or connect to another source
   without the user's approval.

Read supporting files using their Resources URIs relative to this Skill's
root. A read does not execute a file or grant permission to perform an action.
If the Host cannot load remote Skills, use the separately reviewed local
variant through ZIP/CLI installation in a capable local Agent environment.

${safety}
