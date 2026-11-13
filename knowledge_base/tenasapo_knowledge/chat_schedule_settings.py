"""Staff-only calendar editor; saving never sends a notification."""

from datetime import datetime
import json
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_protect
from django.views.generic import FormView

from .chat_schedule import (
    ChatNotificationDelivery, ChatNotificationSchedule, JAPAN_TIMEZONE, ScheduleConflict,
    current_chat_environment, save_reservation,
)
from .chat_schedule_delivery import can_attempt, claim_delivery, send_claimed_delivery
from .notifications import get_google_chat_target_url, send_scheduled_google_chat_message
from .drive_oauth import DriveOAuthError, get_connection, get_drive_upload_config
from .scheduled_chat_messages import build_deadline_notification as build_deadline_message, build_report_message
from .views import StaffRequiredMixin


class JapanDateTimeField(forms.DateTimeField):
    def clean(self, value):
        with timezone.override(JAPAN_TIMEZONE):
            return super().clean(value)


class ChatScheduleForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        kind = self.data.get('kind') if self.is_bound else self.initial.get('kind')
        if kind == 'report':
            del self.fields['deadline']

    kind = forms.ChoiceField(choices=ChatNotificationSchedule.Kind.choices, widget=forms.HiddenInput)
    environment = forms.ChoiceField(choices=[('development', '開発'), ('production', '本番')],
                                    widget=forms.HiddenInput)
    schedule_id = forms.IntegerField(min_value=1, required=False, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    deadline = forms.DateField(label='投稿期限', widget=forms.DateInput(
        format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}))
    notice_at = JapanDateTimeField(label='通知日時（日本時間）', widget=forms.DateTimeInput(
        format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'class': 'form-control'}))
    enabled = forms.BooleanField(label='この予約を有効にする', required=False,
                                 widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))


@method_decorator(csrf_protect, name='dispatch')
class ChatScheduleSettingsView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/chat_schedule_settings.html'
    form_class = ChatScheduleForm
    http_method_names = ['get', 'post', 'head', 'options']

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff

    def selected_kind(self):
        source = self.request.POST if self.request.method == 'POST' else self.request.GET
        value = source.get('kind', 'deadline')
        return value if value in ChatNotificationSchedule.Kind.values else 'deadline'

    def selected_month(self):
        raw = self.request.GET.get('month', '')
        try:
            month = datetime.strptime(raw, '%Y-%m').date()
            if month.strftime('%Y-%m') == raw and month.year < 9999:
                return month
        except ValueError:
            pass
        return timezone.localdate(timezone=JAPAN_TIMEZONE).replace(day=1)

    def schedules(self):
        return ChatNotificationSchedule.objects.filter(
            environment=current_chat_environment(), target_month=self.selected_month(),
        ).order_by('-revision')

    def get_initial(self):
        initial = {'kind': self.selected_kind(), 'environment': current_chat_environment(),
                   'expected_revision': 0, 'enabled': False}
        edit = self.request.GET.get('edit')
        if edit:
            try:
                edit = int(edit)
            except ValueError:
                raise Http404('予約の指定が不正です。') from None
            latest = get_object_or_404(self.schedules(), pk=edit, kind=self.selected_kind())
            initial.update(schedule_id=latest.pk, expected_revision=latest.revision,
                           deadline=latest.deadline, enabled=latest.enabled,
                           notice_at=timezone.localtime(latest.notice_at, JAPAN_TIMEZONE).replace(tzinfo=None))
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        archived = self.request.GET.get('history') == 'archive'
        history = self.schedules()
        forms_by_kind = []
        for kind, label in ChatNotificationSchedule.Kind.choices:
            entry = (context['form'] if self.selected_kind() == kind else ChatScheduleForm(
                initial={'kind': kind, 'environment': current_chat_environment(),
                         'expected_revision': 0, 'enabled': False}, auto_id=kind + '_%s'))
            forms_by_kind.append((label, entry))
        visible_history = list(history.filter(enabled=not archived).select_related('updated_by').prefetch_related('deliveries'))
        for row in visible_history:
            row.manual_available = can_attempt(row, row.kind)
        context.update(
            reservation_forms=forms_by_kind,
            history_archived=archived,
            active_schedule_count=history.filter(enabled=True).count(),
            archived_schedule_count=history.filter(enabled=False).count(),
            selected_month=self.selected_month(),
            schedule_environment=current_chat_environment(),
            sending_enabled=getattr(settings, 'GOOGLE_CHAT_SCHEDULED_SEND_ENABLED', False) is True,
            report_pdf_enabled=getattr(settings, 'GOOGLE_CHAT_REPORT_PDF_ENABLED', False) is True,
            schedule_history=visible_history,
            configured_months=ChatNotificationSchedule.objects.filter(
                environment=current_chat_environment()).values_list('target_month', flat=True).distinct().order_by('-target_month')[:36],
        )
        context['drive_oauth_mode'] = getattr(settings, 'GOOGLE_DRIVE_REPORT_BACKEND', 'gas') == 'oauth'
        if context['drive_oauth_mode']:
            context['drive_callback_uri'] = self.request.build_absolute_uri(reverse('drive_oauth_callback'))
            try:
                context['drive_connection'] = get_connection(self.request.user)
            except DriveOAuthError:
                context['drive_connection_error'] = 'Drive連携を確認できません。設定を確認し、再連携してください。'
        return context

    def form_valid(self, form):
        if self.request.GET.get('month') != self.selected_month().strftime('%Y-%m'):
            form.add_error(None, '対象月が不正です。対象月を選び直してください。')
            return self.form_invalid(form)
        try:
            values = dict(form.cleaned_data)
            if values['kind'] == 'report':
                # Legacy non-null storage field; not a report deadline or filter.
                values['deadline'] = self.selected_month()
            save_reservation(target_month=self.selected_month(), actor=self.request.user, **values)
        except ScheduleConflict as error:
            form.add_error(None, error)
            return self.render_to_response(self.get_context_data(form=form), status=409)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)
        messages.success(self.request, '通知予約を保存しました。保存操作ではChatに投稿しません。')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('chat_schedule_settings') + '?' + urlencode({'month': self.selected_month().strftime('%Y-%m')})


