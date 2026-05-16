#!/usr/bin/env python3
"""
VIP & Industry Newsletter Automation Script
Generates a professional HTML newsletter from structured data.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# For email sending (optional - install with: pip install jinja2)
try:
    from jinja2 import Template
    JINJA_AVAILABLE = True
except ImportError:
    JINJA_AVAILABLE = False
    print("Warning: jinja2 not installed. Using basic string replacement.")
    print("Install with: pip install jinja2")


class NewsletterGenerator:
    def __init__(self, template_path="newsletter_template.html"):
        self.template_path = template_path
        self.template = self._load_template()

    def _load_template(self):
        """Load the HTML template file."""
        with open(self.template_path, 'r', encoding='utf-8') as f:
            return f.read()

    def load_data(self, data_path="newsletter_data.json"):
        """Load newsletter data from JSON file."""
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Auto-populate month/year if not provided
        now = datetime.now()
        if 'MONTH' not in data or not data['MONTH']:
            data['MONTH'] = now.strftime('%B')
        if 'YEAR' not in data or not data['YEAR']:
            data['YEAR'] = str(now.year)

        return data

    def _render_basic(self, data):
        """Render using basic string replacement (no jinja2 required)."""
        html = self.template

        # Simple replacements
        simple_keys = ['MONTH', 'YEAR', 'SENDER_NAME', 'SENDER_TITLE', 
                      'COMPANY_NAME', 'COMPANY_ADDRESS', 'SENDER_EMAIL',
                      'SENDER_PHONE', 'COMPANY_URL', 'CALENDAR_LINK']

        for key in simple_keys:
            if key in data:
                html = html.replace(f'{{{{{key}}}}}', str(data.get(key, '')))

        # Render arrays
        html = self._render_array(html, 'events', data.get('events', []), 
                                 lambda e: f'<div class="event-date">{e.get("date","")}</div>\n<div class="event-name">{e.get("name","")}</div>')

        html = self._render_array(html, 'industryNews', data.get('industryNews', []),
                                 lambda n: f'<li><strong>{n.get("headline","")}</strong>: {n.get("detail","")}</li>')

        html = self._render_array(html, 'ambassadors', data.get('ambassadors', []),
                                 lambda a: f'<div class="ambassador-item"><strong>{a.get("talent","")}</strong> for {a.get("brand","")}</div>')

        html = self._render_array(html, 'partnerships', data.get('partnerships', []),
                                 lambda p: f'<li><strong>{p.get("partner1","")}</strong> x <strong>{p.get("partner2","")}</strong></li>')

        html = self._render_array(html, 'castings', data.get('castings', []),
                                 lambda c: f'<li><strong>{c.get("talent","")}</strong> {c.get("project","")}</li>')

        html = self._render_array(html, 'filmProjects', data.get('filmProjects', []),
                                 lambda f: f'<li><strong>{f.get("talent","")}</strong> - {f.get("project","")}</li>')

        html = self._render_array(html, 'tvProjects', data.get('tvProjects', []),
                                 lambda t: f'<li><strong>{t.get("talent","")}</strong> - {t.get("project","")}</li>')

        return html

    def _render_array(self, html, array_name, items, formatter):
        """Helper to render array sections in template."""
        # Find and replace the each block
        start_marker = f'{{{{#each {array_name}}}}}'
        end_marker = f'{{{{/each}}}}'

        start_idx = html.find(start_marker)
        if start_idx == -1:
            return html

        end_idx = html.find(end_marker, start_idx)
        if end_idx == -1:
            return html

        full_block = html[start_idx:end_idx + len(end_marker)]

        rendered = '\n'.join([formatter(item) for item in items])
        html = html.replace(full_block, rendered)

        return html

    def _render_jinja(self, data):
        """Render using Jinja2 templating."""
        template = Template(self.template)
        return template.render(**data)

    def generate(self, data_path="newsletter_data.json", output_path=None):
        """Generate the newsletter HTML file."""
        data = self.load_data(data_path)

        if JINJA_AVAILABLE:
            html = self._render_jinja(data)
        else:
            html = self._render_basic(data)

        # Determine output path
        if not output_path:
            month = data.get('MONTH', 'Unknown').lower()
            year = data.get('YEAR', 'Unknown')
            output_path = f'newsletter_{month}_{year}.html'

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"✅ Newsletter generated: {output_path}")
        print(f"📊 Sections included:")
        print(f"   • Events: {len(data.get('events', []))}")
        print(f"   • Industry News: {len(data.get('industryNews', []))}")
        print(f"   • Ambassadors: {len(data.get('ambassadors', []))}")
        print(f"   • Partnerships: {len(data.get('partnerships', []))}")
        print(f"   • Castings: {len(data.get('castings', []))}")
        print(f"   • Film Projects: {len(data.get('filmProjects', []))}")
        print(f"   • TV Projects: {len(data.get('tvProjects', []))}")

        return output_path


def create_sample_data():
    """Create a sample data file for first-time setup."""
    sample = {
        "MONTH": "",
        "YEAR": "",
        "SENDER_NAME": "Your Name",
        "SENDER_TITLE": "Your Title",
        "COMPANY_NAME": "Your Company",
        "COMPANY_ADDRESS": "Your Address",
        "SENDER_EMAIL": "you@company.com",
        "SENDER_PHONE": "(555) 000-0000",
        "COMPANY_URL": "https://company.com",
        "CALENDAR_LINK": "#",
        "events": [],
        "industryNews": [],
        "ambassadors": [],
        "partnerships": [],
        "castings": [],
        "filmProjects": [],
        "tvProjects": []
    }

    with open('newsletter_data.json', 'w', encoding='utf-8') as f:
        json.dump(sample, f, indent=2)

    print("✅ Created newsletter_data.json - edit this file with your content!")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate VIP & Industry Newsletter')
    parser.add_argument('--data', default='newsletter_data.json', help='Path to data JSON file')
    parser.add_argument('--output', default=None, help='Output HTML file path')
    parser.add_argument('--init', action='store_true', help='Create sample data file')
    args = parser.parse_args()

    if args.init:
        create_sample_data()
        return

    if not Path(args.data).exists():
        print(f"❌ Data file not found: {args.data}")
        print("Run with --init to create a sample data file")
        sys.exit(1)

    if not Path('newsletter_template.html').exists():
        print("❌ Template file not found: newsletter_template.html")
        sys.exit(1)

    generator = NewsletterGenerator()
    generator.generate(args.data, args.output)


if __name__ == "__main__":
    main()
