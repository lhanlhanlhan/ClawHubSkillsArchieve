#!/usr/bin/env python3
"""
Daily scraper: fetch Top N skills by downloads and archive them into this repo.

Data sources:
  - Ranking: https://clawskills.sh/ (downloads-sorted index)
  - Skill files: GitHub repo contents API + raw file downloads

Usage:
  python3 scrape_clawhub_top_skills.py
  python3 scrape_clawhub_top_skills.py --top 100
  python3 scrape_clawhub_top_skills.py --output archives/top100

Environment:
  GITHUB_TOKEN (optional but recommended): raises GitHub API rate limits.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


def _get_env(name: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value else ""


def _github_headers(accept: str) -> dict[str, str]:
    headers = {
        "User-Agent": "ClawHubSkillsArchive/1.0",
        "Accept": accept,
    }
    token = _get_env("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _fetch_bytes(url: str, *, accept: str, retries: int = 3, delay_s: float = 1.0) -> bytes | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_github_headers(accept))
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"[fetch] 404: {url}")
                return None
            if e.code == 403:
                remaining = (e.headers.get("X-RateLimit-Remaining") or "").strip()
                if remaining == "0" or "rate limit" in str(getattr(e, "reason", "")).lower():
                    print(f"[fetch] rate limited: {url}")
                    if attempt < retries - 1:
                        time.sleep(delay_s * (2**attempt))
                        continue
                    return None
            if e.code in (403, 429) and attempt < retries - 1:
                print(f"[fetch] retrying {e.code} ({attempt + 1}/{retries}): {url}")
                time.sleep(delay_s * (2**attempt))
                continue
            print(f"[fetch] http error {e.code}: {url}")
            raise
        except Exception:
            if attempt < retries - 1:
                print(f"[fetch] retrying error ({attempt + 1}/{retries}): {url}")
                time.sleep(delay_s * (2**attempt))
                continue
            raise
    return None


def _fetch_json(url: str, *, retries: int = 3) -> object | None:
    data = _fetch_bytes(url, accept="application/vnd.github+json", retries=retries)
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _derive_slug(display_name: str) -> str:
    slug = re.sub(r"\s+", "-", display_name.lower().strip())
    slug = re.sub(r"[^a-z0-9\-\.]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "unknown"


def _strip_html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"&#x[0-9a-fA-F]+;", " ", text)
    lines = text.split("\n")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in lines)


def _parse_clawskills_text(text: str, top_n: int) -> list[dict[str, object]]:
    lines = text.split("\n")
    start_idx = None
    for i, line in enumerate(lines):
        if "DOWNLOADS" in line and "STARS" in line:
            start_idx = i + 1
            break
    if start_idx is None:
        start_idx = 0

    header_pattern = re.compile(
        r"^\s*(?:[\d,.]+k?\s+[\d,.]+k?\s+)?(\d{1,4})\s+(.+?)\s+([\w][\w\-\.]*)\s+/skills\s*$"
    )
    stats_pattern = re.compile(r"^\s*([\d,.]+k?)\s+([\d,.]+k?)\s+\d{1,4}\s+")

    entries: list[dict[str, object]] = []
    for i in range(start_idx, len(lines)):
        m = header_pattern.match(lines[i])
        if not m:
            continue
        rank = int(m.group(1))
        display_name = m.group(2).strip()
        owner = m.group(3).strip().lower()
        desc = ""
        if i + 1 < len(lines) and lines[i + 1].strip():
            desc = lines[i + 1].strip()
        entries.append(
            {
                "rank": rank,
                "owner": owner,
                "display_name": display_name,
                "slug_guess": _derive_slug(display_name),
                "description": desc,
                "downloads": "?",
                "stars": "?",
                "line_idx": i,
            }
        )

    for i in range(len(entries)):
        if i + 1 < len(entries):
            next_line = lines[int(entries[i + 1]["line_idx"])]
            sm = stats_pattern.match(next_line)
            if sm:
                entries[i]["downloads"] = sm.group(1)
                entries[i]["stars"] = sm.group(2)
        else:
            for j in range(int(entries[i]["line_idx"]) + 1, min(int(entries[i]["line_idx"]) + 6, len(lines))):
                sm2 = re.match(r"^([\d,.]+k?)\s+([\d,.]+k?)\s*$", lines[j].strip())
                if sm2:
                    entries[i]["downloads"] = sm2.group(1)
                    entries[i]["stars"] = sm2.group(2)
                    break

    seen: set[tuple[str, str]] = set()
    result: list[dict[str, object]] = []
    for entry in sorted(entries, key=lambda e: int(e["rank"])):  # type: ignore[arg-type]
        if int(entry["rank"]) > top_n:
            break
        key = (str(entry["owner"]), str(entry["slug_guess"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result[:top_n]


def fetch_top_skills(top_n: int) -> list[dict[str, object]]:
    print(f"[rank] fetching top {top_n} from clawskills.sh")
    html_bytes = _fetch_bytes("https://clawskills.sh/", accept="text/html", retries=3)
    if not html_bytes:
        print("[rank] failed to fetch clawskills.sh")
        return []
    html = html_bytes.decode("utf-8", errors="replace")
    text = _strip_html_to_text(html)
    entries = _parse_clawskills_text(text, top_n)
    print(f"[rank] parsed {len(entries)} entries")
    return entries


def _github_raw_url(repo: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"


def fetch_meta(repo: str, ref: str, owner: str, slug: str) -> dict[str, object] | None:
    url = _github_raw_url(repo, ref, f"skills/{owner}/{slug}/_meta.json")
    data = _fetch_bytes(url, accept="application/json, */*", retries=2)
    if not data:
        print(f"[meta] missing: {owner}/{slug}")
        return None
    try:
        meta = json.loads(data)
    except json.JSONDecodeError:
        return None
    return meta if isinstance(meta, dict) else None


def _list_repo_files_recursive(repo: str, path: str) -> list[dict[str, str]]:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    data = _fetch_json(url, retries=3)
    if not isinstance(data, list):
        print(f"[list] no content list: {path}")
        return []

    files: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        item_path = item.get("path")
        if not isinstance(item_path, str):
            continue
        if item_type == "file":
            dl_url = item.get("download_url")
            if isinstance(dl_url, str) and dl_url:
                files.append({"path": item_path, "download_url": dl_url})
        elif item_type == "dir":
            files.extend(_list_repo_files_recursive(repo, item_path))
    return files


def _write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _remove_tree(path: str) -> None:
    if not os.path.exists(path):
        return
    if os.path.isfile(path) or os.path.islink(path):
        os.remove(path)
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))
    os.rmdir(path)


def _prune_skill_tree(skills_dir: str, keep: set[tuple[str, str]]) -> None:
    if not os.path.isdir(skills_dir):
        return
    for owner in os.listdir(skills_dir):
        owner_path = os.path.join(skills_dir, owner)
        if not os.path.isdir(owner_path):
            continue
        for slug in os.listdir(owner_path):
            slug_path = os.path.join(owner_path, slug)
            if not os.path.isdir(slug_path):
                continue
            if (owner, slug) in keep:
                continue
            _remove_tree(slug_path)
        if not os.listdir(owner_path):
            os.rmdir(owner_path)


def _has_contents_api_dir(repo: str, path: str) -> bool:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    data = _fetch_json(url, retries=2)
    return isinstance(data, list) and len(data) > 0


def _resolve_slug_path(repo: str, owner: str, slug_guess: str, canonical_slug: str) -> str:
    token = _get_env("GITHUB_TOKEN")
    if not token:
        print(f"[slug] no token, using guess: {owner}/{slug_guess}")
        return slug_guess

    guess_path = f"skills/{owner}/{slug_guess}"
    if _has_contents_api_dir(repo, guess_path):
        print(f"[slug] resolved: {owner}/{slug_guess}")
        return slug_guess

    if canonical_slug and canonical_slug != slug_guess:
        canonical_path = f"skills/{owner}/{canonical_slug}"
        if _has_contents_api_dir(repo, canonical_path):
            print(f"[slug] resolved canonical: {owner}/{canonical_slug}")
            return canonical_slug

    print(f"[slug] fallback to guess: {owner}/{slug_guess}")
    return slug_guess


def download_skill_dir(repo: str, ref: str, owner: str, slug_path: str, skills_out_dir: str) -> bool:
    root = f"skills/{owner}/{slug_path}"
    files = _list_repo_files_recursive(repo, root)

    wrote_any = False
    if not files:
        print(f"[archive] fallback raw: {owner}/{slug_path}")
        for fname in ["SKILL.md", "_meta.json"]:
            raw_url = _github_raw_url(repo, ref, f"{root}/{fname}")
            content = _fetch_bytes(raw_url, accept="application/octet-stream, */*", retries=2)
            if content is None:
                continue
            local_path = os.path.join(skills_out_dir, owner, slug_path, fname)
            _write_bytes(local_path, content)
            wrote_any = True
        return wrote_any

    print(f"[archive] files: {owner}/{slug_path} ({len(files)})")
    for file_item in files:
        dl_url = file_item["download_url"]
        file_path = file_item["path"]
        rel = file_path[len(root) + 1 :] if file_path.startswith(root + "/") else file_path
        local_path = os.path.join(skills_out_dir, owner, slug_path, rel)
        content = _fetch_bytes(dl_url, accept="application/octet-stream, */*", retries=3)
        if content is None:
            continue
        _write_bytes(local_path, content)
        wrote_any = True

    return wrote_any


def _safe_filename(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.\-]+", "_", s).strip("_") or "unknown"


def _write_json(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _prune_dir(dir_path: str, keep_filenames: set[str]) -> None:
    if not os.path.isdir(dir_path):
        return
    for name in os.listdir(dir_path):
        full = os.path.join(dir_path, name)
        if not os.path.isfile(full):
            continue
        if name in keep_filenames:
            continue
        os.remove(full)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--repo", type=str, default="clawdbot/skills")
    parser.add_argument("--ref", type=str, default="main")
    args = parser.parse_args()

    top_n = int(args.top)
    repo = str(args.repo).strip()
    ref = str(args.ref).strip() or "main"
    output_dir = str(args.output).strip() or f"archives/top{top_n}"

    meta_dir = os.path.join(output_dir, "metadata")
    skills_dir = os.path.join(output_dir, "skills")
    os.makedirs(meta_dir, exist_ok=True)
    os.makedirs(skills_dir, exist_ok=True)

    print(f"[run] repo={repo} ref={ref} top={top_n} output={output_dir}")
    ranking = fetch_top_skills(top_n)
    if not ranking:
        print("[run] no ranking, exiting")
        return 1

    results: list[dict[str, object]] = []
    keep_meta: set[str] = set()
    keep_skills: set[tuple[str, str]] = set()
    success = 0
    failed = 0

    for idx, entry in enumerate(sorted(ranking, key=lambda e: int(e["rank"])), 1):  # type: ignore[arg-type]
        owner = str(entry["owner"])
        slug_guess = str(entry["slug_guess"])
        downloads = str(entry.get("downloads") or "?")
        stars = str(entry.get("stars") or "?")
        print(f"[skill] #{idx} {owner}/{slug_guess} ({downloads} dl, {stars} ★)")

        meta = fetch_meta(repo, ref, owner, slug_guess)
        canonical_slug = str(meta.get("slug") if meta else slug_guess)  # type: ignore[union-attr]
        display_name = str(meta.get("displayName") if meta else entry.get("display_name") or slug_guess)  # type: ignore[union-attr]
        latest = meta.get("latest") if isinstance(meta, dict) else None
        version = str(latest.get("version") if isinstance(latest, dict) else "unknown")
        published_at = int(latest.get("publishedAt") if isinstance(latest, dict) else 0)
        commit_url = str(latest.get("commit") if isinstance(latest, dict) else "")

        slug_path = _resolve_slug_path(repo, owner, slug_guess, canonical_slug)

        meta_filename = f"{_safe_filename(owner)}__{_safe_filename(canonical_slug)}__meta.json"
        meta_path = os.path.join(meta_dir, meta_filename)
        if meta:
            _write_json(meta_path, meta)
            keep_meta.add(meta_filename)

        keep_skills.add((owner, slug_path))

        archived = download_skill_dir(repo, ref, owner, slug_path, skills_dir)
        if archived:
            success += 1
            print(f"[skill] archived: {owner}/{slug_path}")
        else:
            failed += 1
            print(f"[skill] archive failed: {owner}/{slug_path}")

        results.append(
            {
                "rank": idx,
                "owner": owner,
                "slug": canonical_slug,
                "slug_path": slug_path,
                "version": version,
                "display_name": display_name,
                "downloads": downloads,
                "stars": stars,
                "published_at": published_at,
                "commit": commit_url or None,
                "clawhub_url": f"https://clawhub.ai/{owner}/{canonical_slug}",
                "github_url": f"https://github.com/{repo}/tree/{ref}/skills/{owner}/{slug_path}",
                "archive_dir": f"skills/{owner}/{slug_path}",
                "archived": archived,
                "meta_file": f"metadata/{meta_filename}" if meta else None,
            }
        )

        if idx % 10 == 0:
            time.sleep(1.0)
        else:
            time.sleep(0.2)

    _prune_dir(meta_dir, keep_meta)
    _prune_skill_tree(skills_dir, keep_skills)
    print(f"[run] done: success={success} failed={failed}")

    scraped_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest = {
        "scraped_at": scraped_at,
        "repo": repo,
        "ref": ref,
        "ranking_source": "https://clawskills.sh/",
        "top": top_n,
        "total": len(results),
        "success": success,
        "failed": failed,
        "skills": results,
    }

    manifest_path = os.path.join(output_dir, f"top{top_n}_skills_manifest.json")
    _write_json(manifest_path, manifest)

    csv_lines = [
        "rank,owner,slug,version,downloads,stars,clawhub_url,github_url,archive_dir,archived,meta_file",
    ]
    for r in results:
        csv_lines.append(
            ",".join(
                [
                    str(r["rank"]),
                    str(r["owner"]).replace(",", ";"),
                    str(r["slug"]).replace(",", ";"),
                    str(r["version"]).replace(",", ";"),
                    str(r["downloads"]).replace(",", ";"),
                    str(r["stars"]).replace(",", ";"),
                    str(r["clawhub_url"]),
                    str(r["github_url"]),
                    str(r["archive_dir"] or "N/A"),
                    str(bool(r["archived"])),
                    str(r["meta_file"] or "N/A"),
                ]
            )
        )

    csv_path = os.path.join(output_dir, f"top{top_n}_skills_summary.csv")
    _write_text(csv_path, "\n".join(csv_lines) + "\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
