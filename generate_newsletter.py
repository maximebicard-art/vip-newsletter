#!/usr/bin/env python3
"""
Manual newsletter generator — updated for v4.0 template.
Usage: python generate_newsletter.py
"""
import json
from jinja2 import Template
from datetime import datetime


def main():
    with open("newsletter_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    with open("newsletter_template.html", "r", encoding="utf-8") as f:
        template = Template(f.read())

    html = template.render(
        week_of=data.get("week_of", datetime.now().strftime("%B %d, %Y")),
        company_address=data.get("company_address", ""),
        events=data.get("events", []),
        industryNews=data.get("industryNews", []),
        ambassadors=data.get("ambassadors", []),
        partnerships=data.get("partnerships", []),
        castings=data.get("castings", []),
        filmProjects=data.get("filmProjects", []),
        tvProjects=data.get("tvProjects", []),
        inTheaters=data.get("inTheaters", []),
        newMusic=data.get("newMusic", []),
        tvPremieres=data.get("tvPremieres", []),
    )

    filename = f"newsletter_manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Generated: {filename}")


if __name__ == "__main__":
    main()
