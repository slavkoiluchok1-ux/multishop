"""Rate limits for write endpoints that can be abused anonymously."""
from rest_framework.throttling import SimpleRateThrottle


class WriteScopedRateThrottle(SimpleRateThrottle):
    """Apply a scoped rate only to the write methods of an endpoint."""

    methods = frozenset({"POST", "PUT", "PATCH"})

    def allow_request(self, request, view):
        if request.method not in self.methods:
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = str(request.user.pk)
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginRateThrottle(WriteScopedRateThrottle):
    scope = "login"


class RegistrationRateThrottle(WriteScopedRateThrottle):
    scope = "registration"


class SupportRateThrottle(WriteScopedRateThrottle):
    scope = "support"


class ComplaintRateThrottle(WriteScopedRateThrottle):
    scope = "complaint"


class ReviewRateThrottle(WriteScopedRateThrottle):
    scope = "review"


class UploadRateThrottle(WriteScopedRateThrottle):
    scope = "upload"


class ActivationCodeCheckThrottle(WriteScopedRateThrottle):
    scope = "activation_code_check"
