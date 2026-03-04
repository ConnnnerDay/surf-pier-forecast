"""OpenAPI spec generation for Surf & Pier Forecast API."""

from __future__ import annotations

from typing import Any, Dict


def build_openapi_spec() -> Dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Surf & Pier Forecast API",
            "version": "1.0.0",
            "description": "Versioned API for forecast, profile, and catch log operations.",
        },
        "servers": [{"url": "/"}],
        "components": {
            "schemas": {
                "ApiError": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                    },
                    "required": ["code", "message", "details"],
                },
                "Envelope": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "data": {"type": "object", "nullable": True},
                        "error": {"oneOf": [{"$ref": "#/components/schemas/ApiError"}, {"type": "null"}]},
                        "meta": {
                            "type": "object",
                            "properties": {"version": {"type": "string"}},
                            "required": ["version"],
                        },
                    },
                    "required": ["ok", "data", "error", "meta"],
                },
            }
        },
        "paths": {
            "/api/v1/forecast": {
                "get": {
                    "summary": "Get forecast",
                    "parameters": [
                        {"name": "location_id", "in": "query", "schema": {"type": "string"}},
                        {"name": "force_refresh", "in": "query", "schema": {"type": "boolean", "default": False}},
                    ],
                    "responses": {
                        "200": {"description": "Forecast response", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}}},
                        "404": {"description": "Location not found"},
                        "503": {"description": "No forecast available"},
                    },
                }
            },
            "/api/v1/forecast/{location_id}/status": {
                "get": {
                    "summary": "Get cache refresh status for a location",
                    "parameters": [{"name": "location_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Forecast cache status", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}}},
                    },
                }
            },
            "/api/v1/profile": {
                "get": {"summary": "Get current user profile"},
                "post": {"summary": "Update current user profile"},
            },
            "/api/v1/log": {
                "get": {"summary": "Get catch log for current user/location"},
                "post": {"summary": "Create catch log entry"},
            },
            "/api/v1/log/{entry_id}": {
                "delete": {
                    "summary": "Delete catch log entry",
                    "parameters": [{"name": "entry_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                }
            },
            "/api/openapi.json": {
                "get": {"summary": "OpenAPI spec"}
            },
        },
    }
