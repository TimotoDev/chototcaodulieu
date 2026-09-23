from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError, OperationalError
from django_ratelimit.exceptions import Ratelimited
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .observability import (
    emit_api_exception_log,
    generate_error_id,
    map_exception_to_code,
    resolve_route_template,
    validate_or_generate_request_id,
)


def custom_exception_handler(exc, context):
    """
    Custom exception handler for DRF:
    - Emits structured JSON `api_exception` log event with stack frames and fingerprint.
    - Attaches correlation error context to request for the outer request summary log.
    - Sanitizes client response on production (no raw DB error leakage; only stable code + request_id).
    """
    request = context.get('request') if isinstance(context, dict) else None
    request = getattr(request, '_request', request)
    incoming_req_id = None
    path_info = ''
    if request is not None:
        incoming_req_id = getattr(request, 'api_request_id', None) or (
            request.META.get('HTTP_X_REQUEST_ID') if hasattr(request, 'META') else None
        )
        path_info = getattr(request, 'path_info', '') or getattr(request, 'path', '')

    request_id = validate_or_generate_request_id(incoming_req_id)
    if request is not None and not hasattr(request, 'api_request_id'):
        request.api_request_id = request_id

    route = resolve_route_template(path_info)
    error_id = generate_error_id()

    # Rate limiting
    if isinstance(exc, Ratelimited):
        error_code = 'RATE_LIMITED'
        if request is not None:
            request._api_error_context = {
                'error_id': error_id,
                'error_code': error_code,
                'error_fingerprint': f"{route}:RATE_LIMITED",
            }
        return Response(
            {
                'error': {
                    'code': error_code,
                    'message': 'Quá nhiều lần thử trong thời gian ngắn. Vui lòng đợi một lát rồi thử lại.',
                },
                'request_id': request_id,
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # Let DRF handle standard exceptions (ValidationError, AuthenticationFailed, 404, etc.)
    response = exception_handler(exc, context)

    # Database exceptions (OperationalError, DatabaseError)
    if isinstance(exc, (OperationalError, DatabaseError)):
        error_code, fingerprint = emit_api_exception_log(
            request_id=request_id,
            error_id=error_id,
            exc=exc,
            route=route,
        )
        if request is not None:
            request._api_error_context = {
                'error_id': error_id,
                'error_code': error_code,
                'error_fingerprint': fingerprint,
            }

        http_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if error_code == 'DB_CONNECTION_FAILED'
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )

        user_message = (
            'Unable to connect to service. Please try again later.'
            if error_code == 'DB_CONNECTION_FAILED'
            else 'A database error occurred. Please try again later.'
        )

        resp_body = {
            'error': {
                'code': error_code,
                'message': user_message,
            },
            'request_id': request_id,
        }
        if settings.DEBUG:
            resp_body['error']['debug_detail'] = str(exc)

        return Response(resp_body, status=http_status)

    # Unhandled 500 exceptions that DRF did not handle (response is None)
    if response is None:
        error_code, fingerprint = emit_api_exception_log(
            request_id=request_id,
            error_id=error_id,
            exc=exc,
            route=route,
            error_code='INTERNAL_ERROR',
        )
        if request is not None:
            request._api_error_context = {
                'error_id': error_id,
                'error_code': error_code,
                'error_fingerprint': fingerprint,
            }

        resp_body = {
            'error': {
                'code': 'INTERNAL_ERROR',
                'message': 'An unexpected error occurred. Please try again later.',
            },
            'request_id': request_id,
        }
        if settings.DEBUG:
            resp_body['error']['debug_detail'] = str(exc)

        return Response(resp_body, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # For DRF-handled 5xx errors (rare), log the exception event
    if response.status_code >= 500:
        error_code, fingerprint = emit_api_exception_log(
            request_id=request_id,
            error_id=error_id,
            exc=exc,
            route=route,
        )
        if request is not None:
            request._api_error_context = {
                'error_id': error_id,
                'error_code': error_code,
                'error_fingerprint': fingerprint,
            }
        # Add request_id to response data if dict
        if isinstance(response.data, dict):
            response.data.setdefault('request_id', request_id)

    return response
