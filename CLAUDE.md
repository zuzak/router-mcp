# router-mcp

MCP server exposing a ZyXEL home router's DAL API to Claude Code instances
on the local network. Access is split into three tiers to limit blast radius.

## Architecture

Three independent MCP/SSE servers run in a single process on separate ports:

| Port | Tier | Tools | Side effects |
|------|------|-------|--------------|
| 8080 | `read` | Status, WAN, DHCP leases, WLAN, DNS, ports | None |
| 8081 | `routine` | DHCP reservations, port forwarding | Config changes; no disruption |
| 8082 | `dangerous` | Reboot, WiFi credentials | Network-wide disruption |

Each port requires an `X-API-Key` header. The keys are independent secrets —
losing one doesn't compromise the others.

The server runs on the Pi node in Kubernetes because it needs LAN access to
`192.168.1.1`. It is not reachable from, or schedulable on, the VPS node.

## Running locally

```bash
pip install -r requirements.txt

export ROUTER_BASE_URL=https://192.168.1.1
export ROUTER_USERNAME=admin
export ROUTER_PASSWORD=yourpassword
export READ_API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ROUTINE_API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export DANGEROUS_API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

python server.py
```

## Deploying to Kubernetes

1. Create the secret (see `k8s/secret.example.yaml` for the full list of keys):

```bash
kubectl -n claude create secret generic router-mcp \
  --from-literal=ROUTER_BASE_URL=https://192.168.1.1 \
  --from-literal=ROUTER_USERNAME=admin \
  --from-literal=ROUTER_PASSWORD=<password> \
  --from-literal=READ_API_KEY=<key> \
  --from-literal=ROUTINE_API_KEY=<key> \
  --from-literal=DANGEROUS_API_KEY=<key>
```

2. Add the ArgoCD Application to the kube repo:

```bash
cp deploy/argocd-app.yaml /path/to/kube/apps/claude/router-mcp.yaml
# commit and push — ArgoCD will sync automatically
```

3. Make the GHCR image public (first push only):
   GitHub → Packages → router-mcp → Package settings → Change visibility → Public

## Claude Code configuration

Add to `~/.claude/settings.json` (or the machine-specific override):

```json
{
  "mcpServers": {
    "router-read": {
      "type": "sse",
      "url": "http://<pi-lan-ip>:8080/sse",
      "headers": { "X-API-Key": "<READ_API_KEY>" }
    },
    "router-routine": {
      "type": "sse",
      "url": "http://<pi-lan-ip>:8081/sse",
      "headers": { "X-API-Key": "<ROUTINE_API_KEY>" }
    },
    "router-dangerous": {
      "type": "sse",
      "url": "http://<pi-lan-ip>:8082/sse",
      "headers": { "X-API-Key": "<DANGEROUS_API_KEY>" }
    }
  }
}
```

Set `router-read` to auto-allow; leave `router-routine` and `router-dangerous`
on `ask` so they prompt before executing.

## Discovering write OIDs

The ZyXEL DAL API exposes objects by OID. The read OIDs in `tools/read.py`
are confirmed. The write OIDs in `tools/routine.py` and `tools/dangerous.py`
are best guesses and need verification:

```python
from router_client import RouterClient
import os, json

c = RouterClient("https://192.168.1.1", "admin", os.environ["ROUTER_PASSWORD"])

# Try candidate OIDs — a 200 JSON response means the OID exists
for oid in ["dhcphost", "dhcpreserve", "portforward", "portfwd", "reboot", "sysreboot"]:
    try:
        print(oid, json.dumps(c.dal_get(oid))[:120])
    except Exception as e:
        print(oid, "ERROR:", e)
```

Update the `_OID_*` constants in `tools/routine.py` and `tools/dangerous.py`
once confirmed, and remove the TODO comments.

## Multi-account support

If the router supports separate user accounts with different permissions,
set the optional per-tier credentials in the k8s Secret:

```
ROUTER_READ_USERNAME / ROUTER_READ_PASSWORD
ROUTER_ROUTINE_USERNAME / ROUTER_ROUTINE_PASSWORD
```

The dangerous tier always uses the admin account (`ROUTER_USERNAME` /
`ROUTER_PASSWORD`). When tier-specific credentials are not set, all tiers
fall back to the admin account with enforcement handled server-side.
