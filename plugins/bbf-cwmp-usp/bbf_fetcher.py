"""
Discovers and downloads BBF data model files from GitHub repositories.

Uses GitHub Trees API for file discovery and raw.githubusercontent.com for downloads.
This avoids cloning entire repos (~209MB) and instead fetches only needed files (~12-15MB).
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

GITHUB_API_BASE = "https://api.github.com"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

# Direct downloads (files not available via GitHub repos)
DIRECT_DOWNLOADS: dict[str, dict] = {
    "cwmp-spec": {
        "url": "https://www.broadband-forum.org/pdfs/tr-069-1-6-1.pdf",
        "filename": "tr-069-amendment-6-corrigendum-1.pdf",
        "description": "TR-069 CWMP Specification (Amendment 6 Corrigendum 1)",
    },
    "rfc": {
        "url": "https://www.ietf.org/rfc/rfc2119.txt",
        "filename": "rfc2119.txt",
        "description": "RFC 2119 - Key words for use in RFCs to Indicate Requirement Levels",
    },
}

# Repo configurations: patterns to match and how to select files
REPOS: dict[str, dict] = {
    "cwmp-data-models": {
        "owner": "BroadbandForum",
        "branch": "master",
        "categories": {
            "data_models": {
                "patterns": [
                    r"^tr-181-2-\d+-\d+-cwmp-full\.xml$",
                    r"^tr-098-\d+-\d+-\d+-full\.xml$",
                ],
                "latest_only": True,
            },
            "protocols": {
                "patterns": [r"^cwmp-\d+-\d+\.xsd$"],
                "latest_only": True,
            },
        },
        "dest_dir": "cwmp",
    },
    "usp-data-models": {
        "owner": "BroadbandForum",
        "branch": "master",
        "categories": {
            "data_models": {
                "patterns": [r"^tr-181-2-\d+-\d+-usp-full\.xml$"],
                "latest_only": True,
            },
        },
        "dest_dir": "usp",
    },
    "usp": {
        "owner": "BroadbandForum",
        "branch": "master",
        "categories": {
            "spec_markdown": {
                "patterns": [r"^specification/.+\.md$"],
                "latest_only": False,
            },
            "protocols": {
                "patterns": [
                    r"^specification/usp-msg-\d+-\d+\.proto$",
                    r"^specification/usp-record-\d+-\d+\.proto$",
                ],
                "latest_only": True,
            },
        },
        "dest_dir": "usp-spec",
    },
}


@dataclass
class InitResult:
    """Summary of the init/onboarding process."""

    total_files: int = 0
    repos: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class BBFDataFetcher:
    """Discovers and downloads BBF data model files from GitHub."""

    def __init__(self, github_token: str | None = None):
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            self.headers["Authorization"] = f"Bearer {github_token}"

    def _parse_version(self, filename: str) -> tuple[int, ...]:
        """Extract version numbers from a filename for comparison.

        Handles patterns like:
          tr-181-2-20-1-cwmp-full.xml -> (181, 2, 20, 1)
          tr-098-1-8-0-full.xml -> (98, 1, 8, 0)
          cwmp-1-4.xsd -> (1, 4)
          usp-msg-1-5.proto -> (1, 5)
        """
        # Strip directory prefix (e.g., "specification/")
        base = filename.rsplit("/", 1)[-1]

        # Try TR-xxx pattern first: tr-NNN-N-N-N-...
        tr_match = re.match(r"tr-(\d+)-([\d-]+?)-(cwmp|usp|full)", base)
        if tr_match:
            tr_num = int(tr_match.group(1))
            version_parts = tuple(int(x) for x in tr_match.group(2).split("-"))
            return (tr_num,) + version_parts

        # Try protocol pattern: name-N-N.ext
        proto_match = re.match(r"[a-z][\w-]*?-([\d]+-[\d]+)\.", base)
        if proto_match:
            return tuple(int(x) for x in proto_match.group(1).split("-"))

        return (0,)

    async def discover_files(self, repo_name: str) -> dict[str, list[str]]:
        """Discover files in a BBF GitHub repo matching configured patterns.

        Uses the GitHub Trees API (single request per repo) to get all file paths,
        then filters by regex patterns and selects latest versions where configured.

        Returns a dict mapping category name to list of file paths.
        """
        repo_config = REPOS[repo_name]
        owner = repo_config["owner"]
        branch = repo_config["branch"]

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/git/trees/{branch}?recursive=1"

        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            tree_data = response.json()

        all_paths = [
            item["path"] for item in tree_data["tree"] if item.get("type") == "blob"
        ]

        result: dict[str, list[str]] = {}

        for category_name, category_config in repo_config["categories"].items():
            matched: dict[str, list[str]] = {}  # pattern -> list of matching paths

            for pattern in category_config["patterns"]:
                regex = re.compile(pattern)
                matches = [p for p in all_paths if regex.search(p)]

                if category_config["latest_only"] and matches:
                    # Group by pattern "family" and pick latest version of each
                    # For TR docs, family is the TR number; for protocols, it's the base name
                    families: dict[str, list[str]] = {}
                    for m in matches:
                        base = m.rsplit("/", 1)[-1]
                        # Extract family key: "tr-181-cwmp-full", "tr-098-full", "cwmp-xsd", etc.
                        family = re.sub(r"\d+", "#", base)
                        families.setdefault(family, []).append(m)

                    for family_paths in families.values():
                        latest = max(family_paths, key=self._parse_version)
                        matched.setdefault(pattern, []).append(latest)
                else:
                    matched[pattern] = matches

            # Flatten all matches for this category
            result[category_name] = [
                path for paths in matched.values() for path in paths
            ]

        return result

    async def download_file(
        self, client: httpx.AsyncClient, repo_name: str, file_path: str, dest: Path
    ) -> Path:
        """Download a single file from GitHub raw content."""
        repo_config = REPOS[repo_name]
        owner = repo_config["owner"]
        branch = repo_config["branch"]

        url = f"{GITHUB_RAW_BASE}/{owner}/{repo_name}/{branch}/{file_path}"

        dest.parent.mkdir(parents=True, exist_ok=True)

        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)

        return dest

    async def download_direct_files(
        self, client: httpx.AsyncClient, data_dir: Path
    ) -> tuple[int, list[str]]:
        """Download files from direct URLs (not GitHub repos).

        Returns (count, errors) tuple.
        """
        count = 0
        errors = []

        for dest_name, config in DIRECT_DOWNLOADS.items():
            dest_path = data_dir / dest_name / config["filename"]
            if dest_path.exists():
                count += 1
                continue

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                async with client.stream("GET", config["url"]) as response:
                    response.raise_for_status()
                    with open(dest_path, "wb") as f:
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)
                count += 1
            except httpx.HTTPError as e:
                errors.append(f"Failed to download {config['description']}: {e}")

        return count, errors

    async def run_init(self, data_dir: Path) -> InitResult:
        """Run the full init/onboarding process.

        1. Discover files in each BBF repo
        2. Download them to data_dir
        3. Write manifest.json with metadata
        """
        result = InitResult()
        manifest: dict = {
            "created": datetime.now(timezone.utc).isoformat(),
            "repos": {},
        }

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            for repo_name, repo_config in REPOS.items():
                owner = repo_config["owner"]
                branch = repo_config["branch"]
                dest_dir = repo_config["dest_dir"]

                # Discover files
                url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/git/trees/{branch}?recursive=1"
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    tree_data = response.json()
                except httpx.HTTPError as e:
                    result.errors.append(f"Failed to fetch tree for {repo_name}: {e}")
                    continue

                tree_sha = tree_data.get("sha", "unknown")

                all_paths = [
                    item["path"]
                    for item in tree_data["tree"]
                    if item.get("type") == "blob"
                ]

                repo_files: dict[str, list[str]] = {}

                for category_name, category_config in repo_config["categories"].items():
                    for pattern in category_config["patterns"]:
                        regex = re.compile(pattern)
                        matches = [p for p in all_paths if regex.search(p)]

                        if category_config["latest_only"] and matches:
                            families: dict[str, list[str]] = {}
                            for m in matches:
                                base = m.rsplit("/", 1)[-1]
                                family = re.sub(r"\d+", "#", base)
                                families.setdefault(family, []).append(m)

                            for family_paths in families.values():
                                latest = max(family_paths, key=self._parse_version)
                                repo_files.setdefault(category_name, []).append(latest)
                        else:
                            repo_files.setdefault(category_name, []).extend(matches)

                # Download files
                downloaded = []
                for category_name, file_paths in repo_files.items():
                    for file_path in file_paths:
                        # Determine local filename
                        if dest_dir == "usp-spec":
                            # Preserve subdirectory structure under specification/
                            rel = file_path.removeprefix("specification/")
                            local_path = data_dir / dest_dir / rel
                        else:
                            local_path = data_dir / dest_dir / Path(file_path).name

                        try:
                            await self.download_file(
                                client, repo_name, file_path, local_path
                            )
                            downloaded.append(file_path)
                            result.total_files += 1
                        except httpx.HTTPError as e:
                            result.errors.append(
                                f"Failed to download {repo_name}/{file_path}: {e}"
                            )

                manifest["repos"][repo_name] = {
                    "tree_sha": tree_sha,
                    "files": downloaded,
                    "dest_dir": dest_dir,
                }
                result.repos[repo_name] = {
                    "files_downloaded": len(downloaded),
                    "tree_sha": tree_sha,
                }

            # Download direct files (PDFs, etc.)
            direct_count, direct_errors = await self.download_direct_files(
                client, data_dir
            )
            result.total_files += direct_count
            result.errors.extend(direct_errors)
            if direct_count:
                manifest["direct_downloads"] = {
                    name: config["filename"]
                    for name, config in DIRECT_DOWNLOADS.items()
                }

        # Write manifest
        manifest_path = data_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        return result
