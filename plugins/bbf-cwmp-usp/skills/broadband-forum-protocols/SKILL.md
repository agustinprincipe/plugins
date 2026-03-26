---
name: broadband-forum-protocols
description: "Use when working with TR-069 (CWMP), TR-369 (USP), TR-181, or TR-098 broadband forum protocols - querying device parameters, RPC methods, SOAP/protobuf message structures, data models, or protocol specifications. Triggers on mentions of ACS, CPE management, CWMP, USP, Device:2 data model, parameter paths like Device.WiFi.*, InternetGatewayDevice.*, or any BBF/Broadband Forum data model navigation."
---

# Broadband Forum Protocols (TR-069 / TR-369)

Skill for querying CWMP and USP protocol documentation, data models, and message schemas via the BBF MCP server. The server indexes the official Broadband Forum XML data models and specifications directly from GitHub.

## Tool Selection

The server provides 5 tools. Choosing the right one matters — it's the difference between getting a precise answer and wading through noise.

| You want to... | Use this tool | Why |
|---|---|---|
| Find parameters by description or feature area | `search_datamodel` | Semantic search — good when you don't know exact paths |
| Look up a specific parameter or object by path | `get_parameter` | Exact match — instant, no embeddings, complete metadata |
| Browse what's under a data model path | `list_objects` | Tree navigation — shows direct children with descriptions |
| Understand CWMP protocol behavior | `search_cwmp_spec` | Searches the TR-069 specification (from PDF) |
| Understand USP protocol behavior | `search_usp_spec` | Searches the TR-369 specification markdown |
| Look up CWMP/USP message structures | `search_protocol_schema` | Searches XSD (CWMP) and protobuf (USP) schemas |
| Understand RFC 2119 keywords (MUST, SHOULD, MAY…) | `get_rfc2119_keyword` | Returns precise definitions from RFC 2119 |

### When to use which — a decision tree

```
"I need info about a data model parameter or object"
  ├─ I know the exact path (e.g. Device.WiFi.SSID.{i}.SSID)
  │   └─ get_parameter
  ├─ I want to see what's under a path (e.g. what's in Device.WiFi.?)
  │   └─ list_objects
  └─ I'm searching by concept/feature (e.g. "wifi channel configuration")
      └─ search_datamodel

"I need info about protocol behavior"
  ├─ CWMP (TR-069): procedures, RPC methods, sessions, security
  │   └─ search_cwmp_spec
  ├─ USP (TR-369): messages, MTPs, security, discovery
  │   └─ search_usp_spec
  └─ CWMP/USP message schemas (XSD/protobuf)
      └─ search_protocol_schema
```

## Tool Details

### `search_datamodel` — Semantic search over data models

Searches CWMP and USP XML data model objects and parameters by meaning, not just exact paths. Each result includes the object/parameter path, type, access mode, and description.

**Parameters:**
- `query` (required): what you're looking for — natural language or partial paths
- `protocol` (optional): `"cwmp"` or `"usp"` to filter. Omit to search both.
- `top_k` (optional, default 10): number of results

**Good queries:** `"WiFi SSID configuration"`, `"NAT port mapping rules"`, `"device manufacturer info"`, `"DHCP server parameters"`

**Tips:**
- Include domain context: `"WiFi radio channel selection"` beats `"channel"`
- Use `protocol: "cwmp"` to include TR-098 InternetGatewayDevice results
- CWMP collection includes both TR-181 Device:2 and TR-098 IGD models

### `get_parameter` — Exact path lookup

Returns complete metadata for a specific data model path. This is deterministic (no embeddings) — it directly queries the parsed XML structure in memory.

**Parameters:**
- `path` (required): full data model path
- `protocol` (optional, default `"cwmp"`): `"cwmp"` or `"usp"`

**For parameters** (leaf nodes), returns: type, access, description, constraints (min/max length, range, enumerations, pattern, default value).

