from __future__ import annotations

import logging
import time

from django.conf import settings
from django.db import DatabaseError, OperationalError
from django.http import JsonResponse

from .observability import (
    emit_api_exception_log,
    emit_api_request_log,
    generate_error_id,
    map_exception_to_code,
    resolve_route_template,
    validate_or_generate_request_id,
)

logger = logging.getLogger(__name__)

# High-churn paths — skip to keep CloudWatch noise/cost down.
_SKIP_API_REQUEST_LOG_PREFIXES = (
    '/api/v1/health',
    '/api/v1/bikes/health',  # ECS container healthcheck
)


class APIRequestLoggingMiddleware:
    """
    Emit one structured JSON api_request event per /api/ request (schema version 1).
    Never logs credentials, cookies, tokens, or PII.
    Correlates with api_exception events via request_id and error_id.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ''
        if not path.startswith('/api/'):
            return self.get_response(request)
        if any(path.startswith(p) for p in _SKIP_API_REQUEST_LOG_PREFIXES):
            return self.get_response(request)

        incoming = request.META.get('HTTP_X_REQUEST_ID')
        request_id = validate_or_generate_request_id(incoming)
        request.api_request_id = request_id

        started = time.perf_counter()
        status_code = 500
        response = None

        try:
            response = self.get_response(request)
            status_code = int(getattr(response, 'status_code', 500) or 500)
            try:
                response['X-Request-Id'] = request_id
            except Exception:
                pass
            return response
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            route = resolve_route_template(path)

            error_context = getattr(request, '_api_error_context', None) or {}
            error_id = error_context.get('error_id')
            error_code = error_context.get('error_code')
            error_fingerprint = error_context.get('error_fingerprint')

            if status_code >= 500:
                if not error_id:
                    error_id = generate_error_id()
                if not error_code:
                    error_code = 'HTTP_5XX'
                if not error_fingerprint:
                    error_fingerprint = f"{route}:HTTP_5XX"
            elif status_code >= 400:
                if not error_code:
                    error_code = f"HTTP_{status_code}"

            emit_api_request_log(
                request_id=request_id,
                method=request.method,
                route=route,
                status_code=status_code,
                duration_ms=duration_ms,
                error_id=error_id,
                error_code=error_code,
                error_fingerprint=error_fingerprint,
            )


class APIExceptionMiddleware:
    """
    Catch unhandled database errors and general exceptions for non-DRF views
    or any exceptions that bypass DRF's exception handler.
    Emits a structured JSON api_exception log and returns a sanitized JSON error.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not (request.path or '').startswith('/api/'):
            return self.get_response(request)

        try:
            return self.get_response(request)
        except (OperationalError, DatabaseError) as exc:
            return self._handle_db_exception(request, exc)
        except Exception as exc:
            return self._handle_generic_exception(request, exc)

    def process_exception(self, request, exception):
        from django.core.exceptions import PermissionDenied, SuspiciousOperation
        from django.http import Http404
        if isinstance(exception, (Http404, PermissionDenied, SuspiciousOperation)):
            return None
        if not (request.path or '').startswith('/api/'):
            return None
        if isinstance(exception, (OperationalError, DatabaseError)):
            return self._handle_db_exception(request, exception)
        return self._handle_generic_exception(request, exception)

    def _handle_db_exception(self, request, exc: BaseException) -> JsonResponse:
        request_id = getattr(request, 'api_request_id', None) or validate_or_generate_request_id(
            request.META.get('HTTP_X_REQUEST_ID')
        )
        error_id = generate_error_id()
        route = resolve_route_template(request.path or '')

        error_code, fingerprint = emit_api_exception_log(
            request_id=request_id,
            error_id=error_id,
            exc=exc,
            route=route,
        )
        request._api_error_context = {
            'error_id': error_id,
            'error_code': error_code,
            'error_fingerprint': fingerprint,
        }

        http_status = 503 if error_code == 'DB_CONNECTION_FAILED' else 500
        user_message = (
            'Unable to connect to service. Please try again later.'
            if error_code == 'DB_CONNECTION_FAILED'
            else 'A database error occurred. Please try again later.'
        )

        body = {
            'error': {
                'code': error_code,
                'message': user_message,
            },
            'request_id': request_id,
        }
        if settings.DEBUG:
            body['error']['debug_detail'] = str(exc)

        response = JsonResponse(body, status=http_status, json_dumps_params={'ensure_ascii': False})
        response['X-Request-Id'] = request_id
        return response

    def _handle_generic_exception(self, request, exc: BaseException) -> JsonResponse:
        request_id = getattr(request, 'api_request_id', None) or validate_or_generate_request_id(
            request.META.get('HTTP_X_REQUEST_ID')
        )
        error_id = generate_error_id()
        route = resolve_route_template(request.path or '')

        error_code, fingerprint = emit_api_exception_log(
            request_id=request_id,
            error_id=error_id,
            exc=exc,
            route=route,
            error_code='INTERNAL_ERROR',
        )
        request._api_error_context = {
            'error_id': error_id,
            'error_code': error_code,
            'error_fingerprint': fingerprint,
        }

        body = {
            'error': {
                'code': 'INTERNAL_ERROR',
                'message': 'An unexpected error occurred. Please try again later.',
            },
            'request_id': request_id,
        }
        if settings.DEBUG:
            body['error']['debug_detail'] = str(exc)

        response = JsonResponse(body, status=500, json_dumps_params={'ensure_ascii': False})
        response['X-Request-Id'] = request_id
        return response
