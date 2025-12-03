import logging

from django.conf import settings
from django.http import JsonResponse
from django.template.response import ContentNotRenderedError, TemplateResponse

logger = logging.getLogger(__name__)


class ApiJsonErrorMiddleware:
    """Convert HTML error responses for API paths into JSON responses."""

    def __init__(self, get_response):  # noqa: D107
        self.get_response = get_response

    def __call__(self, request):  # noqa: D102
        response = self.get_response(request)

        try:
            path = request.path or ""
        except Exception:
            path = ""

        if path.startswith("/api/") and response is not None:
            status = getattr(response, "status_code", 200)

            if isinstance(response, TemplateResponse):
                try:
                    response = response.render()
                except ContentNotRenderedError:
                    logger.exception(
                        "TemplateResponse could not be rendered for %s", path
                    )
                except Exception:
                    logger.exception(
                        "Error while rendering TemplateResponse for %s", path
                    )

            if hasattr(response, "headers"):
                content_type = response.headers.get("Content-Type", "")
            elif hasattr(response, "get"):
                try:
                    content_type = response.get("Content-Type", "")
                except Exception:
                    content_type = getattr(response, "content_type", "")
            else:
                content_type = getattr(response, "content_type", "")

            if status >= 400 and (not content_type or "html" in content_type.lower()):
                if content_type and "application/json" in content_type:
                    return response

                default_messages = {
                    400: "Bad request.",
                    401: "Unauthorized.",
                    403: "Permission denied.",
                    404: "Not found.",
                    405: "Method not allowed.",
                    500: "Internal server error.",
                }

                message = default_messages.get(status, "Error")
                if settings.DEBUG and hasattr(response, "content"):
                    try:
                        detail = response.content.decode(errors="ignore")
                    except Exception:
                        detail = None
                    payload = {"detail": message}
                    if detail:
                        payload["debug_html_snippet"] = detail[:2000]
                else:
                    payload = {"detail": message}

                return JsonResponse(payload, status=status)

        return response

    def process_exception(self, request, exception):
        """Intercept unhandled exceptions for API paths and return JSON.

        This prevents Django from rendering the HTML technical 500 page
        (when DEBUG=True) for API calls.
        """
        try:
            path = request.path or ""
        except Exception:
            path = ""

        if not path.startswith("/api/"):
            return None

        logger.exception("Unhandled exception during API request: %s", path)

        detail = str(exception) if settings.DEBUG else "Internal server error."

        return JsonResponse({"detail": detail}, status=500)
