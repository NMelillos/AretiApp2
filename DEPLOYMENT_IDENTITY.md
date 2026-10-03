# Release identity gate

Previously the gate compared only two runtime environment strings:
`RENDER_GIT_COMMIT` and `NOMAD_APPROVED_RELEASE_SHA`. It emitted the same
`RELEASE_IDENTITY_UNVERIFIED` error for a missing variable, malformed value,
or mismatch. Successful build/deployment logs cannot distinguish those cases.
Render normally supplies `RENDER_GIT_COMMIT` at runtime, but that string alone
does not verify the files the application is actually executing.

`deployment_identity.py` derives the actual SHA from the Git commit object.
It verifies the commit's tree objects and every tracked file against the
content-addressed Git blob hashes. Windows CRLF checkout equivalence is accepted
only when normalization produces the committed blob hash. Changed/missing files,
untracked top-level Python modules, symlinks, unsupported tree entries, invalid
evidence, and unavailable identity all fail closed.

The Render build command writes ignored `.deployment_identity.json` containing
the raw public Git commit and tree objects. Runtime verification needs no Git
binary when this evidence exists. If no artifact exists, the retained Git
checkout supplies the same evidence in memory; no runtime artifact is written.
Invalid existing evidence never falls back to another source. A different
nonempty `RENDER_GIT_COMMIT` also blocks verification.

Approval remains exclusively `NOMAD_APPROVED_RELEASE_SHA`. It never supplies
the actual identity. Setup displays actual SHA, approved SHA, and a specific
reason without requiring a PDF or running the financial precheck. This display
does not confer repair readiness or execute any database operation.

A release containing this fix has its own new SHA. Keeping approval set to
`47e02d42d16242e5ec292cbe6e4be03549d3b296` must block that new release. An operator
must independently review and approve the new SHA before Final Precheck can pass.
The fix does not change approval configuration or run Repair.

If the service uses Dashboard commands instead of Blueprint configuration,
the equivalent build command is:

```
pip install -r requirements.txt && python deployment_identity.py
```

Git checkout verification still works without changing that Dashboard command
when Render retains the checkout at runtime.
