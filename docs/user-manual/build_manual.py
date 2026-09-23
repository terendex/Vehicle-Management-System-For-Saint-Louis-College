# -*- coding: utf-8 -*-
"""Rebuild SLC-VMS-User-Manual.docx from images/README.md.

The markdown in images/ is the single source of truth for the manual: the .docx
is a rendering of it, not a second copy to be edited by hand. Editing the .docx
directly means the next rebuild silently discards the change, so edit the
markdown and re-run this.

    backend/venv/Scripts/python.exe docs/user-manual/build_manual.py

The existing .docx is used as the template, and only its body is replaced. That
is deliberate: styles.xml, numbering.xml, the footer, the theme and the A4 page
setup were all settled in Word, and reproducing them from python-docx defaults
would quietly change the look of every page. Image parts are dropped from the
template's relationships first, so the rebuilt file carries the pictures this
run put in it rather than accumulating every picture the manual has ever had.

Markdown the parser understands, per `### ` section:

    ![alt](path)        the screenshot; at most one, rendered under the
                        one-line description with a "Figure N.M" caption
    plain paragraph     body text
    > quoted lines      a callout box. One opening with "**Picture out of
                        date.**" or "**No picture yet.**" is drawn in amber as
                        a warning; every other note is drawn in blue.
    1. **Lead**: text   the numbered callouts, rendered as the orange-numbered
                        table under the figure

Word rebuilds the table of contents when the file is opened (the field is
written dirty), so page numbers do not need to be maintained here.
"""

import io
import os
import re
import shutil
import sys
import tempfile

from PIL import Image
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Emu, Inches, Pt, RGBColor, Twips

HERE     = os.path.dirname(os.path.abspath(__file__))
IMAGES   = os.path.join(HERE, 'images')
SOURCE   = os.path.join(IMAGES, 'README.md')
DOCX     = os.path.join(HERE, 'SLC-VMS-User-Manual.docx')

VERSION_LINE = 'Version 1.2  ·  September 2026'

# Page geometry, copied from the document Word produced: A4 with 1304-twip side
# margins leaves 9298 twips of text, and the figures and tables are sized to
# fill exactly that.
CONTENT_TWIPS = 9298
NUM_COL_TWIPS = 624
TXT_COL_TWIPS = CONTENT_TWIPS - NUM_COL_TWIPS
FIGURE_WIDTH  = Emu(5903999)

# The screenshots are 2280 px wide, which is about 350 dpi at the printed
# figure width - more than a page can show and enough to put the file over
# 20 MB. Word's own "print quality" compression, which produced the first
# edition, resampled them to 1800 px JPEG; this does the same thing explicitly
# so the size does not depend on whether anyone re-saved the file in Word.
FIGURE_PIXELS  = 1800
FIGURE_QUALITY = 85

ORANGE     = 'E8590C'   # callout number cells, and the out-of-date warning
CELL_RULE  = 'E0E8EF'   # the hairline round a callout's text cell
BODY_GREY  = '4A5D6E'   # secondary text
SLC_BLUE   = '03396C'
NOTE_FILL  = 'F2F6FA'
WARN_FILL  = 'FFF4E5'
GOLD       = '9A6B00'

# The front matter is written here rather than taken from README.md: the
# markdown's own preamble is a note to whoever maintains the screenshots, which
# is not what a reader of the manual needs on page 2.
ABOUT_INTRO = (
    'This guide walks through the system screen by screen in {figures} annotated pictures. '
    'It is organised by who uses each part of the system:'
)
ABOUT_BULLETS = [
    ('Getting Started', 'How to sign in, reset a forgotten password, apply for a vehicle pass, '
                        'and how security guards sign in at a gate.'),
    ('CDSO (Administrator)', 'Every screen available to the CDSO account: the dashboard, '
                             'registrations, users, devices, suppliers, operations, parking, '
                             'violations, logs, rules and system settings.'),
    ('Security Guard', 'The gate terminal used by security guards: checking vehicles in and out, '
                       'recording vehicles with no plate, watching parking and issuing violations.'),
    ('Vehicle Owner', 'The portal a registered vehicle owner sees after signing in.'),
    ('Installing the Campus System', 'Installing the on-campus half of the system with '
                                     'SLC-Smart-Parking-Campus-Setup.exe. Run it on the computer '
                                     'that will serve the gate terminals.'),
    ('Running the Campus System', 'The launcher window that runs the campus server after '
                                  'installation.'),
]
HOW_TO_READ = (
    'Each picture has numbered orange boxes. The table under the picture explains each number: '
    'what that part of the screen is and what it does. Work through the numbers in order the '
    'first time you use a screen. Boxed notes beside a table add what the numbers cannot: a rule '
    'the screen enforces, or a panel that only appears when it has something in it. Each picture '
    'also carries its own copy of the legend along the bottom, so a page photocopied on its own '
    'still explains itself.'
)
PICTURE_NOTES = [
    'The names, plate numbers, email addresses and records shown are sample data for '
    'illustration. They are not real students, staff or vehicles.',
    'Camera pictures are sample illustrations. On a live system these panels show the real '
    'camera feed.',
    'Dates and counts will differ on your system.',
    'The Activity log in the launcher picture is deliberately blurred.',
    'Every picture in this edition was taken from the September 2026 system. Where a screen is '
    'described here and looks different on yours, the system has moved on since — tell the '
    'CDSO Office so the picture can be re-taken.',
]


