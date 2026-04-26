"""
Machine-readable error code constants for Sparrow DroneID API responses.

Each constant maps to a canonical HTTP status code range; the actual HTTP
status is set by the handler.  Using string codes keeps error identities
stable even if HTTP semantics shift in future versions.
"""


class ErrorCode:
    VALIDATION_ERROR    = 'VALIDATION_ERROR'       # 400 — malformed request / bad params
    AUTH_REQUIRED       = 'AUTH_REQUIRED'           # 401 — missing or invalid Bearer token
    FORBIDDEN           = 'FORBIDDEN'               # 403 — valid auth but insufficient rights
    NOT_FOUND           = 'NOT_FOUND'               # 404 — resource does not exist
    CONFLICT            = 'CONFLICT'                # 409 — request conflicts with current state
    INTERNAL_ERROR      = 'INTERNAL_ERROR'          # 500 — unexpected server-side failure
    BAD_GATEWAY         = 'BAD_GATEWAY'             # 502 — upstream / remote dependency failed
    SERVICE_UNAVAILABLE = 'SERVICE_UNAVAILABLE'     # 503 — engine or subsystem not ready
