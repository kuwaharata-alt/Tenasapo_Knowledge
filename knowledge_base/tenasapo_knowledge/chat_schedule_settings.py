"""Staff-only calendar editor; saving never sends a notification."""

from datetime import datetime
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView

from .chat_schedule import (
    ChatNotificationSchedule, JAPAN_TIMEZONE, ScheduleConflict,
    current_chat_environment, save_schedule,
)
from .views import StaffRequiredMixin


class JapanDateTimeField(forms.DateTimeField):
    def clean(self, value):
        with timezone.override(JAPAN_TIMEZONE):
            return super().clean(value)


class ChatScheduleForm(forms.Form):
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    deadline = forms.DateField(label='投稿期限', widget=forms.DateInput(
        format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}))
    deadline_notice_at = JapanDateTimeField(label='投稿期限の通知日時（日本時間）', widget=forms.DateTimeInput(
        format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'class': 'form-control'}))
    report_notice_at = JapanDateTimeField(label='投稿レポートの通知日時（日本時間）', widget=forms.DateTimeInput(
        format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'class': 'form-control'}))
    enabled = forms.BooleanField(label='この月の予約を有効にする', required=False,
                                 widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))


class ChatScheduleSettingsView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/chat_schedule_settings.html'
    form_class = ChatScheduleForm
    http_method_names = ['get', 'post', 'head', 'options']

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
        latest = self.schedules().first()
        initial = {'expected_revision': 0, 'enabled': False}
        if latest:
            initial.update(expected_revision=latest.revision, deadline=latest.deadline,
                           enabled=latest.enabled)
            for name in ('deadline_notice_at', 'report_notice_at'):
                initial[name] = timezone.localtime(getattr(latest, name), JAPAN_TIMEZONE).replace(tzinfo=None)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            selected_month=self.selected_month(),
            schedule_environment=current_chat_environment(),
            sending_enabled=getattr(settings, 'GOOGLE_CHAT_SCHEDULED_SEND_ENABLED', False) is True,
            schedule_history=self.schedules().select_related('updated_by').prefetch_related('deliveries')[:24],
            configured_months=ChatNotificationSchedule.objects.filter(
                environment=current_chat_environment()).values_list('target_month', flat=True).distinct().order_by('-target_month')[:36],
        )
        return context

    def form_valid(self, form):
        try:
            save_schedule(target_month=self.selected_month(), actor=self.request.user, **form.cleaned_data)
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