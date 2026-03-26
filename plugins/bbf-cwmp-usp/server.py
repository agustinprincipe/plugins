"""
BBF CWMP/USP MCP Server

Tools:
- search_datamodel: semantic search over CWMP/USP data model objects and parameters
- get_parameter: exact path lookup (e.g. Device.WiFi.SSID.1.SSID)
- list_objects: list child objects of a path (e.g. Device.WiFi.)
- search_cwmp_spec: semantic search over CWMP specification (TR-069)
- search_usp_spec: semantic search over USP specification (TR-369)
- search_protocol_schema: semantic search over XSD/proto schemas
- init_data: fetch BBF data from GitHub
- index_data: index fetched data into vector DB
"""
import asyncio
import json
from pathlib import Path
from sys import stderr
from typing import Any, Sequence

import chromadb
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from sentence_transformers import SentenceTransformer

from xml_parser import BBFXMLParser, DataModel

DATA_DIR = Path(__file__).parent / "data"
VECTOR_DB_DIR = DATA_DIR / "vector_db"

app = Server("bbf-mcp-server")

# Global state
collections: dict[str, Any] = {}
model: SentenceTransformer | None = None
data_models: dict[str, DataModel] = {}  # protocol -> DataModel for exact lookups


def _load_data_models():
    """Load parsed XML data models into memory for exact lookups."""
    parser = BBFXMLParser()

    cwmp_dir = DATA_DIR / "cwmp"
    usp_dir = DATA_DIR / "usp"

    # Load CWMP data models
    for xml_path in sorted(cwmp_dir.glob("*-full.xml")) if cwmp_dir.exists() else []:
        key = "cwmp-tr098" if "tr-098" in xml_path.name else "cwmp"
        dm = parser.parse(xml_path)
        data_models[key] = dm
        print(f"  Loaded {xml_path.name}: {len(dm.objects)} objects", file=stderr)

    # Load USP data model
    for xml_path in sorted(usp_dir.glob("*-full.xml")) if usp_dir.exists() else []:
        dm = parser.parse(xml_path)
        data_models["usp"] = dm
        print(f"  Loaded {xml_path.name}: {len(dm.objects)} objects", file=stderr)


def init_server():
    """Initialize embedding model, ChromaDB collections, and in-memory data models."""
    global model

    # Embedding model
    try:
        model = SentenceTransformer("all-MiniLM-L6-v2")
        print("Embedding model loaded", file=stderr)
    except Exception as e:
        print(f"Failed to load embedding model: {e}", file=stderr)
        return

    # ChromaDB collections
    if VECTOR_DB_DIR.exists():
        try:
            client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
            for name in ["cwmp_datamodel", "usp_datamodel", "cwmp_spec", "usp_spec", "cwmp_protocols", "usp_protocols"]:
                try:
                    collections[name] = client.get_collection(name)
                    count = collections[name].count()
                    print(f"  Collection '{name}': {count} docs", file=stderr)
                except Exception:
                    print(f"  Collection '{name}' not found", file=stderr)
        except Exception as e:
            print(f"Failed to load ChromaDB: {e}", file=stderr)
    else:
        print("Vector DB not found. Run 'python main.py index' first.", file=stderr)

    # In-memory data models for exact lookups
    print("Loading data models...", file=stderr)
    _load_data_models()

    if not collections and not data_models:
        print("No data loaded. Run 'python main.py init && python main.py index'.", file=stderr)


def _semantic_search(collection_name: str, query: str, top_k: int = 5) -> str:
    """Run semantic search on a ChromaDB collection."""
    collection = collections.get(collection_name)
    if collection is None:
        return f"Collection '{collection_name}' not available. Run 'python main.py index'."

    if model is None:
        return "Embedding model not loaded."

    query_embedding = model.encode(query).tolist()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["metadatas", "documents", "distances"],
    )

    if not results["documents"][0]:
        return "No results found."

    output_parts = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        dist = results["distances"][0][i] if results.get("distances") else None

        header = f"**[{i+1}]**"
        if meta.get("path"):
            header += f" `{meta['path']}`"
        if meta.get("source"):
            header += f" ({meta['source']})"
        if dist is not None:
            header += f" — {1 - dist:.0%}"

        output_parts.append(f"{header}\n{doc}")

    return "\n\n---\n\n".join(output_parts)


def _format_param(obj_path: str, param) -> dict:
    """Format a parameter for JSON output."""
    result = {
        "path": f"{obj_path}{param.name}",
        "type": param.data_type,
        "access": param.access,
    }
    if param.description:
        result["description"] = param.description
    if param.enumerations:
        result["values"] = param.enumerations
    if param.range_min is not None or param.range_max is not None:
        result["range"] = {"min": param.range_min, "max": param.range_max}
    if param.default is not None:
        result["default"] = param.default
    return result


