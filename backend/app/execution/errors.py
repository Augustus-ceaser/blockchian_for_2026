class ExecutionCoordinatorError(ValueError):
    """Base fail-closed coordinator error."""


class ExecutionSubmissionConflict(ExecutionCoordinatorError):
    """A stable submission key was reused with a different request digest."""


class ExecutionSubmissionUnknown(ExecutionCoordinatorError):
    """Submission outcome is unknown and must be reconciled before retry."""


class NonRetryableExecutionRequest(ExecutionCoordinatorError):
    """The current authorization or request is no longer executable."""
