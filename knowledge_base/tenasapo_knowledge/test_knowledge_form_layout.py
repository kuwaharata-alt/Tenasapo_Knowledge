"""FAQ/Tips form layout and hidden metadata regression tests."""

from datetime import date
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import KnowledgeArticleCreateForm, TipsCreateForm
from .models import FAQCategory, KnowledgeArticle, TipsArticle, default_expires_on


class FormControlsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = {}
        self.hidden_containers = []
        self.section_ids = []
        self.section_links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-form-section' in attrs:
            self.section_ids.append(attrs['id'])
        if tag == 'a' and attrs.get('href', '').startswith('#form-section-'):
            self.section_links.append(attrs['href'][1:])
        if tag in ('input', 'textarea', 'select') and attrs.get('name'):
            self.controls[attrs['name']] = attrs
        if tag == 'div' and 'hidden' in attrs:
            self.hidden_containers.append(attrs)


class KnowledgeFormLayoutTestsMixin:
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='form-layout-admin', email='forms@example.test', password='test-password'
        )
        self.client.force_login(self.user)
        self.category = FAQCategory.objects.create(
            parent_name='テスト', middle_name='登録', child_name='画面'
        )

    def payload(self, **overrides):
        data = {
            'title': '画面改修テスト',
            'registered_category': [str(self.category.pk)],
            'target_os_name': 'Windows PC',
            'target_os_version': '11',
            'question': 'テスト質問',
            'answer': 'テスト回答',
            'body': 'テスト内容',
            'visible_to_systena': 'on',
            'reference_links': '[]',
            'tags': '既存タグ, 保持',
        }
        data.update(overrides)
        return data

    def test_create_without_dates_uses_automatic_defaults(self):
        response = self.client.post(reverse(self.create_url), self.payload())
        self.assertEqual(response.status_code, 302)
        item = self.model.objects.get(title='画面改修テスト')
        self.assertEqual(item.source_published_at, timezone.localdate())
        self.assertEqual(item.expires_on, default_expires_on())

    def test_edit_preserves_dates_and_tags_when_dates_omitted(self):
        for source, expiry in [(date(2020, 1, 2), date(2030, 3, 4)), (None, None)]:
            with self.subTest(source=source, expiry=expiry):
                item = self.model.objects.create(
                    title='編集テスト', body='本文', category=self.category.full_name,
                    tags='既存タグ, 保持', source_published_at=source, expires_on=expiry,
                    created_by=self.user, visible_to_systena=True,
                )
                response = self.client.post(reverse(self.edit_url, args=[item.pk]), self.payload())
                self.assertEqual(response.status_code, 302)
                item.refresh_from_db()
                self.assertEqual(item.source_published_at, source)
                self.assertEqual(item.expires_on, expiry)
                self.assertEqual(item.tags, '既存タグ, 保持')
                self.assertTrue(item.visible_to_systena)
                self.assertFalse(item.visible_to_customer)

    def test_hidden_date_values_survive_validation_error(self):
        data = self.payload(title='', source_published_at='2020-01-02', expires_on='2030-03-04')
        response = self.client.post(reverse(self.create_url), data)
        self.assertEqual(response.status_code, 200)
        parser = FormControlsParser()
        parser.feed(response.content.decode())
        for field in ('source_published_at', 'expires_on'):
            self.assertEqual(parser.controls[field]['type'], 'hidden')
            self.assertEqual(parser.controls[field]['value'], data[field])
        self.assertEqual(parser.controls['tags']['value'], data['tags'])

    def test_remanded_edit_shows_only_correction_action(self):
        prefix = 'faq' if self.create_url == 'article_create' else 'tips'
        for state in ('remanded', 'corrected', 'approved', 'registered'):
            with self.subTest(state=state):
                item = self.model.objects.create(
                    title='差戻し編集', body='本文', created_by=self.user,
                    remand_reason='修正してください' if state != 'registered' else '',
                    is_corrected=state == 'corrected', is_approved=state == 'approved',
                )
                response = self.client.get(reverse(self.edit_url, args=[item.pk]))
                self.assertEqual(response.status_code, 200)
                parser = FormControlsParser()
                parser.feed(response.content.decode())
                if state == 'remanded':
                    self.assertContains(response, f'id="{prefix}CorrectBtn"')
                    self.assertNotContains(response, f'id="{prefix}UpdateBtn"')
                    self.assertContains(response, 'この内容で修正</button>')
                    self.assertEqual(parser.controls['submit_action']['value'], 'correct')
                else:
                    self.assertNotContains(response, f'id="{prefix}CorrectBtn"')
                    self.assertContains(response, f'id="{prefix}UpdateBtn"')
                    self.assertContains(response, 'この内容で更新</button>')
                    self.assertEqual(parser.controls['submit_action']['value'], 'update')

    def test_remanded_correction_post_sets_corrected_status(self):
        item = self.model.objects.create(
            title='差戻し編集', body='本文', created_by=self.user,
            remand_reason='修正してください', is_approved=False, is_corrected=False,
        )
        response = self.client.post(
            reverse(self.edit_url, args=[item.pk]), self.payload(submit_action='correct'),
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertTrue(item.is_corrected)
        self.assertFalse(item.is_approved)
        self.assertEqual(item.remand_reason, '修正してください')

    def test_registration_layout_and_hidden_fields(self):
        response = self.client.get(reverse(self.create_url))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        parser = FormControlsParser()
        parser.feed(html)
        self.assertLess(html.index('for="id_title"'), html.index('カテゴリブラウザ'))
        self.assertLess(html.index('name="file_attachments"'), html.index('id="form-section-visibility"'))
        for field in ('source_published_at', 'expires_on'):
            self.assertEqual(parser.controls[field]['type'], 'hidden')
            self.assertNotIn('required', parser.controls[field])
            self.assertNotIn(f'for="id_{field}"', html)
        self.assertNotIn('disabled', parser.controls['tags'])
        self.assertTrue(parser.hidden_containers)
        self.assertIn('hidden>タグ登録</a>', html)
        self.assertNotIn('ソース公開日 ', html)
        self.assertNotIn('掲載期限 ', html)

    def test_section_navigation_matches_form_order(self):
        response = self.client.get(reverse(self.create_url))
        html = response.content.decode()
        parser = FormControlsParser()
        parser.feed(html)
        middle = ['body', 'pdf'] if self.create_url == 'tip_create' else ['question', 'answer']
        expected = ['form-section-' + name for name in ['title', 'category', 'os', *middle, 'reference', 'visibility']]
        self.assertEqual(parser.section_ids, expected)
        self.assertEqual(parser.section_links, expected)
        self.assertContains(response, 'aria-label="入力項目へ移動"')
        self.assertContains(response, 'aria-current="location"', count=1)

    def test_blank_dates_preserve_existing_null_values(self):
        form = self.form_class(
            self.payload(source_published_at='', expires_on=''),
            initial={'source_published_at': None, 'expires_on': None},
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['source_published_at'])
        self.assertIsNone(form.cleaned_data['expires_on'])

    def test_completion_icons_are_enabled_for_create_and_edit_forms(self):
        kind = 'tips' if self.create_url == 'tip_create' else 'faq'
        item = self.model.objects.create(
            title='入力済み記事', body='本文', created_by=self.user,
            category=self.category.full_name, visible_to_systena=True,
        )
        for url in (reverse(self.create_url), reverse(self.edit_url, args=[item.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-knowledge-completion="{kind}"', count=1)
                self.assertContains(response, "editor.on('init input change SetContent Undo Redo focus blur'")
                self.assertContains(response, "new CustomEvent('knowledge-editor-state'")
                self.assertContains(response, '必須未入力・要確認')
                self.assertContains(response, '任意は警告なし')


class FAQFormLayoutTests(KnowledgeFormLayoutTestsMixin, TestCase):
    model = KnowledgeArticle
    form_class = KnowledgeArticleCreateForm
    create_url = 'article_create'
    edit_url = 'article_edit'


class TipsFormLayoutTests(KnowledgeFormLayoutTestsMixin, TestCase):
    model = TipsArticle
    form_class = TipsCreateForm
    create_url = 'tip_create'
    edit_url = 'tip_edit'