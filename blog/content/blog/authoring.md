+++
title = "Authoring guide"
date = 2026-01-15
author = "Jennifer Hamon"
tags = ["meta"]
description = "How to write, preview, and publish a post on the moonbuggy blog."
+++

This guide covers everything you need to write a blog post for moonbuggy. If you've written Markdown before, you already know 95% of it.

## Quick start

```bash
# 1. Create a new post
cp blog/content/posts/welcome.md blog/content/posts/my-post.md

# 2. Edit it — change the frontmatter and write your content
#    Frontmatter is the block at the top between +++ markers:
#
#    +++
#    title = "Your title here"
#    date = 2026-03-15
#    author = "Your Name"
#    tags = ["tag1", "tag2"]
#    description = "One-sentence summary for previews and SEO."
#    +++

# 3. Preview locally
make blog       # builds the blog
open docs/_build/html/blog/index.html
```

## Frontmatter reference

Every post needs this block at the top. Hugo uses TOML frontmatter (the `+++` delimiters).

| Field | Required | Notes |
|---|---|---|
| `title` | yes | Post title. Appears in the page `<h1>` and the index listing. |
| `date` | yes | `YYYY-MM-DD` format. Hugo uses this for ordering. |
| `author` | no | Displayed in the post meta line. Defaults to nothing. |
| `tags` | no | Array of strings. Rendered as small tag chips on the post. |
| `description` | no | One sentence. Used for SEO description and index card previews. |

## Markdown conventions

The blog uses standard Markdown with a few extras:

- **Code blocks** — fenced with triple backticks, language name on the opening fence: ` ```python `
- **Blockquotes** — use `>` for callouts. Rendered with the moonbuggy mint left-border.
- **Images** — `![alt text](image.png)`. Place images in `blog/static/` and reference them as `/blog/image.png`.
- **Headings** — use `##` and `###` (the post title is already `h1`). Do not use `#` inside the post body.

## Preview locally

```bash
# Build the blog (requires Hugo):
make blog

# Open in browser:
open docs/_build/html/blog/index.html

# Or use Hugo's dev server with live reload:
cd blog && hugo server -D
# Then open http://localhost:1313/moonbuggy/blog/
```

The `make blog` command:
1. Creates `docs/_build/html/blog/` if it doesn't exist
2. Runs Hugo to build the blog into that directory
3. Prints the path to the index

## Publishing workflow

1. Write your post in `blog/content/posts/`
2. Run `make blog` and verify it looks right locally
3. Commit the post and open a PR against `main`
4. When merged, the Docs workflow rebuilds and deploys everything

The blog is deployed alongside the Sphinx docs — they're one site at `jhamon.github.io/moonbuggy/`. The blog lives at `/blog/`.

## File naming

Post filenames become the URL slug. `my-post.md` → `/blog/2026/03/my-post/`. Use lowercase, hyphens, no dates in filenames (the date comes from frontmatter).

## Images and assets

Place static assets in `blog/static/`. They're served from the blog root. A file at `blog/static/screenshot.png` is referenced in Markdown as `![screenshot](/blog/screenshot.png)`.

## Need help?

The Hugo server (`hugo server -D`) has live reload — edit your post and see changes instantly. If something doesn't look right, check the browser console for 404s on CSS or font files.