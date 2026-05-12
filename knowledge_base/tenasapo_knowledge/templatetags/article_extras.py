from django import template
import os
import re
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe


register = template.Library()
IMAGE_TOKEN_PATTERN = re.compile(r'<image(?:(\d+)|:([^>]+))?>', flags=re.IGNORECASE)


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
            parts.append(format_html('<p>{}</p>', mark_safe(rendered_line)))
        else:
            parts.append('<br>')

    if not has_marker:
        for image in image_list:
            parts.append(_image_html(image))
    elif (not has_explicit_marker) and next_image_index < len(image_list):
        for image in image_list[next_image_index:]:
            parts.append(_image_html(image))

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
