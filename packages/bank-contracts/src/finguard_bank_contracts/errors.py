class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def require(condition: bool, code: str, message: str, status: int = 400) -> None:
    if not condition:
        raise ServiceError(code, message, status)


class NotSubmitted(Exception):
    pass


class ResultUnknown(Exception):
    pass


class WorkerCrash(Exception):
    pass
