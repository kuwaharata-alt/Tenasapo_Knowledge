"""QR knowledge browsing and favorites."""

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import TemplateView

from ...models import ConvenienceFeature, ConvenienceFavorite
from ..shared.access import can_use_convenience_favorite
from ..shared.history import record_view_history
from ..shared.qr_categories import get_qr_category_hierarchy


class ConvenienceListView(TemplateView):
    template_name = 'tenasapo_knowledge/convenience_list.html'

    def dispatch(self, request, *args, **kwargs):
        record_view_history(request, 'QR一覧')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sort_mode = (self.request.GET.get('sort') or 'frequency').strip().lower()
        if sort_mode not in {'frequency', 'favorite'}:
            sort_mode = 'frequency'
        query = self.request.GET.get('q', '').strip()
        selected_big = (self.request.GET.get('big_category') or '').strip()
        selected_mid = (self.request.GET.get('category') or '').strip()
        selected_small = (self.request.GET.get('small_category') or '').strip()

        all_features = list(ConvenienceFeature.objects.all())
        favorite_ids = set()
        can_use_convenience_favorite_flag = can_use_convenience_favorite(self.request.user)
        if can_use_convenience_favorite_flag:
            favorite_ids = set(
                ConvenienceFavorite.objects.filter(user=self.request.user, feature__in=all_features)
                .values_list('feature_id', flat=True)
            )
        for feature in all_features:
            try:
                frequency = int(feature.usage_frequency or '0')
            except (TypeError, ValueError):
                frequency = 0
            frequency = max(0, min(5, frequency))
            feature.usage_frequency_value = frequency
            feature.usage_frequency_stars = ('★' * frequency) + ('☆' * (5 - frequency))
            feature.is_favorited = feature.id in favorite_ids

        if sort_mode == 'favorite' and can_use_convenience_favorite_flag:
            all_features.sort(
                key=lambda f: (
                    not f.is_favorited,
                    -f.usage_frequency_value,
                    f.reference_type or '',
                    f.category or '',
                    f.middle_category or '',
                    f.display_text or '',
                    f.id,
                )
            )
        else:
            sort_mode = 'frequency'
            all_features.sort(
                key=lambda f: (
                    -f.usage_frequency_value,
                    f.reference_type or '',
                    f.category or '',
                    f.middle_category or '',
                    f.display_text or '',
                    f.id,
                )
            )

        # サイドバー階層データを構築（QR_CATEGORY_HIERARCHY を骨格に DB のカウントを付与）
        existing_big_vals = set(f.reference_type for f in all_features)
        sidebar_hierarchy = []
        for big_item in get_qr_category_hierarchy():
            big_val = big_item['value']
            big_features = [f for f in all_features if f.reference_type == big_val]
            # DBに存在するが階層未登録の中カテゴリも追加
            registered_mid_vals = {c['value'] for c in big_item['children']}
            extra_mid_vals = set(f.category for f in big_features if f.category and f.category not in registered_mid_vals)
            mid_list = list(big_item['children']) + [{'value': v, 'label': v, 'children': []} for v in sorted(extra_mid_vals)]
            mid_entries = []
            for mid_item in mid_list:
                mid_val = mid_item['value']
                mid_features = [f for f in big_features if f.category == mid_val]
                registered_small_vals = list(mid_item.get('children', []))
                extra_small_vals = [
                    v for v in dict.fromkeys(f.middle_category for f in mid_features if f.middle_category)
                    if v not in registered_small_vals
                ]
                small_entries = []
                for small_val in registered_small_vals + extra_small_vals:
                    small_features = [f for f in mid_features if f.middle_category == small_val]
                    small_entries.append({'value': small_val, 'label': small_val, 'count': len(small_features)})
                mid_entries.append({
                    'value': mid_val,
                    'label': mid_item['label'],
                    'count': len(mid_features),
                    'children': small_entries,
                })
            sidebar_hierarchy.append({
                'value': big_val,
                'label': big_item['label'],
                'count': len(big_features),
                'children': mid_entries,
            })

        # 選択値のバリデーション
        all_big_vals = [item['value'] for item in sidebar_hierarchy]
        if selected_big and selected_big not in all_big_vals:
            selected_big = ''
        if selected_big:
            big_entry = next((b for b in sidebar_hierarchy if b['value'] == selected_big), None)
            all_mid_vals = [m['value'] for m in big_entry['children']] if big_entry else []
            if selected_mid and selected_mid not in all_mid_vals:
                selected_mid = ''
                selected_small = ''
        else:
            if selected_mid:
                selected_mid = ''
            selected_small = ''

        # フィルタリング
        filtered_features = list(all_features)
        if selected_big:
            filtered_features = [f for f in filtered_features if f.reference_type == selected_big]
        if selected_mid:
            filtered_features = [f for f in filtered_features if f.category == selected_mid]
        if selected_small:
            filtered_features = [f for f in filtered_features if f.middle_category == selected_small]

        if query:
            query_lower = query.lower()
            filtered_features = [
                f for f in filtered_features
                if query_lower in (f.display_text or '').lower()
                or query_lower in (f.shortcut_key or '').lower()
                or query_lower in (f.note or '').lower()
            ]

        # 小カテゴリでグループ化
        group_map = {}
        for feature in filtered_features:
            group_name = (feature.middle_category or '（なし）').strip() or '（なし）'
            group_map.setdefault(group_name, []).append(feature)

        category_groups = [
            {'middle_name': middle_name, 'features': items}
            for middle_name, items in group_map.items()
        ]

        context['category_groups'] = category_groups
        context['sidebar_hierarchy'] = sidebar_hierarchy
        context['selected_big'] = selected_big
        context['selected_category'] = selected_mid
        context['selected_small'] = selected_small
        context['query'] = query
        context['can_create_convenience'] = (
            self.request.user.is_authenticated
            and (self.request.user.is_staff or self.request.user.is_superuser)
        )
        context['can_edit_convenience'] = context['can_create_convenience']
        context['can_use_convenience_favorite'] = can_use_convenience_favorite_flag
        context['sort_mode'] = sort_mode
        return context


class ConvenienceFavoriteToggleView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)
        if not can_use_convenience_favorite(request.user):
            return JsonResponse({'detail': 'forbidden'}, status=403)

        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            payload = {}

        feature_id = payload.get('feature_id')
        if not feature_id:
            return JsonResponse({'detail': 'feature_id is required'}, status=400)

        feature = get_object_or_404(ConvenienceFeature, pk=feature_id)
        favorite, created = ConvenienceFavorite.objects.get_or_create(feature=feature, user=request.user)
        favorited = True
        if not created:
            favorite.delete()
            favorited = False

        return JsonResponse({'ok': True, 'favorited': favorited})