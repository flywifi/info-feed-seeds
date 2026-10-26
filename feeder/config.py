"""Repository configuration: source folders, profiles, guard releases, and the checks `hub lint`
runs over them. Profile matching rules (which sources a profile selects, which topic tags an item
earns) also live here so the poller and the tests share one definition.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ROOT, adapters, categories, guard, items, jsonio, topics
from .schema_validate import validate

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GUARD_RANK = {guard.CLEAN: 0, guard.REVIEW: 1, guard.QUARANTINE: 2, guard.BLOCK: 3}
RELEASES_COMMENT = ("Items held by the injection guard and released by a person: "
                    "{sha, date, reason}. Appended by hub release after a person checks the item; never edited by the poll.")
PROFILE_DEFAULTS = {"window_days": 30, "backfill_days": 0, "outputs": ["md", "json"],
                    "guard": {"hold_at": guard.QUARANTINE}}


class ConfigError(Exception):
    """A manifest, profile or release file is missing or invalid (exit code 2)."""


@dataclass
class Source:
    id: str
    manifest: dict
    methods: dict | None
    folder: Path
    local: bool = False
    digest_default: bool = True   # False when the source's category is shown only by a profile that names it

    @property
    def category(self) -> str | None:
        return self.manifest.get("category")

    @property
    def subcategory(self) -> str | None:
        return self.manifest.get("subcategory")

    @property
    def publisher(self) -> str:
        return self.manifest.get("publisher") or self.id

    @property
    def topics(self) -> list[str]:
        return list(self.manifest.get("topics") or [])

    def poll_routes(self) -> list[dict]:
        """Routes the poller reads (a registered adapter with its required params), in manifest
        order with the route that worked last first. Landing routes are left out."""
        routes = [r for r in self.manifest.get("routes", []) if adapters.configured(r)]
        records = {rec.get("url"): rec for rec in (self.methods or {}).get("routes", [])}
        last = None
        best = ""
        for route in routes:
            success = (records.get(route["url"]) or {}).get("last_success")
            if success and success.get("tried_at", "") > best:
                best, last = success["tried_at"], route["url"]
        if last is not None:
            routes.sort(key=lambda r: r["url"] != last)
        return routes

    def proof_records(self) -> list[dict]:
        """The latest attempt of every configured route that methods.json records."""
        polled = {r["url"] for r in self.poll_routes()}
        return [rec["attempts"][-1] for rec in (self.methods or {}).get("routes", [])
                if rec.get("url") in polled and rec.get("attempts")]


@dataclass
class Repo:
    root: Path = ROOT
    _topics: dict | None = field(default=None, repr=False)
    _categories: dict | None = field(default=None, repr=False)

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def schema_dir(self) -> Path:
        return self.root / "schema"

    @property
    def releases_file(self) -> Path:
        return self.root / "guard" / "releases.json"

    def schema(self, name: str) -> dict:
        return jsonio.load_json(self.schema_dir / name)

    def topics(self) -> dict:
        if self._topics is None:
            self._topics = topics.load_topics(self.schema_dir / "topics.json")
        return self._topics

    @property
    def categories_file(self) -> Path:
        return self.schema_dir / "categories.json"

    def categories(self) -> dict | None:
        """The category registry, or None when schema/categories.json is missing or unreadable (lint reports it)."""
        if self._categories is None:
            try:
                self._categories = categories.load_categories(self.categories_file)
            except (OSError, ValueError):
                return None
        return self._categories

    # -- sources

    def source_folders(self) -> list[Path]:
        if not self.sources_dir.is_dir():
            return []
        return sorted(p for p in self.sources_dir.iterdir() if p.is_dir() and p.name != "retired")

    def load_source(self, folder: Path) -> Source:
        local = False
        manifest_path = folder / "source.json"
        if not manifest_path.exists() and (folder / "source.local.json").exists():
            manifest_path, local = folder / "source.local.json", True
        if not manifest_path.exists():
            raise ConfigError(f"{_rel(folder, self.root)}: no source.json")
        methods_path = folder / ("methods.local.json" if local else "methods.json")
        try:
            manifest = jsonio.load_json(manifest_path)
            methods = jsonio.load_json(methods_path) if methods_path.exists() else None
        except ValueError as exc:
            raise ConfigError(f"{_rel(folder, self.root)}: invalid JSON: {exc}") from None
        reg = self.categories()
        shown = True if reg is None else categories.digest_default(reg, manifest.get("category"))
        return Source(id=folder.name, manifest=manifest, methods=methods, folder=folder, local=local,
                      digest_default=shown)

    def load_sources(self) -> dict[str, Source]:
        return {f.name: self.load_source(f) for f in self.source_folders()}

    # -- profiles

    def profile_path(self, name: str) -> Path:
        path = self.profiles_dir / f"{name}.json"
        if not path.exists() and (self.profiles_dir / f"{name}.local.json").exists():
            path = self.profiles_dir / f"{name}.local.json"
        return path

    def load_profile(self, name: str) -> dict:
        path = self.profile_path(name)
        if not path.exists():
            raise ConfigError(f"no profile named {name!r} (looked for {_rel(path, self.root)})")
        try:
            profile = jsonio.load_json(path)
        except ValueError as exc:
            raise ConfigError(f"{_rel(path, self.root)}: invalid JSON: {exc}") from None
        errors = validate(profile, self.schema("profile.schema.json"))
        if errors:
            raise ConfigError(f"{_rel(path, self.root)}: " + "; ".join(errors))
        return with_defaults(profile)

    def profile_names(self) -> list[str]:
        """Tracked profiles only; *.local.json profiles are loaded by name, never listed."""
        if not self.profiles_dir.is_dir():
            return []
        return sorted(p.stem for p in self.profiles_dir.glob("*.json") if not p.name.endswith(".local.json"))

    # -- releases

    def load_releases(self) -> dict:
        if not self.releases_file.exists():
            return {"_comment": RELEASES_COMMENT, "releases": []}
        data = jsonio.load_json(self.releases_file)
        problems = release_problems(data)
        if problems:
            raise ConfigError("guard/releases.json: " + "; ".join(problems))
        return data

    def released_shas(self) -> set[str]:
        return {r["sha"] for r in self.load_releases()["releases"]}


def with_defaults(profile: dict) -> dict:
    out = dict(profile)
    for key, value in PROFILE_DEFAULTS.items():
        out.setdefault(key, copy.deepcopy(value))
    out.setdefault("subtopic_keywords", {})
    out.setdefault("topic_keywords", {})
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- matching

def _parent(tag: str) -> str:
    return tag.split("/", 1)[0]


def category_allowed(profile: dict, source: Source) -> bool:
    """A profile's `categories` list (category or category/subcategory) names what it shows; without
    the list it shows every category whose digest_default is true, so machine data stays out."""
    names = profile.get("categories")
    if names is None:
        return source.digest_default
    return source.category in names or f"{source.category}/{source.subcategory}" in names


def profile_selects(profile: dict, source: Source) -> bool:
    """A source listed by id is selected; otherwise one of its topics must match and its category must be allowed."""
    if source.id in profile.get("sources", []):
        return True
    wanted = set(profile.get("topics", []))
    if not any(t in wanted or _parent(t) in wanted for t in source.topics):
        return False
    return category_allowed(profile, source)


def selected_sources(profile: dict, sources: dict[str, Source]) -> list[Source]:
    return [s for _, s in sorted(sources.items()) if profile_selects(profile, s)]


def match_text(item: dict) -> str:
    return items.sanitize(item.get("title")) + " " + items.sanitize(item.get("summary"))


def accepts(profile: dict, item: dict) -> bool:
    return items.match_keywords(match_text(item), profile.get("include_keywords", []),
                                profile.get("exclude_keywords", []))["accepted"]


def keyword_tags(profile: dict, item: dict) -> list[str]:
    """Tags earned by keywords: `subtopic_keywords` (include and exclude per tag) and the older
    include-only `topic_keywords`. A subtopic tag also adds its parent topic."""
    text = match_text(item)
    tags: set[str] = set()
    for tag, rule in profile.get("subtopic_keywords", {}).items():
        include = rule.get("include", [])
        if include and items.match_keywords(text, include, rule.get("exclude", []))["accepted"]:
            tags.update({tag, _parent(tag)})
    tags.update(items.tag_topics(text, profile.get("topic_keywords", {})))
    return sorted(tags)


def item_tags(profile: dict, source: Source, item: dict) -> tuple[list[str], str]:
    """(all tags, primary tag). The primary tag picks the digest section: the first keyword-earned
    subtopic, else the source's first subtopic, else the source's first topic."""
    earned = keyword_tags(profile, item)
    tags = sorted(set(source.topics) | set(earned) | {_parent(t) for t in source.topics})
    primary = next((t for t in earned if "/" in t), None)
    if primary is None:
        primary = next((t for t in source.topics if "/" in t), source.topics[0] if source.topics else "uncategorised")
    return tags, primary


def is_held(level: str, hold_at: str) -> bool:
    return level != guard.CLEAN and GUARD_RANK.get(level, 0) >= GUARD_RANK.get(hold_at, 2)


# --------------------------------------------------------------------------- lint

def release_problems(data) -> list[str]:
    if not isinstance(data, dict) or set(data) != {"_comment", "releases"}:
        return ["must be an object with exactly _comment and releases"]
    if not isinstance(data["releases"], list):
        return ["releases must be a list"]
    problems = []
    for i, rec in enumerate(data["releases"]):
        if not isinstance(rec, dict) or set(rec) != {"sha", "date", "reason"}:
            problems.append(f"releases[{i}] must have exactly sha, date and reason")
            continue
        if not SHA_RE.match(str(rec["sha"])):
            problems.append(f"releases[{i}].sha is not a sha256")
        if not DATE_RE.match(str(rec["date"])):
            problems.append(f"releases[{i}].date is not YYYY-MM-DD")
        if not str(rec["reason"]).strip():
            problems.append(f"releases[{i}].reason is empty")
    return problems


def json_files(repo: Repo) -> list[Path]:
    """Every tracked-config JSON file `hub fmt` owns."""
    out: list[Path] = []
    for folder in (repo.sources_dir, repo.profiles_dir, repo.schema_dir, repo.root / "guard"):
        if folder.is_dir():
            out.extend(sorted(p for p in folder.rglob("*.json") if not p.name.endswith(".local.json")))
    return out


def lint(repo: Repo) -> list[str]:
    """Return every defect found; an empty list is clean."""
    problems: list[str] = []
    tags = topics.all_tags(repo.topics())
    reg = repo.categories()
    if reg is None:
        problems.append("schema/categories.json is missing or not valid JSON")
    else:
        problems += [f"schema/categories.json: {e}" for e in validate(reg, repo.schema("categories.schema.json"))]
        if not problems:
            problems += [f"schema/categories.json: {p}" for p in categories.registry_problems(reg, tags)]
        problems += [f"schema/categories.json: key {k!r} appears twice in one object"
                     for k in categories.duplicate_keys(repo.categories_file)]
    refs = categories.all_refs(reg) if reg is not None and not problems else None
    source_schema = repo.schema("source.schema.json")
    methods_schema = repo.schema("methods.schema.json")
    known_ids: set[str] = set()

    for folder in repo.source_folders():
        rel = _rel(folder, repo.root)
        try:
            src = repo.load_source(folder)
        except ConfigError as exc:
            problems.append(str(exc))
            continue
        known_ids.add(src.id)
        name = "source.local.json" if src.local else "source.json"
        problems += [f"{rel}/{name}: {e}" for e in validate(src.manifest, source_schema)]
        if src.manifest.get("id") != folder.name:
            problems.append(f"{rel}/{name}: id {src.manifest.get('id')!r} does not equal the folder name")
        for tag in src.topics:
            if tag not in tags:
                problems.append(f"{rel}/{name}: topic {tag!r} is not in schema/topics.json")
        if refs is not None and src.category is not None:
            if src.category not in reg["categories"]:
                problems.append(f"{rel}/{name}: category {src.category!r} is not in schema/categories.json")
            elif not categories.is_valid_pair(reg, src.category, src.subcategory):
                problems.append(f"{rel}/{name}: subcategory {src.subcategory!r} is not under category {src.category!r}")
            for industry in src.manifest.get("industries", []):
                if industry not in categories.industry_ids(reg):
                    problems.append(f"{rel}/{name}: industry {industry!r} is not in schema/categories.json")
        if "auth" in src.manifest and not src.local:
            problems.append(f"{rel}: a credential-gated source must live in source.local.json")
        for route in src.manifest.get("routes", []):
            problems += [f"{rel}/{name}: {p}" for p in adapters.lint_route(route)]
        if not src.poll_routes():
            problems.append(f"{rel}/{name}: no configured route the poller can read")
        if src.methods is None:
            problems.append(f"{rel}: no methods.json")
        else:
            if src.poll_routes() and not any(adapters.live_proof(a) for a in src.proof_records()):
                problems.append(f"{rel}/methods.json: no configured route whose latest attempt is a live 200 "
                                "with a sample item")
            problems += [f"{rel}/methods.json: {e}" for e in validate(src.methods, methods_schema)]
            if src.methods.get("id") != folder.name:
                problems.append(f"{rel}/methods.json: id does not equal the folder name")
            route_urls = {r.get("url") for r in src.manifest.get("routes", [])}
            for rec in src.methods.get("routes", []):
                if rec.get("url") not in route_urls:
                    problems.append(f"{rel}/methods.json: route {rec.get('url')} is not in source.json")

    profile_schema = repo.schema("profile.schema.json")
    for name in repo.profile_names():
        path = repo.profiles_dir / f"{name}.json"
        rel = _rel(path, repo.root)
        try:
            profile = jsonio.load_json(path)
        except ValueError as exc:
            problems.append(f"{rel}: invalid JSON: {exc}")
            continue
        problems += [f"{rel}: {e}" for e in validate(profile, profile_schema)]
        if profile.get("name") != name:
            problems.append(f"{rel}: name {profile.get('name')!r} does not equal the file name")
        for tag in list(profile.get("topics", [])) + list(profile.get("subtopic_keywords", {})) \
                + list(profile.get("topic_keywords", {})):
            if tag not in tags:
                problems.append(f"{rel}: topic {tag!r} is not in schema/topics.json")
        for sid in profile.get("sources", []):
            if sid not in known_ids:
                problems.append(f"{rel}: source {sid!r} has no folder under sources/")
        for ref in profile.get("categories", []):
            if refs is not None and ref not in refs:
                problems.append(f"{rel}: category {ref!r} is not in schema/categories.json")

    if repo.releases_file.exists():
        try:
            problems += [f"guard/releases.json: {p}" for p in release_problems(jsonio.load_json(repo.releases_file))]
        except ValueError as exc:
            problems.append(f"guard/releases.json: invalid JSON: {exc}")
    else:
        problems.append("guard/releases.json is missing")

    for path in json_files(repo):
        try:
            canonical = jsonio.is_canonical(path)
        except OSError:
            canonical = False
        if not canonical:
            problems.append(f"{_rel(path, repo.root)}: not in canonical form (run hub fmt)")
    return problems


__all__ = [
    "ConfigError", "GUARD_RANK", "RELEASES_COMMENT", "Repo", "Source", "accepts", "category_allowed", "is_held",
    "item_tags", "json_files", "keyword_tags", "lint", "profile_selects", "release_problems",
    "selected_sources", "with_defaults",
]
