"""Allowlist cleaning for the Policies page HTML.

The CDSO writes the policies in a rich-text editor (frontend PolicyEditor.jsx),
which saves HTML, and the public /policy page renders that HTML. Anything the
editor cannot produce is removed here before it is stored, so a forged request
cannot plant a script on a page every applicant opens. The page also cleans it
again with DOMPurify as it renders; this is the server's half.

Built on the standard library's HTMLParser so the campus install needs no new
package. Disallowed tags are dropped but their text is kept, except for the
handful whose content is itself dangerous (script, style, ...), which go whole.
"""
import re
from html import escape
from html.parser import HTMLParser

# Tags the editor produces, and the attributes each may carry.
_GLOBAL_ATTRS = {'style'}
ALLOWED = {
    'p': set(), 'br': set(), 'hr': set(),
    'h1': set(), 'h2': set(), 'h3': set(), 'h4': set(),
    'strong': set(), 'b': set(), 'em': set(), 'i': set(), 'u': set(), 's': set(), 'del': set(),
    'span': set(), 'mark': {'data-color'},
    'a': {'href', 'target', 'rel'},
    'ul': set(), 'ol': {'type', 'start'}, 'li': set(),
    'blockquote': set(),
    'table': set(), 'colgroup': set(), 'col': set(), 'thead': set(), 'tbody': set(), 'tr': set(),
    'td': {'colspan', 'rowspan', 'colwidth', 'data-background-color'},
    'th': {'colspan', 'rowspan', 'colwidth', 'data-background-color'},
}
VOID = {'br', 'hr', 'col'}
# Dropped together with everything inside them.
DROP_CONTENT = {'script', 'style', 'iframe', 'object', 'embed', 'template', 'noscript', 'svg', 'math',
                'textarea', 'select', 'title', 'head'}

# Inline CSS the toolbar can set. The value pattern admits colours, sizes,
# font names and rgb()/hsl() — never url(), expression() or a second declaration.
STYLE_PROPS = {'text-align', 'color', 'background-color', 'font-size', 'font-family',
               'line-height', 'width', 'min-width', 'list-style-type'}
_SAFE_VALUE = re.compile(r"""^[#\w\s.,%'"()+-]{1,200}$""")
_BAD_VALUE = re.compile(r'url|expression|javascript|@import|\\', re.I)
_SAFE_ATTR_VALUE = {
    'type':     re.compile(r'^[1aAiI]$'),
    'start':    re.compile(r'^\d{1,4}$'),
    'colspan':  re.compile(r'^\d{1,3}$'),
    'rowspan':  re.compile(r'^\d{1,3}$'),
    'colwidth': re.compile(r'^\d{1,4}(,\d{1,4})*$'),
    'target':   re.compile(r'^_blank$'),
}
_SAFE_HREF = re.compile(r'^(https?:|mailto:|tel:|#)', re.I)


def _clean_style(value):
    kept = []
    for decl in value.split(';'):
        if ':' not in decl:
            continue
        prop, _, val = decl.partition(':')
        prop, val = prop.strip().lower(), val.strip()
        if prop in STYLE_PROPS and _SAFE_VALUE.match(val) and not _BAD_VALUE.search(val):
            kept.append(f'{prop}: {val}')
    return '; '.join(kept)


def _clean_attr(tag, name, value):
    """The cleaned value, or None to drop the attribute."""
    value = value or ''
    if name == 'style':
        return _clean_style(value) or None
    if name not in ALLOWED[tag]:
        return None
    if name == 'href':
        # Strip control characters and whitespace first: "java\tscript:" is
        # still javascript: to a browser.
        compact = re.sub(r'[\x00-\x20]', '', value)
        return value if _SAFE_HREF.match(compact) else None
    if name == 'rel':
        return 'noopener noreferrer nofollow'
    if name in ('data-color', 'data-background-color'):
        return value if _SAFE_VALUE.match(value) and not _BAD_VALUE.search(value) else None
    rule = _SAFE_ATTR_VALUE.get(name)
    return value if rule is None or rule.match(value) else None


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open = []          # allowed tags currently open, to close any left dangling
        self.skip_depth = 0     # >0 while inside a DROP_CONTENT element

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            if tag not in VOID:
                self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED:
            return
        parts = [tag]
        for name, value in attrs:
            name = name.lower()
            if name not in ALLOWED[tag] and name not in _GLOBAL_ATTRS:
                continue
            cleaned = _clean_attr(tag, name, value)
            if cleaned is not None:
                parts.append(f'{name}="{escape(cleaned, quote=True)}"')
        self.out.append('<' + ' '.join(parts) + '>')
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in DROP_CONTENT and tag not in VOID:
            self.skip_depth -= 1

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth or tag not in ALLOWED or tag in VOID or tag not in self.open:
            return
        # Close anything opened inside it that was never closed.
        while self.open:
            top = self.open.pop()
            self.out.append(f'</{top}>')
            if top == tag:
                break

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(escape(data, quote=False))

    def result(self):
        self.close()
        return ''.join(self.out) + ''.join(f'</{t}>' for t in reversed(self.open))


def clean_policy_html(html):
    """The HTML with everything outside the allowlist removed."""
    cleaner = _Cleaner()
    cleaner.feed(html or '')
    return cleaner.result()


def visible_text(html):
    """The text a reader would see, to refuse a policy that is only markup."""
    return re.sub(r'<[^>]*>', '', html or '').strip()
