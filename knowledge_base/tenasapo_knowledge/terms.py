"""Public plaintext terms with the existing effective upper-admin access gate."""

import hashlib
import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.db import connection, transaction
from django.db.models import F
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.cache import never_cache
from django.views.generic import FormView, TemplateView

from .forms import ManualForm
from .models import TermsAcceptance, TermsOfUse
from .utils import can_view_view_history as can_edit_terms


DEFAULT_TERMS_TITLE = 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件'


def terms_header_title(terms):
    """Presentation metadata is intentionally absent from consent snapshots."""
    return next((term.title for term in terms if term.is_header_title), DEFAULT_TERMS_TITLE)


def terms_are_registered(terms):
    return bool(terms and all(term.title.strip() and term.description.strip() for term in terms))


def terms_acceptance_applies(user):
    """Only real effective customers, never demo or account-view previews."""
    # Local import: views imports this module from the login success path.
    from .views import get_forced_account_view_mode, is_customer_user, is_demo_user

    return bool(user.is_authenticated
                and not get_forced_account_view_mode(user)
                and not user.is_staff and not user.is_superuser
                and not getattr(user, '_original_is_staff', False)
                and not getattr(user, '_original_is_superuser', False)
                and not is_demo_user(user)
                and is_customer_user(user))


def requires_terms_acceptance(user):
    return bool(terms_acceptance_applies(user)
                and not TermsAcceptance.objects.filter(user=user).exists()
                and terms_are_registered(list(TermsOfUse.objects.order_by('pk'))))


def terms_fingerprint(terms):
    payload = terms_snapshots(terms)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode('utf-8')).hexdigest()


def terms_snapshots(terms):
    return [{'id': term.pk, 'title': term.title, 'description': term.description,
             'updated_at': term.updated_at.isoformat()} for term in terms]


def lock_terms_set():
    """Within atomic(), prevent PostgreSQL inserts/deletes as well as row edits.

    Row locks alone cannot protect additions to the set. On SQLite acquire the
    writer reservation BEFORE reading, including when there are no rows. This
    serializes editor/consent transactions without a stale read-to-write upgrade.
    """
    if connection.vendor == 'postgresql':
        table = connection.ops.quote_name(TermsOfUse._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f'LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE')
    elif connection.vendor == 'sqlite':
        TermsOfUse.objects.update(is_header_title=F('is_header_title'))


@method_decorator([csrf_protect, never_cache], name='dispatch')
class TermsAcceptanceView(LoginRequiredMixin, TemplateView):
    template_name = 'tenasapo_knowledge/terms_accept.html'
    http_method_names = ['get', 'post', 'head', 'options']

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not terms_acceptance_applies(request.user):
            # Check all methods before issuing tokens or processing consent POSTs.
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'terms_list' not in context:
            context['terms_list'] = list(TermsOfUse.objects.order_by('pk'))
        terms = context['terms_list']
        context['default_terms_title'] = terms_header_title(terms)
        context['can_edit_terms'] = can_edit_terms(self.request)
        context['terms_registered'] = terms_are_registered(terms)
        context['acceptance'] = TermsAcceptance.objects.filter(user=self.request.user).first()
        if context['terms_registered']:
            context['terms_token'] = signing.dumps(
                [str(self.request.user.pk), terms_fingerprint(terms)], salt='terms-consent')
        return context

    def post(self, request, *args, **kwargs):
        # Snapshot and validate the ENTIRE ordered set in the same transaction.
        with transaction.atomic():
            lock_terms_set()
            terms = list(TermsOfUse.objects.select_for_update().order_by('pk'))
            if TermsAcceptance.objects.filter(user=request.user).exists():
                return redirect('home')
            if not terms_are_registered(terms) and not request.POST.get('terms_token'):
                return redirect('home')
            try:
                viewed = signing.loads(request.POST.get('terms_token', ''), salt='terms-consent')
            except signing.BadSignature:
                viewed = None
            if viewed != [str(request.user.pk), terms_fingerprint(terms)]:
                return self.render_to_response(self.get_context_data(
                    terms_list=terms, consent_error='利用規約が更新されたか、確認情報が無効です。内容を再確認して同意してください。'),
                    status=409)
            if not terms_are_registered(terms):
                return redirect('home')
            # Match the checkbox's explicit value; a token alone is not consent.
            if request.POST.get('terms_accepted') != 'on':
                return self.render_to_response(self.get_context_data(
                    terms_list=terms, consent_error='利用規約に同意するにチェックを入れてください。'),
                    status=400)
            TermsAcceptance.objects.get_or_create(user=request.user, defaults={
                'terms_updated_at': terms[0].updated_at,
                'terms_title': terms[0].title,
                'terms_description': terms[0].description,
                'terms_snapshots': terms_snapshots(terms),
            })
        # Deliberately ignore next: home retains the existing onboarding flow.
        return redirect('home')


