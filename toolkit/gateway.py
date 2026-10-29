"""The one error type every gateway (real client or fake) raises."""


class GatewayError(Exception):
    """A service error: the HTTP status plus the exact JSON error body the service sent.

    Everything else (type, code, decline_code, ...) is derived from ``body`` by the provider.
    """

    def __init__(self, http_status: int, body: dict):
        self.http_status = http_status
        self.body = body if isinstance(body, dict) else {"error": {"message": str(body)}}
        super().__init__(f"HTTP {http_status}: {self.message}")

    @property
    def error(self) -> dict:
        err = self.body.get("error")
        return err if isinstance(err, dict) else {}

    @property
    def message(self) -> str:
        return str(self.error.get("message") or "Unknown error")
