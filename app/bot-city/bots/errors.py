class SafetyHalt(RuntimeError):
    """Fail-closed condition that requires operator review before trading resumes."""


class OrderStateUnknown(SafetyHalt):
    """Raised when broker order state cannot be reconciled safely."""
