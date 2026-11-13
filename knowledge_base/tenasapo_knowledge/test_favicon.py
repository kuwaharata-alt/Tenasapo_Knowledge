from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from PIL import Image


class FaviconTests(SimpleTestCase):
    def test_base_template_uses_transparent_version(self):
        source = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')
        self.assertIn("{% static 'Nexus_icon_v4.ico' %}", source)
        self.assertNotIn("{% static 'Nexus_icon_v3.ico' %}", source)
        self.assertIsNotNone(finders.find('Nexus_icon_v4.ico'))

    def test_outer_white_background_is_transparent_and_artwork_is_preserved(self):
        directory = Path(settings.BASE_DIR) / 'logo'
        with Image.open(directory / 'Nexus_icon_v3.ico') as original, \
                Image.open(directory / 'Nexus_icon_v4.ico') as updated:
            source = original.convert('RGBA')
            result = updated.convert('RGBA')
        self.assertEqual(source.size, result.size)
        width, height = result.size
        for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
            self.assertEqual(result.getpixel(point)[3], 0)
        changed = 0
        for before, after in zip(source.getdata(), result.getdata()):
            self.assertEqual(before[:3], after[:3])
            if before != after:
                changed += 1
                self.assertGreaterEqual(min(before[:3]), 240)
                self.assertEqual(after[3], 0)
        self.assertGreater(changed, 0)
        self.assertEqual(result.getchannel('A').getextrema(), (0, 255))