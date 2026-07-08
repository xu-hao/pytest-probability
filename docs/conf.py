"""Sphinx configuration for pytest-probability documentation."""

project = "pytest-probability"
author = "Hao Xu"
copyright = "2026, Hao Xu"
release = "0.1.0"
version = "0.1.0"

extensions = [
    "myst_parser",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]
myst_heading_anchors = 3

templates_path = []
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "requirements.txt"]

html_theme = "furo"
html_title = "pytest-probability"
html_static_path = []

# GitHub-flavored source links in the theme footer
html_theme_options = {
    "source_repository": "https://github.com/xu-hao/pytest-probability",
    "source_branch": "main",
    "source_directory": "docs/",
}
