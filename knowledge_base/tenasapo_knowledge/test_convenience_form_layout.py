"""QR form layout and existing edit behavior regressions."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import get_qr_category_hierarchy
from .models import ConvenienceFeature
from .test_knowledge_form_layout import FormControlsParser


class ConvenienceFormLayoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='qr-layout-admin', email='qr@example.test', password='test-password'
        )
        self.client.force_login(self.user)
        big = get_qr_category_hierarchy()[0]
        middle = big['children'][0]
        self.data = {
            'reference_type': big['value'],
            'category': middle['value'],
            'middle_category': next(iter(middle.get('children', [])), ''),
            'usage_frequency': '4',
            'shortcut_key': 'Ctrl + C',
            'display_text': '選択した内容をコピー',
            'note': 'テスト備考',
        }
        self.feature = ConvenienceFeature.objects.create(
            **self.data, image='manuals/existing.png'
        )

    def assert_layout(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'includes/knowledge_form_completion_assets.html')
        self.assertContains(response, 'data-knowledge-completion="convenience"', count=1)
        parser = FormControlsParser()
        parser.feed(response.content.decode())
        expected = ['form-section-' + name for name in (
            'result', 'shortcut', 'category', 'frequency', 'note'
        )]
        self.assertEqual(parser.section_ids, expected)
        self.assertEqual(parser.section_links, expected)
        self.assertContains(response, 'knowledge-form-layout')
        self.assertContains(response, 'knowledge-form-nav-column')
        self.assertContains(response, 'counter(form-section, decimal-leading-zero)')
        self.assertContains(response, 'aria-current="location"', count=1)
        self.assertNotIn('image', parser.controls)
        self.assertNotContains(response, '得られる結果')
        self.assertEqual(response.context['form'].fields['display_text'].label, '内容')
        self.assertContains(response, 'shadow-none', count=5)
        header = response.content.decode().split('id="app-main"')[0]
        self.assertNotIn('col-12 col-lg-8', header)
        return parser

    def test_create_and_edit_have_matching_numbered_navigation(self):
        for url in (
            reverse('convenience_create'),
            reverse('convenience_edit', args=[self.feature.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assert_layout(response)
                html = response.content.decode()
                browser_start = html.index('id="qrCategoryBrowser"')
                self.assertLess(browser_start, html.index('>カテゴリ登録</a>', browser_start))
                self.assertIn('next=', response.context['category_create_url'])

    def test_edit_preserves_values_and_existing_image_without_upload(self):
        url = reverse('convenience_edit', args=[self.feature.pk])
        response = self.client.get(url)
        parser = self.assert_layout(response)
        for name in ('reference_type', 'category', 'middle_category', 'usage_frequency', 'shortcut_key'):
            self.assertEqual(parser.controls[name]['value'], self.data[name])
        response = self.client.post(url, {**self.data, 'note': '更新した備考'})
        self.assertRedirects(response, reverse('convenience_list'), fetch_redirect_response=False)
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.note, '更新した備考')
        self.assertEqual(self.feature.image.name, 'manuals/existing.png')
        self.assertEqual(self.feature.usage_frequency, '4')

    def test_validation_error_preserves_input(self):
        response = self.client.post(
            reverse('convenience_edit', args=[self.feature.pk]),
            {**self.data, 'shortcut_key': '', 'note': '入力途中の備考'},
        )
        parser = self.assert_layout(response)
        self.assertIn('shortcut_key', response.context['form'].errors)
        self.assertEqual(response.context['form']['note'].value(), '入力途中の備考')
        self.assertEqual(parser.controls['usage_frequency']['value'], '4')
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.shortcut_key, 'Ctrl + C')

    def test_list_does_not_display_management_code(self):
        response = self.client.get(reverse('convenience_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.feature.management_code)
        self.assertNotContains(response, '>管理番号</th>')
        self.assertContains(response, self.feature.display_text)

    def test_create_saves_existing_fields(self):
        response = self.client.post(reverse('convenience_create'), self.data)
        self.assertRedirects(response, reverse('convenience_list'), fetch_redirect_response=False)
        item = ConvenienceFeature.objects.exclude(pk=self.feature.pk).get()
        for name, value in self.data.items():
            self.assertEqual(getattr(item, name), value)

    def test_regular_user_cannot_edit(self):
        user = get_user_model().objects.create_user(username='qr-reader')
        self.client.force_login(user)
        response = self.client.post(
            reverse('convenience_edit', args=[self.feature.pk]),
            {**self.data, 'note': '権限なし'},
        )
        self.assertEqual(response.status_code, 302)
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.note, self.data['note'])