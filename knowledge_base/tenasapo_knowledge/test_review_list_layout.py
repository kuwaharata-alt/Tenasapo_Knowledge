"""Review status counts, filtering, permissions, and rendered UI contracts."""

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.urls import reverse_lazy

from .models import KnowledgeArticle, TipsArticle
from .views import ReviewListView, _open_gas_newtab_response


class ReviewDOMParser(HTMLParser):
    """Record real elements and ancestry without depending on CSS whitespace."""

    void_tags = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'parents': tuple(self.stack), 'text': ''}
        self.nodes.append(node)
        if tag not in self.void_tags:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'] += data

    def find(self, *, tag=None, css_class=None, **attrs):
        return [node for node in self.nodes
                if (tag is None or node['tag'] == tag)
                and (css_class is None or css_class in node['attrs'].get('class', '').split())
                and all(node['attrs'].get(key) == value for key, value in attrs.items())]


class ReviewListLayoutTests(TestCase):
    def test_mail_draft_response_accepts_lazy_fallback_url(self):
        response = _open_gas_newtab_response(
            RequestFactory().get('/'), 'https://example.com/draft', reverse_lazy('review_list'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(reverse('review_list'), response.content.decode())
        self.assertIn('window.open', response.content.decode())

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.reviewer = users.objects.create_user(username='review-layout-reviewer')
        group, _ = Group.objects.get_or_create(name='レビュアー')
        cls.reviewer.groups.add(group)
        cls.editor = users.objects.create_user(username='review-layout-editor', is_staff=True)
        cls.creator = users.objects.create_user(username='review-layout-creator')
        cls.other = users.objects.create_user(username='review-layout-other')
        cls.start = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        cls.items = []
        cls.states = ('registered', 'corrected', 'remanded', 'approved')
        for kind, model, prefix in (
            ('faq', KnowledgeArticle, 'FQ'), ('tips', TipsArticle, 'TP'),
        ):
            for index, status in enumerate(cls.states):
                item = model.objects.create(
                    title=f'{kind} {index} <& "title">', body='本文',
                    management_code=f'{prefix}{index + 2:06d}',
                    created_by=cls.creator, is_approved=status == 'approved',
                    is_corrected=status == 'corrected',
                    # Corrected takes precedence even with an old remand reason.
                    remand_reason='差戻し理由' if status in ('corrected', 'remanded') else '',
                    ai_review='AI <& "comment">',
                )
                model.objects.filter(pk=item.pk).update(
                    created_at=cls.start + timedelta(days=index),
                    updated_at=cls.start + timedelta(days=10 - index),
                )
                item.refresh_from_db()
                cls.items.append((kind, status, item))
        for model in (KnowledgeArticle, TipsArticle):
            for creator, day in ((cls.other, 1), (cls.creator, -1), (cls.creator, 4)):
                item = model.objects.create(
                    title='除外対象', body='本文', created_by=creator, is_approved=False,
                )
                model.objects.filter(pk=item.pk).update(created_at=cls.start + timedelta(days=day))

    def params(self, **overrides):
        return {'creator_id': str(self.creator.pk), 'date_from': '2026-10-10',
                'date_to': '2026-10-13', **overrides}

    def context(self, params=None, *, user=None, url=None):
        request = RequestFactory().get(url or reverse('review_list'), params or {})
        request.user = user or self.reviewer
        view = ReviewListView()
        view.setup(request)
        return view.get_context_data(), request

    def counts(self, context):
        return {tab['value']: tab['count'] for tab in context['review_status_tabs']}

    def parse(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tenasapo_knowledge/review_list.html')
        parser = ReviewDOMParser()
        parser.feed(response.content.decode(response.charset))
        return parser

    def test_tab_schema_order_labels_and_status_independent_counts(self):
        for status in ('', *self.states):
            with self.subTest(status=status):
                context, _ = self.context(self.params(status=status))
                tabs = context['review_status_tabs']
                self.assertEqual([(tab['value'], tab['label']) for tab in tabs], [
                    ('', 'すべて'), ('registered', '登録'), ('corrected', '修正済'),
                    ('remanded', '差戻し'), ('approved', '承認済'),
                ])
                for tab in tabs:
                    self.assertEqual(set(tab), {'value', 'label', 'count', 'url', 'active'})
                    self.assertIsInstance(tab['active'], bool)
                self.assertEqual(self.counts(context), {'': 8, **dict.fromkeys(self.states, 2)})
                self.assertEqual([tab['value'] for tab in tabs if tab['active']], [status])
                self.assertEqual(context['review_result_count'], 8 if not status else 2)
                self.assertEqual(len(context['review_items']), context['review_result_count'])
                if status:
                    self.assertEqual({item['approval_status'] for item in context['review_items']}, {status})

    def test_counts_respect_each_type_and_combined_creator_date_filters(self):
        for kind in ('faq', 'tips'):
            with self.subTest(kind=kind):
                context, _ = self.context(self.params(type=kind, status='corrected'))
                self.assertEqual(self.counts(context), {'': 4, **dict.fromkeys(self.states, 1)})
                self.assertEqual([(item['type'], item['approval_status'])
                                  for item in context['review_items']], [(kind, 'corrected')])
        context, _ = self.context(self.params(date_from='2026-10-11', date_to='2026-10-12'))
        self.assertEqual(self.counts(context), {
            '': 4, 'registered': 0, 'corrected': 2, 'remanded': 2, 'approved': 0,
        })
        context, _ = self.context(self.params(creator_id=str(self.other.pk)))
        self.assertEqual(self.counts(context), {
            '': 2, 'registered': 2, 'corrected': 0, 'remanded': 0, 'approved': 0,
        })

    def test_tab_urls_preserve_repeated_get_values_and_sorts_without_mutation(self):
        params = self.params(type='faq', status='registered', focus='faq-999',
                             sort='title', sort_dir='asc', extra=['a&b', '日本語 空白'])
        context, request = self.context(params)
        original = request.GET.copy()
        for tab in context['review_status_tabs']:
            with self.subTest(tab=tab['value']):
                parts = urlsplit(tab['url'])
                self.assertEqual(parts.path, reverse('review_list'))
                expected = {key: values for key, values in request.GET.lists()
                            if key not in {'focus', 'status'}}
                if tab['value']:
                    expected['status'] = [tab['value']]
                self.assertEqual(parse_qs(parts.query), expected)
                switched, _ = self.context(url=tab['url'])
                self.assertEqual(switched['review_result_count'], 4 if not tab['value'] else 1)
        self.assertEqual(request.GET, original)

    def test_all_url_has_no_query_when_only_focus_and_status_were_present(self):
        context, _ = self.context({'status': 'corrected', 'focus': 'faq-999'})
        self.assertEqual(context['review_status_tabs'][0]['url'], reverse('review_list'))

    def test_existing_sort_order_and_sort_links_are_preserved(self):
        for sort in ('title', 'created_at', 'updated_at', 'management_code'):
            for direction in ('asc', 'desc'):
                with self.subTest(sort=sort, direction=direction):
                    params = self.params(sort=sort, sort_dir=direction, status='corrected')
                    context, request = self.context(params)
                    items = context['review_items']
                    expected = [item for _, state, item in self.items if state == 'corrected']
                    if sort != 'management_code':
                        expected.sort(key=lambda item: getattr(item, sort), reverse=direction == 'desc')
                    self.assertEqual([(item['type'], item['id']) for item in items], [
                        ('faq' if isinstance(item, KnowledgeArticle) else 'tips', item.pk)
                        for item in expected
                    ])
                    for target, key in (('title', 'sort_title_url'), ('created_at', 'sort_created_at_url'),
                                        ('management_code', 'sort_management_code_url')):
                        query = parse_qs(urlsplit(context[key]).query)
                        expected_query = {key: values for key, values in request.GET.lists()}
                        expected_query['sort'] = [target]
                        expected_query['sort_dir'] = [
                            ('desc' if direction == 'asc' else 'asc') if target == sort
                            else ('desc' if target == 'created_at' else 'asc')
                        ]
                        self.assertEqual(query, expected_query)

    def test_management_numbers_keep_numeric_order_with_faq_before_tips(self):
        for direction in ('asc', 'desc'):
            context, _ = self.context(self.params(sort_dir=direction))
            expected = []
            for kind in ('faq', 'tips'):
                group = [item for item_kind, _, item in self.items if item_kind == kind]
                group.sort(key=lambda item: int(item.management_code[2:]), reverse=direction == 'desc')
                expected.extend(item.management_code for item in group)
            self.assertEqual([item['management_code'] for item in context['review_items']], expected)

    def test_invalid_filters_keep_existing_fallback_and_empty_result_behavior(self):
        context, _ = self.context({'type': 'unknown', 'date_from': 'invalid', 'date_to': 'invalid',
                                   'sort': 'invalid', 'sort_dir': 'invalid'})
        self.assertEqual((context['review_type'], context['date_from'], context['date_to']), ('', '', ''))
        self.assertEqual((context['sort_by'], context['sort_dir']), ('management_code', 'desc'))
        self.assertEqual(context['review_result_count'], 14)
        context, _ = self.context(self.params(status='unknown'))
        self.assertEqual(context['review_result_count'], 0)
        self.assertEqual(self.counts(context)[''], 8)
        self.assertFalse(any(tab['active'] for tab in context['review_status_tabs']))
        context, _ = self.context(self.params(creator_id='missing'))
        self.assertEqual(self.counts(context), dict.fromkeys(('', *self.states), 0))

    def test_get_renders_existing_ids_data_and_post_form_contracts(self):
        self.client.force_login(self.reviewer)
        params = self.params(status='corrected', sort='title', sort_dir='asc')
        response = self.client.get(reverse('review_list'), params)
        parser = self.parse(response)
        self.assertTrue(response.context['can_approve_review'])
        for kind, status, item in self.items:
            if status != 'corrected':
                continue
            key = f'{kind}-{item.pk}'
            for element_id in (f'review-row-{key}', f'inline-review-{key}', f'review-content-{key}'):
                self.assertEqual(len(parser.find(id=element_id)), 1)
            row = parser.find(id=f'review-row-{key}')[0]
            self.assertIn('review-expandable-row', row['attrs']['class'].split())
            button = parser.find(**{'data-focus-key': key})[0]['attrs']
            self.assertEqual(button['data-review-id'], f'review-content-{key}')
            self.assertEqual(button['data-title'], item.title)
            self.assertEqual(button['data-ai-review'], item.ai_review)
            self.assertEqual(button['data-remand-reason'], item.remand_reason)
            self.assertEqual(button['data-bs-target'], '#reviewModal')
            self.assertEqual(button['data-standard-only'], '1' if item.standard_contract_only else '0')
            self.assertEqual(button['data-visible-to-customer'], '1' if item.visible_to_customer else '0')
            prefix = 'article' if kind == 'faq' else 'tip'
            for action in ('approve', 'remand'):
                self.assertEqual(button[f'data-{action}-url'], reverse(f'{prefix}_{action}', args=[item.pk]))
        for form_id, controls in (
            ('reviewApproveForm', {'standard_contract_only', 'visible_to_customer', 'ai_review', 'reviewer_comment', 'next'}),
            ('reviewRemandForm', {'ai_review', 'reviewer_comment', 'remand_reason', 'next'}),
        ):
            form = parser.find(tag='form', id=form_id)[0]
            self.assertEqual(form['attrs']['method'], 'post')
            inputs = [node for node in parser.find(tag='input') if form in node['parents']]
            self.assertEqual({node['attrs']['name'] for node in inputs}, controls | {'csrfmiddlewaretoken'})
            next_input = next(node for node in inputs if node['attrs']['name'] == 'next')
            self.assertEqual(next_input['attrs']['value'], response.wsgi_request.get_full_path())

    def test_new_template_tabs_workspace_and_corrected_visible_status(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse('review_list'), self.params(status='corrected'))
        parser = self.parse(response)
        tabs = parser.find(css_class='review-status-options')
        self.assertEqual(len(tabs), 1)
        self.assertEqual(len(parser.find(css_class='review-workspace')), 1)
        links = [node for node in parser.find(tag='label') if tabs[0] in node['parents']]
        for link, tab in zip(links, response.context['review_status_tabs']):
            self.assertIn(tab['label'], link['text'])
            self.assertIn(str(tab['count']), link['text'])
        for kind, status, item in self.items:
            if status == 'corrected':
                row = parser.find(id=f'review-row-{kind}-{item.pk}')[0]
                self.assertIn('修正済', row['text'])
        details = parser.find(tag='details', css_class='review-code-details')
        self.assertEqual(len(details), response.context['review_result_count'])
        for detail in details:
            self.assertNotIn('open', detail['attrs'])
            self.assertTrue(any(detail in node['parents'] for node in parser.find(tag='summary')))
            self.assertTrue(any(detail in node['parents']
                                for node in parser.find(css_class='review-html-code-block')))

    def test_new_modal_footer_contains_existing_approve_and_remand_buttons(self):
        self.client.force_login(self.reviewer)
        parser = self.parse(self.client.get(reverse('review_list'), self.params()))
        for button_id in ('reviewApproveBtn', 'reviewRemandBtn'):
            buttons = parser.find(tag='button', id=button_id)
            self.assertEqual(len(buttons), 1)
            parents = buttons[0]['parents']
            self.assertTrue(any(node['attrs'].get('id') == 'reviewModal' for node in parents))
            self.assertTrue(any('modal-footer' in node['attrs'].get('class', '').split() for node in parents),
                            'Separately edited template must place approval actions in the modal footer')

    def test_publication_switches_and_separate_reviewer_comment(self):
        self.client.force_login(self.reviewer)
        kind, _, item = self.items[0]
        item.reviewer_comment = '<script>コメント & "確認"</script>'
        item.save(update_fields=['reviewer_comment'])
        response = self.client.get(reverse('review_list'), self.params())
        parser = self.parse(response)
        for control_id in ('reviewStandardOnly', 'reviewVisibleToCustomer'):
            control = parser.find(id=control_id)[0]
            self.assertEqual(control['attrs']['role'], 'switch')
            self.assertEqual(control['attrs']['type'], 'checkbox')
            self.assertIn('form-switch', control['parents'][-1]['attrs']['class'].split())
        self.assertTrue(parser.find(tag='textarea', id='reviewReviewerComment'))
        button = parser.find(**{'data-focus-key': f'{kind}-{item.pk}'})[0]
        self.assertEqual(button['attrs']['data-reviewer-comment'], item.reviewer_comment)
        self.assertContains(response, '&lt;script&gt;コメント &amp; &quot;確認&quot;&lt;/script&gt;')

    def test_non_reviewer_editor_has_no_approval_controls(self):
        self.client.force_login(self.editor)
        response = self.client.get(reverse('review_list'), self.params())
        parser = self.parse(response)
        self.assertFalse(response.context['can_approve_review'])
        for element_id in (
            'reviewApproveBtn', 'reviewRemandBtn', 'reviewRemandSubmitBtn',
            'reviewApproveForm', 'reviewRemandForm', 'reviewStandardOnly',
            'reviewVisibleToCustomer', 'reviewAiReview', 'reviewRemandComment',
            'reviewReviewerComment',
        ):
            self.assertFalse(parser.find(id=element_id), element_id)
        self.assertTrue(parser.find(css_class='review-open-btn'))

    def test_review_action_emphasis_matches_approval_status(self):
        self.client.force_login(self.reviewer)
        parser = self.parse(self.client.get(reverse('review_list'), self.params()))
        for kind, status, item in self.items:
            with self.subTest(kind=kind, status=status):
                button = parser.find(tag='button', **{'data-focus-key': f'{kind}-{item.pk}'})[0]
                classes = button['attrs']['class'].split()
                self.assertIn('review-open-btn', classes)
                self.assertEqual(button['attrs']['data-bs-target'], '#reviewModal')
                if status == 'approved':
                    self.assertEqual(button['text'].strip(), '確認')
                    self.assertIn('btn-outline-secondary', classes)
                    self.assertIn('review-approved-action', classes)
                    self.assertNotIn('btn-primary', classes)
                else:
                    self.assertEqual(button['text'].strip(), 'レビュー')
                    self.assertIn('btn-primary', classes)
                    self.assertNotIn('review-approved-action', classes)

    def test_non_editor_cannot_access_review_list(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse('review_list'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)

    def test_empty_status_options_remain_selectable_and_selection_is_explicit(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse('review_list'), self.params(
            date_from='2026-10-10', date_to='2026-10-10', status='corrected',
        ))
        parser = self.parse(response)
        panel = parser.find(id='reviewFilterPanel')[0]
        radios = parser.find(tag='input', name='status', type='radio')
        self.assertEqual(len(radios), 5)
        for radio, tab in zip(radios, response.context['review_status_tabs']):
            self.assertIn(panel, radio['parents'])
            self.assertEqual(radio['attrs']['value'], tab['value'])
            self.assertEqual('checked' in radio['attrs'], tab['active'])
            self.assertNotIn('disabled', radio['attrs'])
            label = parser.find(tag='label', **{'for': radio['attrs']['id']})[0]
            self.assertEqual('is-empty' in label['attrs']['class'].split(), tab['count'] == 0 and not tab['active'])

    def test_filter_title_toggle_and_remanded_badge_contracts(self):
        self.client.force_login(self.reviewer)
        parser = self.parse(self.client.get(reverse('review_list'), self.params()))
        self.assertEqual(parser.find(tag='label', **{'for': 'reviewTypeAll'})[0]['text'], 'すべて')
        for toggle in parser.find(css_class='review-title-toggle'):
            self.assertTrue(any('review-row-actions' in node['attrs'].get('class', '').split() for node in toggle['parents']))
            self.assertIn('本文を展開・折りたたみ', toggle['attrs']['aria-label'])
            icons = [node for node in parser.find(css_class='review-toggle-icon') if toggle in node['parents']]
            self.assertEqual(len(icons), 1)
            self.assertEqual(toggle['attrs']['data-bs-toggle'], 'collapse')
        for badge in parser.find(css_class='review-status-remanded'):
            self.assertEqual(badge['text'].strip(), '差戻し')
            self.assertNotIn('text-bg-warning', badge['attrs']['class'].split())
        self.assertEqual(len(parser.find(css_class='review-status-remanded')), 2)

    def test_button_filters_and_list_only_scroll_structure(self):
        self.client.force_login(self.reviewer)
        for kind in ('', 'faq', 'tips'):
            with self.subTest(kind=kind):
                parser = self.parse(self.client.get(reverse('review_list'), self.params(type=kind)))
                panel = parser.find(id='reviewFilterPanel')[0]
                self.assertIn('modal', panel['attrs']['class'].split())
                self.assertNotIn('collapse', panel['attrs']['class'].split())
                toggle = parser.find(css_class='review-filter-toggle')[0]
                self.assertEqual(toggle['attrs']['data-bs-toggle'], 'modal')
                self.assertEqual(toggle['attrs']['data-bs-target'], '#reviewFilterPanel')
                radios = parser.find(tag='input', name='type', type='radio')
                self.assertEqual([node['attrs']['value'] for node in radios], ['', 'faq', 'tips'])
                self.assertEqual([node['attrs']['value'] for node in radios if 'checked' in node['attrs']], [kind])
                self.assertTrue(all(panel in node['parents'] for node in radios))
                region = parser.find(css_class='review-results')[0]
                self.assertEqual(region['attrs']['tabindex'], '0')
                self.assertEqual(region['attrs']['role'], 'region')
                self.assertFalse(region in panel['parents'] or panel in region['parents'])
                self.assertTrue(any(region in node['parents'] for node in parser.find(css_class='review-list-table')))
                self.assertIn('review-list-page', parser.find(tag='body')[0]['attrs']['class'].split())

    def test_filter_modal_keeps_all_conditions_and_submit_actions_in_one_get_form(self):
        self.client.force_login(self.reviewer)
        parser = self.parse(self.client.get(reverse('review_list'), self.params(
            type='tips', created_month='2026-10', status='approved', sort='title', sort_dir='asc',
        )))
        panel = parser.find(id='reviewFilterPanel')[0]
        self.assertEqual(panel['attrs']['aria-labelledby'], 'reviewFilterLabel')
        form = parser.find(tag='form', id='reviewFilterForm')[0]
        self.assertIn(panel, form['parents'])
        self.assertEqual(form['attrs']['method'], 'get')
        headings = [node['text'].strip() for node in parser.nodes
                    if form in node['parents'] and (
                        node['tag'] == 'legend' or node['attrs'].get('for') in
                        ('creatorFilter', 'reviewCreatedMonth'))]
        self.assertEqual(headings, ['状態', '種別', '作成者', '作成日'])
        for control_id in ('creatorFilter', 'reviewCreatedMonth'):
            control = parser.find(id=control_id)[0]
            self.assertIn('col-md-6', control['parents'][-1]['attrs']['class'].split())
        for name in ('type', 'creator_id', 'created_month', 'status', 'sort', 'sort_dir'):
            controls = parser.find(name=name)
            self.assertTrue(controls, name)
            self.assertTrue(all(form in node['parents'] for node in controls), name)
        footer = next(node for node in parser.find(css_class='modal-footer') if form in node['parents'])
        submit = next(node for node in parser.find(tag='button', type='submit') if form in node['parents'])
        self.assertIn(footer, submit['parents'])
        self.assertEqual(submit['text'].strip(), '絞り込み')
        reset = next(node for node in parser.find(tag='a') if footer in node['parents'])
        self.assertEqual(reset['attrs']['href'], reverse('review_list'))
        self.assertTrue(any(footer in node['parents'] for node in parser.find(
            tag='button', **{'data-bs-dismiss': 'modal'},
        )))

    def test_expand_button_has_its_own_rightmost_slot_for_each_user_role(self):
        for user in (self.reviewer, self.editor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                parser = self.parse(self.client.get(reverse('review_list'), self.params()))
                for actions in parser.find(css_class='review-row-actions'):
                    children = [node for node in parser.nodes
                                if node['parents'] and node['parents'][-1] is actions]
                    self.assertEqual(len(children), 2)
                    self.assertIn('review-main-actions', children[0]['attrs']['class'].split())
                    self.assertIn('review-title-toggle', children[-1]['attrs']['class'].split())
                    self.assertEqual(children[-1]['attrs']['data-bs-toggle'], 'collapse')
                    self.assertTrue(any(children[0] in node['parents']
                                        for node in parser.find(css_class='review-open-btn')))

    def test_sort_controls_indicate_active_key_and_direction(self):
        self.client.force_login(self.reviewer)
        for key in ('management_code', 'title', 'created_at'):
            for direction in ('asc', 'desc'):
                with self.subTest(key=key, direction=direction):
                    response = self.client.get(reverse('review_list'), self.params(sort=key, sort_dir=direction))
                    parser = self.parse(response)
                    group = parser.find(css_class='review-sort-links')[0]
                    links = [node for node in parser.find(tag='a') if group in node['parents']]
                    self.assertEqual(len(links), 3)
                    current = [node for node in links if node['attrs'].get('aria-current') == 'true']
                    self.assertEqual(len(current), 1)
                    self.assertEqual(parse_qs(urlsplit(current[0]['attrs']['href']).query)['sort'], [key])
                    icon_class = 'bi-arrow-up' if direction == 'asc' else 'bi-arrow-down'
                    self.assertTrue(any(current[0] in node['parents'] for node in parser.find(css_class=icon_class)))
                    self.assertEqual(len(parser.find(css_class='review-sort-idle-icon')), 2)

    def test_created_month_filters_both_types_and_status_counts(self):
        for kind in ('', 'faq', 'tips'):
            with self.subTest(kind=kind):
                context, _ = self.context({'type': kind, 'created_month': '2026-10', 'status': 'approved'})
                self.assertEqual(context['created_month'], '2026-10')
                self.assertEqual(context['review_result_count'], 2 if not kind else 1)
                self.assertEqual(self.counts(context)[''], 14 if not kind else 7)
        for month in ('2026-09', '2026-11', '2025-10'):
            context, _ = self.context({'created_month': month})
            self.assertEqual(context['review_result_count'], 0)
        for month in ('invalid', '2026-13', '2026-00'):
            context, _ = self.context({'created_month': month})
            self.assertEqual(context['created_month'], '')
            self.assertEqual(context['review_result_count'], 14)

    def test_month_filter_rendering_and_sort_preservation(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse('review_list'), {
            'created_month': '2026-10', 'status': 'approved', 'type': 'tips',
        })
        parser = self.parse(response)
        control = parser.find(tag='input', name='created_month', type='month')[0]
        self.assertEqual(control['attrs']['value'], '2026-10')
        self.assertFalse(parser.find(tag='input', name='date_from'))
        self.assertFalse(parser.find(tag='input', name='date_to'))
        self.assertFalse(parser.find(css_class='review-status-tabs'))
        for key in ('sort_management_code_url', 'sort_title_url', 'sort_created_at_url'):
            query = parse_qs(urlsplit(response.context[key]).query)
            self.assertEqual(query['created_month'], ['2026-10'])
            self.assertEqual(query['status'], ['approved'])
            self.assertEqual(query['type'], ['tips'])
        self.assertIn('btn-outline-danger', parser.find(tag='label', **{'for': 'reviewTypeFaq'})[0]['attrs']['class'])
        self.assertIn('review-type-tips', parser.find(tag='label', **{'for': 'reviewTypeTips'})[0]['attrs']['class'])