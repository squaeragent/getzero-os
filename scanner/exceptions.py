"""Domain exception hierarchy for ZERO OS.

All modules should raise these instead of bare Exception/ValueError/RuntimeError.
Enables targeted error handling, retry logic, and centralized error classification.
"""


class ZeroOSError(Exception):
    """Base exception for all ZERO OS errors."""
    pass


# ---------------------------------------------------------------------------
# API / Network errors
# ---------------------------------------------------------------------------

class APIError(ZeroOSError):
    """External API call failed (HL, Telegram, ENVY, etc.)."""

    def __init__(self, message: str, service: str = "", status_code: int = 0, retryable: bool = True):
        self.service = service
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class RateLimitError(APIError):
    """API rate limit exceeded — should back off and retry."""

    def __init__(self, message: str = "Rate limit exceeded", service: str = "", retry_after: float = 0):
        self.retry_after = retry_after
        super().__init__(message, service=service, retryable=True)


# ---------------------------------------------------------------------------
# Exchange / Trading errors
# ---------------------------------------------------------------------------

class ExchangeError(ZeroOSError):
    """Base for exchange-specific errors."""
    pass


class OrderError(ExchangeError):
    """Order placement, fill, or cancellation failed."""

    def __init__(self, message: str, coin: str = "", order_type: str = ""):
        self.coin = coin
        self.order_type = order_type
        super().__init__(message)


class StopLossError(OrderError):
    """Stop loss placement or verification failed — position may be unprotected."""

    def __init__(self, message: str, coin: str = "", attempts: int = 0):
        self.attempts = attempts
        super().__init__(message, coin=coin, order_type="stop_loss")


class InsufficientFundsError(ExchangeError):
    """Not enough equity/margin to place trade."""

    def __init__(self, message: str, required: float = 0, available: float = 0):
        self.required = required
        self.available = available
        super().__init__(message)


class DesyncError(ExchangeError):
    """Local state is out of sync with exchange state."""

    def __init__(self, message: str, local_count: int = 0, exchange_count: int = 0):
        self.local_count = local_count
        self.exchange_count = exchange_count
        super().__init__(message)


# ---------------------------------------------------------------------------
# Risk / Safety errors
# ---------------------------------------------------------------------------

class RiskError(ZeroOSError):
    """Risk check failed — trade should be rejected."""

    def __init__(self, message: str, gate: str = "", coin: str = ""):
        self.gate = gate
        self.coin = coin
        super().__init__(message)


class CircuitBreakerError(RiskError):
    """Circuit breaker tripped — all trading halted."""

    def __init__(self, message: str = "Circuit breaker tripped", reason: str = ""):
        self.reason = reason
        super().__init__(message, gate="circuit_breaker")


# ---------------------------------------------------------------------------
# Data / I/O errors
# ---------------------------------------------------------------------------

class BusIOError(ZeroOSError):
    """Bus file read/write failed — state may be inconsistent."""

    def __init__(self, message: str, path: str = "", operation: str = ""):
        self.path = path
        self.operation = operation
        super().__init__(message)


class ConfigError(ZeroOSError):
    """Configuration loading or validation failed."""

    def __init__(self, message: str, key: str = ""):
        self.key = key
        super().__init__(message)


# ---------------------------------------------------------------------------
# Evaluation errors
# ---------------------------------------------------------------------------

class EvaluationError(ZeroOSError):
    """Signal evaluation or indicator computation failed."""

    def __init__(self, message: str, coin: str = "", indicator: str = ""):
        self.coin = coin
        self.indicator = indicator
        super().__init__(message)
