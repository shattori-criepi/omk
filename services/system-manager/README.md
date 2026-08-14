# OMK System Manager

`system-manager` is a host-side FastAPI service for rotating B-route
credentials. It runs as the OMK user, writes only
`broute-meter/config/credentials.yaml`, and invokes only the two sudoers-
approved `systemctl` commands for `omk-broute-meter.service`.

Install it on the OMK host from the repository root:

```bash
./scripts/setup-system-manager.sh
```

The installer creates `/etc/omk/system-manager.env` as `root:root`, mode
`0600`. It contains `OMK_SYSTEM_MANAGER_TOKEN`; do not print, commit, or copy
that token into a browser client. The API requires
`Authorization: Bearer <token>` for all B-route endpoints.

Endpoints:

- `GET /api/broute/credentials/status` returns only a masked ID and whether a
  password is configured.
- `PUT /api/broute/credentials` accepts a 32-byte printable-ASCII ID and a
  12-byte printable-ASCII password, atomically saves a mode-`0600` YAML file,
  then restarts and verifies the B-route service.

Run tests:

```bash
cd services/system-manager
pytest -q
```
