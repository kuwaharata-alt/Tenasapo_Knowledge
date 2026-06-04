from django import template
import os
import re
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe


register = template.Library()
IMAGE_TOKEN_PATTERN = re.compile(r'<image(?:(\d+)|:([^>]+))?>', flags=re.IGNORECASE)
RICH_TEXT_BOLD_PATTERN = re.compile(r'\[b\](.*?)\[/b\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_UNDERLINE_PATTERN = re.compile(r'\[u\](.*?)\[/u\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_STRIKE_PATTERN = re.compile(r'\[s\](.*?)\[/s\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_SIZE_PATTERN = re.compile(r'\[size=(\d{1,3})\](.*?)\[/size\]', flags=re.IGNORECASE | re.DOTALL)
RICH_TEXT_COLOR_PATTERN = re.compile(
    r'\[color=(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|[a-zA-Z]{3,20})\](.*?)\[/color\]',
    flags=re.IGNORECASE | re.DOTALL,
)


@register.filter
def render_inline_images(value, images):
    image_list = list(images or [])
    next_image_index = 0
    parts = []
    has_marker = False
    has_explicit_marker = False

    for line in str(value or '').splitlines():
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
            parts.append('<br>')

    if not has_marker:
        for image in image_list:
            parts.append(_image_html(image))
    elif (not has_explicit_marker) and next_image_index < len(image_list):
        for image in image_list[next_image_index:]:
            parts.append(_image_html(image))

    return mark_safe(''.join(str(part) for part in parts))


@register.filter
def render_rich_text(value):
    text = str(value or '')
    
    # Apply rich-text markup to entire text first (before splitting into lines)
    rendered_text = _apply_rich_text_markup(text)
    
    # Then split into paragraphs for display
    parts = []
    for line in rendered_text.splitlines():
        if line.strip():
            parts.append(format_html('<p>{}</p>', mark_safe(line)))
        else:
            parts.append('<br>')
    return mark_safe(''.join(str(part) for part in parts))


def _replace_image_tokens(text, image_list, next_image_index):
    cursor = 0
    parts = []
    found_marker = False
    has_explicit_marker = False

    for match in IMAGE_TOKEN_PATTERN.finditer(text):
        token_start, token_end = match.span()
        parts.append(conditional_escape(text[cursor:token_start]))
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

    parts.append(conditional_escape(text[cursor:]))
    return ''.join(str(part) for part in parts), found_marker, has_explicit_marker, next_image_index


def _apply_rich_text_markup(text):
    # First escape raw HTML to prevent XSS
    rendered_text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

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

    return rendered_text


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
