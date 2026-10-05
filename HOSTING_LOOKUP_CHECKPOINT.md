# Hosting lookup: recorded identity, unresolved current access

Read-only lookup performed 2026-10-05T08:32:36Z. User-confirmed references: production URL `https://aretiapp.onrender.com/`, repository `NMelillos/AretiApp2`, previous Render service name `aretiapp`. These are lookup references, not proof of current hosting ownership or code.

## Exact existing deployment evidence

The preserved local deployment receipt was reread and a fresh authenticated GitHub API lookup of the repository's ten most recent deployment records confirmed:

- GitHub deployment ID: `6830426793`.
- Environment: `main - aretiapp`.
- Recorded successful SHA: `f3ea9ce2d0143ef3239871bb94791ffb39fd857e`.
- Recorded success time: `2026-10-03T16:57:30Z`.
- Environment URL: `https://aretiapp.onrender.com`.
- Exact recorded Render deployment dashboard: `https://dashboard.render.com/web/srv-d7aj84khg0os73b4g0i0/deploys/dep-db0j78mq1p3s73edtobg`.
- Service ID extracted from that recorded URL: **`srv-d7aj84khg0os73b4g0i0`**.
- Service-dashboard path from the same URL: `https://dashboard.render.com/web/srv-d7aj84khg0os73b4g0i0`.
- Render deploy ID extracted from the same record: `dep-db0j78mq1p3s73edtobg`.

Multiple preceding repository deployments also record that exact service ID and environment URL. No service ID was guessed and no replacement service was created. The newest fetched GitHub record is historical evidence, not an observation of the running service or its current rollback controls. It cannot exclude manual redeployments or other later hosting changes.

Fresh private receipt: `E:/AretiCodex/_runtime/master-continuation-20261005/hosting-record-lookup-20261005.json`. Original receipt `deployment-f3ea9ce2d0143ef3239871bb94791ffb39fd857e.json` remains intact. GitHub credentials were consumed privately through the existing Git credential manager; no credential values were printed or stored in the receipt.

## Actual connector result

`list_workspaces()` exposes only the already user-confirmed `My Workspace`, `tea-d8nphahkh4rs73fesktg`. This is the connector's accessible workspace inventory, not proof that it owns the recorded service.

Within that confirmed workspace:

- `get_service(serviceId=srv-d7aj84khg0os73b4g0i0)` returns `404: not found: service`.
- `get_deploy(serviceId=srv-d7aj84khg0os73b4g0i0, deployId=dep-db0j78mq1p3s73edtobg)` returns `404: not found: deploy ... for service`.
- The immediately preceding service enumeration, including previews, returned `null`.

These results do not establish whether the service moved, was removed, or is inaccessible to this connector identity. Current owning workspace, service access, live SHA, logs and practical rollback remain **UNVERIFIED**. Do not describe the historical SHA as verified live code or interpret 404 as proof of deletion.

## Resumption boundary

Deployment remains HELD. No push, merge, deployment, repair, schema change, reimport, taxonomy change or safeguard removal occurred. Reduced application/test bytes at `034e7b137c05388fd9c2e4f71ed436f0923f24a7` remain unchanged; full candidate/handover `eb46d0ee2c263ab3d3e3108a398d3ecac1c39a8e` remains preserved.

The next hosting step requires the Render account/workspace that can actually open the recorded service and connector access to it. If a different workspace becomes accessible, obtain its explicit workspace confirmation before using it. Then read the actual service and current live deployment, verify repository/branch/auto-deploy identity, practical rollback and inherited startup behavior. Do not push or merge to discover the deployment target. Existing regression, performance, acceptance and release-verification gates remain independently open; no unchanged QA was rerun for this lookup and interim scope approval is not requested again.
