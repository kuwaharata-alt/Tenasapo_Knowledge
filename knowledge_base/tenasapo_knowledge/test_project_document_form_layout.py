"""Document form navigation, optional project metadata and persistence regressions."""

import json
from contextlib import ExitStack
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils.html import format_html

from .models import FAQCategory, ProjectDocument, ProjectDocumentRevisionHistory
from .test_knowledge_form_layout import FormControlsParser


class ProjectDocumentFormLayoutTests(TestCase):
    PDF_CONTENT = b'%PDF-1.4\nDocument form regression test\n%%EOF'
    OFFICE_CONTENT = b'Existing Office document'
    SECTIONS = ('title', 'product', 'category', 'type', 'files', 'template', 'project')
    PROJECT_VALUES = (
        ('', ''),
        ('PRJ-001', ''),
        ('', 'テスト顧客'),
        ('PRJ-001', 'テスト顧客'),
    )

    def setUp(self):
        # Both models have explicit external storage, so overriding STORAGES alone
        # would not isolate uploads or revision-file reads from Google Drive.
        self.storage = InMemoryStorage()
        patches = ExitStack()
        self.addCleanup(patches.close)
        for model in (ProjectDocument, ProjectDocumentRevisionHistory):
            for name in ('file_office', 'file_pdf'):
                patches.enter_context(
                    patch.object(model._meta.get_field(name), 'storage', self.storage)
                )

        self.user = get_user_model().objects.create_superuser(
            username='document-form-admin',
            email='document-forms@example.test',
            password='test-password',
        )
        self.client.force_login(self.user)
        self.category = FAQCategory.objects.create(
            parent_name='テスト', middle_name='ドキュメント', child_name='画面'
        )
        self.modern_category = FAQCategory.objects.create(
            parent_name='テスト', middle_name='中末端 <& "現行">', child_name=''
        )
        self.legacy_category = FAQCategory.objects.create(
            parent_name='テスト', middle_name='', child_name='旧中末端 <& "旧式">'
        )

    def pdf_upload(self):
        return SimpleUploadedFile(
            'document.pdf', self.PDF_CONTENT, content_type='application/pdf'
        )

    def payload(self, **overrides):
        data = {
            'title': 'ドキュメント画面テスト',
            'product_name': 'テスト製品',
            'product_version': '2.0',
            'parent_category': self.category.parent_name,
            'middle_category': self.category.middle_name,
            'child_category': self.category.child_name,
            'category_selection_id': str(self.category.pk),
            'document_type': ProjectDocument.DOCUMENT_TYPE_PARAMETER,
            'is_template': 'on',
            'project_number': 'PRJ-001',
            'customer_name': 'テスト顧客',
        }
        data.update(overrides)
        return data

    def category_values(self, category):
        return {
            'parent_category': category.parent_name,
            'middle_category': category.middle_name,
            'child_category': category.child_name,
            'category_selection_id': str(category.pk),
        }

    def existing_document(self):
        return ProjectDocument.objects.create(
            title='既存ドキュメント',
            project_number='OLD-001',
            customer_name='既存顧客',
            product_name='既存製品',
            product_version='1.0',
            category=self.category.full_name,
            document_type=ProjectDocument.DOCUMENT_TYPE_HEARING,
            is_template=True,
            created_by=self.user,
            created_by_name=self.user.username,
            file_office=SimpleUploadedFile('existing.docx', self.OFFICE_CONTENT),
            file_pdf=self.pdf_upload(),
        )

    def parse_controls(self, response):
        parser = FormControlsParser()
        parser.feed(response.content.decode(response.charset))
        return parser

    def assert_layout(self, response, *, is_update):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tenasapo_knowledge/project_document_form.html')
        self.assertTemplateUsed(response, 'includes/knowledge_form_completion_assets.html')
        self.assertContains(response, 'data-knowledge-completion="document"', count=1)
        parser = self.parse_controls(response)
        sections = [*self.SECTIONS, *(['revision'] if is_update else [])]
        expected = ['form-section-' + name for name in sections]
        self.assertEqual(parser.section_ids, expected)
        self.assertEqual(parser.section_links, expected)
        self.assertContains(response, 'knowledge-form-nav-column')
        self.assertContains(response, 'class="knowledge-form-nav"')
        html = response.content.decode(response.charset)
        self.assertLess(html.index('<aside '), html.index('id="documentForm"'))
        # Numbering is supplied by the same CSS counters as FAQ/Tips, not text.
        for counter in ('form-section', 'form-nav'):
            self.assertContains(response, f'counter-increment: {counter};')
            self.assertContains(response, f'counter({counter}, decimal-leading-zero)')
        for name in ('project_number', 'customer_name'):
            required = not bool(response.context['form']['is_template'].value())
            self.assertEqual('required' in parser.controls[name], required)
            self.assertEqual(response.context['form'].fields[name].required, required)
        for name in ('title', 'document_type'):
            self.assertIn('required', parser.controls[name])
        for name in (
            'parent_category', 'middle_category', 'child_category', 'category_selection_id'
        ):
            self.assertEqual(parser.controls[name]['type'], 'hidden')
            self.assertNotIn('required', parser.controls[name])
        self.assertTrue(response.context['form'].fields['parent_category'].required)
        self.assertFalse(response.context['form'].fields['child_category'].required)
        for pane in ('Parent', 'Middle', 'Child'):
            self.assertContains(response, f'id="documentCategory{pane}Pane"')
        self.assertContains(response, '中カテゴリまで選択してください。小カテゴリは必要に応じて選択できます。')
        records = json.loads(response.context['document_category_records_json'])
        self.assertIsInstance(records, list)
        self.assertCountEqual(
            records,
            list(FAQCategory.objects.values('id', 'parent_name', 'middle_name', 'child_name')),
        )
        if is_update:
            self.assertIn('required', parser.controls['revision_note'])
            self.assertTrue(response.context['form'].fields['revision_note'].required)
        else:
            self.assertNotIn('revision_note', parser.controls)

    def assert_rendered_values(self, response, values):
        parser = self.parse_controls(response)
        form = response.context['form']
        for name in (
            'title', 'product_name', 'product_version', 'project_number', 'customer_name'
        ):
            self.assertEqual(parser.controls[name].get('value', ''), values[name], name)
            self.assertEqual(form[name].value(), values[name], name)
        self.assertEqual(form['document_type'].value(), values['document_type'])
        label = dict(ProjectDocument.DOCUMENT_TYPE_CHOICES)[values['document_type']]
        self.assertContains(
            response,
            format_html('<option value="{}" selected>{}</option>', values['document_type'], label),
            html=True,
        )
        self.assertIn('checked', parser.controls['is_template'])
        # The three-pane browser restores its selection from hidden inputs.
        for name in (
            'parent_category', 'middle_category', 'child_category', 'category_selection_id'
        ):
            expected = str(values.get(name, ''))
            self.assertEqual(str(form[name].value() or ''), expected, name)
            self.assertEqual(parser.controls[name].get('value', ''), expected, name)
        if 'revision_note' in values and 'revision_note' in form.fields:
            self.assertEqual(form['revision_note'].value(), values['revision_note'])
            self.assertContains(response, format_html('>{}</textarea>', values['revision_note']))

    def assert_file_content(self, field_file, expected):
        self.assertTrue(field_file)
        self.assertIs(field_file.storage, self.storage)
        with field_file.open('rb') as uploaded:
            self.assertEqual(uploaded.read(), expected)

    def assert_saved_values(self, document, data, *, category=None):
        for name in (
            'title', 'product_name', 'product_version', 'document_type',
            'project_number', 'customer_name',
        ):
            self.assertEqual(getattr(document, name), data[name], name)
        self.assertEqual(document.category, (category or self.category).full_name)
        self.assertTrue(document.is_template)
        self.assertEqual(document.created_by, self.user)

    def test_create_get_has_numbered_navigation_and_optional_project_fields(self):
        response = self.client.get(reverse('project_document_create'))
        self.assert_layout(response, is_update=False)
        parser = self.parse_controls(response)
        self.assertEqual(parser.controls['project_number']['value'], '')
        self.assertEqual(parser.controls['customer_name']['value'], '')

    def test_registered_category_paths_save_on_create_and_edit(self):
        for category in (self.modern_category, self.legacy_category, self.category):
            for provide_id in (False, True):
                for is_update in (False, True):
                    with self.subTest(
                        category=category.full_name, provide_id=provide_id, is_update=is_update
                    ):
                        data = self.payload(
                            **self.category_values(category), revision_note='カテゴリを変更'
                        )
                        if not provide_id:
                            data.pop('category_selection_id')
                        if is_update:
                            document = self.existing_document()
                            url = reverse('project_document_edit', args=[document.pk])
                        else:
                            data['file_pdf'] = self.pdf_upload()
                            url = reverse('project_document_create')
                        count = ProjectDocument.objects.count()
                        history_count = ProjectDocumentRevisionHistory.objects.count()
                        response = self.client.post(url, data)
                        self.assertRedirects(
                            response, reverse('project_document_list'), fetch_redirect_response=False
                        )
                        self.assertEqual(ProjectDocument.objects.count(), count + int(not is_update))
                        self.assertEqual(ProjectDocumentRevisionHistory.objects.count(), history_count + 1)
                        if is_update:
                            document.refresh_from_db()
                        else:
                            document = ProjectDocument.objects.latest('pk')
                        self.assert_saved_values(document, data, category=category)
                        self.assert_file_content(document.file_pdf, self.PDF_CONTENT)
                        revision = document.revisions.get()
                        self.assertEqual(
                            revision.revision_note, data['revision_note'] if is_update else '新規作成'
                        )
                        self.assert_file_content(revision.file_pdf, self.PDF_CONTENT)

    def test_old_post_shape_without_id_falls_back_to_modern_category_on_create_and_edit(self):
        for is_update in (False, True):
            with self.subTest(is_update=is_update):
                data = self.payload(
                    parent_category=self.modern_category.parent_name,
                    middle_category='', child_category=self.modern_category.middle_name,
                    revision_note='旧形式から現行カテゴリへ更新',
                )
                data.pop('category_selection_id')
                if is_update:
                    document = self.existing_document()
                    url = reverse('project_document_edit', args=[document.pk])
                else:
                    data['file_pdf'] = self.pdf_upload()
                    url = reverse('project_document_create')
                count = ProjectDocument.objects.count()
                history_count = ProjectDocumentRevisionHistory.objects.count()
                response = self.client.post(url, data)
                self.assertRedirects(
                    response, reverse('project_document_list'), fetch_redirect_response=False
                )
                self.assertEqual(ProjectDocument.objects.count(), count + int(not is_update))
                self.assertEqual(ProjectDocumentRevisionHistory.objects.count(), history_count + 1)
                if is_update:
                    document.refresh_from_db()
                else:
                    document = ProjectDocument.objects.latest('pk')
                self.assert_saved_values(document, data, category=self.modern_category)
                self.assert_file_content(document.file_pdf, self.PDF_CONTENT)

    def test_invalid_category_paths_are_rejected_and_preserved_on_create_and_edit(self):
        parent_only = FAQCategory.objects.create(
            parent_name='大カテゴリのみ', middle_name='', child_name=''
        )
        cases = (
            ('parent_only', self.category_values(parent_only)),
            ('missing_parent', {'parent_category': ''}),
            ('unregistered_middle', {'middle_category': '未登録', 'child_category': ''}),
            ('virtual_middle', {'child_category': ''}),
            ('wrong_child', {'child_category': '存在しない小カテゴリ'}),
            ('wrong_parent', {'parent_category': '別の大カテゴリ'}),
            ('same_full_name_different_child_path', {
                'middle_category': '',
                'child_category': f'{self.category.middle_name}/{self.category.child_name}',
            }),
            ('same_full_name_different_middle_path', {
                'middle_category': f'{self.category.middle_name}/{self.category.child_name}',
                'child_category': '',
            }),
            ('id_path_mismatch', {'category_selection_id': str(self.modern_category.pk)}),
            ('unknown_id', {'category_selection_id': str(parent_only.pk + 1000)}),
            ('old_shape_with_id', {
                **self.category_values(self.modern_category),
                'middle_category': '', 'child_category': self.modern_category.middle_name,
            }),
        )
        for case, overrides in cases:
            # Exact path validation must work independently of the optional ID.
            provide_ids = (True,) if case in (
                'id_path_mismatch', 'unknown_id', 'old_shape_with_id'
            ) else (False, True)
            for provide_id in provide_ids:
                for is_update in (False, True):
                    with self.subTest(case=case, provide_id=provide_id, is_update=is_update):
                        document = self.existing_document()
                        original = ProjectDocument.objects.filter(pk=document.pk).values().get()
                        data = self.payload(**overrides, revision_note='保存されない更新')
                        if not provide_id:
                            data.pop('category_selection_id')
                        if is_update:
                            url = reverse('project_document_edit', args=[document.pk])
                        else:
                            data['file_pdf'] = self.pdf_upload()
                            url = reverse('project_document_create')
                        count = ProjectDocument.objects.count()
                        history_count = ProjectDocumentRevisionHistory.objects.count()
                        response = self.client.post(url, data)
                        self.assert_layout(response, is_update=is_update)
                        self.assertIn('middle_category', response.context['form'].errors)
                        self.assert_rendered_values(response, data)
                        self.assertEqual(ProjectDocument.objects.count(), count)
                        self.assertEqual(ProjectDocumentRevisionHistory.objects.count(), history_count)
                        self.assertEqual(
                            ProjectDocument.objects.filter(pk=document.pk).values().get(), original
                        )

    def test_edit_get_restores_registered_modern_legacy_and_three_level_categories(self):
        for category in (self.modern_category, self.legacy_category, self.category):
            with self.subTest(category=category.full_name):
                document = self.existing_document()
                document.category = category.full_name
                document.save(update_fields=['category'])
                response = self.client.get(reverse('project_document_edit', args=[document.pk]))
                self.assert_layout(response, is_update=True)
                self.assert_rendered_values(
                    response,
                    self.payload(
                        **self.category_values(category),
                        title=document.title, project_number=document.project_number,
                        customer_name=document.customer_name, product_name=document.product_name,
                        product_version=document.product_version, document_type=document.document_type,
                    ),
                )

    def test_validation_error_preserves_changed_category_instead_of_edit_initial(self):
        for category in (self.modern_category, self.legacy_category, self.category):
            for is_update in (False, True):
                with self.subTest(category=category.full_name, is_update=is_update):
                    document = self.existing_document()
                    document.category = (
                        self.legacy_category if category == self.modern_category else self.modern_category
                    ).full_name
                    document.save(update_fields=['category'])
                    original = ProjectDocument.objects.filter(pk=document.pk).values().get()
                    data = self.payload(
                        **self.category_values(category), title='', revision_note='選択を保持 <& "確認">'
                    )
                    if is_update:
                        url = reverse('project_document_edit', args=[document.pk])
                    else:
                        data['file_pdf'] = self.pdf_upload()
                        url = reverse('project_document_create')
                    count = ProjectDocument.objects.count()
                    history_count = ProjectDocumentRevisionHistory.objects.count()
                    response = self.client.post(url, data)
                    self.assert_layout(response, is_update=is_update)
                    self.assertEqual(set(response.context['form'].errors), {'title'})
                    self.assert_rendered_values(response, data)
                    self.assertEqual(ProjectDocument.objects.count(), count)
                    self.assertEqual(ProjectDocumentRevisionHistory.objects.count(), history_count)
                    self.assertEqual(
                        ProjectDocument.objects.filter(pk=document.pk).values().get(), original
                    )

    def test_edit_get_preserves_existing_values_and_requires_revision_note(self):
        document = self.existing_document()
        response = self.client.get(reverse('project_document_edit', args=[document.pk]))
        self.assert_layout(response, is_update=True)
        self.assert_rendered_values(
            response,
            self.payload(
                title=document.title,
                project_number=document.project_number,
                customer_name=document.customer_name,
                product_name=document.product_name,
                product_version=document.product_version,
                document_type=document.document_type,
            ),
        )
        self.assertContains(response, document.download_office_url + '?download=1')
        self.assertContains(response, document.download_pdf_url + '?download=1')

    def test_create_saves_all_optional_project_metadata_combinations(self):
        for index, (project_number, customer_name) in enumerate(self.PROJECT_VALUES):
            with self.subTest(project_number=project_number, customer_name=customer_name):
                data = self.payload(
                    title=f'新規ドキュメント {index}',
                    project_number=project_number,
                    customer_name=customer_name,
                )
                count = ProjectDocument.objects.count()
                response = self.client.post(
                    reverse('project_document_create'), {**data, 'file_pdf': self.pdf_upload()}
                )
                self.assertRedirects(
                    response, reverse('project_document_list'), fetch_redirect_response=False
                )
                self.assertEqual(ProjectDocument.objects.count(), count + 1)
                document = ProjectDocument.objects.get(title=data['title'])
                self.assert_saved_values(document, data)
                self.assert_file_content(document.file_pdf, self.PDF_CONTENT)
                self.assertFalse(document.file_office)
                revision = document.revisions.get()
                self.assertEqual(revision.revision_note, '新規作成')
                self.assertEqual(revision.updated_by, self.user)
                self.assert_file_content(revision.file_pdf, self.PDF_CONTENT)

    def test_edit_saves_optional_metadata_without_reupload_and_creates_history(self):
        for project_number, customer_name in self.PROJECT_VALUES:
            with self.subTest(project_number=project_number, customer_name=customer_name):
                document = self.existing_document()
                original_files = (document.file_office.name, document.file_pdf.name)
                data = self.payload(
                    project_number=project_number,
                    customer_name=customer_name,
                    revision_note='案件情報と製品情報を更新しました。',
                )
                count = ProjectDocument.objects.count()
                response = self.client.post(
                    reverse('project_document_edit', args=[document.pk]), data
                )
                self.assertRedirects(
                    response, reverse('project_document_list'), fetch_redirect_response=False
                )
                self.assertEqual(ProjectDocument.objects.count(), count)
                document.refresh_from_db()
                self.assert_saved_values(document, data)
                self.assertEqual(
                    (document.file_office.name, document.file_pdf.name), original_files
                )
                self.assert_file_content(document.file_office, self.OFFICE_CONTENT)
                self.assert_file_content(document.file_pdf, self.PDF_CONTENT)
                revision = document.revisions.get()
                self.assertEqual(revision.revision_note, data['revision_note'])
                self.assertEqual(revision.updated_by, self.user)
                self.assert_file_content(revision.file_office, self.OFFICE_CONTENT)
                self.assert_file_content(revision.file_pdf, self.PDF_CONTENT)

    def test_validation_error_restores_entered_values_on_create_and_edit(self):
        document = self.existing_document()
        original_title = document.title
        for is_update in (False, True):
            with self.subTest(is_update=is_update):
                data = self.payload(
                    title='',
                    project_number='入力中 & "案件"',
                    customer_name='入力中 <顧客>',
                    product_name='変更中 <製品>',
                    product_version='3.0 & beta',
                )
                if is_update:
                    data['revision_note'] = '入力中の更新内容 <保持>\n二行目'
                    url = reverse('project_document_edit', args=[document.pk])
                else:
                    data['file_pdf'] = self.pdf_upload()
                    url = reverse('project_document_create')
                count = ProjectDocument.objects.count()
                history_count = ProjectDocumentRevisionHistory.objects.count()
                response = self.client.post(url, data)
                self.assert_layout(response, is_update=is_update)
                self.assertIn('title', response.context['form'].errors)
                self.assert_rendered_values(response, data)
                self.assertEqual(ProjectDocument.objects.count(), count)
                self.assertEqual(ProjectDocumentRevisionHistory.objects.count(), history_count)
                document.refresh_from_db()
                self.assertEqual(document.title, original_title)

    def test_non_template_requires_both_project_fields_on_create_and_edit(self):
        document = self.existing_document()
        for is_update in (False, True):
            for project_number, customer_name in self.PROJECT_VALUES[:-1]:
                with self.subTest(is_update=is_update, project_number=project_number, customer_name=customer_name):
                    data = self.payload(
                        is_template='', project_number=project_number,
                        customer_name=customer_name, revision_note='通常資料へ変更',
                    )
                    if is_update:
                        url = reverse('project_document_edit', args=[document.pk])
                    else:
                        url = reverse('project_document_create')
                        data['file_pdf'] = self.pdf_upload()
                    response = self.client.post(url, data)
                    self.assert_layout(response, is_update=is_update)
                    errors = response.context['form'].errors
                    for name, value in (('project_number', project_number), ('customer_name', customer_name)):
                        self.assertEqual(name in errors, not bool(value))
                    self.assertEqual(ProjectDocument.objects.count(), 1)
                    self.assertFalse(document.revisions.exists())
                    document.refresh_from_db()
                    self.assertTrue(document.is_template)
                    self.assertEqual(document.project_number, 'OLD-001')

    def test_non_template_saves_with_both_project_fields_on_create_and_edit(self):
        data = self.payload(is_template='', file_pdf=self.pdf_upload())
        response = self.client.post(reverse('project_document_create'), data)
        self.assertEqual(response.status_code, 302)
        document = ProjectDocument.objects.get()
        self.assertFalse(document.is_template)
        self.assertEqual(document.project_number, 'PRJ-001')
        self.assertEqual(document.customer_name, 'テスト顧客')
        response = self.client.post(
            reverse('project_document_edit', args=[document.pk]),
            self.payload(is_template='', revision_note='通常資料の更新'),
        )
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertFalse(document.is_template)
        self.assertEqual(document.revisions.count(), 2)
        self.assert_file_content(document.file_pdf, self.PDF_CONTENT)

    def test_non_template_rejects_whitespace_project_fields(self):
        response = self.client.post(
            reverse('project_document_create'),
            self.payload(is_template='', project_number='  ', customer_name='  ', file_pdf=self.pdf_upload()),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('project_number', response.context['form'].errors)
        self.assertIn('customer_name', response.context['form'].errors)
        self.assertFalse(ProjectDocument.objects.exists())

    def test_create_without_any_file_is_rejected_and_preserves_input(self):
        data = self.payload(project_number='', customer_name='')
        response = self.client.post(reverse('project_document_create'), data)
        self.assert_layout(response, is_update=False)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertContains(
            response,
            'OfficeドキュメントまたはPDFドキュメントの少なくともどちらか一方はアップロードしてください。',
        )
        self.assert_rendered_values(response, data)
        self.assertFalse(ProjectDocument.objects.exists())
        self.assertFalse(ProjectDocumentRevisionHistory.objects.exists())

    def test_edit_without_revision_note_does_not_save_or_create_history(self):
        document = self.existing_document()
        original = ProjectDocument.objects.filter(pk=document.pk).values().get()
        data = self.payload(project_number='', customer_name='', revision_note='')
        response = self.client.post(
            reverse('project_document_edit', args=[document.pk]), data
        )
        self.assert_layout(response, is_update=True)
        self.assertIn('revision_note', response.context['form'].errors)
        self.assertNotIn('__all__', response.context['form'].errors)
        self.assert_rendered_values(response, data)
        self.assertEqual(ProjectDocument.objects.filter(pk=document.pk).values().get(), original)
        self.assertEqual(ProjectDocument.objects.count(), 1)
        self.assertFalse(document.revisions.exists())
        document.refresh_from_db()
        self.assert_file_content(document.file_office, self.OFFICE_CONTENT)
        self.assert_file_content(document.file_pdf, self.PDF_CONTENT)