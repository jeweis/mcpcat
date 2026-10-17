# ${display_name}

Use the bundled `config/mcporter.json`; it disables automatic imports and limits
calls to this Skill's tools. Never print, persist, or pass `MCPCAT_API_KEY` as a
command argument.

## Workflow

1. Read [the generated tool reference](references/tools.md) and choose the
   smallest tool that matches the user's request.
2. ${service_step}
3. Before calling a tool, inspect its live schema with
   `npx --yes mcporter@${mcporter_version} --config config/mcporter.json list ${slug}.<tool> --schema`.
4. If no documented tool fits or the live schema has drifted, refresh the live
   list with `npx --yes mcporter@${mcporter_version} --config config/mcporter.json list ${slug} --brief`.
5. Call only the tool authorized by the user's request with
   `npx --yes mcporter@${mcporter_version} --config config/mcporter.json call ${slug}.<tool> --args '<JSON object>' --output json`.
6. On authentication, connection, or schema errors, stop and report safe
   diagnostics. Do not rewrite the bundled config or start OAuth automatically.

## Connection

The bundled config uses the generating mcpcat instance by default. Set
`MCPCAT_URL` only when the user intends another compatible instance, and make
`MCPCAT_API_KEY` available in the environment when authentication is required.

${safety}
