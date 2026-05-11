from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe


register = template.Library()


@register.filter
def render_inline_images(value, images):
    image_list = list(images or [])
    image_index = 0
    parts = []
    has_marker = False

    for line in str(value or '').splitlines():
        if line.strip() == '<image>' and image_index < len(image_list):
            has_marker = True
            parts.append(_image_html(image_list[image_index]))
            image_index += 1
            continue

        if line.strip():
            parts.append(format_html('<p>{}</p>', line))
        else:
            parts.append('<br>')

    if not has_marker:
        for image in image_list:
            parts.append(_image_html(image))
    elif image_index < len(image_list):
        for image in image_list[image_index:]:
            parts.append(_image_html(image))

    return mark_safe(''.join(str(part) for part in parts))


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