def _format_object(obj) -> dict:
    """Format an object for JSON output."""
    result = {
        "path": obj.name,
        "access": obj.access,
        "multi_instance": obj.is_multi_instance,
    }
    if obj.is_multi_instance:
        result["max_entries"] = obj.max_entries
    if obj.description:
        result["description"] = obj.description
    result["parameter_count"] = len(obj.parameters)
    return result


def _truncate(text: str, max_chars: int = 80000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[Truncated. Total: {len(text)} chars]"


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_datamodel",
            description=(
                "Semantic search over CWMP and USP data model objects and parameters. "
                "Use for: finding device parameters by description, discovering objects "
                "related to a feature (WiFi, Ethernet, NAT, etc.), understanding parameter "
                "types and constraints. Returns objects and parameters with their paths, "
                "types, and descriptions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Semantic query. Examples: 'WiFi SSID parameters', "
                            "'NAT port mapping', 'device manufacturer info'"
                        ),
                    },
                    "protocol": {
                        "type": "string",
                        "enum": ["cwmp", "usp"],
                        "description": "Filter by protocol. Omit to search both.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_parameter",
            description=(
                "Get exact data model parameter or object by its full path. "
                "Returns complete metadata: type, access, description, constraints. "
                "Use when you know the exact path like 'Device.WiFi.SSID.{i}.SSID' "
                "or 'Device.DeviceInfo.Manufacturer'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Full data model path. Examples: "
                            "'Device.WiFi.SSID.{i}.SSID', 'Device.DeviceInfo.', "
                            "'Device.ManagementServer.URL'"
                        ),
                    },
                    "protocol": {
                        "type": "string",
                        "enum": ["cwmp", "usp"],
                        "description": "Protocol to look up in (default: cwmp)",
                        "default": "cwmp",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="list_objects",
            description=(
                "List direct child objects of a data model path. "
                "Use for navigating the data model tree. "
                "Example: 'Device.' returns Device.DeviceInfo., Device.WiFi., etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Parent object path (with trailing dot). "
                            "Examples: 'Device.', 'Device.WiFi.', "
                            "'InternetGatewayDevice.'"
                        ),
                    },
                    "protocol": {
                        "type": "string",
                        "enum": ["cwmp", "usp"],
                        "description": "Protocol (default: cwmp)",
                        "default": "cwmp",
                    },
                    "include_params": {
                        "type": "boolean",
                        "description": "Also list parameters of the parent object (default: false)",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="search_usp_spec",
            description=(
                "Search the USP (TR-369) specification. "
                "Returns relevant sections from the USP spec covering: "
                "architecture, messages, MTPs (STOMP, MQTT, WebSocket), "
                "security, discovery, E2E message exchange, software module management."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query. Examples: 'STOMP MTP binding', "
                            "'USP Record structure', 'subscription notifications'"
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_cwmp_spec",
            description=(
                "Search the CWMP (TR-069) specification. "
                "Returns relevant sections from the TR-069 spec covering: "
                "architecture, procedures, RPC methods, session management, "
                "security, XMPP connection requests, proxy management, "
                "firmware management, and protocol requirements."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query. Examples: 'Inform RPC procedure', "
                            "'connection request mechanism', 'session retry policy'"
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_protocol_schema",
            description=(
                "Search protocol schema definitions. "
                "For CWMP: XSD schemas defining SOAP/XML message structures. "
                "For USP: protobuf schemas defining USP Message/Record structures. "
                "Use for: message formats, RPC structures, field definitions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query. Examples: 'Inform RPC', "
                            "'Get message proto', 'ParameterValueStruct'"
                        ),
                    },
                    "protocol": {
                        "type": "string",
                        "enum": ["cwmp", "usp"],
                        "description": "Protocol to search (default: both)",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="init_data",
            description=(
                "Fetch BBF data model files from GitHub. "
                "Downloads CWMP/USP XML data models, USP specification, "
                "and protocol schemas. Run this before index_data. "
                "Only needs to run once, or to update to latest versions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "github_token": {
                        "type": "string",
                        "description": "Optional GitHub token for higher rate limits",
                    },
                },
            },
        ),
        Tool(
            name="index_data",
            description=(
                "Index fetched BBF data into the vector database for semantic search. "
                "Parses XML data models and creates embeddings. "
                "Run after init_data. Takes ~5 minutes."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_rfc2119_keyword",
            description=(
                "Get RFC 2119 keyword definitions (MUST, SHOULD, MAY, etc.). "
                "These keywords indicate requirement levels in IETF specifications "
                "and are widely used in BBF/TR-069/TR-369 documents. "
                "Call without arguments to list all keywords, or with a specific "
                "keyword to get its definition."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": (
                            "RFC 2119 keyword to look up. Examples: 'MUST', "
                            "'SHOULD NOT', 'MAY', 'REQUIRED', 'OPTIONAL'. "
                            "Omit to list all keyword definitions."
                        ),
                    },
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent]:
    try:
        result = await _handle_tool(name, arguments)
    except Exception as e:
        result = f"Error: {e}"
    return [TextContent(type="text", text=_truncate(result))]