class TermsOfUseForm(ManualForm):
    """Reuse the manual's required multiline plaintext editor, not rich HTML."""

    class Meta:
        model = TermsOfUse
        fields = ('title', 'is_header_title', 'description')
        widgets = {'is_header_title': forms.CheckboxInput(attrs={
            'class': 'btn-check', 'autocomplete': 'off',
            'aria-describedby': 'header-title-help',
        })}

    def _get_validation_exclusions(self):
        # A checked form switches the current choice inside the locked save
        # transaction, so the existing choice is not a form validation error.
        # Direct ORM writes remain guarded by the database unique constraint.
        return super()._get_validation_exclusions() | {'is_header_title'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['description'].label = '内容'
        # An initial value, not saved content: never replace an existing title
        # or fill a missing title on a submitted form behind the user's back.
        if self.instance._state.adding:
            self.initial.setdefault('title', DEFAULT_TERMS_TITLE)


@method_decorator(login_not_required, name='dispatch')
class TermsOfUseDetailView(TemplateView):
    template_name = 'tenasapo_knowledge/terms_detail.html'
    http_method_names = ['get', 'head', 'options']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['terms_list'] = list(TermsOfUse.objects.order_by('pk'))
        context['terms'] = context['terms_list'][0] if context['terms_list'] else None
        context['default_terms_title'] = terms_header_title(context['terms_list'])
        context['can_edit_terms'] = can_edit_terms(self.request)
        return context


@method_decorator(csrf_protect, name='dispatch')
class TermsOfUseEditView(FormView):
    template_name = 'tenasapo_knowledge/terms_form.html'
    form_class = TermsOfUseForm
    success_url = reverse_lazy('terms_detail')
    http_method_names = ['get', 'post', 'head', 'options']

    def dispatch(self, request, *args, **kwargs):
        if not can_edit_terms(request):
            return HttpResponseForbidden('利用規約を編集できるのは上位管理者のみです。')
        pk = kwargs.get('pk')
        self.terms = get_object_or_404(TermsOfUse, pk=pk) if pk is not None else None
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.terms
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = '利用規約を編集' if self.terms else '利用規約を登録'
        context['submit_label'] = '更新する' if self.terms else '登録する'
        return context

    def form_valid(self, form):
        with transaction.atomic():
            lock_terms_set()
            selected = form.cleaned_data['is_header_title']
            if self.terms is None:
                if selected:
                    TermsOfUse.objects.filter(is_header_title=True).update(is_header_title=False)
                form.save()
            else:
                # Never resurrect a document removed while its editor was open.
                terms = get_object_or_404(TermsOfUse.objects.select_for_update(), pk=self.terms.pk)
                if selected:
                    TermsOfUse.objects.filter(is_header_title=True).exclude(pk=terms.pk).update(
                        is_header_title=False)
                legal_changed = (terms.title != form.cleaned_data['title']
                                 or terms.description != form.cleaned_data['description'])
                terms.title = form.cleaned_data['title']
                terms.description = form.cleaned_data['description']
                terms.is_header_title = selected
                fields = ['is_header_title']
                if legal_changed:
                    fields += ['title', 'description', 'updated_at']
                terms.save(update_fields=fields)
        messages.success(self.request, '利用規約を保存しました。')
        return super().form_valid(form)