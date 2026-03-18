# AIP Playground — External Enforcement with aip-go

This document covers deployment patterns beyond the basic walkthrough in
[tutorials.md](tutorials.md#tutorial-6--external-enforcement-with-aip-go).

For the step-by-step quickstart (build, policy, Cursor, Claude Desktop, CLI testing),
start with **Tutorial 6** in `tutorials.md`.

---

## Architecture

```
AI Client (Cursor / Claude Desktop / CLI)
  │  stdio JSON-RPC
  ▼
aip-go proxy                ← Layer 2: policy enforcement (Go)
  │  stdio JSON-RPC
  ▼
mcp_server_plain.py         ← data only, zero enforcement
  │
  ▼
data.py                     ← in-memory email store
```

`mcp_server_plain.py` accepts `user_id` directly — no AAT, no token validation, no role
checks. It is the correct target for an external enforcement proxy.

Do **not** wrap `mcp_server.py` with aip-go — it already bundles AIP Layers 1 and 2
internally and enforcement would run twice.

| Server | Enforcement | Use with |
|---|---|---|
| `mcp_server_plain.py` | None — data only | aip-go proxy, custom enforcement layer |
| `mcp_server.py` | Bundled Layer 1 + Layer 2 | Standalone CLI demo (`main.py`) |

---

## Policy reference

```yaml
apiVersion: aip.io/v1alpha1
kind: AgentPolicy
metadata:
  name: playground-policy
spec:
  mode: enforce          # enforce | monitor (dry run, logs only)
  allowed_tools:
    - list_my_emails
    - list_all_emails
  tool_rules:
    - tool: list_all_emails
      action: ask        # allow | ask (human approval) | block
```

`allowed_tools` is an allowlist — any tool not listed returns a `-32001` error.
`tool_rules` apply on top: a tool can be listed but still require approval (`ask`) or
be permanently blocked (`block`).

---

## Docker

Build a self-contained image that bundles the aip-go proxy and `mcp_server_plain.py`:

```dockerfile
# Dockerfile.aip
FROM golang:1.23-alpine AS builder
WORKDIR /app
RUN git clone https://github.com/openagentidentityprotocol/aip-go .
RUN make build

FROM python:3.12-alpine
WORKDIR /app
COPY --from=builder /app/bin/aip /usr/local/bin/aip
COPY mcp_server_plain.py data.py ./
COPY policy.yaml /etc/aip/policy.yaml
ENTRYPOINT ["aip", "--policy", "/etc/aip/policy.yaml", "--target", "python3 mcp_server_plain.py"]
```

```bash
docker build -f Dockerfile.aip -t aip-playground:latest .

# Run — pipe JSON-RPC over stdio
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \
  docker run -i aip-playground:latest
```

### Docker Compose

```yaml
# docker-compose.aip.yaml
version: "3.8"
services:
  aip-proxy:
    build:
      context: .
      dockerfile: Dockerfile.aip
    volumes:
      - ./playground-policy.yaml:/etc/aip/policy.yaml:ro
      - ./audit:/var/log/aip
    command:
      - --policy
      - /etc/aip/policy.yaml
      - --target
      - "python3 mcp_server_plain.py"
      - --audit
      - /var/log/aip/audit.jsonl
      - --verbose
    stdin_open: true
    tty: true
```

```bash
docker compose -f docker-compose.aip.yaml run aip-proxy
```

---

## Kubernetes sidecar

Deploy aip-go as a sidecar next to `mcp_server_plain.py` for cluster workloads:

```yaml
# k8s-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aip-playground
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: mcp-server
          image: python:3.12-alpine
          command: ["python3", "/app/mcp_server_plain.py"]
          volumeMounts:
            - name: app
              mountPath: /app

        - name: aip-proxy
          image: aip-playground:latest
          args:
            - --policy
            - /config/policy.yaml
            - --target
            - "python3 /app/mcp_server_plain.py"
            - --audit
            - /var/log/aip/audit.jsonl
          volumeMounts:
            - name: policy
              mountPath: /config
            - name: audit
              mountPath: /var/log/aip
            - name: app
              mountPath: /app

      volumes:
        - name: app
          configMap:
            name: playground-source
        - name: policy
          configMap:
            name: aip-policy
        - name: audit
          emptyDir: {}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: aip-policy
data:
  policy.yaml: |
    apiVersion: aip.io/v1alpha1
    kind: AgentPolicy
    metadata:
      name: playground-policy
    spec:
      mode: enforce
      allowed_tools:
        - list_my_emails
        - list_all_emails
      tool_rules:
        - tool: list_all_emails
          action: ask
```

---

## Audit log reference

aip-go writes a JSONL audit log. Each line is one decision:

```jsonl
{"timestamp":"2026-03-17T10:00:00Z","tool":"list_my_emails","decision":"ALLOW","agent":"cursor"}
{"timestamp":"2026-03-17T10:00:05Z","tool":"list_all_emails","decision":"ASK","agent":"cursor","outcome":"USER_APPROVED"}
{"timestamp":"2026-03-17T10:00:10Z","tool":"delete_emails","decision":"BLOCK","agent":"cursor"}
```

Useful queries:

```bash
# All blocked calls
jq 'select(.decision == "BLOCK")' aip-audit.jsonl

# Calls that required human approval
jq 'select(.decision == "ASK")' aip-audit.jsonl

# Tool usage frequency
jq -r '.tool' aip-audit.jsonl | sort | uniq -c | sort -rn

# DLP-triggered events
jq 'select(.event_type == "DLP_TRIGGERED")' aip-audit.jsonl
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `Policy file not found` | Use an absolute path to `playground-policy.yaml` |
| `Empty response from proxy` | Run `python3 mcp_server_plain.py` on its own to verify |
| `Permission denied` | `chmod +x /path/to/aip-go/bin/aip` |
| `action: ask` auto-denies | Expected in headless/CI — use `action: allow` for automation |
| `-32001` on an allowed tool | Check spelling in `allowed_tools` matches `tools/list` output |

Full debug output:

```bash
/path/to/aip-go/bin/aip \
  --policy ~/.config/aip/playground-policy.yaml \
  --target "python3 /path/to/sample-application/mcp_server_plain.py" \
  --verbose 2>aip-debug.log
```
