import tempfile
from datetime import timedelta
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    ArticleAttachment,
    Customer,
    FAQCategory,
    KnowledgeArticle,
    LoginHistory,
    TipsArticle,
    UserProfile,
    default_expires_on,
)


class KnowledgeArticleListTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='member', password='password')
        self.other_user = User.objects.create_user(username='other', password='password')

        self.customer = Customer.objects.create(name='顧客A')
        self.customer.users.add(self.user)
        other_customer = Customer.objects.create(name='顧客B')
        other_customer.users.add(self.other_user)

        self.visible_article = KnowledgeArticle.objects.create(
            title='閲覧できる記事',
            customer=self.customer,
            category='ネットワーク,AWS',
            body='本文',
        )
        self.hidden_article = KnowledgeArticle.objects.create(
            title='閲覧できない記事',
            customer=other_customer,
            category='サーバ',
            body='本文',
        )

    def test_login_is_required(self):
        response = self.client.get(reverse('article_list'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_user_can_see_faq_without_customer_filtering(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_list'))

        self.assertContains(response, self.visible_article.title)
        self.assertContains(response, self.hidden_article.title)

    def test_user_can_filter_faq_by_parent_category(self):
        self.client.force_login(self.user)
        parent_category = self.visible_article.category.split(',', 1)[0].split('/', 1)[0]

        response = self.client.get(reverse('article_list'), {'parent_category': parent_category})

        self.assertContains(response, self.visible_article.title)
        self.assertNotContains(response, self.hidden_article.title)

    def test_article_list_shows_parent_category_sidebar_and_group_titles(self):
        self.client.force_login(self.user)
        parent_category = self.visible_article.category.split(',', 1)[0].split('/', 1)[0]

        response = self.client.get(reverse('article_list'))

        self.assertContains(response, 'category-sidebar')
        self.assertContains(response, 'すべて')
        self.assertContains(response, parent_category)
        self.assertContains(response, 'faq-group-title')

    def test_staff_can_see_all_articles(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_list'))

        self.assertContains(response, self.visible_article.title)
        self.assertContains(response, self.hidden_article.title)
        self.assertContains(response, 'FAQ登録')

    def test_hidden_for_all_article_is_not_shown_even_to_staff(self):
        self.user.is_staff = True
        self.user.save()
        hidden_for_all_article = KnowledgeArticle.objects.create(
            title='全員非表示FAQ',
            category='PC/設定',
            body='本文',
            visible_to_customer=False,
            visible_to_systena=False,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_list'))

        self.assertNotContains(response, hidden_for_all_article.title)

    def test_non_staff_cannot_view_article_create_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_create'))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_create_article(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('article_create'),
            {
                'category': 'ネットワーク/VPN',
                'question': 'VPN に接続できない場合は？',
                'answer': '認証情報と接続先を確認します。',
                'source_published_at': '2026-05-01',
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        article = KnowledgeArticle.objects.get(title='VPN に接続できない場合は？')
        self.assertEqual(article.category, 'ネットワーク/VPN')
        self.assertEqual(article.body, '認証情報と接続先を確認します。')
        self.assertEqual(str(article.source_published_at), '2026-05-01')
        self.assertIsNone(article.customer)
        self.assertEqual(article.created_by, self.user)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_staff_can_create_article_with_question_and_answer_images(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        question_image = SimpleUploadedFile(
            'question.png',
            b'question-image',
            content_type='image/png',
        )
        answer_image = SimpleUploadedFile(
            'answer.png',
            b'answer-image',
            content_type='image/png',
        )

        response = self.client.post(
            reverse('article_create'),
            {
                'category': 'PC/画面',
                'question': '画面が表示されない場合は？',
                'answer': 'ケーブルを確認します。',
                'question_images': question_image,
                'answer_images': answer_image,
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        article = KnowledgeArticle.objects.get(title='画面が表示されない場合は？')
        self.assertTrue(
            ArticleAttachment.objects.filter(
                article=article,
                placement=ArticleAttachment.PLACEMENT_QUESTION,
                display_name='question.png',
            ).exists()
        )
        self.assertTrue(
            ArticleAttachment.objects.filter(
                article=article,
                placement=ArticleAttachment.PLACEMENT_ANSWER,
                display_name='answer.png',
            ).exists()
        )

    def test_staff_can_view_article_edit_page(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_edit', args=[self.visible_article.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FAQ編集')
        self.assertContains(response, self.visible_article.title)
        self.assertContains(response, self.visible_article.body)

    def test_non_staff_cannot_view_article_edit_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_edit', args=[self.visible_article.id]))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_update_article(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('article_edit', args=[self.visible_article.id]),
            {
                'category': 'PC/電源',
                'question': '電源が入らない場合は？',
                'answer': 'ケーブルとランプを確認します。',
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        self.visible_article.refresh_from_db()
        self.assertEqual(self.visible_article.category, 'PC/電源')
        self.assertEqual(self.visible_article.title, '電源が入らない場合は？')
        self.assertEqual(self.visible_article.body, 'ケーブルとランプを確認します。')

    def test_staff_can_delete_article_from_edit_page_with_confirmation_button(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

        edit_response = self.client.get(reverse('article_edit', args=[self.visible_article.id]))
        self.assertContains(edit_response, 'FAQ削除')
        self.assertContains(edit_response, "confirm('このFAQを削除しますか？')")

        response = self.client.post(reverse('article_delete', args=[self.visible_article.id]))

        self.assertRedirects(response, reverse('article_list'))
        self.assertFalse(KnowledgeArticle.objects.filter(id=self.visible_article.id).exists())

    def test_non_staff_cannot_delete_article(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('article_delete', args=[self.visible_article.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(KnowledgeArticle.objects.filter(id=self.visible_article.id).exists())

    def test_article_list_hides_expired_article(self):
        self.client.force_login(self.user)
        expired_article = KnowledgeArticle.objects.create(
            title='期限切れFAQ',
            category='PC/電源',
            body='本文',
            expires_on=timezone.localdate() - timedelta(days=1),
        )

        response = self.client.get(reverse('article_list'))

        self.assertNotContains(response, expired_article.title)

    def test_article_expires_on_is_saved_and_shown_in_edit(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('article_create'),
            {
                'category': 'ネットワーク/VPN',
                'question': '掲載期限保持FAQ',
                'answer': '本文',
                'expires_on': '2026-12-01',
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        article = KnowledgeArticle.objects.get(title='掲載期限保持FAQ')
        self.assertEqual(str(article.expires_on), '2026-12-01')

        edit_response = self.client.get(reverse('article_edit', args=[article.id]))
        self.assertContains(edit_response, 'value="2026-12-01"', html=False)

    def test_article_list_shows_expires_on(self):
        self.client.force_login(self.user)
        article = KnowledgeArticle.objects.create(
            title='期限表示FAQ',
            category='PC/電源',
            body='本文',
            expires_on=timezone.localdate() + timedelta(days=10),
        )

        response = self.client.get(reverse('article_list'))

        self.assertContains(response, article.title)
        self.assertContains(response, '掲載期限')

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_staff_can_add_answer_image_when_updating_article(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        answer_image = SimpleUploadedFile(
            'updated-answer.png',
            b'answer-image',
            content_type='image/png',
        )

        response = self.client.post(
            reverse('article_edit', args=[self.visible_article.id]),
            {
                'category': self.visible_article.category,
                'question': self.visible_article.title,
                'answer': self.visible_article.body,
                'answer_images': answer_image,
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        self.assertTrue(
            ArticleAttachment.objects.filter(
                article=self.visible_article,
                placement=ArticleAttachment.PLACEMENT_ANSWER,
                display_name='updated-answer.png',
            ).exists()
        )

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_staff_can_delete_article_image_with_confirmation_button(self):
        self.user.is_staff = True
        self.user.save()
        attachment = ArticleAttachment.objects.create(
            article=self.visible_article,
            file=SimpleUploadedFile(
                'delete-me.png',
                b'answer-image',
                content_type='image/png',
            ),
            placement=ArticleAttachment.PLACEMENT_ANSWER,
            display_name='delete-me.png',
        )
        self.client.force_login(self.user)

        edit_response = self.client.get(reverse('article_edit', args=[self.visible_article.id]))
        self.assertContains(edit_response, '削除')
        self.assertContains(edit_response, "confirm('この画像を削除しますか？')")

        response = self.client.post(reverse('attachment_delete', args=[attachment.id]))

        self.assertRedirects(response, reverse('article_edit', args=[self.visible_article.id]))
        self.assertFalse(ArticleAttachment.objects.filter(id=attachment.id).exists())

    def test_non_staff_cannot_delete_article_image(self):
        attachment = ArticleAttachment.objects.create(
            article=self.visible_article,
            file=SimpleUploadedFile(
                'delete-me.png',
                b'answer-image',
                content_type='image/png',
            ),
            placement=ArticleAttachment.PLACEMENT_ANSWER,
            display_name='delete-me.png',
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse('attachment_delete', args=[attachment.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ArticleAttachment.objects.filter(id=attachment.id).exists())

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_article_list_renders_image_marker_inside_answer_text(self):
        ArticleAttachment.objects.create(
            article=self.visible_article,
            file=SimpleUploadedFile(
                'inline-answer.png',
                b'answer-image',
                content_type='image/png',
            ),
            placement=ArticleAttachment.PLACEMENT_ANSWER,
            display_name='inline-answer.png',
        )
        self.visible_article.body = 'ボタンをクリックします。\n<image>\n確認します。'
        self.visible_article.save(update_fields=['body'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_list'))

        self.assertContains(response, 'ボタンをクリックします。')
        self.assertContains(response, 'inline-faq-image')
        self.assertContains(response, '確認します。')
        self.assertNotContains(response, '<p>&lt;image&gt;</p>', html=True)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_article_list_renders_numbered_image_markers(self):
        ArticleAttachment.objects.create(
            article=self.visible_article,
            file=SimpleUploadedFile(
                'first.png',
                b'first-image',
                content_type='image/png',
            ),
            placement=ArticleAttachment.PLACEMENT_ANSWER,
            display_name='first.png',
        )
        ArticleAttachment.objects.create(
            article=self.visible_article,
            file=SimpleUploadedFile(
                'second.png',
                b'second-image',
                content_type='image/png',
            ),
            placement=ArticleAttachment.PLACEMENT_ANSWER,
            display_name='second.png',
        )
        self.visible_article.body = '最初の画像 <image1> 次の画像 <image2>'
        self.visible_article.save(update_fields=['body'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_list'))

        self.assertContains(response, 'first.png')
        self.assertContains(response, 'second.png')
        self.assertContains(response, '最初の画像')
        self.assertContains(response, '次の画像')

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_article_list_renders_same_number_marker_multiple_times(self):
        ArticleAttachment.objects.create(
            article=self.visible_article,
            file=SimpleUploadedFile(
                'repeat.png',
                b'repeat-image',
                content_type='image/png',
            ),
            placement=ArticleAttachment.PLACEMENT_ANSWER,
            display_name='repeat.png',
        )
        self.visible_article.body = '<image1>\n手順説明\n<image1>'
        self.visible_article.category = 'ネットワーク'
        self.visible_article.save(update_fields=['body', 'category'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_list'))
        html = response.content.decode('utf-8')

        self.assertEqual(html.count('<img class="inline-faq-image"'), 2)

    def test_staff_can_view_category_create_page(self):
        self.user.is_staff = True
        self.user.save()
        FAQCategory.objects.create(parent_name='PC', child_name='電源')
        self.client.force_login(self.user)

        response = self.client.get(reverse('category_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'カテゴリ登録')
        self.assertContains(response, '大カテゴリ')
        self.assertContains(response, '小カテゴリ')
        self.assertContains(response, 'PC')
        self.assertContains(response, 'サーバー')
        self.assertContains(response, 'ネットワーク')
        self.assertContains(response, 'アプリ')
        self.assertContains(response, 'その他')
        self.assertContains(response, '登録済みカテゴリ')
        self.assertContains(response, '修正')
        self.assertContains(response, '電源')

    def test_non_staff_cannot_view_category_create_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('category_create'))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_create_two_level_category(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('category_create'),
            {
                'parent_name': 'PC',
                'child_name': '電源',
            },
        )

        self.assertRedirects(response, reverse('category_create'))
        self.assertTrue(
            FAQCategory.objects.filter(parent_name='PC', child_name='電源').exists()
        )

    def test_staff_can_view_category_edit_page(self):
        self.user.is_staff = True
        self.user.save()
        category = FAQCategory.objects.create(parent_name='PC', child_name='電源')
        self.client.force_login(self.user)

        response = self.client.get(reverse('category_edit', args=[category.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'カテゴリ修正')
        self.assertContains(response, '電源')
        self.assertContains(response, 'キャンセル')

    def test_staff_can_update_category_and_article_category_names(self):
        self.user.is_staff = True
        self.user.save()
        category = FAQCategory.objects.create(parent_name='PC', child_name='電源')
        article = KnowledgeArticle.objects.create(
            title='電源FAQ',
            category='PC/電源,ネットワーク/AWS',
            body='本文',
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('category_edit', args=[category.id]),
            {
                'parent_name': 'PC',
                'child_name': '電源トラブル',
            },
        )

        self.assertRedirects(response, reverse('category_create'))
        category.refresh_from_db()
        article.refresh_from_db()
        self.assertEqual(category.child_name, '電源トラブル')
        self.assertEqual(article.category, 'PC/電源トラブル,ネットワーク/AWS')

    def test_non_staff_cannot_view_category_edit_page(self):
        category = FAQCategory.objects.create(parent_name='PC', child_name='電源')
        self.client.force_login(self.user)

        response = self.client.get(reverse('category_edit', args=[category.id]))

        self.assertEqual(response.status_code, 403)

    def test_article_create_can_use_registered_category(self):
        self.user.is_staff = True
        self.user.save()
        category = FAQCategory.objects.create(parent_name='SV', child_name='ActiveDirectory')
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('article_create'),
            {
                'registered_category': category.id,
                'category': '',
                'question': 'ADにログインできない場合は？',
                'answer': 'アカウント状態を確認します。',
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        article = KnowledgeArticle.objects.get(title='ADにログインできない場合は？')
        self.assertEqual(article.category, 'SV/ActiveDirectory')

    def test_article_create_groups_registered_categories_by_parent(self):
        self.user.is_staff = True
        self.user.save()
        FAQCategory.objects.create(parent_name='PC', child_name='電源')
        FAQCategory.objects.create(parent_name='サーバー', child_name='ActiveDirectory')
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'category-group-toggle')
        self.assertContains(response, 'PC')
        self.assertContains(response, '電源')
        self.assertContains(response, 'サーバー')
        self.assertContains(response, 'ActiveDirectory')
        self.assertContains(response, 'type="checkbox"')

    def test_article_create_can_use_multiple_registered_categories(self):
        self.user.is_staff = True
        self.user.save()
        category1 = FAQCategory.objects.create(parent_name='PC', child_name='電源')
        category2 = FAQCategory.objects.create(parent_name='サーバー', child_name='ActiveDirectory')
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('article_create'),
            {
                'registered_category': [category1.id, category2.id],
                'category': '',
                'question': '複数カテゴリのFAQ',
                'answer': '回答です。',
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        article = KnowledgeArticle.objects.get(title='複数カテゴリのFAQ')
        self.assertEqual(article.category, 'PC/電源,サーバー/ActiveDirectory')

    @override_settings(FAQ_APPROVAL_ENABLED=True)
    def test_customer_cannot_see_unapproved_article_even_if_visible_to_customer(self):
        customer_group, _ = Group.objects.get_or_create(name='カスタマー')
        self.user.groups.add(customer_group)
        unapproved_article = KnowledgeArticle.objects.create(
            title='未承認FAQ',
            category='PC/設定',
            body='本文',
            visible_to_customer=True,
            is_approved=False,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('article_list'))

        self.assertNotContains(response, unapproved_article.title)

    @override_settings(FAQ_APPROVAL_ENABLED=True)
    def test_reviewer_can_approve_article_from_edit_page(self):
        reviewer_group, _ = Group.objects.get_or_create(name='レビュアー')
        reviewer = get_user_model().objects.create_user(username='reviewer', password='password')
        reviewer.groups.add(reviewer_group)
        article = KnowledgeArticle.objects.create(
            title='承認待ちFAQ',
            category='PC/設定',
            body='本文',
            visible_to_customer=True,
            is_approved=False,
        )
        self.client.force_login(reviewer)

        edit_response = self.client.get(reverse('article_edit', args=[article.id]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, '承認')

        approve_response = self.client.post(reverse('article_approve', args=[article.id]))

        self.assertRedirects(approve_response, reverse('article_list'))
        article.refresh_from_db()
        self.assertTrue(article.is_approved)

    def test_reviewer_cannot_republish_hidden_for_all_article(self):
        reviewer_group, _ = Group.objects.get_or_create(name='レビュアー')
        reviewer = get_user_model().objects.create_user(username='reviewer_republish', password='password')
        reviewer.groups.add(reviewer_group)
        article = KnowledgeArticle.objects.create(
            title='全員非表示FAQ',
            category='PC/設定',
            body='本文',
            visible_to_customer=False,
            visible_to_systena=False,
        )
        self.client.force_login(reviewer)

        response = self.client.post(
            reverse('article_edit', args=[article.id]),
            {
                'category': article.category,
                'question': article.title,
                'answer': article.body,
                'visible_to_customer': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '全ユーザー非表示のFAQを再公開できるのはSystenaAdminのみです。')
        article.refresh_from_db()
        self.assertFalse(article.visible_to_customer)
        self.assertFalse(article.visible_to_systena)

    def test_admin_can_republish_hidden_for_all_article(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        article = KnowledgeArticle.objects.create(
            title='再公開対象FAQ',
            category='PC/設定',
            body='本文',
            visible_to_customer=False,
            visible_to_systena=False,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('article_edit', args=[article.id]),
            {
                'category': article.category,
                'question': article.title,
                'answer': article.body,
                'visible_to_customer': 'on',
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        article.refresh_from_db()
        self.assertTrue(article.visible_to_customer)
        self.assertFalse(article.visible_to_systena)


class UserCreateViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(
            username='staff',
            password='password',
            is_staff=True,
        )
        self.member = User.objects.create_user(username='member', password='password')

    def test_staff_can_view_user_create_page(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('user_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ユーザー作成')

    def test_non_staff_cannot_view_user_create_page(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse('user_create'))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_user_list_page(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('user_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ユーザー一覧')
        self.assertContains(response, 'ユーザー作成')
        self.assertContains(response, self.staff.username)

    def test_non_staff_cannot_view_user_list_page(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse('user_list'))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_create_user_with_profile_and_customer_access(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('user_create'),
            {
                'username': 'newuser',
                'password': 'secure-password',
                'company_name': '株式会社サンプル',
                'role': 'user',
                'email_addresses': 'one@example.com\ntwo@example.com',
                'note': '初回作成',
            },
        )

        self.assertRedirects(response, reverse('user_list'))
        created_user = get_user_model().objects.get(username='newuser')
        self.assertTrue(created_user.check_password('secure-password'))
        self.assertFalse(created_user.is_staff)
        self.assertEqual(created_user.email, 'one@example.com')
        self.assertEqual(
            created_user.knowledge_profile.email_addresses,
            'one@example.com\ntwo@example.com',
        )
        self.assertEqual(created_user.knowledge_profile.note, '初回作成')
        self.assertTrue(
            Customer.objects.get(name='株式会社サンプル').users.filter(id=created_user.id).exists()
        )

    def test_admin_role_creates_staff_superuser(self):
        self.client.force_login(self.staff)

        self.client.post(
            reverse('user_create'),
            {
                'username': 'newadmin',
                'password': 'secure-password',
                'company_name': '株式会社サンプル',
                'role': 'admin',
                'email_addresses': '',
                'note': '',
            },
        )

        created_user = get_user_model().objects.get(username='newadmin')
        self.assertTrue(created_user.is_staff)
        self.assertTrue(created_user.is_superuser)
        self.assertTrue(UserProfile.objects.filter(user=created_user).exists())

    def test_staff_can_reset_user_password(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('user_password_reset', args=[self.member.id]),
            {'reset_mode': 'random'},
        )

        self.assertRedirects(response, reverse('user_list'))
        self.member.refresh_from_db()
        self.assertFalse(self.member.check_password('password'))

    def test_staff_can_manually_set_user_password(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('user_password_reset', args=[self.member.id]),
            {'reset_mode': 'manual', 'new_password': 'manual-password-123'},
        )

        self.assertRedirects(response, reverse('user_list'))
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password('manual-password-123'))

    def test_manual_password_is_required_when_manual_reset_is_selected(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('user_password_reset', args=[self.member.id]),
            {'reset_mode': 'manual', 'new_password': ''},
        )

        self.assertRedirects(response, reverse('user_list'))
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password('password'))

    def test_staff_stays_logged_in_after_resetting_own_password(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse('user_password_reset', args=[self.staff.id]))
        user_list_response = self.client.get(reverse('user_list'))

        self.assertRedirects(response, reverse('user_list'))
        self.assertEqual(user_list_response.status_code, 200)
        self.assertContains(user_list_response, 'ユーザー一覧')

    def test_non_staff_cannot_reset_user_password(self):
        self.client.force_login(self.member)

        response = self.client.post(reverse('user_password_reset', args=[self.staff.id]))

        self.assertEqual(response.status_code, 403)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password('password'))


class LoginHistorySignalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='login_user', password='password')

    def test_relogin_closes_previous_open_login_history(self):
        self.client.force_login(self.user)
        first_history = LoginHistory.objects.filter(user=self.user).latest('id')

        self.client.force_login(self.user)
        first_history.refresh_from_db()
        second_history = LoginHistory.objects.filter(user=self.user).latest('id')

        self.assertIsNotNone(first_history.logged_out_at)
        self.assertIsNone(second_history.logged_out_at)
        self.assertNotEqual(first_history.id, second_history.id)


class TipsListTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='tips_member', password='password')
        self.client.force_login(self.user)

    def test_tip_list_hides_expired_tip(self):
        TipsArticle.objects.create(
            title='期限切れTips',
            category='PC/設定',
            body='本文',
            expires_on=timezone.localdate() - timedelta(days=1),
        )
        TipsArticle.objects.create(
            title='有効なTips',
            category='PC/設定',
            body='本文',
            expires_on=timezone.localdate() + timedelta(days=1),
        )

        response = self.client.get(reverse('tip_list'))

        self.assertNotContains(response, '期限切れTips')
        self.assertContains(response, '有効なTips')

    def test_tip_list_shows_expires_on(self):
        TipsArticle.objects.create(
            title='期限表示Tips',
            category='PC/設定',
            body='本文',
            expires_on=timezone.localdate() + timedelta(days=5),
        )

        response = self.client.get(reverse('tip_list'))

        self.assertContains(response, '期限表示Tips')
        self.assertContains(response, '掲載期限')

    def test_hidden_for_all_tip_is_not_shown_even_to_staff(self):
        self.user.is_staff = True
        self.user.save()
        TipsArticle.objects.create(
            title='全員非表示Tips',
            category='PC/設定',
            body='本文',
            visible_to_customer=False,
            visible_to_systena=False,
        )

        response = self.client.get(reverse('tip_list'))

        self.assertNotContains(response, '全員非表示Tips')


class TipsFormTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(username='tips_staff', password='password', is_staff=True)
        self.client.force_login(self.staff)

    def test_staff_can_create_tip_with_source_published_at(self):
        response = self.client.post(
            reverse('tip_create'),
            {
                'category': 'PC/設定',
                'title': '公開日付きTips',
                'target_os': 'Windows 11',
                'body': '本文',
                'source_published_at': '2026-05-10',
                'expires_on': '2026-12-10',
            },
        )

        self.assertRedirects(response, reverse('tip_list'))
        tip = TipsArticle.objects.get(title='公開日付きTips')
        self.assertEqual(str(tip.source_published_at), '2026-05-10')
        self.assertEqual(str(tip.expires_on), '2026-12-10')

    def test_staff_can_update_tip_source_published_at(self):
        tip = TipsArticle.objects.create(
            title='更新対象Tips',
            category='PC/設定',
            body='本文',
            created_by=self.staff,
            created_by_name=self.staff.get_username(),
        )

        response = self.client.post(
            reverse('tip_edit', args=[tip.id]),
            {
                'category': 'PC/設定',
                'title': '更新対象Tips',
                'target_os': 'Windows 11',
                'body': '更新本文',
                'source_published_at': '2026-05-12',
                'expires_on': '2026-12-20',
            },
        )

        self.assertRedirects(response, reverse('tip_list'))
        tip.refresh_from_db()
        self.assertEqual(str(tip.source_published_at), '2026-05-12')
        self.assertEqual(str(tip.expires_on), '2026-12-20')

    def test_reviewer_cannot_republish_hidden_for_all_tip(self):
        reviewer_group, _ = Group.objects.get_or_create(name='レビュアー')
        reviewer = get_user_model().objects.create_user(username='tips_reviewer', password='password')
        reviewer.groups.add(reviewer_group)
        tip = TipsArticle.objects.create(
            title='全員非表示Tips',
            category='PC/設定',
            body='本文',
            visible_to_customer=False,
            visible_to_systena=False,
            created_by=self.staff,
            created_by_name=self.staff.get_username(),
        )
        self.client.force_login(reviewer)

        response = self.client.post(
            reverse('tip_edit', args=[tip.id]),
            {
                'category': tip.category,
                'title': tip.title,
                'target_os': 'Windows 11',
                'body': tip.body,
                'visible_to_customer': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '全ユーザー非表示のTipsを再公開できるのはSystenaAdminのみです。')
        tip.refresh_from_db()
        self.assertFalse(tip.visible_to_customer)
        self.assertFalse(tip.visible_to_systena)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@example.com',
)
class ExpiringArticleNotificationCommandTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.creator = User.objects.create_user(
            username='creator',
            password='password',
            email='creator@example.com',
        )
        self.reviewer = User.objects.create_user(
            username='reviewer',
            password='password',
            email='reviewer@example.com',
        )
        UserProfile.objects.create(
            user=self.reviewer,
            company_name='レビュアー社',
            email_addresses='reviewer-sub@example.com',
        )

    def test_notify_expiring_articles_sends_to_creator_and_approver(self):
        target_date = timezone.localdate() + timedelta(days=7)
        KnowledgeArticle.objects.create(
            title='期限通知FAQ',
            category='PC/設定',
            body='本文',
            expires_on=target_date,
            created_by=self.creator,
            created_by_name='投稿者A',
            approved_by=self.reviewer,
            approved_by_name='承認者A',
        )
        TipsArticle.objects.create(
            title='期限通知Tips',
            category='PC/設定',
            body='本文',
            expires_on=target_date,
            created_by=self.creator,
            created_by_name='投稿者A',
            approved_by=self.reviewer,
            approved_by_name='承認者A',
        )

        call_command('notify_expiring_articles')

        self.assertEqual(len(mail.outbox), 2)
        recipients = set(mail.outbox[0].to) | set(mail.outbox[1].to)
        self.assertIn('creator@example.com', recipients)
        self.assertIn('reviewer@example.com', recipients)
        self.assertIn('reviewer-sub@example.com', recipients)

    def test_notify_expiring_articles_skips_non_target_date(self):
        KnowledgeArticle.objects.create(
            title='通知対象外FAQ',
            category='PC/設定',
            body='本文',
            expires_on=timezone.localdate() + timedelta(days=6),
            created_by=self.creator,
            created_by_name='投稿者A',
        )

        call_command('notify_expiring_articles')

        self.assertEqual(len(mail.outbox), 0)


class ExpirationDefaultTests(TestCase):
    def test_default_expires_on_is_six_months_later_when_weekday(self):
        with patch('tenasapo_knowledge.models.timezone.localdate', return_value=date(2026, 5, 14)):
            result = default_expires_on()

        self.assertEqual(result, date(2026, 11, 13))

    def test_default_expires_on_moves_to_friday_when_saturday(self):
        with patch('tenasapo_knowledge.models.timezone.localdate', return_value=date(2026, 1, 4)):
            result = default_expires_on()

        self.assertEqual(result, date(2026, 7, 3))

    def test_article_create_uses_default_expires_on_when_blank(self):
        User = get_user_model()
        staff = User.objects.create_user(username='staff_default_exp', password='password', is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(
            reverse('article_create'),
            {
                'category': 'ネットワーク/VPN',
                'question': 'デフォルト期限FAQ',
                'answer': '本文',
                'expires_on': '',
            },
        )

        self.assertRedirects(response, reverse('article_list'))
        article = KnowledgeArticle.objects.get(title='デフォルト期限FAQ')
        self.assertIsNotNone(article.expires_on)


class UnpublishExpiredArticlesCommandTests(TestCase):
    def test_unpublish_expired_articles_command_unpublishes_faq(self):
        article = KnowledgeArticle.objects.create(
            title='期限切れFAQ',
            category='PC/設定',
            body='本文',
            is_published=True,
            expires_on=timezone.localdate() - timedelta(days=1),
        )

        call_command('unpublish_expired_articles')

        article.refresh_from_db()
        self.assertFalse(article.is_published)

    def test_unpublish_expired_articles_command_unpublishes_tip(self):
        tip = TipsArticle.objects.create(
            title='期限切れTips',
            category='PC/設定',
            body='本文',
            is_published=True,
            expires_on=timezone.localdate() - timedelta(days=1),
        )

        call_command('unpublish_expired_articles')

        tip.refresh_from_db()
        self.assertFalse(tip.is_published)

    def test_unpublish_expired_articles_command_skips_future_expiry(self):
        article = KnowledgeArticle.objects.create(
            title='有効なFAQ',
            category='PC/設定',
            body='本文',
            is_published=True,
            expires_on=timezone.localdate() + timedelta(days=10),
        )

        call_command('unpublish_expired_articles')

        article.refresh_from_db()
        self.assertTrue(article.is_published)

    def test_unpublish_expired_articles_command_dry_run(self):
        article = KnowledgeArticle.objects.create(
            title='期限切れFAQ',
            category='PC/設定',
            body='本文',
            is_published=True,
            expires_on=timezone.localdate() - timedelta(days=1),
        )

        call_command('unpublish_expired_articles', '--dry-run')

        article.refresh_from_db()
        self.assertTrue(article.is_published)
