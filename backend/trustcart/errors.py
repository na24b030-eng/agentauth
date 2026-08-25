from dataclasses import dataclass


@dataclass(slots=True)
class TrustCartError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict | None = None

    def __str__(self) -> str:
        return self.message


class AuthorizationError(TrustCartError):
    pass


class ConflictError(TrustCartError):
    pass


class ProviderAmbiguousError(TrustCartError):
    pass
