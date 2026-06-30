"""Constants for the GitHub fetch provider."""

from __future__ import annotations

API_BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
API_KEY_ENV_NAME = "GITHUB_API_KEY"
USER_AGENT = "omnisearch-mcp/1.0"
TIMEOUT_MS = 30_000

RESERVED_ROUTES = frozenset(
    {
        "trending",
        "explore",
        "new",
        "settings",
        "notifications",
        "login",
        "logout",
        "signup",
        "join",
        "features",
        "pricing",
        "about",
        "contact",
        "security",
        "sponsors",
        "marketplace",
        "codespaces",
        "copilot",
        "enterprise",
        "topics",
        "collections",
        "search",
        "pulls",
        "issues",
        "stars",
        "dashboard",
    }
)

BINARY_EXTENSIONS = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "ico",
        "webp",
        "svg",
        "bmp",
        "tiff",
        "mp3",
        "wav",
        "ogg",
        "flac",
        "mp4",
        "avi",
        "mkv",
        "mov",
        "webm",
        "zip",
        "tar",
        "gz",
        "rar",
        "7z",
        "bz2",
        "xz",
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "ttf",
        "otf",
        "woff",
        "woff2",
        "eot",
        "exe",
        "dll",
        "so",
        "dylib",
        "bin",
        "o",
        "a",
        "db",
        "sqlite",
        "psd",
        "ai",
        "sketch",
    }
)

CONTEXT_FILE_LIMITS = {
    "CLAUDE.md": 100_000,
    "AGENTS.md": 100_000,
    "GEMINI.md": 100_000,
    "AGENT.md": 100_000,
    "ARCHITECTURE.md": 100_000,
    "DEVELOPMENT.md": 100_000,
    "CONVENTIONS.md": 100_000,
    "REVIEW.md": 100_000,
    ".cursorrules": 50_000,
    ".windsurfrules": 50_000,
    ".clinerules": 50_000,
    ".goosehints": 50_000,
    ".roorules": 50_000,
    ".continuerules": 50_000,
    ".github/copilot-instructions.md": 50_000,
    ".junie/guidelines.md": 50_000,
    "llms.txt": 50_000,
    "llms-full.txt": 30_000,
}

AI_RULES_DIRS = {
    ".cursor/rules": ("cursor_rules_dir", 50_000),
    ".windsurf/rules": ("windsurf_rules_dir", 50_000),
    ".roo/rules": ("roo_rules_dir", 50_000),
    ".amazonq/rules": ("amazonq_rules_dir", 50_000),
    ".augment/rules": ("augment_rules_dir", 50_000),
    ".continue/rules": ("continue_rules_dir", 50_000),
    ".trae/rules": ("trae_rules_dir", 50_000),
    ".github/instructions": ("github_instructions_dir", 50_000),
    ".agents/skills": ("agents_skills_dir", 50_000),
}

CONTEXT_FILE_NAMES = tuple(CONTEXT_FILE_LIMITS)
README_TOKEN_CAP = 5_000
README_CHAR_CAP = README_TOKEN_CAP * 4
README_MAX_BYTES = 500_000
AI_RULES_INLINE_MAX_BYTES = 20_000
COMMIT_MESSAGE_MAX_CHARS = 80
ISSUE_BODY_MAX_CHARS = 500
RELEASE_BODY_MAX_CHARS = 1_000
PATCH_MAX_CHARS = 3_000
LIST_PER_PAGE = 100
OVERVIEW_COMMITS_PER_PAGE = 10
OVERVIEW_ISSUES_PER_PAGE = 5
OVERVIEW_PRS_PER_PAGE = 5
OVERVIEW_RELEASES_PER_PAGE = 3
COMMENTS_PER_PAGE = 50
STARGAZER_MAX_PAGE = 400
MAX_TREE_CHILDREN_DIRS = 25

DEP_CONFIG_ALLOWLIST = {
    "package.json": ("dep_package_json", 10_000),
    "pyproject.toml": ("dep_pyproject_toml", 5_000),
    "Cargo.toml": ("dep_cargo_toml", 4_000),
    "go.mod": ("dep_go_mod", 5_000),
    "Gemfile": ("dep_gemfile", 4_000),
    "requirements.txt": ("dep_requirements_txt", 2_000),
    "pnpm-workspace.yaml": ("dep_pnpm_workspace_yaml", 1_000),
    ".nvmrc": ("dep_nvmrc", 100),
    ".npmrc": ("dep_npmrc", 1_000),
    "lerna.json": ("dep_lerna_json", 1_000),
}

DOCS_DIR_NAMES = frozenset({"docs", "doc", "documentation"})
DOCS_MD_EXTENSIONS = frozenset({"md", "mdx", "rst"})

NOISY_DIR_NAMES = frozenset(
    {
        "test",
        "tests",
        "spec",
        "specs",
        "__tests__",
        "__mocks__",
        "docs",
        "doc",
        "documentation",
        "vendor",
        "node_modules",
        "third_party",
        "third-party",
        "thirdparty",
        "fixtures",
        "testdata",
        "test_data",
        "test-data",
        "examples",
        "example",
        "samples",
        "sample",
        "demo",
        "demos",
        "build",
        "dist",
        "out",
        "output",
        ".build",
        "scripts",
        "tools",
        "hack",
        "misc",
        "packages",
        "plugins",
        "assets",
        "static",
        "public",
        "images",
        "img",
        "icons",
        "fonts",
        "locales",
        "translations",
        "i18n",
        "l10n",
    }
)