**For objects** (containers), returns: description, access, multi-instance flag, max entries, and all parameters within that object.

**Path format examples:**
- Parameter: `Device.DeviceInfo.Manufacturer`
- Object: `Device.WiFi.` (trailing dot) or `Device.WiFi` (auto-corrected)
- Multi-instance: `Device.WiFi.SSID.{i}.SSID`
- TR-098: `InternetGatewayDevice.WANDevice.{i}.` (use `protocol: "cwmp"`)

### `list_objects` — Tree navigation

Lists direct child objects of a given path. Think of it as "ls" for the data model tree.

**Parameters:**
- `path` (required): parent path (trailing dot added automatically if missing)
- `protocol` (optional, default `"cwmp"`): `"cwmp"` or `"usp"`
- `include_params` (optional, default false): also list parameters of the parent object

**Common starting points:**
- `Device.` — top-level objects (DeviceInfo, WiFi, Ethernet, IP, etc.)
- `Device.WiFi.` — WiFi sub-objects
- `InternetGatewayDevice.` — TR-098 legacy root (CWMP only)

**Note on depth:** Multi-instance objects like `Device.WiFi.Radio.{i}.` appear deeper than expected because `{i}` counts as an extra level. If `list_objects` returns no children for a path you expect to have sub-objects, the children might be two levels down. Try `search_datamodel` with the parent path name to discover them.

### `search_cwmp_spec` — CWMP specification search

Searches the TR-069 CWMP specification (276-page PDF, extracted to markdown via pymupdf4llm). Covers the full protocol: architecture, procedures, RPC methods, session management, security, XMPP, proxy management, firmware management, and all annexes.

**Parameters:**
- `query` (required): what you're looking for
- `top_k` (optional, default 5): number of results

**Good for:**
- CWMP session procedures (CPE/ACS operation, version negotiation)
- RPC method behavior (Inform, GetParameterValues, Download, etc.)
- Connection request mechanisms (HTTP, XMPP, NAT traversal)
- Security: TLS, authentication, digest auth
- Proxy management (virtual CWMP device, embedded object mechanism)
- Software module management, firmware image handling
- HTTP bulk data collection, UDP lightweight notifications

### `search_usp_spec` — USP specification search

Searches the TR-369 USP specification (20 markdown files from the official repo). Covers architecture, messages, MTPs, security, discovery, extensions.

**Parameters:**
- `query` (required): what you're looking for
- `top_k` (optional, default 5): number of results

**Good for:**
- USP message types and their behavior (Get, Set, Add, Delete, Operate, Notify)
- Message Transfer Protocols (STOMP, MQTT, WebSocket, CoAP, Unix Domain Socket)
- End-to-end security, session context, trust models
- Discovery mechanisms (DNS-SD, DHCP)
- Software module management, IoT proxying, bulk data

### `search_protocol_schema` — Message schema search

Searches the raw protocol schema definitions — XSD for CWMP, protobuf for USP.

**Parameters:**
- `query` (required): message name, structure, or concept
- `protocol` (optional): `"cwmp"` or `"usp"`. Omit to search both.
- `top_k` (optional, default 5): number of results

**Good queries:**
- CWMP: `"Inform RPC"`, `"ParameterValueStruct"`, `"SetParameterValues fault"`, `"SOAP envelope"`
- USP: `"Get request message"`, `"Notify protobuf"`, `"Error message fields"`

### `get_rfc2119_keyword` — RFC 2119 keyword definitions

Returns the precise definition of RFC 2119 requirement-level keywords. These keywords (MUST, SHOULD, MAY, etc.) appear throughout all BBF specifications (TR-069, TR-369, TR-181, TR-098) and have exact meanings defined in RFC 2119.

**Parameters:**
- `keyword` (optional): specific keyword to look up. Omit to list all definitions.