async def _handle_tool(name: str, arguments: dict) -> str:
    if name == "search_datamodel":
        return _tool_search_datamodel(arguments)
    elif name == "get_parameter":
        return _tool_get_parameter(arguments)
    elif name == "list_objects":
        return _tool_list_objects(arguments)
    elif name == "search_usp_spec":
        return _tool_search_usp_spec(arguments)
    elif name == "search_cwmp_spec":
        return _tool_search_cwmp_spec(arguments)
    elif name == "search_protocol_schema":
        return _tool_search_protocol_schema(arguments)
    elif name == "init_data":
        return await _tool_init_data(arguments)
    elif name == "index_data":
        return _tool_index_data()
    elif name == "get_rfc2119_keyword":
        return _tool_get_rfc2119_keyword(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


def _tool_search_datamodel(args: dict) -> str:
    query = args["query"]
    protocol = args.get("protocol")
    top_k = args.get("top_k", 10)

    results = []
    if protocol in (None, "cwmp"):
        r = _semantic_search("cwmp_datamodel", query, top_k)
        if r != "No results found.":
            results.append(f"## CWMP Data Model\n\n{r}")
    if protocol in (None, "usp"):
        r = _semantic_search("usp_datamodel", query, top_k)
        if r != "No results found.":
            results.append(f"## USP Data Model\n\n{r}")

    return "\n\n".join(results) if results else "No results found."


def _tool_get_parameter(args: dict) -> str:
    path = args["path"]
    protocol = args.get("protocol", "cwmp")

    # Find the right DataModel
    dm = data_models.get(protocol)
    if dm is None:
        # Try cwmp-tr098 for InternetGatewayDevice paths
        if path.startswith("InternetGatewayDevice"):
            dm = data_models.get("cwmp-tr098")
        if dm is None:
            return f"No data model loaded for protocol '{protocol}'."

    # Try as parameter first
    param = dm.get_parameter(path)
    if param:
        obj_path = path.rsplit(".", 1)[0] + "."
        return json.dumps(_format_param(obj_path, param), indent=2)

    # Try as object (ensure trailing dot)
    obj_path = path if path.endswith(".") else path + "."
    obj = dm.get_object(obj_path)
    if obj:
        result = _format_object(obj)
        result["parameters"] = [
            _format_param(obj_path, p) for p in obj.parameters.values()
        ]
        return json.dumps(result, indent=2)

    return f"Path '{path}' not found in {protocol} data model."


def _tool_list_objects(args: dict) -> str:
    path = args["path"]
    protocol = args.get("protocol", "cwmp")
    include_params = args.get("include_params", False)

    dm = data_models.get(protocol)
    if dm is None:
        if path.startswith("InternetGatewayDevice"):
            dm = data_models.get("cwmp-tr098")
        if dm is None:
            return f"No data model loaded for protocol '{protocol}'."

    # Ensure trailing dot
    if not path.endswith("."):
        path += "."

    children = dm.list_children(path)
    if not children:
        return f"No child objects under '{path}'."

    result = {"parent": path, "children": [_format_object(c) for c in children]}

    if include_params:
        obj = dm.get_object(path)
        if obj and obj.parameters:
            result["parameters"] = [
                _format_param(path, p) for p in obj.parameters.values()
            ]

    return json.dumps(result, indent=2)


def _tool_search_usp_spec(args: dict) -> str:
    return _semantic_search("usp_spec", args["query"], args.get("top_k", 5))


def _tool_search_cwmp_spec(args: dict) -> str:
    return _semantic_search("cwmp_spec", args["query"], args.get("top_k", 5))


def _tool_search_protocol_schema(args: dict) -> str:
    query = args["query"]
    protocol = args.get("protocol")
    top_k = args.get("top_k", 5)

    results = []
    if protocol in (None, "cwmp"):
        r = _semantic_search("cwmp_protocols", query, top_k)
        if r != "No results found.":
            results.append(f"## CWMP Schema (XSD)\n\n{r}")
    if protocol in (None, "usp"):
        r = _semantic_search("usp_protocols", query, top_k)
        if r != "No results found.":
            results.append(f"## USP Schema (Protobuf)\n\n{r}")

    return "\n\n".join(results) if results else "No results found."


# ── RFC 2119 keyword definitions ──────────────────────────────────────

_RFC2119_KEYWORDS: dict[str, str] = {
    "MUST": (
        'This word, or the terms "REQUIRED" or "SHALL", mean that the '
        "definition is an absolute requirement of the specification."
    ),
    "MUST NOT": (
        'This phrase, or the phrase "SHALL NOT", mean that the '
        "definition is an absolute prohibition of the specification."
    ),
    "SHOULD": (
        'This word, or the adjective "RECOMMENDED", mean that there '
        "may exist valid reasons in particular circumstances to ignore a "
        "particular item, but the full implications must be understood and "
        "carefully weighed before choosing a different course."
    ),
    "SHOULD NOT": (
        'This phrase, or the phrase "NOT RECOMMENDED" mean that '
        "there may exist valid reasons in particular circumstances when the "
        "particular behavior is acceptable or even useful, but the full "
        "implications should be understood and the case carefully weighed "
        "before implementing any behavior described with this label."
    ),
    "MAY": (
        'This word, or the adjective "OPTIONAL", mean that an item is '
        "truly optional. One vendor may choose to include the item because a "
        "particular marketplace requires it or because the vendor feels that "
        "it enhances the product while another vendor may omit the same item."
    ),
}

# Aliases that map to a canonical keyword
_RFC2119_ALIASES: dict[str, str] = {
    "REQUIRED": "MUST",
    "SHALL": "MUST",
    "SHALL NOT": "MUST NOT",
    "RECOMMENDED": "SHOULD",
    "NOT RECOMMENDED": "SHOULD NOT",
    "OPTIONAL": "MAY",
}


def _tool_get_rfc2119_keyword(args: dict) -> str:
    keyword = args.get("keyword")

    if keyword is None:
        lines = ["# RFC 2119 — Keyword Definitions\n"]
        for kw, definition in _RFC2119_KEYWORDS.items():
            aliases = [a for a, canon in _RFC2119_ALIASES.items() if canon == kw]
            alias_str = f" (also: {', '.join(aliases)})" if aliases else ""
            lines.append(f"**{kw}**{alias_str}: {definition}\n")
        lines.append(
            "\nSource: RFC 2119 — https://www.ietf.org/rfc/rfc2119.txt"
        )
        return "\n".join(lines)

    normalized = keyword.strip().upper()
    # Resolve aliases
    if normalized in _RFC2119_ALIASES:
        canonical = _RFC2119_ALIASES[normalized]
        definition = _RFC2119_KEYWORDS[canonical]
        return (
            f"**{normalized}** is an alias for **{canonical}**.\n\n"
            f"Definition: {definition}\n\n"
            f"Source: RFC 2119 — https://www.ietf.org/rfc/rfc2119.txt"
        )

    if normalized in _RFC2119_KEYWORDS:
        definition = _RFC2119_KEYWORDS[normalized]
        aliases = [a for a, canon in _RFC2119_ALIASES.items() if canon == normalized]
        alias_str = f"\nAliases: {', '.join(aliases)}" if aliases else ""
        return (
            f"**{normalized}**: {definition}{alias_str}\n\n"
            f"Source: RFC 2119 — https://www.ietf.org/rfc/rfc2119.txt"
        )

    valid = sorted(set(list(_RFC2119_KEYWORDS.keys()) + list(_RFC2119_ALIASES.keys())))
    return f"Unknown keyword: '{keyword}'. Valid keywords: {', '.join(valid)}"


async def _tool_init_data(args: dict) -> str:
    """Fetch BBF data from GitHub."""
    from bbf_fetcher import BBFDataFetcher

    token = args.get("github_token")
    fetcher = BBFDataFetcher(github_token=token)

    result = await fetcher.run_init(DATA_DIR)

    lines = [f"Fetched {result.total_files} files to {DATA_DIR}"]
    for repo_name, info in result.repos.items():
        lines.append(f"  {repo_name}: {info['files_downloaded']} files")
    if result.errors:
        lines.append(f"\nErrors ({len(result.errors)}):")
        for err in result.errors:
            lines.append(f"  - {err}")
        return "\n".join(lines)

    lines.append("\nRun 'index_data' tool next to index the data for search.")
    return "\n".join(lines)


def _tool_index_data() -> str:
    """Index fetched data into ChromaDB."""
    from indexer import BBFIndexer

    if not DATA_DIR.exists():
        return "No data directory found. Run 'init_data' tool first."

    indexer = BBFIndexer(data_dir=DATA_DIR)
    indexer.run_full_indexing()

    # Reload server state
    init_server()
    return "Indexing complete. Server reloaded with new data. All search tools are now available."


async def main():
    """Run the MCP server."""
    print("=" * 60, file=stderr)
    print("BBF CWMP/USP MCP Server", file=stderr)
    print("=" * 60, file=stderr)

    init_server()

    print("\nServer ready", file=stderr)
    print("=" * 60, file=stderr)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
