# Discover and use available tools

Use the bundled `config/mcporter.json`; it exposes only `search_tools` and
`call_tool`. Never print, persist, or pass `MCPCAT_API_KEY` as a command argument.

## Workflow

1. Turn the user's requested capability into a concise natural-language query.
2. Call `search_tools` with
   `npx --yes mcporter@${mcporter_version} --config config/mcporter.json call ${slug}.search_tools --args '<JSON object>' --output json`.
3. Review the candidates and select only a tool whose purpose and inputs match
   the request. If no result fits, refine the query or explain that no suitable
   tool was found.
4. A search result is not authorization. Before any write, delete, send,
   purchase, publish, or account-changing action, confirm clear user intent.
5. Inspect the live `call_tool` schema, then invoke the selected result with
   `npx --yes mcporter@${mcporter_version} --config config/mcporter.json call ${slug}.call_tool --args '<JSON object>' --output json`.
6. On authentication, connection, schema, or tool errors, stop and report safe
   diagnostics. Do not rewrite the bundled config or start OAuth automatically.

Read [the generated tool reference](references/tools.md) when the current
`search_tools` or `call_tool` input shape is needed.

## Connection

The bundled config uses the generating mcpcat instance by default. Set
`MCPCAT_URL` only when the user intends another compatible instance, and make
`MCPCAT_API_KEY` available in the environment when authentication is required.

${safety}
