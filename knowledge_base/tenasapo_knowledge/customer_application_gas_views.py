"""GAS発信専用。ブラウザーセッションの代わりに各POSTのHMACを必須にする。"""

import json

from django.contrib.auth.decorators import login_not_required
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables

from . import customer_application_gas as gas
from .customer_applications import ApplicationError


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def endpoint(action):
    @transaction.non_atomic_requests
    @csrf_exempt
    @login_not_required
    @sensitive_variables()
    def view(request):
        try:
            if not request.is_secure():
                raise gas.GasError('https_required', 400)
            actor = gas.authorize(request)
            payload = json.loads(request.body.decode('utf-8'), object_pairs_hook=_reject_duplicate_keys,
                                 parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            result = gas.dispatch(action, payload, actor)
            response = JsonResponse(result)
        except gas.GasError as exc:
            response = JsonResponse({'ok': False, 'error': exc.code}, status=exc.status)
        except (ValueError, UnicodeError, RecursionError, ApplicationError):
            response = JsonResponse({'ok': False, 'error': 'invalid_request'}, status=400)
        except Exception:
            # HTTP/debug応答や例外ログに回答・共有秘密・招待URLを出さない。
            response = JsonResponse({'ok': False, 'error': 'service_unavailable'}, status=503)
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    # 独自の規約・パスワード強制ゲートからも、この署名専用ビューだけ除外。
    view.customer_application_signed_endpoint = True
    return view


gas_status = endpoint('ping')
gas_import = endpoint('import')
gas_claim = endpoint('claim')
gas_ack = endpoint('ack')