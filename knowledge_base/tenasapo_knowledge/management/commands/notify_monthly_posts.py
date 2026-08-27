import io
import logging
import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q

from PIL import Image, ImageDraw, ImageFont

from tenasapo_knowledge.models import KnowledgeArticle, TipsArticle
from tenasapo_knowledge.utils import resolve_user_display_name, resolve_saved_or_user_display_name
from tenasapo_knowledge.notifications import (
    send_google_chat_message,
    send_google_chat_card_with_image,
    GoogleChatNotificationError,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '当月のFAQおよびTipsのメンバー投稿状況を図（棒グラフ画像）とテキストでGoogle Chatへ通知します。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際にはGoogle Chatへ通知を送信せず、送信内容を標準出力に表示し、画像をローカル環境に一時保存します。',
        )
        parser.add_argument(
            '--webhook-url',
            type=str,
            default=None,
            help='Google ChatのWebhook URLを個別に指定して送信します（テスト等に便利です）。',
        )
        parser.add_argument(
            '--text-only',
            action='store_true',
            help='図（画像）の生成と送信を行わず、従来のテキストグラフのみで送信します。',
        )

    def make_progress_bar(self, count, target=1):
        """10文字の進捗バーの文字列を生成します。"""
        if target <= 0:
            return "░" * 10
        pct = min(1.0, float(count) / target)
        filled = int(round(pct * 10))
        empty = 10 - filled
        return "█" * filled + "░" * empty

    def get_japanese_font(self, size):
        """システムから利用可能な日本語フォントを探してロードします。"""
        font_paths = [
            'C:\\Windows\\Fonts\\meiryo.ttc',    # メイリオ
            'C:\\Windows\\Fonts\\msgothic.ttc',  # MS ゴシック
            'msjh.ttc',                          # Microsoft JhengHei
            '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc', # macOS
            '/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf', # Linux
            '/usr/share/fonts/truetype/fonts-japanese-gothic.ttf', # Linux
        ]
        for path in font_paths:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        # フォールバック
        return ImageFont.load_default()

    def generate_member_chart_image(self, members_data, today):
        """Pillowを使用して、添付画像のダッシュボードと等価な美しい縦積み上げ棒グラフ画像を生成します。"""
        font_title = self.get_japanese_font(16)
        font_label = self.get_japanese_font(12)
        font_val = self.get_japanese_font(11)
        font_tab = self.get_japanese_font(11)

        width = 960
        height = 340

        # 全体背景（薄いオレンジの暖色トーン）
        img = Image.new('RGB', (width, height), color='#FAF1EC')
        draw = ImageDraw.Draw(img)

        # 白い角丸カードエリア
        card_margin = 15
        card_x0 = card_margin
        card_y0 = card_margin
        card_x1 = width - card_margin
        card_y1 = height - card_margin

        draw.rounded_rectangle([card_x0, card_y0, card_x1, card_y1], radius=8, fill='#FFFFFF', outline='#E6D3C8', width=1)

        # タイトル「メンバーの投稿数」（左上に配置、オレンジ色の下線つき）
        title_text = "メンバーの投稿数"
        draw.text((35, 35), title_text, fill='#A85324', font=font_title)
        
        # 下線の描画
        draw.line([35, 62, 145, 62], fill='#D66F31', width=2)

        # 右上の「期間切り替えボタン」の模倣
        # 「全期間」(通常)、「当月」(アクティブ: オレンジ)、「前月」(通常)
        # 右端を基準に並べる
        tab_y0 = 34
        tab_h = 24
        tab_x_end = card_x1 - 25

        # 1. 「前月」ボタン
        px_prev_x1 = tab_x_end
        px_prev_x0 = px_prev_x1 - 42
        draw.rounded_rectangle([px_prev_x0, tab_y0, px_prev_x1, tab_y0 + tab_h], radius=3, fill='#FFFFFF', outline='#D1D5DB', width=1)
        draw.text((px_prev_x0 + 10, tab_y0 + 5), "前月", fill='#374151', font=font_tab)

        # 2. 「当月」ボタン (アクティブ：オレンジ)
        px_curr_x1 = px_prev_x0 - 2
        px_curr_x0 = px_curr_x1 - 42
        draw.rounded_rectangle([px_curr_x0, tab_y0, px_curr_x1, tab_y0 + tab_h], radius=3, fill='#E87524', outline='#D25F12', width=1)
        draw.text((px_curr_x0 + 10, tab_y0 + 5), "当月", fill='#FFFFFF', font=font_tab)

        # 3. 「全期間」ボタン
        px_all_x1 = px_curr_x0 - 2
        px_all_x0 = px_all_x1 - 52
        draw.rounded_rectangle([px_all_x0, tab_y0, px_all_x1, tab_y0 + tab_h], radius=3, fill='#FFFFFF', outline='#D1D5DB', width=1)
        draw.text((px_all_x0 + 10, tab_y0 + 5), "全期間", fill='#374151', font=font_tab)

        # 中央の「凡例」（FAQ: オレンジ / Tips: 青）
        legend_y = 75
        legend_w_half = 50
        legend_center_x = width // 2

        # FAQ凡例
        draw.rounded_rectangle([legend_center_x - legend_w_half, legend_y + 3, legend_center_x - legend_w_half + 14, legend_y + 11], radius=2, fill='#E87524')
        draw.text((legend_center_x - legend_w_half + 20, legend_y), "FAQ", fill='#6B7280', font=font_val)

        # Tips凡例
        draw.rounded_rectangle([legend_center_x + 15, legend_y + 3, legend_center_x + 29, legend_y + 11], radius=2, fill='#4988E8')
        draw.text((legend_center_x + 35, legend_y), "Tips", fill='#6B7280', font=font_val)

        # プロット領域の定義
        plot_x0 = 50
        plot_y0 = 105
        plot_x1 = card_x1 - 30
        plot_y1 = card_y1 - 40

        # Y軸の最大レベルの計算
        max_val = 0
        if members_data:
            max_val = max(m['faq_count'] + m['tips_count'] for m in members_data)
        max_level = max(3, max_val)

        # グリッド横線の描画 (0 から max_level まで)
        for val in range(max_level + 1):
            y_pos = plot_y1 - int((val / max_level) * (plot_y1 - plot_y0))
            
            # グリッド線
            draw.line([plot_x0, y_pos, plot_x1, y_pos], fill='#E5E7EB', width=1)
            # 目盛りラベル
            draw.text((plot_x0 - 20, y_pos - 6), str(val), fill='#6B7280', font=font_val)

        # 積み上げ縦棒グラフの描画
        n_members = len(members_data)
        if n_members > 0:
            plot_width = plot_x1 - plot_x0
            member_width = plot_width / n_members
            bar_w = 28  # 棒の太さ

            for i, m in enumerate(members_data):
                # メンバーの中央座標
                x_center = plot_x0 + (member_width * (i + 0.5))
                bar_x0 = int(x_center - bar_w / 2)
                bar_x1 = int(x_center + bar_w / 2)

                faq = m['faq_count']
                tips = m['tips_count']
                total = faq + tips

                # 積み上げ描画
                if total > 0:
                    y_total = plot_y1 - int((total / max_level) * (plot_y1 - plot_y0))
                    y_faq = plot_y1 - int((faq / max_level) * (plot_y1 - plot_y0))

                    # 1. FAQ (オレンジ)
                    if faq > 0:
                        # 全体が丸みを持つ可能性を想定し rounded_rectangle を使い、下側の余分な丸みを削る
                        if tips == 0:
                            # トップがFAQのため上部のみ角丸に
                            draw.rounded_rectangle([bar_x0, y_faq, bar_x1, plot_y1], radius=4, fill='#E87524')
                            draw.rectangle([bar_x0, y_faq + (plot_y1 - y_faq) // 2, bar_x1, plot_y1], fill='#E87524')
                        else:
                            draw.rectangle([bar_x0, y_faq, bar_x1, plot_y1], fill='#E87524')

                    # 2. Tips (青)
                    if tips > 0:
                        # トップがTipsのため上部のみ角丸にして重ねる
                        draw.rounded_rectangle([bar_x0, y_total, bar_x1, y_faq], radius=4, fill='#4988E8')
                        draw.rectangle([bar_x0, y_total + (y_faq - y_total) // 2, bar_x1, y_faq], fill='#4988E8')

                    # 3. グラフの上部（てっぺん）に合計数値を太字で表示
                    total_str = str(total)
                    bbox_ts = draw.textbbox((0, 0), total_str, font=font_label)
                    tw_ts = bbox_ts[2] - bbox_ts[0]
                    draw.text((x_center - tw_ts / 2, y_total - 16), total_str, fill='#374151', font=font_label)

                # 4. 横軸のメンバー名表示（下線より少し下、中央揃え）
                name = m['display_name']
                bbox_n = draw.textbbox((0, 0), name, font=font_label)
                tw_n = bbox_n[2] - bbox_n[0]
                draw.text((x_center - tw_n / 2, plot_y1 + 10), name, fill='#4B5563', font=font_label)

        # バイトストリームに変換
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        text_only = options.get('text_only', False)
        today = timezone.localdate()
        current_month_start = today.replace(day=1)

        User = get_user_model()
        CONTRIBUTOR_GROUP_NAME = getattr(
            settings,
            'USER_ROLE_CONTRIBUTOR_NAME',
            getattr(settings, 'USER_GROUP_CONTRIBUTOR_NAME', '投稿者'),
        )
        excluded_contributor_names = {'admin'}

        # メンバーの取得
        users = User.objects.filter(
            groups__name=CONTRIBUTOR_GROUP_NAME
        ).distinct().prefetch_related('knowledge_profile').order_by('knowledge_profile__uid', 'id')

        members = []
        member_map = {}
        for user in users:
            display_name = resolve_user_display_name(user).strip()
            if not display_name or display_name.lower() in excluded_contributor_names:
                continue
            members.append((display_name, user))
            member_map[display_name] = {
                'display_name': display_name,
                'faq_count': 0,
                'tips_count': 0,
            }

        # 当月のFAQ/Tips of the Month
        faq_qs = KnowledgeArticle.objects.filter(
            Q(visible_to_customer=True) | Q(visible_to_systena=True),
            created_at__date__gte=current_month_start,
            created_at__date__lte=today,
        ).select_related('created_by')

        tips_qs = TipsArticle.objects.filter(
            Q(visible_to_customer=True) | Q(visible_to_systena=True),
            created_at__date__gte=current_month_start,
            created_at__date__lte=today,
        ).select_related('created_by')

        # 投稿数のカウント
        for article in faq_qs:
            creator_name = resolve_saved_or_user_display_name(
                article.created_by_name, article.created_by, default=''
            ).strip()
            if creator_name in member_map:
                member_map[creator_name]['faq_count'] += 1

        for tip in tips_qs:
            creator_name = resolve_saved_or_user_display_name(
                tip.created_by_name, tip.created_by, default=''
            ).strip()
            if creator_name in member_map:
                member_map[creator_name]['tips_count'] += 1

        # 表形式の組み立て (Google Chat が対応しているMarkdown表、または等幅ブロックを使った綺麗な表組み)
        target_faq = 1
        target_tips = 1
        members_data = []

        # Google Chat の等幅ブロックフォント(RobotoMono)で日本語全角文字と半角スペースが「2:1」の理想比率で完璧にインデント一致するハイブリッド等幅テーブルを構築
        table_lines = [
            "```",
            " 状況 |      メンバー名      |  FAQ  | Tips  ",
            "------+----------------------+-------+-------"
        ]

        # 各行の追加
        for display_name, _ in members:
            data = member_map[display_name]
            faq = data['faq_count']
            tips = data['tips_count']
            members_data.append(data)

            # 完了判定
            is_completed = (faq >= target_faq and tips >= target_tips)
            status_text = "  ✅  " if is_completed else "  ❌  "

            # 全角混じりのメンバー名を、等幅フォント環境でも極力崩さないためのマルチバイト対応幅寄せ
            # 文字数をカウントして、等幅フォーマット用の空白を埋める
            display_name_pad = display_name
            # 全角を2文字、半角を1文字として簡易幅計算 (20スペース分)
            visual_len = sum(2 if ord(c) > 127 else 1 for c in display_name)
            pad_needed = max(0, 20 - visual_len)
            
            # 名前の後ろに適切なスペースを足してアライメントを完全に統一
            display_name_pad = display_name + (" " * pad_needed)

            # 数値のアライメント (3マス幅にセンタリング)
            faq_str = f"{faq}/{target_faq}".center(5)
            tips_str = f"{tips}/{target_tips}".center(5)

            table_lines.append(f"{status_text}| {display_name_pad} | {faq_str} | {tips_str} ")

        table_lines.append("```")

        post_date_str = today.strftime('%Y/%m/%d')
        current_month_str = f"{today.month}月"

        intro_text = (
            f"{post_date_str}時点の投稿状況です。\n"
            f"Tips1件、FAQ1件登録が完了していない人は、期限までに投稿してください。"
        )

        message_body = (
            f"{intro_text}\n\n"
            f"■ {current_month_str}度のメンバー投稿状況\n"
            + "\n".join(table_lines)
        )

        # 画像描画
        image_bytes = None
        if not text_only and members_data:
            try:
                image_bytes = self.generate_member_chart_image(members_data, today)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"画像の生成中にエラーが発生したため、テキストモードにフォールバックします: {e}"))
                text_only = True

        if dry_run:
            self.stdout.write(self.style.SUCCESS("--- DRY RUN (Google Chat への送信はスキップ) ---"))
            self.stdout.write(message_body)
            if image_bytes:
                # 一時ファイルとして保存して確認できるようにする
                tmp_dir = os.path.join(settings.MEDIA_ROOT, 'tmp')
                os.makedirs(tmp_dir, exist_ok=True)
                tmp_filepath = os.path.join(tmp_dir, 'nexus_monthly_chart_dryrun.png')
                with open(tmp_filepath, 'wb') as f:
                    f.write(image_bytes)
                self.stdout.write(self.style.SUCCESS(f"[dry-run] グラフ画像を一時的に保存しました: {tmp_filepath}"))
            self.stdout.write(self.style.SUCCESS("------------------------------------------------"))
        else:
            target_webhook = options.get('webhook_url') or getattr(settings, 'GOOGLE_CHAT_WEBHOOK_URL', '')
            target_gas = getattr(settings, 'GOOGLE_CHAT_GAS_WEB_APP_URL', '')

            if text_only or not image_bytes:
                # 従来通りのテキストメッセージによる通知
                try:
                    result = send_google_chat_message(message_body, webhook_url=target_webhook, gas_url='')
                    status_code = result.get('status_code') if result else '-'
                    self.stdout.write(
                        self.style.SUCCESS(f"Google Chat 通知の送信に成功しました (テキストのみ)。ステータス: {status_code}")
                    )
                except GoogleChatNotificationError as exc:
                    self.stdout.write(
                        self.style.ERROR(f"Google Chat 通知の送信中にエラーが発生しました: {exc}")
                    )
            else:
                # 画像付きリッチカードによる通知を試み、エラー時は自動的にテキストにフォールバック
                try:
                    result = send_google_chat_card_with_image(
                        text=intro_text,
                        image_bytes=image_bytes,
                        webhook_url=target_webhook,
                        gas_url=target_gas,
                    )
                    via_info = f"画像付き図形カード (送信経路: {result.get('via', '-')})"
                    status_code = result.get('status_code') if result else '-'
                    self.stdout.write(
                        self.style.SUCCESS(f"Google Chat 通知の送信に成功しました ({via_info})。ステータス: {status_code}")
                    )
                except GoogleChatNotificationError as card_exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"図付きでの送信に失敗したため、テキストのみでの送信へ自動フォールバックします。理由: {card_exc}"
                        )
                    )
                    try:
                        result = send_google_chat_message(message_body, webhook_url=target_webhook, gas_url='')
                        status_code = result.get('status_code') if result else '-'
                        self.stdout.write(
                            self.style.SUCCESS(f"Google Chat 通知の送信に成功しました (テキストフォールバック)。ステータス: {status_code}")
                        )
                    except GoogleChatNotificationError as exc:
                        self.stdout.write(
                            self.style.ERROR(f"フォールバック先テキスト通知の送信中にエラーが発生しました: {exc}")
                        )
