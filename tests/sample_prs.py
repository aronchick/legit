"""Sample PR data for integration tests.

Each sample represents a different PR archetype that exercises different
parts of the review pipeline. Use these as fixtures in integration tests
to avoid duplicating large data structures.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Simple bug fix — single file, nil check
# ---------------------------------------------------------------------------

BUGFIX_NIL_CHECK = {
    "metadata": {
        "title": "Fix nil pointer dereference in auth handler",
        "user": {"login": "contributor-alice"},
        "body": (
            "The auth handler panics when claims are nil (e.g., expired token).\n"
            "This adds a defensive nil check before accessing claims fields.\n\n"
            "Fixes #101."
        ),
        "number": 200,
    },
    "diff": (
        "diff --git a/pkg/auth/handler.go b/pkg/auth/handler.go\n"
        "--- a/pkg/auth/handler.go\n"
        "+++ b/pkg/auth/handler.go\n"
        "@@ -22,6 +22,10 @@ func HandleAuth(w http.ResponseWriter, r *http.Request) {\n"
        "     claims, err := parseClaims(tokenStr)\n"
        "+    if claims == nil {\n"
        "+        log.Warn(\"nil claims from valid token parse\")\n"
        "+        http.Error(w, \"unauthorized\", http.StatusUnauthorized)\n"
        "+        return\n"
        "+    }\n"
        "     userID := claims.Subject\n"
    ),
    "files": [
        {"filename": "pkg/auth/handler.go", "additions": 5, "deletions": 0},
    ],
    "comments": [],
    "reviews": [],
    "linked_issues": [{"number": 101, "title": "Auth panic on nil claims"}],
}


# ---------------------------------------------------------------------------
# 2. New feature — multiple files, new middleware + tests
# ---------------------------------------------------------------------------

FEATURE_VALIDATION_MIDDLEWARE = {
    "metadata": {
        "title": "Add request validation middleware",
        "user": {"login": "dev-charlie"},
        "body": (
            "Adds input validation middleware that checks request bodies before "
            "they reach handlers.\n\n"
            "## Changes\n"
            "- New `ValidateRequest` middleware\n"
            "- Validation rules from struct tags\n"
            "- Error responses with field-level details\n\n"
            "Closes #250"
        ),
        "number": 300,
    },
    "diff": (
        "diff --git a/pkg/middleware/validate.go b/pkg/middleware/validate.go\n"
        "--- /dev/null\n"
        "+++ b/pkg/middleware/validate.go\n"
        "@@ -0,0 +1,45 @@\n"
        "+package middleware\n"
        "+\n"
        "+import (\n"
        '+    "encoding/json"\n'
        '+    "net/http"\n'
        "+)\n"
        "+\n"
        "+// ValidateRequest checks the request body against struct tag rules.\n"
        "+func ValidateRequest(next http.Handler) http.Handler {\n"
        "+    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {\n"
        "+        if r.Body == nil {\n"
        "+            http.Error(w, \"missing request body\", http.StatusBadRequest)\n"
        "+            return\n"
        "+        }\n"
        "+        var body map[string]interface{}\n"
        "+        if err := json.NewDecoder(r.Body).Decode(&body); err != nil {\n"
        "+            http.Error(w, \"invalid JSON\", http.StatusBadRequest)\n"
        "+            return\n"
        "+        }\n"
        "+        // TODO: validate fields against struct tags\n"
        "+        next.ServeHTTP(w, r)\n"
        "+    })\n"
        "+}\n"
        "\n"
        "diff --git a/pkg/middleware/validate_test.go b/pkg/middleware/validate_test.go\n"
        "--- /dev/null\n"
        "+++ b/pkg/middleware/validate_test.go\n"
        "@@ -0,0 +1,20 @@\n"
        "+package middleware\n"
        "+\n"
        '+import "testing"\n'
        "+\n"
        "+func TestValidateRequest_NilBody(t *testing.T) {\n"
        "+    // TODO: test nil body returns 400\n"
        "+}\n"
        "+\n"
        "+func TestValidateRequest_InvalidJSON(t *testing.T) {\n"
        "+    // TODO: test invalid JSON returns 400\n"
        "+}\n"
    ),
    "files": [
        {"filename": "pkg/middleware/validate.go", "additions": 25, "deletions": 0},
        {"filename": "pkg/middleware/validate_test.go", "additions": 12, "deletions": 0},
    ],
    "comments": [],
    "reviews": [],
    "linked_issues": [{"number": 250, "title": "Add request body validation"}],
}


# ---------------------------------------------------------------------------
# 3. Large refactor — many files, existing discussion
# ---------------------------------------------------------------------------

REFACTOR_HANDLER_MODULES = {
    "metadata": {
        "title": "Refactor handler modules for consistent error handling",
        "user": {"login": "contributor-bob"},
        "body": (
            "Standardizes error handling across all handler modules.\n\n"
            "Each handler now uses the shared `respondError` helper instead of "
            "inline http.Error calls. This ensures consistent error response format "
            "and makes it easier to add observability.\n\n"
            "Part of #400 (error handling improvement epic)."
        ),
        "number": 500,
    },
    "diff": "\n".join(
        [
            f"diff --git a/pkg/handlers/{name}.go b/pkg/handlers/{name}.go\n"
            f"--- a/pkg/handlers/{name}.go\n"
            f"+++ b/pkg/handlers/{name}.go\n"
            f"@@ -{10 + i * 5},3 +{10 + i * 5},5 @@ func Handle{name.title()}(w http.ResponseWriter, r *http.Request) {{\n"
            f"-    http.Error(w, \"internal error\", 500)\n"
            f"+    respondError(w, r, fmt.Errorf(\"{name} handler failed: %w\", err), http.StatusInternalServerError)\n"
            for i, name in enumerate(["auth", "users", "projects", "billing", "notifications", "settings", "webhooks", "search"])
        ]
    ),
    "files": [
        {"filename": f"pkg/handlers/{name}.go", "additions": 2, "deletions": 1}
        for name in ["auth", "users", "projects", "billing", "notifications", "settings", "webhooks", "search"]
    ],
    "comments": [
        {
            "body": "Have you benchmarked the new respondError helper? The old inline calls were zero-alloc.",
            "user": {"login": "perf-reviewer"},
            "path": "pkg/handlers/auth.go",
        },
        {
            "body": "Should we also update the gRPC handlers for consistency?",
            "user": {"login": "team-lead"},
            "path": "",
        },
    ],
    "reviews": [
        {"body": "Conceptually LGTM. Let's make sure the error format is documented.", "user": {"login": "senior-dev"}},
    ],
    "linked_issues": [{"number": 400, "title": "Error handling improvement epic"}],
}


# ---------------------------------------------------------------------------
# 4. Documentation-only PR
# ---------------------------------------------------------------------------

DOCS_ONLY = {
    "metadata": {
        "title": "Update API documentation for v2 endpoints",
        "user": {"login": "docs-writer"},
        "body": "Updates the API docs to reflect the v2 endpoint changes shipped in #450.",
        "number": 460,
    },
    "diff": (
        "diff --git a/docs/api.md b/docs/api.md\n"
        "--- a/docs/api.md\n"
        "+++ b/docs/api.md\n"
        "@@ -10,5 +10,12 @@ ## Authentication\n"
        "-All endpoints require Bearer token auth.\n"
        "+All endpoints require Bearer token auth. See [Auth Guide](/docs/auth.md) for details.\n"
        "+\n"
        "+## v2 Changes\n"
        "+- All responses now include `request_id` header\n"
        "+- Error responses use RFC 7807 format\n"
        "+- Pagination uses cursor-based tokens instead of offset\n"
    ),
    "files": [
        {"filename": "docs/api.md", "additions": 6, "deletions": 1},
    ],
    "comments": [],
    "reviews": [],
    "linked_issues": [],
}


# ---------------------------------------------------------------------------
# 5. Generated / vendor code — should trigger abstention
# ---------------------------------------------------------------------------

VENDOR_AND_GENERATED = {
    "metadata": {
        "title": "Regenerate protobuf stubs and update vendor deps",
        "user": {"login": "bot-renovate"},
        "body": "Automated PR: regenerated proto stubs and updated vendored dependencies.",
        "number": 600,
    },
    "diff": (
        "diff --git a/vendor/google.golang.org/grpc/status.go b/vendor/google.golang.org/grpc/status.go\n"
        "--- a/vendor/google.golang.org/grpc/status.go\n"
        "+++ b/vendor/google.golang.org/grpc/status.go\n"
        "@@ -1,3 +1,3 @@\n"
        "-// Package status v1.60.0\n"
        "+// Package status v1.61.0\n"
        "\n"
        "diff --git a/pkg/api/v1/service.pb.go b/pkg/api/v1/service.pb.go\n"
        "--- a/pkg/api/v1/service.pb.go\n"
        "+++ b/pkg/api/v1/service.pb.go\n"
        "@@ -1,4 +1,4 @@\n"
        "-// Code generated by protoc-gen-go. DO NOT EDIT. v1.32.0\n"
        "+// Code generated by protoc-gen-go. DO NOT EDIT. v1.33.0\n"
    ),
    "files": [
        {"filename": "vendor/google.golang.org/grpc/status.go", "additions": 1, "deletions": 1},
        {"filename": "pkg/api/v1/service.pb.go", "additions": 1, "deletions": 1},
    ],
    "comments": [],
    "reviews": [],
    "linked_issues": [],
}


# ---------------------------------------------------------------------------
# Helper: all sample PRs for parametrized tests
# ---------------------------------------------------------------------------

ALL_SAMPLE_PRS = {
    "bugfix_nil_check": BUGFIX_NIL_CHECK,
    "feature_validation": FEATURE_VALIDATION_MIDDLEWARE,
    "refactor_handlers": REFACTOR_HANDLER_MODULES,
    "docs_only": DOCS_ONLY,
    "vendor_generated": VENDOR_AND_GENERATED,
}
