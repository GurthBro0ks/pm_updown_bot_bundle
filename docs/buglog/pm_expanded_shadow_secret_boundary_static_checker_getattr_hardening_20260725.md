# Redacted discovery static-checker `getattr` hardening

## Problem

The reviewed credential boundary was sound, but its AST gate resolved call
targets only when `ast.Call.func` was a direct name or attribute. A disposable
fixture could therefore obtain `builtins.open` through
constant-string `getattr`, assign the callable, and invoke it without
`FILE_OPEN`. The same gap allowed a `getattr`-resolved `os.environ` object to
escape `RAW_ENVIRONMENT_ACCESS`.

Neither pattern was present in the reviewed redacted discovery closure. The
defect was in the checker gate, not the runtime credential implementation.

## Repair

The checker now assigns bounded semantic labels to reviewed module imports,
from-imports, aliases, local classes, exception bindings, simple name
assignments, and target-producing assignments. It resolves literal-string
`getattr` and chained resolvable targets without importing or executing
inspected code.

Resolved labels feed the existing rules for:

- file open and `Path` read methods;
- environment objects and lookups;
- subprocess and shell entrypoints;
- dotenv loaders;
- output functions and logging methods;
- import-time network calls; and
- exception and arbitrary output.

The two new stable rule IDs are:

- `UNRESOLVED_SECURITY_GETATTR`: a non-literal or otherwise unresolved
  `getattr` target on a security-relevant, imported, unknown-wrapper, or
  otherwise non-demonstrably-safe base;
- `DYNAMIC_IMPORT_ACCESS`: a resolved dynamic-import callable.

The original eighteen rule IDs remain unchanged. The checker now reports
twenty rules.

## Exact supported scope

The resolver supports direct and aliased imports for `builtins`, `os`,
`pathlib`, `subprocess`, `sys`, `io`, `importlib`, `dotenv`, logging, and the
network modules already relevant to the gate. It supports reviewed
from-import targets, simple `Name` and annotated-name assignments, assignment
chains, literal-string `getattr`, and chained `getattr` where every stage is
statically labeled. `Path` constructor results retain a path-object label.

Direct environment presence checks are allowed only for the two reviewed
authentication field-name constants. Direct scalar configuration lookup is
allowed only for the four existing non-secret discovery configuration field
names. An environment object obtained through `getattr` is rejected, including
assignment, lookup, subscript, iteration, conversion, or output. This is not an
allowance for either reproduced bypass.

Dynamic `getattr` is accepted only when its base is demonstrably a local-safe
object such as a local class instance or synthetic in-memory collection.
Unknown imported modules, unresolved aliases, unknown wrapper results, and
security-relevant modules or objects fail closed. Constant suspicious target
names also fail closed on unknown imported or unresolved bases.

## Deliberate limitations

This is not a Python interpreter or a universal data-flow proof. Labels are
conservative per-file unions and do not model arbitrary control flow,
destructuring, descriptors, monkey-patching, reflection beyond the enumerated
`getattr` forms, generated code, runtime module mutation, or interprocedural
argument provenance. A harmless constant attribute on a non-security local
object is intentionally accepted.

The gate is purpose-built for the redacted CLI and its reviewed dependency
closure. It reads tracked source text only after rejecting secret-bearing and
symlink scan targets. It never imports inspected modules, evaluates AST nodes,
executes fixture code, or opens paths named by inspected literals.

Passing the checker is one gate. Focused tests, full regression tests, source
review, independent adversarial review, runtime preflight, output validation,
production non-activation checks, and fresh approval for any later live call
remain separately required.

## Compatibility and safety

No redacted discovery, authentication, pagination, taxonomy, stage-count,
output, trading, weather, capture, cron, service, or database runtime code was
changed. All bypass fixtures are disposable synthetic source and are parsed
but never executed.
