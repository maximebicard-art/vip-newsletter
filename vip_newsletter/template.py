#!/usr/bin/env python3
"""
VIP Newsletter — Template Rendering
v4.0: Extracted from monolithic v3.36.
"""
import os
import re
from jinja2 import Template


def render_newsletter(data, template_path="newsletter_template.html"):
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())
    return template.render(**data)


def generate_plain_text(data):
    lines = [
        "VIP & INDUSTRY NEWS",
        f"Week of {data.get('week_of', '')}",
        "=" * 50,
        "",
    ]

    sections = [
        ("EVENTS", "events"),
        ("INDUSTRY NEWS", "industryNews"),
        ("AMBASSADORS", "ambassadors"),
        ("PARTNERSHIPS", "partnerships"),
        ("CASTINGS", "castings"),
        ("FILM PROJECTS", "filmProjects"),
        ("TV PROJECTS", "tvProjects"),
        ("IN THEATERS", "inTheaters"),
        ("NEW MUSIC", "newMusic"),
        ("TV PREMIERES", "tvPremieres"),
    ]

    for label, key in sections:
        items = data.get(key, [])
        if not items:
            continue
        lines.append(f"\n{label}")
        lines.append("-" * 40)
        for item in items:
            headline = item.get("headline", item.get("title", ""))
            link = item.get("link", "")
            source = item.get("source", "")
            lines.append(f"• {headline}")
            if source:
                lines.append(f"  Source: {source}")
            if link:
                lines.append(f"  {link}")
            lines.append("")

    return "\n".join(lines)


def save_newsletter(html_content, plain_text, filename=None):
    if filename is None:
        from datetime import datetime
        filename = f"newsletter_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    html_path = f"{filename}.html"
    txt_path = f"{filename}.txt"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(plain_text)
    print(f"💾 Saved: {html_path}, {txt_path}")
    return html_path, txt_path
