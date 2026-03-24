# ClawHub Top 50 Skills Scraper

> **绕过 ClawHub 速率限制**，通过 GitHub 官方归档仓库抓取下载量最高的 Top 50 个 OpenClaw Skill，包括完整的 slug + version 元数据和 ZIP 打包文件。

---

## 为什么需要这个脚本？

[ClawHub](https://clawhub.ai) 是 OpenClaw 生态的公共 Skill 注册中心，但其 API 有**极其严格的速率限制**：

- 3-4 次请求即可触发限制
- 触发后封禁持续 30 分钟以上
- 无速率限制 Header 反馈，无官方文档说明限额
- 基本的 `install → publish → install` 工作流都会被封

本脚本完全绕过 clawhub.ai，改用两个**无速率限制问题**的替代数据源。

## 工作原理

```
┌─────────────────────────────────────────────────────────────────┐
│                        数据流架构                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ① ClawSkills.sh ──────────► Top 50 排名列表                   │
│      (clawskills.sh)           (owner, slug, downloads)         │
│      第三方索引站                                                │
│      5,147 个已过滤 Skill                                       │
│      按下载量排序                                                │
│                                                                 │
│   ② GitHub Archive ─────────► 元数据 + 文件内容                  │
│      (openclaw/skills)         _meta.json → slug, version       │
│      官方归档仓库               SKILL.md   → 技能文档             │
│      101K+ commits             其他文件   → 打包为 ZIP           │
│                                                                 │
│   ③ 本地输出 ───────────────► manifest.json + CSV + ZIPs        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 三步流程

| 步骤 | 数据源 | 作用 | 速率限制 |
|------|--------|------|----------|
| **Step 1** | `clawskills.sh` | 获取按下载量排序的 Top 50 Skill 列表 | 无限制 |
| **Step 2** | `raw.githubusercontent.com` | 获取每个 Skill 的 `_meta.json`（含 slug + version） | 无限制 |
| **Step 3** | `api.github.com` | 列出 Skill 目录文件并下载，打包为 ZIP | 未认证 60 次/小时，认证 5,000 次/小时 |

### 关键数据结构

每个 Skill 在 GitHub 仓库中的目录结构：

```
openclaw/skills/
└── skills/
    └── {owner}/
        └── {slug}/
            ├── SKILL.md          # 技能定义文档
            ├── _meta.json        # 元数据（slug, version, 发布时间）
            └── CONTRIBUTING.md   # 可选：贡献指南
```

`_meta.json` 示例：

```json
{
  "owner": "steipete",
  "slug": "gog",
  "displayName": "Gog",
  "latest": {
    "version": "1.0.0",
    "publishedAt": 1767545346060,
    "commit": "https://github.com/clawdbot/skills/commit/02cc22..."
  },
  "history": []
}
```

## 快速开始

### 环境要求

- Python 3.8+（仅使用标准库，无需 `pip install`）
- 网络连接（访问 GitHub + clawskills.sh）

### 运行

```bash
# 默认抓取 Top 50
python3 clawhub_scraper.py

# 自定义数量和输出目录
python3 clawhub_scraper.py --top 100 --output ./data

# 配置 GitHub Token（推荐，提升速率限制到 5000 次/小时）
GITHUB_TOKEN=ghp_xxxxxxxxxxxx python3 clawhub_scraper.py
```

### v2 关键特性：自动排名更新

v2 版本**每次运行都会实时从 clawskills.sh 抓取最新排名**，无需手动维护硬编码的 Skill 列表。这意味着：

- 新 Skill 冲入 Top 50 会被自动发现
- 掉出 Top 50 的 Skill 会被自动移除
- 下载量和星标数据每次运行都是最新的

### 输出结构

```
clawhub_top50_skills/
├── top50_skills_manifest.json   # 完整 JSON 清单
├── top50_skills_summary.csv     # CSV 格式汇总表
├── metadata/                    # 原始 _meta.json 文件
│   ├── thesethrose__agent-browser__meta.json
│   ├── steipete__gog__meta.json
│   └── ...
└── zips/                        # 每个 Skill 的独立 ZIP
    ├── thesethrose__agent-browser.zip
    ├── steipete__gog.zip
    └── ...
```

### manifest.json 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `rank` | int | 下载量排名（1-50） |
| `owner` | string | Skill 作者的 GitHub 用户名 |
| `slug` | string | Skill 唯一标识符（来自 `_meta.json`） |
| `version` | string | 最新版本号（语义化版本） |
| `unique_id` | string | 唯一标识 `{owner}/{slug}@{version}` |
| `display_name` | string | 显示名称 |
| `downloads` | string | 下载量（如 "137.7k"） |
| `stars` | string | 星标数 |
| `published_at` | int | 发布时间戳（Unix ms） |
| `clawhub_url` | string | ClawHub 页面链接 |
| `github_url` | string | GitHub 归档链接 |
| `zip_file` | string\|null | ZIP 文件相对路径 |
| `meta_file` | string\|null | 元数据文件相对路径 |

## 每日定时运行

### 方案一：GitHub Actions（推荐）

```yaml
# .github/workflows/daily-scrape.yml
name: Daily ClawHub Top 50 Scrape

on:
  schedule:
    - cron: '0 0 * * *'   # 每天 UTC 00:00（北京时间 08:00）
  workflow_dispatch:        # 允许手动触发

jobs:
  scrape:z
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Run scraper
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python3 clawhub_scraper.py --top 50

      - name: Commit and push results
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add clawhub_top50_skills/
          git diff --staged --quiet || git commit -m "chore: update top 50 skills $(date +%Y-%m-%d)"
          git push
```

### 方案二：Linux Crontab

```bash
# 编辑 crontab
crontab -e

# 每天早上 8 点运行
0 8 * * * cd /path/to/project && GITHUB_TOKEN=ghp_xxx python3 clawhub_scraper.py >> /var/log/clawhub_scraper.log 2>&1
```

### 方案三：Mira 定时任务

可以直接在 Mira 中创建定时任务，让 Mira 每天自动执行脚本并输出结果。

### 速率限制评估

| 场景 | 每次请求数 | GitHub 限额 | 是否足够 |
|------|-----------|------------|----------|
| Top 50, 无 Token | ~150 次 | 60 次/小时 | ⚠️ 需分多小时 |
| Top 50, 有 Token | ~150 次 | 5,000 次/小时 | ✅ 绰绰有余 |
| Top 100, 有 Token | ~300 次 | 5,000 次/小时 | ✅ 无问题 |
| 每日运行, 有 Token | ~150 次/天 | 120,000 次/天 | ✅ 完全无压力 |

> **强烈建议**：设置 `GITHUB_TOKEN` 环境变量。GitHub Personal Access Token 无需任何权限 scope，仅用于提升 API 速率限制。

## 方案对比

| 方案 | 速率限制 | 数据完整性 | 实时性 | 自动排名 | 复杂度 |
|------|----------|-----------|--------|---------|--------|
| ❌ 直接爬 clawhub.ai | 3-4 次即封 | 高 | 实时 | ✓ | 低 |
| ✅ **本方案 (GitHub + ClawSkills)** | **5,000/hr** | **高** | **准实时** | **✓** | **低** |
| ⚠️ ClawHub CLI | 同样受限 | 高 | 实时 | ✗ | 中 |
| ⚠️ Apify Scraper | 付费 | 高 | 实时 | ✓ | 低 |
| ⚠️ npm Registry API | 无限制 | 部分 | 实时 | ✗ | 高 |

## 限制与说明

1. **排名数据来源**：Top 50 排名基于 `clawskills.sh` 的下载量统计，其已过滤掉垃圾/重复/低质量 Skill（从 12,000+ 过滤到 5,147 个）
2. **实时性**：GitHub 归档通常在 Skill 发布后数分钟内同步，属于"准实时"
3. **已删除 Skill**：如果 Skill 已从 ClawHub 下架但 GitHub 归档尚未清理，`_meta.json` 获取会失败，脚本会跳过
4. **版本唯一性**：`owner/slug@version` 构成唯一标识（如 `steipete/gog@1.0.0`）
5. **纯标准库**：无需安装任何第三方 Python 包

## 许可证

MIT License
