"""
Observability and structured logging helpers for TiMoto Mobile Backend.
Emits schema-versioned JSON events (api_request and api_exception) for CloudWatch Logs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from django.conf import settings
from django.urls import Resolver404, resolve

api_request_logger = logging.getLogger('api.request')
api_exception_logger = logging.getLogger('api.exception')

_SAFE_REQUEST_ID_REGEX = re.compile(r'^[a-zA-Z0-9_\-\.]{8,64}$')


def get_service_name() -> str:
    return getattr(settings, 'SERVICE_NAME', 'timoto-mobile-backend')


def get_environment() -> str:
    return getattr(settings, 'ENVIRONMENT', 'development')


def get_aws_region() -> str:
    return getattr(settings, 'AWS_REGION', os.environ.get('AWS_REGION', 'ap-southeast-7'))


def get_release_sha() -> str:
    return getattr(settings, 'RELEASE_SHA', os.environ.get('RELEASE_SHA', 'dev-local'))


def validate_or_generate_request_id(incoming_header: str | None) -> str:
    """Validate client X-Request-ID to prevent injection; generate UUID hex fallback."""
    if incoming_header:
        cleaned = incoming_header.strip()
        if _SAFE_REQUEST_ID_REGEX.match(cleaned):
            return cleaned
    return uuid.uuid4().hex


def generate_error_id() -> str:
    return f"err_{uuid.uuid4().hex[:12]}"


def resolve_route_template(path: str) -> str:
    """
    Resolve Django URL route template (e.g. '/api/v1/bikes/<uuid:bike_id>/cancel/').
    Falls back cleanly for 404s and non-matching endpoints.
    """
    if not path:
        return 'unknown_empty_path'
    try:
        match = resolve(path)
        route = getattr(match, 'route', None)
        if route:
            clean = route.strip('/')
            return f"/{clean}/" if clean else '/'
        if getattr(match, 'view_name', None):
            return match.view_name
        return path
    except Resolver404:
        return 'unmatched_404'
    except Exception:
        return path


def extract_sanitized_stack_frames(exc: BaseException) -> list[dict[str, Any]]:
    """Extract and sanitize stack frames from exception, keeping paths clean and relative."""
    tb = exc.__traceback__
    if not tb:
        return []
    frames = traceback.extract_tb(tb)
    sanitized: list[dict[str, Any]] = []

    for frame in frames:
        filename = frame.filename
        # Normalize local absolute paths to relative workspace paths
        if '/backend/' in filename:
            filename = 'backend/' + filename.split('/backend/', 1)[1]
        elif '/apps/' in filename:
            filename = 'apps/' + filename.split('/apps/', 1)[1]

        sanitized.append({
            'file': filename,
            'function': frame.name,
            'line': frame.lineno,
        })

    # Return at most last 10 frames to keep log events concise
    return sanitized[-10:]


def calculate_error_fingerprint(
    *,
    service: str,
    exception_type: str,
    route: str,
    frames: list[dict[str, Any]],
) -> str:
    """
    Build a deterministic, stable error fingerprint for grouping incidents.
    Does NOT contain request_id, timestamp, or user data.
    """
    top_frame_repr = "unknown:0"
    for frame in reversed(frames):
        file_name = frame.get('file', '')
        # Prefer frames from app code rather than django/third-party internals
        if 'site-packages' not in file_name:
            top_frame_repr = f"{os.path.basename(file_name)}:{frame.get('line', 0)}"
            break
    if top_frame_repr == "unknown:0" and frames:
        last = frames[-1]
        top_frame_repr = f"{os.path.basename(last.get('file', ''))}:{last.get('line', 0)}"

    clean_route = route.replace('/', '_').strip('_')
    return f"{service}:{exception_type}:{clean_route}:{top_frame_repr}"


def map_exception_to_code(exc: BaseException) -> str:
    """Map exception instances to stable, standardized machine-readable error codes."""
    exc_type = type(exc).__name__
    msg = str(exc).lower()

    from django.db import OperationalError, DatabaseError
    from django_ratelimit.exceptions import Ratelimited
    from rest_framework.exceptions import (
        AuthenticationFailed,
        NotAuthenticated,
        PermissionDenied,
        ValidationError,
    )

    if isinstance(exc, Ratelimited):
        return 'RATE_LIMITED'
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        return 'UNAUTHENTICATED'
    if isinstance(exc, PermissionDenied):
        return 'PERMISSION_DENIED'
    if isinstance(exc, ValidationError):
        return 'VALIDATION_ERROR'
    if isinstance(exc, (OperationalError, DatabaseError)):
        if 'timeout' in msg or 'connection' in msg:
            return 'DB_CONNECTION_FAILED'
        if 'column' in msg and 'does not exist' in msg:
            return 'DB_SCHEMA_MISMATCH'
        return 'DATABASE_ERROR'

    return 'INTERNAL_ERROR'


def emit_api_request_log(
    *,
    request_id: str,
    method: str,
    route: str,
    status_code: int,
    duration_ms: int,
    error_id: str | None = None,
    error_code: str | None = None,
    error_fingerprint: str | None = None,
) -> None:
    """Emit a structured JSON api_request event (schema version 1)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    level = 'ERROR' if status_code >= 500 else ('WARNING' if status_code >= 400 else 'INFO')

    payload: dict[str, Any] = {
        'schema_version': 1,
        'event': 'api_request',
        'timestamp': now_iso,
        'level': level,
        'service': get_service_name(),
        'environment': get_environment(),
        'aws_region': get_aws_region(),
        'release_sha': get_release_sha(),
        'request_id': request_id,
        'trace_id': None,
        'method': method,
        'route': route,
        'status': int(status_code),
        'duration_ms': int(duration_ms),
        'error_id': error_id,
        'error_code': error_code,
        'error_fingerprint': error_fingerprint,
    }

    log_line = json.dumps(payload, ensure_ascii=False)
    if status_code >= 500:
        api_request_logger.error(log_line)
    elif status_code >= 400:
        api_request_logger.warning(log_line)
    else:
        api_request_logger.info(log_line)


def emit_api_exception_log(
    *,
    request_id: str,
    error_id: str,
    exc: BaseException,
    route: str,
    error_code: str | None = None,
) -> tuple[str, str]:
    """
    Emit a structured JSON api_exception event (schema version 1).
    Returns (error_code, error_fingerprint).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    exception_type = type(exc).__name__
    resolved_code = error_code or map_exception_to_code(exc)
    frames = extract_sanitized_stack_frames(exc)
    fingerprint = calculate_error_fingerprint(
        service=get_service_name(),
        exception_type=exception_type,
        route=route,
        frames=frames,
    )

    # Sanitize message to avoid leaking passwords or inline params
    sanitized_msg = resolved_code

    payload: dict[str, Any] = {
        'schema_version': 1,
        'event': 'api_exception',
        'timestamp': now_iso,
        'level': 'ERROR',
        'service': get_service_name(),
        'environment': get_environment(),
        'aws_region': get_aws_region(),
        'release_sha': get_release_sha(),
        'request_id': request_id,
        'error_id': error_id,
        'exception_type': exception_type,
        'error_code': resolved_code,
        'message': sanitized_msg,
        'stack_frames': frames,
        'error_fingerprint': fingerprint,
    }

    api_exception_logger.error(json.dumps(payload, ensure_ascii=False))
    return resolved_code, fingerprint