**Supported keywords:** MUST, MUST NOT, SHOULD, SHOULD NOT, MAY
**Aliases (auto-resolved):** REQUIRED → MUST, SHALL → MUST, SHALL NOT → MUST NOT, RECOMMENDED → SHOULD, NOT RECOMMENDED → SHOULD NOT, OPTIONAL → MAY

**When to use:** When reading spec results that contain capitalized MUST, SHOULD, MAY, SHALL, REQUIRED, OPTIONAL — these are not casual English. They carry precise normative meaning per RFC 2119. Use this tool to clarify the exact obligation level.

## Workflows

### Look up a specific parameter

The fastest path when you know (or suspect) the parameter path:

1. `get_parameter` with the path → get full metadata
2. If path not found, use `search_datamodel` with a partial name or description to find it

**Example:** "What is Device.WiFi.Radio.{i}.Channel?"
→ `get_parameter(path="Device.WiFi.Radio.{i}.Channel", protocol="cwmp")`

### Discover parameters for a feature

When you need to find what parameters exist for something:

1. `search_datamodel` with a descriptive query → find relevant objects
2. `list_objects` on promising paths → see what's inside
3. `get_parameter` on specific parameters → get full details

**Example:** "What WiFi parameters can I configure?"
→ `search_datamodel(query="WiFi configuration parameters", protocol="cwmp")`
→ `list_objects(path="Device.WiFi.", include_params=true)`

### Understand a protocol message

1. `search_cwmp_spec` or `search_usp_spec` for protocol behavior
2. `search_protocol_schema` for the exact message structure

**Example:** "How does CWMP Inform work?"
→ `search_cwmp_spec(query="Inform RPC session procedure")`
→ `search_protocol_schema(query="Inform", protocol="cwmp")`

**Example:** "How does USP Notify work?"
→ `search_usp_spec(query="Notify message subscription")`
→ `search_protocol_schema(query="Notify message", protocol="usp")`

### Compare CWMP vs USP for the same feature

1. `search_datamodel` with `protocol: "cwmp"` then `protocol: "usp"` for parameter differences
2. `search_protocol_schema` for both to compare message structures

### Interpret requirement levels in spec text

When spec results contain capitalized keywords like MUST, SHOULD, MAY:

1. `get_rfc2119_keyword` with the keyword → get the precise normative meaning
2. Apply the definition to understand the obligation level

**Example:** Spec says "The CPE MUST send an Inform message"
→ `get_rfc2119_keyword(keyword="MUST")` → "absolute requirement of the specification"
→ This means the CPE has no choice — it is a hard requirement, not a suggestion.

**Example:** Spec says "The ACS SHOULD use TLS"
→ `get_rfc2119_keyword(keyword="SHOULD")` → valid reasons may exist to ignore, but implications must be weighed
→ This means TLS is strongly recommended but there are valid exceptions.

## Common Pitfalls

- **Using search when you know the path**: `get_parameter` is instant and exact. Use `search_datamodel` only when you're exploring or don't know the path.
- **Forgetting protocol filter**: CWMP and USP data models are similar but not identical. If you need protocol-specific parameters, set the `protocol` filter.
- **TR-098 vs TR-181**: TR-098 uses `InternetGatewayDevice.*` paths (legacy CWMP). TR-181 uses `Device.*` paths (modern, both protocols). Both are in the `cwmp` collection.
- **CWMP spec vs schema**: `search_cwmp_spec` searches the full TR-069 specification narrative (procedures, requirements, behavior). `search_protocol_schema` with protocol "cwmp" searches the XSD schema (message structures, data types). Use the spec for "how does X work?" and the schema for "what fields does message X have?".

## Setup

If the search tools return errors about missing data or collections, run the setup tools:

1. `init_data` — fetches BBF data from GitHub (~12MB download)
2. `index_data` — indexes into vector DB (~5 min, creates embeddings)

These are MCP tools you can call directly — no need for the user to leave Claude Code. After `index_data` completes, all search tools become available immediately.

For manual setup or troubleshooting, see `references/setup.md`.
