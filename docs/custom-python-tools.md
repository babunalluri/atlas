# Source-controlled custom Python tools

Custom Python tools are trusted application code. Tenants can select a registered
tool and provide reviewed settings or credentials, but they cannot upload source,
choose a module path, install a package, or execute a command.

## Add an implementation

1. Add a module under `apps/backend/src/app/tools/custom/`.
2. Define a Pydantic settings model. Only fields in this model can come from a
   tool definition.
3. Write a builder that accepts `CustomToolContext` and returns named callables.
   Use `context.client` for outbound requests so HTTPS, host allowlisting, DNS/IP
   checks, timeouts, redirects, and response limits remain enforced.
4. Declare every callable in a `CustomToolSpec`. Mark mutating capabilities with
   `mutating=True`; the runtime forces human approval for them.
5. Import the spec explicitly in `custom/catalog.py` and add it to
   `CUSTOM_TOOL_SPECS`.
6. Add any reviewed package dependency to `pyproject.toml`, add required public
   API hosts to `ALLOWED_OUTBOUND_HOSTS`, and rebuild the backend image.

`signed_rest.py` is the reference implementation. It demonstrates:

- a required tenant-owned `rest_api` credential;
- Pydantic-validated base URL, paths, header name, and prefix;
- a read function and an approval-gated write function;
- all network calls through `SafeRestClient`.

## Runtime guarantees

- Database rows contain only a registry key, never an import path.
- Unknown registry keys and unknown settings are rejected.
- Credential values are decrypted only while building a tenant runtime and are
  never returned by an API.
- A credential must match the provider declared by the source-controlled spec.
- Registered URL fields must pass the same SSRF checks as HTTP/OpenAPI tools.
- Function names and capability declarations must match before the tool loads.
- Mutating capabilities cannot opt out of confirmation.

These controls do not sandbox trusted repository code. A reviewed custom module
could bypass the framework if it directly imported unrestricted networking or
process APIs. Code review and CI security checks remain required for changes
under `app/tools/custom/`.