class ChatScheduleSendForm(forms.Form):
    schedule_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    kind = forms.ChoiceField(choices=ChatNotificationDelivery.Kind.choices, widget=forms.HiddenInput)
    environment = forms.ChoiceField(choices=[('development', '開発'), ('production', '本番')],
                                    widget=forms.HiddenInput)


@method_decorator(csrf_protect, name='dispatch')
class ChatScheduleSendView(StaffRequiredMixin, View):
    http_method_names = ['get', 'post', 'head', 'options']

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff

    def selection(self, request, schedule_id, kind):
        data = request.POST if request.method == 'POST' else {
            'schedule_id': schedule_id, 'kind': kind,
            'expected_revision': request.GET.get('expected_revision'),
            'environment': current_chat_environment(),
        }
        form = ChatScheduleSendForm(data)
        if not form.is_valid():
            return None, None
        values = form.cleaned_data
        if (values['schedule_id'] != schedule_id or values['kind'] != kind
                or values['environment'] != current_chat_environment()):
            return None, None
        schedule = get_object_or_404(ChatNotificationSchedule, pk=schedule_id,
                                     environment=current_chat_environment())
        return form, schedule

    def get(self, request, schedule_id, kind):
        form, schedule = self.selection(request, schedule_id, kind)
        if form is None:
            return HttpResponseBadRequest('予約・種別・環境・予約版の指定が不正です。')
        available = (schedule.revision == form.cleaned_data['expected_revision'] and can_attempt(schedule, kind))
        preview = ''
        error = ''
        if available:
            try:
                now = timezone.now()
                payload = build_deadline_message(schedule, now) if kind == 'deadline' else build_report_message(schedule, now)
                preview = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
            except Exception:
                available = False
                error = '本文プレビューを作成できません。設定・データを確認してください。'
        return render(request, 'tenasapo_knowledge/chat_schedule_send_confirm.html', {
            'form': form, 'schedule': schedule, 'preview': preview,
            'kind_label': dict(ChatNotificationDelivery.Kind.choices)[kind],
            'available': available, 'preview_error': error,
            'sending_enabled': getattr(settings, 'GOOGLE_CHAT_SCHEDULED_SEND_ENABLED', False) is True,
            'report_pdf_enabled': kind == 'report' and getattr(settings, 'GOOGLE_CHAT_REPORT_PDF_ENABLED', False) is True,
        }, status=200 if available else 409)

    def post(self, request, schedule_id, kind):
        form, schedule = self.selection(request, schedule_id, kind)
        if form is None:
            return HttpResponseBadRequest('予約・種別・環境・予約版の指定が不正です。')
        url = reverse('chat_schedule_settings') + '?' + urlencode({'month': schedule.target_month.strftime('%Y-%m')})
        if getattr(settings, 'GOOGLE_CHAT_SCHEDULED_SEND_ENABLED', False) is not True:
            messages.error(request, '予約送信は無効です。送信しませんでした。')
            return redirect(url)
        if (kind == 'report' and getattr(settings, 'GOOGLE_CHAT_REPORT_PDF_ENABLED', False) is True
                and getattr(settings, 'GOOGLE_DRIVE_REPORT_BACKEND', 'gas') == 'oauth'):
            try:
                get_drive_upload_config(schedule.updated_by)
            except DriveOAuthError:
                messages.error(request, '予約保存者のDrive連携が必要です。予約保存者で連携してから送信してください。今回は送信を試行していません。')
                return redirect(url)
        delivery = claim_delivery(schedule.pk, kind, timezone.now(), manual=True,
                                  expected_revision=form.cleaned_data['expected_revision'])
        if delivery is None:
            messages.warning(request, '送信しませんでした。予約の変更・無効化、または試行済みの配信です。再送は行いません。')
            return redirect(url)
        state = send_claimed_delivery(
            delivery, timezone.now(), target_url=get_google_chat_target_url,
            deadline_builder=build_deadline_message, report_builder=build_report_message,
            sender=send_scheduled_google_chat_message)
        if state == 'sent':
            messages.success(request, '通知を送信しました。送信完了した予約だけをアーカイブします。他の予約には影響しません。')
            schedule.refresh_from_db(fields=['enabled'])
            return redirect(url + '&history=archive' if not schedule.enabled else url)
        messages.error(request, '送信前確認に失敗しました。再試行は行いません。' if state == 'failed' else
                       '送信結果が不明です。スペースを確認してください。自動再送・手動再試行は行いません。')
        return redirect(url)