# ── markdown ──────────────────────────────────────────────────────────────────

def parse(md):
    """README.md -> [(chapter title, chapter intro, [section, ...])].

    A section is (title, [block, ...]) where each block is one of
    ('image', path, alt) / ('para', text) / ('note', text, is_warning) /
    ('callouts', [(lead, text), ...]).
    """
    chapters, chapter, section = [], None, None
    lines, i = md.split('\n'), 0

    # Skip the maintainer preamble: everything before the first '## '.
    while i < len(lines) and not lines[i].startswith('## '):
        i += 1

    def flush_section():
        if chapter is not None and section is not None:
            chapter[2].append(section)

    while i < len(lines):
        line = lines[i]

        if line.startswith('## '):
            flush_section()
            if chapter is not None:
                chapters.append(chapter)
            chapter, section = [line[3:].strip(), '', []], None
            i += 1
            # The chapter's own one-line introduction.
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and not lines[i].startswith('#'):
                chapter[1] = lines[i].strip()
                i += 1
            continue

        if line.startswith('### '):
            flush_section()
            section = (line[4:].strip(), [])
            i += 1
            continue

        if section is None:
            i += 1
            continue

        blocks = section[1]

        m = re.match(r'!\[(.*?)\]\((.*?)\)\s*$', line)
        if m:
            blocks.append(('image', m.group(2), m.group(1)))
            i += 1
            continue

        if line.startswith('> '):
            quoted = []
            while i < len(lines) and lines[i].startswith('>'):
                quoted.append(lines[i].lstrip('>').strip())
                i += 1
            text = ' '.join(q for q in quoted if q)
            warn = text.startswith('**Picture out of date.**') or text.startswith('**No picture yet.**')
            blocks.append(('note', text, warn))
            continue

        if re.match(r'\d+\. ', line):
            items = []
            while i < len(lines) and (re.match(r'\d+\. ', lines[i]) or lines[i].startswith('   ')):
                if re.match(r'\d+\. ', lines[i]):
                    items.append(re.sub(r'^\d+\. ', '', lines[i]).strip())
                else:                                   # continuation line
                    items[-1] += ' ' + lines[i].strip()
                i += 1
            split = []
            for item in items:
                m = re.match(r'\*\*(.+?)\*\*:\s*(.*)$', item)
                split.append((m.group(1), m.group(2)) if m else ('', item))
            blocks.append(('callouts', split))
            continue

        if line.strip():
            para = [line.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(('#', '>', '!')) \
                    and not re.match(r'\d+\. ', lines[i]):
                para.append(lines[i].strip())
                i += 1
            blocks.append(('para', ' '.join(para)))
            continue

        i += 1

    flush_section()
    if chapter is not None:
        chapters.append(chapter)
    return chapters


def add_runs(paragraph, text, color=None, size=None):
    """Write text into a paragraph, honouring **bold** and *italic* spans."""
    for piece in re.split(r'(\*\*.+?\*\*|\*[^*]+?\*)', text):
        if not piece:
            continue
        if piece.startswith('**') and piece.endswith('**'):
            run, run.bold = paragraph.add_run(piece[2:-2]), True
        elif piece.startswith('*') and piece.endswith('*') and len(piece) > 2:
            run, run.italic = paragraph.add_run(piece[1:-1]), True
        else:
            run = paragraph.add_run(piece)
        if color:
            run.font.color.rgb = RGBColor.from_string(color)
        if size:
            run.font.size = size
    return paragraph


# ── low-level docx helpers ────────────────────────────────────────────────────

def shade(cell, fill):
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def borders(cell, color, sides=('top', 'left', 'bottom', 'right'), size=4, left=None):
    """Hairline borders. `left` overrides the left edge, for the note boxes."""
    tc_borders = OxmlElement('w:tcBorders')
    for side in ('top', 'left', 'bottom', 'right'):
        edge = OxmlElement('w:' + side)
        if side in sides:
            edge.set(qn('w:val'), 'single')
            edge.set(qn('w:sz'), str(size * 6 if (left and side == 'left') else size))
            edge.set(qn('w:space'), '0')
            edge.set(qn('w:color'), left if (left and side == 'left') else color)
        else:
            edge.set(qn('w:val'), 'nil')
        tc_borders.append(edge)
    cell._tc.get_or_add_tcPr().append(tc_borders)


def vcenter(cell):
    align = OxmlElement('w:vAlign')
    align.set(qn('w:val'), 'center')
    cell._tc.get_or_add_tcPr().append(align)


def keep_with_next(paragraph):
    keep = OxmlElement('w:keepNext')
    paragraph._p.get_or_add_pPr().append(keep)


def page_break_before(paragraph):
    brk = OxmlElement('w:pageBreakBefore')
    paragraph._p.get_or_add_pPr().append(brk)


def toc_field(paragraph):
    """A TOC field with no cached result, marked dirty so Word fills it in."""
    run = paragraph.add_run()
    begin = OxmlElement('w:fldChar')
    begin.set(qn('w:fldCharType'), 'begin')
    begin.set(qn('w:dirty'), 'true')
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = ' TOC \\o "1-2" \\h \\z \\u '
    separate = OxmlElement('w:fldChar')
    separate.set(qn('w:fldCharType'), 'separate')
    placeholder = OxmlElement('w:t')
    placeholder.text = 'Right-click here and choose Update Field to build the contents.'
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    for node in (begin, instr, separate, placeholder, end):
        run._r.append(node)


def blank(doc, count=1):
    for _ in range(count):
        doc.add_paragraph()


def for_print(source, workdir):
    """Resample a screenshot to the size it is printed at, as JPEG."""
    image = Image.open(source)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    if image.width > FIGURE_PIXELS:
        height = int(round(image.height * FIGURE_PIXELS / float(image.width)))
        image = image.resize((FIGURE_PIXELS, height), Image.LANCZOS)
    out = os.path.join(workdir, '%03d.jpg' % len(os.listdir(workdir)))
    image.save(out, 'JPEG', quality=FIGURE_QUALITY, optimize=True, progressive=True)
    return out


# ── rendering ─────────────────────────────────────────────────────────────────

def note_box(doc, text, warn):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.cell(0, 0)
    cell.width = Twips(CONTENT_TWIPS)
    shade(cell, WARN_FILL if warn else NOTE_FILL)
    borders(cell, WARN_FILL if warn else NOTE_FILL, left=ORANGE if warn else SLC_BLUE)
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    add_runs(paragraph, text, color=BODY_GREY, size=Pt(10))
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def callout_table(doc, items):
    table = doc.add_table(rows=0, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for number, (lead, text) in enumerate(items, start=1):
        row = table.add_row()
        # Keep a callout from being split over a page break mid-row.
        cant_split = OxmlElement('w:cantSplit')
        row._tr.get_or_add_trPr().append(cant_split)

        num_cell = row.cells[0]
        num_cell.width = Twips(NUM_COL_TWIPS)
        shade(num_cell, ORANGE)
        borders(num_cell, ORANGE)
        vcenter(num_cell)
        num_para = num_cell.paragraphs[0]
        num_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        num_para.paragraph_format.space_after = Pt(0)
        num_run = num_para.add_run(str(number))
        num_run.bold = True
        num_run.font.color.rgb = RGBColor.from_string('FFFFFF')
        num_run.font.size = Pt(11)

        txt_cell = row.cells[1]
        txt_cell.width = Twips(TXT_COL_TWIPS)
        borders(txt_cell, CELL_RULE)
        head = txt_cell.paragraphs[0]
        head.paragraph_format.space_after = Pt(1)
        if lead:
            head.add_run(lead).bold = True
            body = txt_cell.add_paragraph()
        else:
            body = head
        body.paragraph_format.space_after = Pt(2)
        add_runs(body, text, color=BODY_GREY, size=Pt(10))


def render_section(doc, chapter_no, section_no, title, blocks, workdir):
    heading = doc.add_heading('%d.%d  %s' % (chapter_no, section_no, title), level=2)
    keep_with_next(heading)

    image  = next((b for b in blocks if b[0] == 'image'), None)
    rest   = [b for b in blocks if b[0] != 'image']

    # The one-line description belongs above the picture; everything after it
    # keeps the order it has in the markdown, so a note written before the
    # numbered list is printed before the table.
    lead_para = None
    if rest and rest[0][0] == 'para':
        lead_para, rest = rest[0], rest[1:]
    if lead_para:
        para = doc.add_paragraph()
        keep_with_next(para)
        add_runs(para, lead_para[1])

    if image:
        path = os.path.join(IMAGES, image[1].replace('/', os.sep))
        if not os.path.exists(path):
            sys.exit('missing image: %s' % path)
        holder = doc.add_paragraph()
        holder.alignment = WD_ALIGN_PARAGRAPH.CENTER
        keep_with_next(holder)
        holder.add_run().add_picture(for_print(path, workdir), width=FIGURE_WIDTH)
        caption = doc.add_paragraph(style='Caption')
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        keep_with_next(caption)
        caption.add_run('Figure %d.%d: %s' % (chapter_no, section_no, title))

    for block in rest:
        if block[0] == 'para':
            add_runs(doc.add_paragraph(), block[1])
        elif block[0] == 'note':
            note_box(doc, block[1], block[2])
        elif block[0] == 'callouts':
            callout_table(doc, block[1])
            doc.add_paragraph().paragraph_format.space_after = Pt(4)


def build_front_matter(doc, figure_count):
    blank(doc, 5)

    logos = doc.add_paragraph()
    logos.alignment = WD_ALIGN_PARAGRAPH.CENTER
    logos.add_run().add_picture(os.path.join(IMAGES, '00-cover', 'slc-seal.png'), height=Inches(1.575))
    logos.add_run('  ')
    logos.add_run().add_picture(os.path.join(IMAGES, '00-cover', 'system-logo.png'), height=Inches(1.575))

    college = doc.add_paragraph()
    college.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = college.add_run('SAINT LOUIS COLLEGE')
    run.bold = True
    run.font.color.rgb = RGBColor.from_string(GOLD)
    run.font.size = Pt(13)

    title = doc.add_paragraph(style='Title')
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run('Smart Parking and Vehicle Verification System')

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run('User Manual: Illustrated Screen Guide')
    run.font.color.rgb = RGBColor.from_string(BODY_GREY)
    run.font.size = Pt(16)

    blank(doc, 8)
    version = doc.add_paragraph()
    version.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = version.add_run(VERSION_LINE)
    run.font.color.rgb = RGBColor.from_string(BODY_GREY)
    run.font.size = Pt(10)

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    doc.add_heading('About this guide', level=1)
    doc.add_paragraph(ABOUT_INTRO.format(figures=figure_count))
    for name, text in ABOUT_BULLETS:
        bullet = doc.add_paragraph(style='List Bullet')
        bullet.add_run(name + ': ').bold = True
        bullet.add_run(text)

    doc.add_heading('How to read the pictures', level=2)
    doc.add_paragraph(HOW_TO_READ)

    doc.add_heading('Notes on the pictures', level=2)
    for text in PICTURE_NOTES:
        doc.add_paragraph(text, style='List Bullet')

    contents = doc.add_heading('Contents', level=1)
    page_break_before(contents)
    toc_field(doc.add_paragraph())


def strip_template(doc):
    """Empty the body, keeping the final sectPr, and drop every image part.

    Without the second half the rebuilt file would carry both the old pictures
    and the new ones: python-docx serialises whatever the relationship graph
    still reaches, and deleting a paragraph does not delete what it pointed at.
    """
    body = doc.element.body
    sect_pr = body.find(qn('w:sectPr'))
    for child in list(body):
        if child is not sect_pr:
            body.remove(child)

    rels = doc.part.rels
    for rel_id in [r for r, rel in rels.items() if 'image' in rel.reltype]:
        del rels[rel_id]


def main():
    md = io.open(SOURCE, encoding='utf-8').read()
    chapters = parse(md)

    figure_count = sum(
        1 for _, _, sections in chapters
        for _, blocks in sections
        for block in blocks if block[0] == 'image'
    )

    doc = Document(DOCX)
    strip_template(doc)
    build_front_matter(doc, figure_count)

    workdir = tempfile.mkdtemp(prefix='slc-manual-')
    try:
        for chapter_no, (chapter_title, chapter_intro, sections) in enumerate(chapters, start=1):
            heading = doc.add_heading('%d. %s' % (chapter_no, chapter_title), level=1)
            page_break_before(heading)
            if chapter_intro:
                doc.add_paragraph(chapter_intro)
            for section_no, (title, blocks) in enumerate(sections, start=1):
                render_section(doc, chapter_no, section_no, title, blocks, workdir)
        # Written beside the target and moved into place, so a run that fails
        # part-way leaves the previous manual intact rather than a half file
        # that is also the template the next run would read.
        staged = os.path.join(workdir, 'manual.docx')
        doc.save(staged)
        shutil.move(staged, DOCX)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    sections_total = sum(len(s) for _, _, s in chapters)
    print('%d chapters, %d sections, %d figures -> %s (%.1f MB)' % (
        len(chapters), sections_total, figure_count, os.path.basename(DOCX),
        os.path.getsize(DOCX) / 1048576.0))


if __name__ == '__main__':
    main()
