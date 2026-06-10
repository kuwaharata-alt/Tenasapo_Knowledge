from django import template
import os
import re
import bleach
from bleach.css_sanitizer import CSSSanitizer
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe


register = template.Library()
IMAGE_TOKEN_PATTERN = re.compile(r'<image(?:(\d+)|:([^>]+))?>', flags=re.IGNORECASE)
IMAGE_NAME_TOKEN_PATTERN = re.compile(r'【画像:([^】]+)】')
RICH_TEXT_BOLD_PATTERN = re.compile(r'\[b\](.*?)\[/b\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_UNDERLINE_PATTERN = re.compile(r'\[u\](.*?)\[/u\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_STRIKE_PATTERN = re.compile(r'\[s\](.*?)\[/s\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_SIZE_PATTERN = re.compile(r'\[size=(\d{1,3})\](.*?)\[/size\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_COLOR_PATTERN = re.compile(
    r'\[color=(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|[a-zA-Z]{3,20})\](.*?)\[/color\]',
    flags=re.IGNORECASE | re.DOTALL,
)
RICH_TEXT_IMAGE_URL_PATTERN = re.compile(
    r'\[img\](https?://[^\s\]]+)\[/img\]',
    flags=re.IGNORECASE,
)
BLOCK_HTML_PATTERN = re.compile(r'</?(p|div|ul|ol|li|h[1-6]|blockquote|pre|br|img)\b', flags=re.IGNORECASE)
ALLOWED_TAGS = [
    'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'span',
    'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'blockquote', 'pre', 'code',
    'div', 'img',
]
ALLOWED_ATTRIBUTES = {
    'span': ['style'],
    'p': ['style'],
    'div': ['class', 'style'],
    'a': ['href', 'target', 'rel'],
    'img': ['src', 'alt', 'class'],
}
CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=['color', 'font-size', 'text-decoration', 'font-weight', 'border', 'padding', 'padding-left', 'margin-left', 'text-indent', 'line-height']
)
TRAILING_BLANK_PARAGRAPH_PATTERN = re.compile(
    r'<p[^>]*>(?:\s|&nbsp;|<span[^>]*>\s*</span>|<br(?:\s[^>]*)?\s*/?>)*</p>\s*$',
    flags=re.IGNORECASE,
)


def _split_lines_preserving_trailing(text):
    normalized = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    return normalized.split('\n')


def _ensure_trailing_blank_paragraph(html):
    rendered = str(html or '')
    if not rendered.strip():
        return '<p>&nbsp;</p>'
    if TRAILING_BLANK_PARAGRAPH_PATTERN.search(rendered):
        return rendered
    return rendered + '<p>&nbsp;</p>'


@register.filter
def render_inline_images(value, images):
    image_source = images
    if image_source is not None and hasattr(image_source, 'all'):
        image_source = image_source.all()
    image_list = list(image_source or [])

    raw = str(value or '')

    # TinyMCE が出力したブロック HTML（<p>, <ul>, <li> 等）を含む場合は
    # 行ごとの <p> 追加をせず、そのまま sanitize して返す
    if BLOCK_HTML_PATTERN.search(raw):
        processed, found_marker, has_explicit_marker, next_image_index = _replace_image_tokens(
            raw, image_list, 0
        )
        result = _apply_rich_text_markup(processed)
        # 空の<p>タグを空白段落として保持
        # <p></p>, <p style="..."></p>, <p><span style="..."></span></p>, <p>&nbsp;</p> 全パターン対応
        result = re.sub(
            r'<(?:p|div)[^>]*>(?:\s|&nbsp;|<span[^>]*>\s*</span>|<br(?:\s[^>]*)?\s*/?>)*</(?:p|div)>',
            '<p>&nbsp;</p>',
            result
        )
        extra = []
        if not found_marker:
            for image in image_list:
                extra.append(_image_html(image))
        elif not has_explicit_marker and next_image_index < len(image_list):
            for image in image_list[next_image_index:]:
                extra.append(_image_html(image))
        final_html = result + ''.join(str(p) for p in extra)
        return mark_safe(_ensure_trailing_blank_paragraph(final_html))

    # 旧形式（プレーンテキスト / BBCode）: 行単位で <p> 追加
    next_image_index = 0
    parts = []
    has_marker = False
    has_explicit_marker = False

    for line in _split_lines_preserving_trailing(raw):
        rendered_line, found_marker, line_has_explicit_marker, next_image_index = _replace_image_tokens(
            line,
            image_list,
            next_image_index,
        )
        has_marker = has_marker or found_marker
        has_explicit_marker = has_explicit_marker or line_has_explicit_marker

        if rendered_line.strip():
            parts.append(format_html('<p>{}</p>', mark_safe(_apply_rich_text_markup(rendered_line))))
        else:
            parts.append('<p>&nbsp;</p>')

    if not has_marker:
        for image in image_list:
            parts.append(_image_html(image))
    elif (not has_explicit_marker) and next_image_index < len(image_list):
        for image in image_list[next_image_index:]:
            parts.append(_image_html(image))

    return mark_safe(_ensure_trailing_blank_paragraph(''.join(str(part) for part in parts)))


@register.filter
def render_rich_text(value):
    text = str(value or '')
    rendered_text = _apply_rich_text_markup(text)

    if BLOCK_HTML_PATTERN.search(rendered_text):
        return mark_safe(_ensure_trailing_blank_paragraph(rendered_text))

    parts = []
    for line in _split_lines_preserving_trailing(rendered_text):
        if line.strip():
            parts.append(format_html('<p>{}</p>', mark_safe(line)))
        else:
            parts.append('<p>&nbsp;</p>')
    return mark_safe(_ensure_trailing_blank_paragraph(''.join(str(part) for part in parts)))


@register.filter
def split_plus(value):
    text = str(value or '')
    return [part.strip() for part in text.split('+') if part.strip()]


def _replace_image_tokens(text, image_list, next_image_index):
    found_name_marker = False

    def replace_name_marker(match):
        nonlocal found_name_marker
        found_name_marker = True
        return _named_image_html_or_fallback(match, image_list)

    text = IMAGE_NAME_TOKEN_PATTERN.sub(replace_name_marker, text)

    cursor = 0
    parts = []
    found_marker = found_name_marker
    has_explicit_marker = found_name_marker

    for match in IMAGE_TOKEN_PATTERN.finditer(text):
        token_start, token_end = match.span()
        parts.append(text[cursor:token_start])
        if match.group(1) is not None or match.group(2) is not None:
            has_explicit_marker = True
        image = _resolve_image(match, image_list, next_image_index)
        if image is not None:
            parts.append(_image_html(image))
            found_marker = True
            if match.group(1) is None and match.group(2) is None:
                next_image_index += 1
        else:
            parts.append(conditional_escape(match.group(0)))
        cursor = token_end

    parts.append(text[cursor:])
    return ''.join(str(part) for part in parts), found_marker, has_explicit_marker, next_image_index


def _apply_rich_text_markup(text):
    rendered_text = text

    for _ in range(10):
        updated_text = RICH_TEXT_IMAGE_URL_PATTERN.sub(_replace_image_url_tag, rendered_text)
        if updated_text == rendered_text:
            break
        rendered_text = updated_text

    for _ in range(10):
        updated_text = RICH_TEXT_BOLD_PATTERN.sub(_replace_bold_tag, rendered_text)
        if updated_text == rendered_text:
            break
        rendered_text = updated_text

    for _ in range(10):
        updated_text = RICH_TEXT_UNDERLINE_PATTERN.sub(_replace_underline_tag, rendered_text)
        if updated_text == rendered_text:
            break
        rendered_text = updated_text

    for _ in range(10):
        updated_text = RICH_TEXT_STRIKE_PATTERN.sub(_replace_strike_tag, rendered_text)
        if updated_text == rendered_text:
            break
        rendered_text = updated_text

    for _ in range(10):
        updated_text = RICH_TEXT_SIZE_PATTERN.sub(_replace_size_tag, rendered_text)
        if updated_text == rendered_text:
            break
        rendered_text = updated_text

    for _ in range(10):
        updated_text = RICH_TEXT_COLOR_PATTERN.sub(_replace_color_tag, rendered_text)
        if updated_text == rendered_text:
            break
        rendered_text = updated_text

    return bleach.clean(
        rendered_text,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        css_sanitizer=CSS_SANITIZER,
        strip=False,
    )


def _replace_bold_tag(match):
    content = match.group(1)
    return f'<strong>{content}</strong>'


def _replace_underline_tag(match):
    content = match.group(1)
    return f'<u>{content}</u>'


def _replace_strike_tag(match):
    content = match.group(1)
    return f'<s>{content}</s>'


def _replace_size_tag(match):
    size_text = match.group(1)
    content = match.group(2)
    try:
        size = int(size_text)
    except ValueError:
        return match.group(0)

    if size < 8 or size > 20 or size % 2 != 0:
        return match.group(0)

    return f'<span style="font-size:{size}pt;">{content}</span>'


def _replace_color_tag(match):
    color = match.group(1)
    content = match.group(2)
    return f'<span style="color:{color};">{content}</span>'


def _replace_image_url_tag(match):
    image_url = match.group(1)
    return format_html(
        '<div class="inline-image-list my-2">'
        '<a href="{}" target="_blank" rel="noopener">'
        '<img class="inline-faq-image" src="{}" alt="URL画像">'
        '</a>'
        '</div>',
        image_url,
        image_url,
    )


def _resolve_image(match, image_list, next_image_index):
    number_part = match.group(1)
    filename_part = match.group(2)

    if number_part:
        image_index = int(number_part) - 1
        if 0 <= image_index < len(image_list):
            return image_list[image_index]
        return None

    if filename_part:
        target = filename_part.strip()
        for image in image_list:
            display_name = (getattr(image, 'display_name', '') or '').strip()
            file_name = os.path.basename(getattr(image.file, 'name', '') or '')
            if target in {display_name, file_name}:
                return image
        return None

    if 0 <= next_image_index < len(image_list):
        return image_list[next_image_index]
    return None


def _named_image_html_or_fallback(match, image_list):
    target = (match.group(1) or '').strip()
    for image in image_list:
        display_name = (getattr(image, 'display_name', '') or '').strip()
        file_name = os.path.basename(getattr(image.file, 'name', '') or '')
        if target in {display_name, file_name}:
            return _image_html(image)
    return conditional_escape(match.group(0))


def _image_html(image):
    display_name = conditional_escape(getattr(image, 'display_name', '') or image.file.name)
    return format_html(
        '<div class="inline-image-list my-2">'
        '<a href="{}" target="_blank" rel="noopener">'
        '<img class="inline-faq-image" src="{}" alt="{}">'
        '</a>'
        '</div>',
        image.file.url,
        image.file.url,
        display_name,
    )
