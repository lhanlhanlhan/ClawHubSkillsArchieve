#!/usr/bin/env python3
"""
ClawHub Top 50 Skills Scraper — GitHub Archive Alternative (v2 - Auto-Ranking)
================================================================================
Fully automated: dynamically fetches the latest Top 50 ranking from clawskills.sh,
then downloads metadata + files from the GitHub archive.

Designed for daily cron / scheduled task usage.

Usage:
    python3 clawhub_scraper.py                    # Default: Top 50
    python3 clawhub_scraper.py --top 100          # Top 100
    python3 clawhub_scraper.py --output ./data    # Custom output dir
    GITHUB_TOKEN=ghp_xxx python3 clawhub_scraper.py  # With auth (5000 req/hr)
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
import argparse

# ============================================================
# Configuration
# ============================================================
CLAWSKILLS_URL = "https://clawskills.sh/"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/openclaw/skills/main/skills"
GITHUB_API_BASE = "https://api.github.com/repos/openclaw/skills/contents/skills"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def get_headers(accept="application/json, */*"):
    headers = {
        "User-Agent": "ClawHub-Skill-Scraper/2.0",
        "Accept": accept,
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def fetch_url(url, retries=3, delay=1.0, accept="application/json, */*"):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=get_headers(accept))
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429) and attempt < retries - 1:
                wait = delay * (2 ** attempt)
                print(f"  ⚠ Rate limited ({e.code}), waiting {wait:.0f}s...")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            raise
    return None


# ============================================================
# Step 1: Dynamically parse Top N from clawskills.sh
# ============================================================
def parse_clawskills_text(text, top_n=50):
    """
    Parse the plain-text output from clawskills.sh (after HTML tag stripping).
    
    The text after "DOWNLOADS ↓ STARS" is a sequence of lines like:
    
        Line A (entry header):
            " {prev_dl} {prev_stars} {rank} {display_name...} {owner} /skills "
            or for rank 1:
            " 1 {display_name...} {owner} /skills "
        
        Line B (description):
            " {description text}"
        
        Line C (empty):
            ""
    
    We match Line A using:
        (optional: downloads + stars) + rank + display_name + owner + /skills
    
    The actual slug will come from _meta.json later. For the GitHub path,
    we derive slug from display_name: lowercase + spaces→hyphens.
    """
    lines = text.split('\n')
    
    # Find the start of listing
    start_idx = None
    for i, line in enumerate(lines):
        if 'DOWNLOADS' in line and 'STARS' in line:
            start_idx = i + 1
            break
    
    if start_idx is None:
        return []
    
    # Pattern for entry header lines:
    # Optional: {downloads} {stars} before the rank
    # Then: {rank} {display_name_words...} {owner} /skills
    #
    # Owner is always the last word before "/skills" and contains no spaces
    # Display name is everything between rank and owner
    
    header_pattern = re.compile(
        r'^\s*'
        r'(?:[\d,.]+k?\s+[\d,.]+k?\s+)?'   # optional: prev_downloads prev_stars
        r'(\d{1,4})\s+'                      # rank (capture group 1)
        r'(.+?)\s+'                          # display name (capture group 2, non-greedy)
        r'([\w][\w\-\.]*)\s+'                # owner (capture group 3, no spaces)
        r'/skills\s*$'                        # literal "/skills" at end
    )
    
    # Also pattern to extract downloads+stars from the NEXT entry's header line
    # Format: "137.7k 597 2 gog steipete /skills"
    # The "137.7k 597" belongs to rank 1
    stats_pattern = re.compile(
        r'^\s*([\d,.]+k?)\s+([\d,.]+k?)\s+\d{1,4}\s+'
    )
    
    entries = []
    
    for i in range(start_idx, len(lines)):
        line = lines[i]
        m = header_pattern.match(line)
        if m:
            rank = int(m.group(1))
            display_name = m.group(2).strip()
            owner = m.group(3).strip().lower()
            
            # Derive slug from display name
            slug = re.sub(r'\s+', '-', display_name.lower().strip())
            slug = re.sub(r'[^a-z0-9\-\.]', '', slug)
            
            # Get description from next non-empty line
            desc = ""
            if i + 1 < len(lines) and lines[i + 1].strip():
                desc = lines[i + 1].strip()
            
            entries.append({
                "rank": rank,
                "slug": slug,
                "owner": owner,
                "display_name": display_name,
                "description": desc,
                "downloads": "?",
                "stars": "?",
                "line_idx": i,
            })
    
    # Now extract downloads/stars:
    # For each entry at line_idx, look at the NEXT entry's header line
    # The downloads+stars prefix of that line belongs to the current entry
    for i in range(len(entries)):
        if i + 1 < len(entries):
            next_line = lines[entries[i + 1]["line_idx"]]
            sm = stats_pattern.match(next_line)
            if sm:
                entries[i]["downloads"] = sm.group(1)
                entries[i]["stars"] = sm.group(2)
        else:
            # Last entry: look for stats in subsequent lines
            for j in range(entries[i]["line_idx"] + 1, min(entries[i]["line_idx"] + 5, len(lines))):
                line_j = lines[j].strip()
                sm2 = re.match(r'^([\d,.]+k?)\s+([\d,.]+k?)\s*$', line_j)
                if sm2:
                    entries[i]["downloads"] = sm2.group(1)
                    entries[i]["stars"] = sm2.group(2)
                    break
                # Also check if it's part of pagination text
                sm3 = re.match(r'^([\d,.]+k?)\s+([\d,.]+k?)\s+', line_j)
                if sm3 and not re.match(r'^\d{1,4}\s', line_j):
                    entries[i]["downloads"] = sm3.group(1)
                    entries[i]["stars"] = sm3.group(2)
                    break
    
    # Filter and deduplicate
    seen = set()
    result = []
    for entry in sorted(entries, key=lambda x: x["rank"]):
        if entry["rank"] > top_n:
            break
        key = (entry["owner"], entry["slug"])
        if key not in seen:
            seen.add(key)
            result.append((
                entry["owner"],
                entry["slug"],
                entry["downloads"],
                entry["stars"],
            ))
    
    return result[:top_n]


def fetch_top_skills_from_clawskills(top_n=50):
    """Fetch and parse Top N skills from clawskills.sh."""
    print(f"[Step 1] Fetching Top {top_n} ranking from clawskills.sh ...")
    
    html_bytes = fetch_url(CLAWSKILLS_URL, accept="text/html")
    if not html_bytes:
        print("  ✗ Failed to fetch clawskills.sh")
        return []
    
    html = html_bytes.decode("utf-8", errors="replace")
    # Strip HTML tags but preserve line breaks
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'&#x[0-9a-fA-F]+;', ' ', text)
    # Normalize spaces within lines but keep newlines
    lines = text.split('\n')
    text = '\n'.join(re.sub(r'[ \t]+', ' ', line) for line in lines)
    
    skills = parse_clawskills_text(text, top_n)
    
    if skills:
        print(f"  ✓ Parsed {len(skills)} skills from live data")
        # Preview first 3
        for owner, slug, dl, st in skills[:3]:
            print(f"    #{skills.index((owner, slug, dl, st))+1}: {owner}/{slug} ({dl} dl, {st} ★)")
    else:
        print("  ⚠ Parsing failed, check clawskills.sh format")
    
    return skills


# ============================================================
# Step 2 & 3: Metadata + ZIP download
# ============================================================
def fetch_meta_json(owner, slug):
    url = f"{GITHUB_RAW_BASE}/{owner}/{slug}/_meta.json"
    data = fetch_url(url)
    if data:
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return None


def download_skill_zip(owner, slug, zip_dir):
    api_url = f"{GITHUB_API_BASE}/{owner}/{slug}"
    
    file_list = []
    api_data = fetch_url(api_url)
    
    if api_data:
        try:
            items = json.loads(api_data)
            if isinstance(items, list):
                file_list = [
                    (item["name"], item["download_url"])
                    for item in items
                    if item["type"] == "file" and item.get("download_url")
                ]
        except (json.JSONDecodeError, KeyError):
            pass
    
    if not file_list:
        base_url = f"{GITHUB_RAW_BASE}/{owner}/{slug}"
        for fname in ["SKILL.md", "_meta.json"]:
            data = fetch_url(f"{base_url}/{fname}", retries=1)
            if data:
                file_list.append((fname, f"{base_url}/{fname}"))
    
    if not file_list:
        return None
    
    zip_filename = f"{owner}__{slug}.zip"
    zip_path = os.path.join(zip_dir, zip_filename)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname, dl_url in file_list:
            try:
                content = fetch_url(dl_url)
                if content:
                    zf.writestr(f"{owner}/{slug}/{fname}", content)
            except Exception as e:
                print(f"    ⚠ Failed to download {fname}: {e}")
    
    return zip_path if os.path.exists(zip_path) else None


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="ClawHub Top N Skills Scraper (GitHub Archive)")
    parser.add_argument("--top", type=int, default=50, help="Number of top skills to fetch (default: 50)")
    parser.add_argument("--output", type=str, default="clawhub_top50_skills", help="Output directory")
    args = parser.parse_args()
    
    output_dir = args.output
    meta_dir = os.path.join(output_dir, "metadata")
    zip_dir = os.path.join(output_dir, "zips")
    os.makedirs(meta_dir, exist_ok=True)
    os.makedirs(zip_dir, exist_ok=True)
    
    top_n = args.top
    
    print("=" * 70)
    print(f"ClawHub Top {top_n} Skills Scraper — v2 (Auto-Ranking)")
    print("=" * 70)
    print(f"  Ranking source : clawskills.sh")
    print(f"  Data source    : github.com/openclaw/skills")
    print(f"  GitHub Token   : {'✓ configured' if GITHUB_TOKEN else '✗ not set (60 req/hr limit)'}")
    print(f"  Output         : {output_dir}/")
    print()
    
    top_skills = fetch_top_skills_from_clawskills(top_n)
    
    if not top_skills:
        print("\n✗ Could not fetch ranking. Exiting.")
        sys.exit(1)
    
    results = []
    success_count = 0
    fail_count = 0
    
    for i, (owner, slug, downloads, stars) in enumerate(top_skills, 1):
        print(f"\n[{i:02d}/{len(top_skills)}] {owner}/{slug} ({downloads} downloads, {stars} stars)")
        
        meta = fetch_meta_json(owner, slug)
        if meta:
            version = meta.get("latest", {}).get("version", "unknown")
            display_name = meta.get("displayName", slug)
            published_at = meta.get("latest", {}).get("publishedAt", 0)
            canonical_slug = meta.get("slug", slug)
            
            meta_path = os.path.join(meta_dir, f"{owner}__{canonical_slug}__meta.json")
            with open(meta_path, 'w') as f:
                json.dump(meta, f, indent=2)
            
            print(f"  ✓ slug={canonical_slug}, version={version}")
        else:
            version = "unknown"
            display_name = slug
            canonical_slug = slug
            published_at = 0
            print(f"  ⚠ _meta.json not found")
        
        zip_path = download_skill_zip(owner, slug, zip_dir)
        if zip_path:
            print(f"  ✓ ZIP: {os.path.basename(zip_path)}")
            success_count += 1
        else:
            print(f"  ✗ ZIP download failed")
            fail_count += 1
        
        results.append({
            "rank": i,
            "owner": owner,
            "slug": canonical_slug,
            "version": version,
            "display_name": display_name,
            "downloads": downloads,
            "stars": stars,
            "published_at": published_at,
            "unique_id": f"{owner}/{canonical_slug}@{version}",
            "clawhub_url": f"https://clawhub.ai/{owner}/{canonical_slug}",
            "github_url": f"https://github.com/openclaw/skills/tree/main/skills/{owner}/{slug}",
            "zip_file": f"zips/{os.path.basename(zip_path)}" if zip_path else None,
            "meta_file": f"metadata/{owner}__{canonical_slug}__meta.json" if meta else None,
        })
        
        if i % 10 == 0:
            time.sleep(1.0)
        else:
            time.sleep(0.3)
    
    manifest = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "github.com/openclaw/skills + clawskills.sh",
        "ranking_source": "clawskills.sh (sorted by downloads)",
        "total": len(results),
        "success": success_count,
        "failed": fail_count,
        "skills": results,
    }
    
    manifest_path = os.path.join(output_dir, "top50_skills_manifest.json")
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    csv_path = os.path.join(output_dir, "top50_skills_summary.csv")
    with open(csv_path, 'w') as f:
        f.write("rank,owner,slug,version,unique_id,downloads,stars,clawhub_url,github_url,zip_file\n")
        for r in results:
            uid = r["unique_id"].replace(",", ";")
            f.write(f'{r["rank"]},{r["owner"]},{r["slug"]},{r["version"]},'
                    f'{uid},{r["downloads"]},{r["stars"]},'
                    f'{r["clawhub_url"]},{r["github_url"]},'
                    f'{r.get("zip_file") or "N/A"}\n')
    
    print("\n" + "=" * 70)
    print(f"DONE! {success_count} succeeded, {fail_count} failed")
    print(f"  Manifest : {manifest_path}")
    print(f"  CSV      : {csv_path}")
    print(f"  ZIPs     : {zip_dir}/ ({success_count} files)")
    print(f"  Metadata : {meta_dir}/")
    print("=" * 70)
    
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
